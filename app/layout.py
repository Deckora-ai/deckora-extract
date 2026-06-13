"""POST /v1/layout — deterministic per-page geometry for layout reconstruction.

Returns the raw building blocks a layout-reconstruction engine needs: text
spans with font/style/color/direction, placed image frames with intrinsic
pixel sizes, and large solid vector fills (bands/backgrounds). Zero LLM, zero
heuristics beyond size/color gates — everything is read straight out of the
PDF via PyMuPDF.

Auth + download are the SAME path as /v1/extract: the route handler lazily
imports app.main and reuses _verify_signature / _download_pdf (lazy to avoid
a circular import — main registers this route at its bottom).

The pure function extract_layout(pdf_path, ...) is the testable core; the
HTTP handler is a thin shell around it.
"""
from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

import fitz
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

# Per-page caps keep the payload bounded on pathological PDFs (vector-art
# pages can carry tens of thousands of drawings). A capped page is flagged
# "truncated": true rather than silently dropped.
SPAN_CAP = 400
IMAGE_CAP = 40
FILL_CAP = 60

DEFAULT_MAX_PAGES = 40
MAX_PAGES_CEILING = 60

# Fill gates: only solid fills big enough to be layout bands/backgrounds,
# and never white/near-white (those are the page itself, not layout).
MIN_FILL_AREA_FRACTION = 0.02
NEAR_WHITE_FLOOR = 0.95

# PyMuPDF span flag bits (see TextPage docs).
_FLAG_ITALIC = 2
_FLAG_BOLD = 16

_BOLD_NAME_HINTS = ("bold", "black", "heavy", "extrabold", "semibold", "demibold")
_ITALIC_NAME_HINTS = ("italic", "oblique")


def _svc():
    """Lazy handle on app.main — avoids the circular import at module load."""
    from app import main as _main  # noqa: PLC0415
    return _main


def _r2(v: float) -> float:
    return round(float(v), 2)


def _bbox(b) -> list:
    """Normalize a fitz.Rect or 4-tuple to a rounded [x0, y0, x1, y1]."""
    if hasattr(b, "x0"):
        return [_r2(b.x0), _r2(b.y0), _r2(b.x1), _r2(b.y1)]
    x0, y0, x1, y1 = b
    return [_r2(x0), _r2(y0), _r2(x1), _r2(y1)]


def _int_color_hex(c) -> str:
    """Span color int (sRGB packed) -> '#rrggbb'."""
    try:
        c = int(c) & 0xFFFFFF
    except (TypeError, ValueError):
        c = 0
    return f"#{c:06x}"


def _tuple_color_hex(col) -> str:
    """Drawing fill color (gray / rgb / cmyk floats in 0..1) -> '#rrggbb'."""
    try:
        comps = [float(x) for x in col]
    except (TypeError, ValueError):
        return "#000000"
    if len(comps) == 1:           # grayscale
        r = g = b = comps[0]
    elif len(comps) == 4:         # CMYK -> RGB
        c, m, y, k = comps
        r, g, b = (1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k)
    elif len(comps) >= 3:
        r, g, b = comps[0], comps[1], comps[2]
    else:
        return "#000000"
    to8 = lambda v: max(0, min(255, int(round(v * 255))))  # noqa: E731
    return f"#{to8(r):02x}{to8(g):02x}{to8(b):02x}"


def _fill_is_near_white(col) -> bool:
    """True when every RGB component is >= NEAR_WHITE_FLOOR (page-colored)."""
    try:
        comps = [float(x) for x in col]
    except (TypeError, ValueError):
        return False
    if len(comps) == 4:  # CMYK: convert before judging
        c, m, y, k = comps
        comps = [(1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k)]
    elif len(comps) == 1:
        comps = comps * 3
    return bool(comps) and all(v >= NEAR_WHITE_FLOOR for v in comps[:3])


# Per-frame image rendering budget (Studio reconstruction "match the PDF" mode).
# Each placed image is normalized to a browser-safe JPEG so the worker can drop
# the PDF's own pixels into the matching Studio frame. Bounded so the response
# stays sane on image-heavy OMs.
FRAME_MAX_EDGE = 1000          # downscale longest edge to this before encoding
FRAME_JPEG_QUALITY = 72
FRAME_MAX_BYTES = 1_200_000    # skip a single frame whose JPEG is still bigger
FRAME_DOC_BUDGET = 28          # max rendered frames per document


def _render_image_b64(doc: fitz.Document, xref: int) -> str | None:
    """Embedded image at `xref` -> base64 browser-safe JPEG, or None.

    Uses the image's OWN pixels (not a page-region render), so no overlaid page
    text or vector branding is baked in — just the picture that was placed.
    Normalizes colorspace (CMYK/alpha -> RGB) and downscales oversized images.
    """
    import base64 as _b64
    try:
        pix = fitz.Pixmap(doc, xref)
        # Normalize colorspace to RGB (handles CMYK=4, indexed, separation, etc.).
        # cs.n is the component count: 1 gray, 3 rgb — both JPEG-encodable as-is.
        cs = pix.colorspace
        if cs is None or cs.n not in (1, 3):
            pix = fitz.Pixmap(fitz.csRGB, pix)
        # Drop the alpha channel — JPEG cannot encode it, and many OM images
        # (esp. PowerPoint exports with SMasks) arrive with alpha; without this
        # tobytes("jpeg") raises and every masked image is silently lost.
        if pix.alpha:
            pix = fitz.Pixmap(pix, 0)
        # Halve until the longest edge is at/under the cap (shrink is power-of-2).
        guard = 0
        while max(pix.width, pix.height) > FRAME_MAX_EDGE and guard < 6:
            pix.shrink(1)
            guard += 1
        jpg = pix.tobytes("jpeg", jpg_quality=FRAME_JPEG_QUALITY)
        if not jpg or len(jpg) > FRAME_MAX_BYTES:
            return None
        return _b64.b64encode(jpg).decode()
    except Exception:  # noqa: BLE001 — a bad stream never sinks the page
        return None


def _page_layout(doc: fitz.Document, page: fitz.Page, number: int,
                 render_frames: bool = False, budget: dict | None = None) -> dict:
    rect = page.rect
    out: dict = {
        "number": number,
        "width_pt": _r2(rect.width),
        "height_pt": _r2(rect.height),
        "spans": [],
        "images": [],
        "fills": [],
    }
    truncated = False

    # ---- text spans -----------------------------------------------------
    spans = out["spans"]
    text_dict = page.get_text("dict")
    for block in text_dict.get("blocks", []):
        if block.get("type") != 0:  # text blocks only
            continue
        for line in block.get("lines", []):
            ldir = line.get("dir") or (1.0, 0.0)
            for span in line.get("spans", []):
                text = (span.get("text") or "").strip()
                if not text:
                    continue
                if len(spans) >= SPAN_CAP:
                    truncated = True
                    break
                flags = int(span.get("flags") or 0)
                font = str(span.get("font") or "")
                fl = font.lower()
                spans.append({
                    "text": text,
                    "bbox": _bbox(span.get("bbox") or (0, 0, 0, 0)),
                    "font": font,
                    "size": _r2(span.get("size") or 0),
                    "bold": bool(flags & _FLAG_BOLD)
                            or any(h in fl for h in _BOLD_NAME_HINTS),
                    "italic": bool(flags & _FLAG_ITALIC)
                              or any(h in fl for h in _ITALIC_NAME_HINTS),
                    "color": _int_color_hex(span.get("color")),
                    "line_dir": [_r2(ldir[0]), _r2(ldir[1])],
                })

    # ---- placed images --------------------------------------------------
    images = out["images"]
    seen_xrefs = set()
    for entry in page.get_images(full=True):
        xref = entry[0]
        if xref in seen_xrefs:
            continue
        seen_xrefs.add(xref)
        try:
            rects = page.get_image_rects(xref)
        except Exception:  # noqa: BLE001 — a bad xref never sinks the page
            continue
        if not rects:
            continue
        # Intrinsic pixel size; extract_image can fail on exotic streams, so
        # fall back to the width/height carried in the get_images tuple.
        try:
            info = doc.extract_image(xref)
            w_px, h_px = int(info.get("width") or 0), int(info.get("height") or 0)
        except Exception:  # noqa: BLE001
            w_px, h_px = int(entry[2] or 0), int(entry[3] or 0)
        for r in rects:
            if len(images) >= IMAGE_CAP:
                truncated = True
                break
            img_entry = {
                "xref": int(xref),
                "bbox": _bbox(r),
                "width_px": w_px,
                "height_px": h_px,
            }
            # "Match the PDF" mode: attach the frame's own pixels so the Studio
            # composer can fill the matching frame. Budget-bounded across the doc.
            if render_frames and budget is not None and budget.get("left", 0) > 0:
                b64 = _render_image_b64(doc, int(xref))
                if b64:
                    img_entry["data_b64"] = b64
                    img_entry["fmt"] = "jpeg"
                    budget["left"] -= 1
            images.append(img_entry)

    # ---- large solid vector fills ---------------------------------------
    fills = out["fills"]
    page_area = max(rect.width * rect.height, 1.0)
    for d in page.get_drawings():
        fill = d.get("fill")
        if fill is None:
            continue
        if _fill_is_near_white(fill):
            continue
        r = d.get("rect")
        if r is None:
            continue
        if r.x1 <= r.x0 or r.y1 <= r.y0:
            continue
        if (r.width * r.height) / page_area < MIN_FILL_AREA_FRACTION:
            continue
        if len(fills) >= FILL_CAP:
            truncated = True
            break
        fills.append({"bbox": _bbox(r), "color": _tuple_color_hex(fill)})

    if truncated:
        out["truncated"] = True
    return out


def extract_layout(pdf_path, asset_id: str = "om",
                   max_pages: int = DEFAULT_MAX_PAGES,
                   render_frames: bool = False) -> dict:
    """Pure, offline layout extraction — the testable core of /v1/layout.

    render_frames: when True, each placed image carries `data_b64` (a browser-
    safe JPEG of its own pixels) so the caller can fill the matching frame with
    the PDF's actual imagery. Bounded by FRAME_DOC_BUDGET across the document.
    """
    try:
        doc = fitz.open(str(pdf_path))
    except Exception as e:  # noqa: BLE001 — corrupt files become a clean 422
        raise HTTPException(422, f"pdf could not be parsed: {e}")
    try:
        if doc.needs_pass:
            raise HTTPException(422, "pdf could not be parsed: encrypted")
        page_count = doc.page_count
        max_pages = max(1, min(MAX_PAGES_CEILING, int(max_pages)))
        budget = {"left": FRAME_DOC_BUDGET} if render_frames else None
        pages = []
        for i in range(min(page_count, max_pages)):
            try:
                pages.append(_page_layout(doc, doc.load_page(i), i + 1,
                                          render_frames=render_frames, budget=budget))
            except Exception as e:  # noqa: BLE001 — one bad page never sinks the doc
                pages.append({"number": i + 1, "error": str(e)})
        try:
            version = _svc().APP_VERSION
        except Exception:  # noqa: BLE001 — pure-function callers without the app
            version = "unknown"
        return {
            "service_version": version,
            "asset_id": asset_id,
            "page_count": page_count,
            "pages": pages,
        }
    finally:
        doc.close()


async def layout_endpoint(request: Request):
    """POST /v1/layout — same HMAC auth + download path as /v1/extract."""
    svc = _svc()
    raw = await request.body()
    svc._verify_signature(request, raw)
    try:
        body = json.loads(raw or b"{}")
    except json.JSONDecodeError:
        raise HTTPException(400, "invalid JSON body")

    pdf_url = body.get("pdf_url")
    asset_id = str(body.get("asset_id") or "om")[:80]
    try:
        max_pages = int(body.get("max_pages") or DEFAULT_MAX_PAGES)
    except (TypeError, ValueError):
        max_pages = DEFAULT_MAX_PAGES
    max_pages = max(1, min(MAX_PAGES_CEILING, max_pages))
    render_frames = bool(body.get("render_frames"))

    t0 = time.time()
    with tempfile.TemporaryDirectory() as td:
        pdf_path = Path(td) / "om.pdf"
        pdf_bytes = svc._download_pdf(pdf_url, pdf_path)
        out = extract_layout(pdf_path, asset_id=asset_id, max_pages=max_pages,
                             render_frames=render_frames)
    out["pdf_bytes"] = pdf_bytes
    out["timings_ms"] = {"total": int((time.time() - t0) * 1000)}
    return JSONResponse(out)

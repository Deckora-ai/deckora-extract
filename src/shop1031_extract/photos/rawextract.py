"""Pass 1a: enumerate embedded image streams and classify them.

Two structural cases drive the whole pipeline (survey of 22 corpus OMs, 434
large image streams):

  case 1 (33%): the raw photo is its own image stream and the broker overlay is
    drawn on top as vector text / separate images. The clean base stream is
    recoverable directly via doc.extract_image. method = "raw_extract".

  case 2 (67%): no vector text overlaps the image bbox. The image is either
    already clean OR the overlay was flattened into the raster. Flattened-page
    rasters (cover > ~75% of the page) route to overlay.py for masking.

This module does the enumeration and the case decision. It does not decode
overlay regions; that is overlay.py. Classical CV only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import fitz
import numpy as np

# A photo candidate must be at least this large; below it the stream is a logo,
# icon, divider rule, or contact-bar fragment.
MIN_W = 400
MIN_H = 300
MIN_ASPECT = 0.30
MAX_ASPECT = 4.0

# Number of overlapping text spans over an image bbox that flips it to case 1.
TEXT_OVERLAP_THRESHOLD = 3


@dataclass
class ImageRecord:
    xref: int
    page: int  # 1-based
    bbox: Optional[tuple] = None  # (x0,y0,x1,y1) on the page, first placement
    width: int = 0
    height: int = 0
    colorspace: int = 0
    ext: str = ""
    has_smask: bool = False
    page_cover: float = 0.0
    color_entropy: float = 0.0
    edge_density: float = 0.0
    text_overlap_count: int = 0
    is_photo_candidate: bool = False
    method: str = "skip"  # "raw_extract" | "needs_overlay_pass" | "skip"
    _data: bytes = field(default=b"", repr=False)


def _rects_intersect(a: fitz.Rect, b: fitz.Rect) -> bool:
    return not (a.x1 <= b.x0 or b.x1 <= a.x0 or a.y1 <= b.y0 or b.y1 <= a.y0)


def _decode_to_array(data: bytes) -> Optional[np.ndarray]:
    """Decode raw image bytes to an RGB uint8 array via PyMuPDF (no cv2 needed
    for codecs PyMuPDF already links). Returns None if it cannot decode."""
    try:
        pm = fitz.Pixmap(data)
        if pm.alpha:
            pm = fitz.Pixmap(pm, 0)
        if pm.n >= 4:
            pm = fitz.Pixmap(fitz.csRGB, pm)
        arr = np.frombuffer(pm.samples, dtype=np.uint8)
        arr = arr.reshape(pm.height, pm.width, pm.n)
        if pm.n == 1:
            arr = np.repeat(arr, 3, axis=2)
        return arr[:, :, :3].copy()
    except Exception:
        return None


def color_entropy(rgb: np.ndarray) -> float:
    """Shannon entropy of a coarse color histogram. Photographs spread across
    many color cells (high entropy); flat logos and gradient bars concentrate
    in a few (low entropy)."""
    small = rgb[::8, ::8, :] if rgb.shape[0] > 64 else rgb
    q = (small.astype(np.int32) >> 5)  # 3 bits per channel -> 512 bins
    idx = (q[..., 0] << 6) | (q[..., 1] << 3) | q[..., 2]
    counts = np.bincount(idx.ravel(), minlength=512).astype(np.float64)
    p = counts / counts.sum() if counts.sum() else counts
    nz = p[p > 0]
    return float(-(nz * np.log2(nz)).sum())


def edge_density(rgb: np.ndarray) -> float:
    """Fraction of pixels that sit on a luminance edge. Photographs carry dense
    texture; flat fills and smooth gradients are near zero."""
    g = rgb.mean(axis=2)
    gx = np.abs(np.diff(g, axis=1))
    gy = np.abs(np.diff(g, axis=0))
    h = min(gx.shape[0], gy.shape[0])
    w = min(gx.shape[1], gy.shape[1])
    mag = gx[:h, :w] + gy[:h, :w]
    return float((mag > 20).mean())


def _looks_photographic(entropy: float, edges: float) -> bool:
    return entropy >= 3.0 and edges >= 0.02


def enumerate_images(pdf_path: str, max_pages: int = 60) -> list[ImageRecord]:
    """Enumerate embedded images per page and classify each candidate.

    For every page: collect text-span rects once, then for each image stream
    record geometry, colorspace, SMask, decoded color statistics, and how many
    text spans overlap its placement. Decide the recovery method.
    """
    doc = fitz.open(pdf_path)
    out: list[ImageRecord] = []
    seen: set[int] = set()
    try:
        for pno in range(min(doc.page_count, max_pages)):
            page = doc[pno]
            page_area = page.rect.width * page.rect.height
            text_rects = []
            d = page.get_text("dict")
            for blk in d.get("blocks", []):
                for line in blk.get("lines", []):
                    for span in line.get("spans", []):
                        if span.get("text", "").strip():
                            text_rects.append(fitz.Rect(span["bbox"]))

            for img in page.get_images(full=True):
                xref = img[0]
                if xref in seen:
                    continue
                seen.add(xref)
                smask = img[1]
                try:
                    info = doc.extract_image(xref)
                except Exception:
                    continue
                w, h = info.get("width", 0), info.get("height", 0)
                rec = ImageRecord(
                    xref=xref, page=pno + 1, width=w, height=h,
                    colorspace=info.get("colorspace", 0),
                    ext=info.get("ext", ""), has_smask=bool(smask),
                    _data=info.get("image", b""),
                )
                try:
                    rects = page.get_image_rects(xref)
                except Exception:
                    rects = []
                if rects:
                    r0 = rects[0]
                    rec.bbox = (r0.x0, r0.y0, r0.x1, r0.y1)
                    rec.page_cover = max(
                        (r.get_area() / page_area) if page_area else 0.0 for r in rects
                    )
                    overlap = 0
                    for r in rects:
                        overlap = max(
                            overlap, sum(1 for tr in text_rects if _rects_intersect(r, tr))
                        )
                    rec.text_overlap_count = overlap

                if w < MIN_W or h < MIN_H:
                    out.append(rec)
                    continue
                ar = w / h if h else 0
                if ar < MIN_ASPECT or ar > MAX_ASPECT:
                    out.append(rec)
                    continue

                arr = _decode_to_array(rec._data)
                if arr is not None:
                    rec.color_entropy = color_entropy(arr)
                    rec.edge_density = edge_density(arr)
                    rec.is_photo_candidate = _looks_photographic(
                        rec.color_entropy, rec.edge_density
                    )

                if rec.is_photo_candidate:
                    # Vector text drawn over the image means the base stream is
                    # clean and the overlay lives in the vector layer: extract
                    # the stream directly (case 1). Absent that, the overlay (if
                    # any) is baked into the pixels and routes to the overlay
                    # pass (case 2).
                    if rec.text_overlap_count >= TEXT_OVERLAP_THRESHOLD:
                        rec.method = "raw_extract"
                    else:
                        rec.method = "needs_overlay_pass"
                out.append(rec)
    finally:
        doc.close()
    return out


def photo_candidates(records: list[ImageRecord]) -> list[ImageRecord]:
    return [r for r in records if r.is_photo_candidate]

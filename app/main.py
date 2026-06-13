"""Deckora extraction service — thin HTTP wrapper around shop1031_extract.

One endpoint: POST /v1/extract. The worker sends a presigned R2 GET URL for an
OM PDF; this service runs the zero-LLM extraction (deal_data + report with
per-field provenance) and optionally the photo de-layering pass, and returns
everything in one JSON response. Stateless: every request works in a temp dir
that is deleted on return. No R2 credentials live here — the presigned URL is
the only access this service ever has to a document.

Auth: HMAC-SHA256 over "<unix-ts>.<raw-body>" with the shared secret in
EXTRACT_SHARED_SECRET, sent as X-Deckora-Ts / X-Deckora-Sig. 5-minute window,
constant-time compare. Mirrors the worker-side signer.

The optional API legs of the package (Street View, Mapbox, EDGAR, r2_preserve)
are never invoked: no keys are configured and no code path here calls them.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import requests as _rq                      # noqa: E402
from fastapi import FastAPI, HTTPException, Request  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402

from shop1031_extract import extract, intake, ocr, schema  # noqa: E402
from shop1031_extract.photos import delayer_om             # noqa: E402

APP_VERSION = "0.1.0"
MAX_PDF_MB = int(os.environ.get("MAX_PDF_MB", "80"))
SIG_WINDOW_SECONDS = 300
OVERLAY_THRESHOLD = float(os.environ.get("PHOTO_OVERLAY_THRESHOLD", "0.15"))
# Test-only escape hatch so a local http://127.0.0.1 fixture server can be used
# in CI. Never set in production; presigned R2 URLs are always https.
ALLOW_INSECURE_PDF_URL = os.environ.get("ALLOW_INSECURE_PDF_URL") == "1"

app = FastAPI(title="deckora-extract", version=APP_VERSION)


def _verify_signature(request: Request, raw_body: bytes) -> None:
    secret = os.environ.get("EXTRACT_SHARED_SECRET", "")
    if not secret:
        raise HTTPException(503, "service not configured: EXTRACT_SHARED_SECRET missing")
    ts = request.headers.get("x-deckora-ts") or ""
    sig = request.headers.get("x-deckora-sig") or ""
    if not ts or not sig:
        raise HTTPException(401, "missing signature headers")
    try:
        ts_val = int(ts)
    except ValueError:
        raise HTTPException(401, "bad timestamp")
    if abs(time.time() - ts_val) > SIG_WINDOW_SECONDS:
        raise HTTPException(401, "stale timestamp")
    expected = hmac.new(secret.encode(), f"{ts}.".encode() + raw_body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        raise HTTPException(401, "bad signature")


def _download_pdf(url: str, dest: Path) -> int:
    if not isinstance(url, str) or not url.startswith("https://"):
        if not (ALLOW_INSECURE_PDF_URL and isinstance(url, str) and url.startswith("http://")):
            raise HTTPException(400, "pdf_url must be an https URL")
    limit = MAX_PDF_MB * 1024 * 1024
    size = 0
    try:
        with _rq.get(url, stream=True, timeout=30) as r:
            r.raise_for_status()
            with open(dest, "wb") as fh:
                for chunk in r.iter_content(1 << 16):
                    size += len(chunk)
                    if size > limit:
                        raise HTTPException(413, f"pdf exceeds {MAX_PDF_MB}MB cap")
                    fh.write(chunk)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001 — network failures become a clean 502
        raise HTTPException(502, f"pdf download failed: {e}")
    if size == 0:
        raise HTTPException(502, "pdf download returned zero bytes")
    # Magic-bytes gate: PyMuPDF happily auto-detects and parses HTML/EPUB/images,
    # which turns a misrouted URL (e.g. an SPA fallback page) into a confident
    # garbage extraction — the dangerous failure mode for the caller. The PDF
    # spec allows the header within the first 1024 bytes.
    with open(dest, "rb") as fh:
        head = fh.read(1024)
    if b"%PDF-" not in head:
        raise HTTPException(422, "not a PDF (missing %PDF- header)")
    return size


@app.get("/healthz")
def healthz():
    return {"ok": True, "version": APP_VERSION, "ocr_available": ocr.available()}


@app.post("/v1/extract")
async def extract_endpoint(request: Request):
    raw = await request.body()
    _verify_signature(request, raw)
    try:
        body = json.loads(raw or b"{}")
    except json.JSONDecodeError:
        raise HTTPException(400, "invalid JSON body")

    pdf_url = body.get("pdf_url")
    want_photos = bool(body.get("want_photos"))
    asset_id = str(body.get("asset_id") or "om")[:80]

    t0 = time.time()
    out: dict = {"service_version": APP_VERSION, "asset_id": asset_id}

    with tempfile.TemporaryDirectory() as td:
        pdf_path = Path(td) / "om.pdf"
        out["pdf_bytes"] = _download_pdf(pdf_url, pdf_path)
        t_dl = time.time()

        # ---- structured extraction (offline, zero-LLM) ----
        try:
            doc = intake.load(pdf_path)
        except Exception as e:  # noqa: BLE001 — corrupt/encrypted PDFs become a clean 422
            raise HTTPException(422, f"pdf could not be parsed: {e}")

        geo = None
        if doc.scanned and not doc.ocr_used:
            out["extraction"] = {
                "status": "pending_review",
                "reason": f"text layer too thin ({doc.chars_per_page} chars/page)",
                "ocr_available": ocr.available(),
            }
        else:
            result = extract.run(doc)

            # ---- Leg 2.5 deterministic confirmation (env-gated, fail-soft) ----
            # Runs BEFORE the schema build so confidence escalations land in
            # deal_data/_meta. Only active when GOOGLE_MAPS_API_KEY is set (or
            # pgeocode is installed); without either, available() is False and
            # this block is a no-op. Never overwrites values — confidence only.
            try:
                from shop1031_extract import api_confirm
                if api_confirm.available():
                    tf = result["fields"].get("tenant")
                    tenant_name = tf.value if (tf and getattr(tf, "found", False)
                                               and isinstance(tf.value, str)) else None
                    confirm_out = api_confirm.confirm(doc, result["fields"], tenant=tenant_name)
                    geo = confirm_out.get("geocode")
                    out["confirm"] = {
                        "conflicts": confirm_out.get("conflicts", []),
                        "tenant_validation": confirm_out.get("tenantValidation"),
                        "geocode": ({"lat": geo["lat"], "lng": geo["lng"],
                                     "formatted": geo.get("formatted")} if geo else None),
                    }
            except Exception as e:  # noqa: BLE001 — confirmation must never sink extraction
                out["confirm_error"] = str(e)

            out["extraction"] = {
                "status": "complete",
                "deal_data": schema.build_deal_data(result, asset_id, pdf_path.name, doc.page_count),
                "report": schema.build_report(result),
            }
        t_ex = time.time()

        # ---- photo de-layering (optional, fail-soft) ----
        photos = []
        if want_photos:
            photo_dir = Path(td) / "photos"
            try:
                manifest = delayer_om(
                    pdf_path=str(pdf_path), out_dir=str(photo_dir),
                    asset_id=asset_id, overlay_threshold=OVERLAY_THRESHOLD,
                    # Geocoded subject coordinates arm the Street View/aerial
                    # fallback chain (itself env-gated); None degrades cleanly.
                    geocode=({"lat": geo["lat"], "lng": geo["lng"]} if geo else None),
                )
                for entry in manifest.get("photos", []):
                    fp = photo_dir / entry["filename"]
                    if not fp.exists():
                        continue
                    e = dict(entry)
                    e["data_b64"] = base64.b64encode(fp.read_bytes()).decode()
                    photos.append(e)
                out["photos_pass_failures"] = manifest.get("pass_failures", [])
            except Exception as e:  # noqa: BLE001 — photo failure never sinks extraction
                out["photos_error"] = str(e)
        out["photos"] = photos

        out["timings_ms"] = {
            "download": int((t_dl - t0) * 1000),
            "extract": int((t_ex - t_dl) * 1000),
            "photos": int((time.time() - t_ex) * 1000),
            "total": int((time.time() - t0) * 1000),
        }

    return JSONResponse(out)


# Layout-geometry endpoint (app/layout.py). Registered AFTER the helpers it
# reuses (_verify_signature / _download_pdf) are defined; layout.py imports
# this module lazily inside the handler to avoid a circular import.
from app.layout import layout_endpoint  # noqa: E402

app.post("/v1/layout")(layout_endpoint)

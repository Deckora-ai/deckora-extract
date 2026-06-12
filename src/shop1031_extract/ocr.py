"""Deterministic OCR fallback for scanned-only / thin-text OMs.

When a page's embedded text layer is too thin to trust (image-only scan, or a
flattened design with no selectable text), the digital extractors have nothing to
read. This module rasterizes such pages with PyMuPDF and runs Tesseract (pytesseract
+ the tesseract binary) to recover a text layer.

Classical OCR only. No LLM, no vision model, no network. Tesseract is a local
deterministic engine and is permitted under the No-API-Extraction Law.

Availability is checked at runtime. If pytesseract or the tesseract binary is
absent, available() returns False and the caller routes the OM to Pending Broker
Review with a clear reason. The package imports and runs whether or not OCR is
installed.

Confidence cap. OCR text is noisier than an embedded text layer, so any field
extracted from an OCR-recovered page is capped at MEDIUM and never reaches HIGH
without an independent non-OCR corroboration path. The caller marks the Doc with
`ocr_used=True`; the field grader applies the cap.
"""
from __future__ import annotations

import shutil

_AVAIL = None
_VERSION = None


def available() -> bool:
    """True if pytesseract is importable AND a tesseract binary is reachable.
    Cached after the first probe."""
    global _AVAIL, _VERSION
    if _AVAIL is not None:
        return _AVAIL
    try:
        import pytesseract
        # honor a configured tesseract_cmd, else require it on PATH
        cmd = getattr(pytesseract.pytesseract, "tesseract_cmd", "tesseract")
        if cmd == "tesseract" and not shutil.which("tesseract"):
            _AVAIL = False
            return _AVAIL
        _VERSION = str(pytesseract.get_tesseract_version())
        _AVAIL = True
    except Exception:  # noqa: BLE001 - any import / binary error means unavailable
        _AVAIL = False
    return _AVAIL


def version() -> str | None:
    available()
    return _VERSION


def ocr_page_text(page, dpi: int = 300) -> str:
    """OCR a single PyMuPDF page to text. Returns "" on any failure. Assumes
    available() is True; caller is responsible for the availability gate."""
    try:
        import pytesseract
        pix = page.get_pixmap(dpi=dpi)
        import io
        from PIL import Image
        img = Image.open(io.BytesIO(pix.tobytes("png")))
        return pytesseract.image_to_string(img) or ""
    except Exception:  # noqa: BLE001 - OCR is best-effort
        return ""


def reason_unavailable() -> str:
    """Human-readable reason for the Pending Broker Review record."""
    try:
        import pytesseract  # noqa: F401
        return ("tesseract binary not found on PATH; install Tesseract OCR or set "
                "pytesseract.pytesseract.tesseract_cmd")
    except Exception:  # noqa: BLE001
        return ("pytesseract not installed; run `pip install pytesseract pillow` and "
                "install the Tesseract OCR binary")

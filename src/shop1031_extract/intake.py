"""Step 1: document understanding.

Turn an OM PDF into a normalized Doc (text + word geometry + tables + section
tags). PyMuPDF only. No interpretation of meaning happens here; this layer has
no idea what a cap rate is. That keeps it reusable and is where an OCR fallback
would slot in later without touching Step 2.
"""
from __future__ import annotations

from pathlib import Path

import fitz  # PyMuPDF

from .model import Doc, Page, Word

# Below this many characters per page, the text layer is too thin to trust and
# the document routes to human review (No-API law: no model fallback).
SCANNED_CPP_THRESHOLD = 100

# Header keywords -> section label. First match on a page wins.
SECTION_KEYWORDS = {
    "cover": ["offering memorandum", "exclusively listed", "for sale"],
    "financials": ["investment summary", "financial summary", "rent schedule",
                   "annualized operating", "pricing", "net operating income",
                   "lease abstract", "rental rate"],
    "lease": ["lease summary", "lease abstract", "tenant summary",
              "lease overview", "rent roll"],
    "tenant": ["tenant overview", "about the tenant", "company overview",
               "tenant profile", "credit"],
    "location": ["location overview", "area overview", "location highlights"],
    "demographics": ["demographics", "population", "household income",
                     "demographic summary"],
}


def _tag_section(text: str) -> str | None:
    low = text.lower()
    # Prefer the section whose keyword appears earliest (likely a header).
    best = None
    best_pos = 10 ** 9
    for label, kws in SECTION_KEYWORDS.items():
        for kw in kws:
            pos = low.find(kw)
            if 0 <= pos < best_pos:
                best_pos = pos
                best = label
    return best


def load(pdf_path: str | Path, with_tables: bool = False,
         ocr_fallback: bool = True) -> Doc:
    """Parse a PDF into a normalized Doc.

    with_tables: run PyMuPDF table detection per page. Off by default because
    the current extractors work from word geometry, not the table grids, and
    per-page table detection dominates runtime (matters for the 100-OM test).

    ocr_fallback: when a page's embedded text is too thin (image-only scan) and
    Tesseract is available, rasterize and OCR that page to recover text. OCR text
    is marked on the Doc (ocr_used) so the grader caps OCR-derived fields at
    MEDIUM. With no Tesseract, thin pages stay empty and the doc routes to Pending
    Broker Review (no model fallback).
    """
    pdf_path = Path(pdf_path)
    doc = fitz.open(pdf_path)
    pages: list[Page] = []
    full_parts: list[str] = []
    ocr_used = False
    ocr_on = ocr_fallback and _ocr_available()

    for i in range(doc.page_count):
        p = doc[i]
        text = p.get_text()
        if ocr_on and len(text.strip()) < SCANNED_CPP_THRESHOLD:
            from . import ocr as _ocr
            recovered = _ocr.ocr_page_text(p)
            if len(recovered.strip()) > len(text.strip()):
                text = recovered
                ocr_used = True
        full_parts.append(text)

        words = [
            Word(text=w[4], x0=w[0], y0=w[1], x1=w[2], y1=w[3], page=i + 1)
            for w in p.get_text("words")
        ]

        tables: list[list[list[str]]] = []
        if with_tables:
            try:
                tf = p.find_tables()
                for t in tf.tables:
                    grid = t.extract()
                    grid = [["" if c is None else str(c) for c in row] for row in grid]
                    if grid:
                        tables.append(grid)
            except Exception:  # noqa: BLE001 - table detection is best-effort
                pass

        rect = p.rect
        pages.append(Page(
            number=i + 1,
            width=rect.width,
            height=rect.height,
            text=text,
            words=words,
            tables=tables,
            section=_tag_section(text),
        ))

    # Cover spans (pages 1-2): line text + font size, for title detection.
    cover_spans: list = []
    for i in range(min(2, doc.page_count)):
        d = doc[i].get_text("dict")
        for block in d.get("blocks", []):
            for line in block.get("lines", []):
                spans = line.get("spans", [])
                if not spans:
                    continue
                txt = " ".join(s.get("text", "") for s in spans).strip()
                size = max((s.get("size", 0) for s in spans), default=0)
                if txt:
                    cover_spans.append((txt, round(size, 1), i + 1))

    doc.close()
    full_text = "\n".join(full_parts)
    npages = len(pages)
    cpp = (len(full_text) / npages) if npages else 0.0

    return Doc(
        path=str(pdf_path),
        page_count=npages,
        pages=pages,
        full_text=full_text,
        chars_per_page=round(cpp, 1),
        scanned=cpp < SCANNED_CPP_THRESHOLD,
        ocr_used=ocr_used,
        cover_spans=cover_spans,
    )


def _ocr_available() -> bool:
    try:
        from . import ocr as _ocr
        return _ocr.available()
    except Exception:  # noqa: BLE001
        return False

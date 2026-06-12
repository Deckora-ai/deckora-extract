"""Core data structures shared across the extraction pipeline.

No external dependencies. The Field shape mirrors the per-field record already
used by the corpus extraction_report.json (value / confidence / method /
source_page / source_snippet), so our output is drop-in comparable.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional


# Confidence levels, high -> low.
HIGH = "high"
MEDIUM = "medium"
LOW = "low"
UNKNOWN = "unknown"

# Extraction methods.
VERBATIM = "verbatim"      # read directly off the page
DERIVED = "derived"        # computed from other extracted values
INTERPRETED = "interpreted"  # mapped to an enum / normalized form
LOOKUP = "lookup"          # from the maintained tenant table (enrichment)
NOT_FOUND = "not_found"    # absent in the document


@dataclass
class Field:
    """One extracted value with full provenance.

    `paths` records every independent extraction path that produced a candidate
    value (multi-path verification). `review_required` is set when paths
    disagree, so downstream surfaces render "AI-extracted, unverified" and the
    human review queue picks the property up before any LOI is generated.
    """
    value: Any = None
    confidence: str = UNKNOWN
    method: str = NOT_FOUND
    source_page: Optional[int] = None
    source_snippet: Optional[str] = None
    notes: Optional[str] = None
    paths: list = field(default_factory=list)   # [{path, value, page, snippet}]
    review_required: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def found(self) -> bool:
        return self.value is not None and self.method != NOT_FOUND

    @classmethod
    def missing(cls, note: str | None = None) -> "Field":
        return cls(value=None, confidence=UNKNOWN, method=NOT_FOUND, notes=note)


@dataclass
class Word:
    """A positioned word from the PDF text layer."""
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    page: int

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2


@dataclass
class Page:
    number: int          # 1-based
    width: float
    height: float
    text: str
    words: list[Word] = field(default_factory=list)
    tables: list[list[list[str]]] = field(default_factory=list)  # grids of cell text
    section: Optional[str] = None  # tagged section label


@dataclass
class Doc:
    """Normalized intermediate produced by Step 1 (intake)."""
    path: str
    page_count: int
    pages: list[Page] = field(default_factory=list)
    full_text: str = ""
    chars_per_page: float = 0.0
    scanned: bool = False  # True if text layer is too thin to trust
    ocr_used: bool = False  # True if any page text came from the OCR fallback
    cover_spans: list = field(default_factory=list)  # (text, size, page) pages 1-2

    def page_text(self, n: int) -> str:
        """1-based page text, empty string if out of range."""
        if 1 <= n <= len(self.pages):
            return self.pages[n - 1].text
        return ""

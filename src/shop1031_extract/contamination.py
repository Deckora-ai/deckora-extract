"""Broker-block contamination detection + subject-corroboration guard.

Two related jobs, both protecting the HIGH-precision invariant:

1. Broker-block detection. Brokers stamp their own contact panel (names, phone,
   email, brokerage street address, license line) on the cover and footers. The
   street address and zip inside that panel parse cleanly and can outvote the
   subject property's address, which is often letter-spaced or image-only on the
   cover. A value sourced from inside a broker block must never reach HIGH for a
   subject-property field.

2. Subject corroboration. Even outside a broker block, a candidate value may only
   be promoted to HIGH for a subject field if it is corroborated as belonging to
   the SUBJECT property. The corroborating signal is the source filename and the
   cover title region (the broker file is reliably named for the subject; the
   subject city/state appears there). A broker city does not appear in the OM
   title, so it cannot corroborate.

The canonical near-miss this stops: the Panera Bread / Wooster Pike OM, where the
broker block "5831 Lancefield Drive, Huntington Beach, CA 92649 / CRE Lic." would
otherwise promote Huntington Beach, CA to HIGH over the subject Cincinnati, OH.
The pgeocode self-confirm (a broker zip resolves to the broker city) is the same
class and is blocked by the same corroboration requirement.

No network, no API, no model. Pure text heuristics over the normalized Doc.
"""
from __future__ import annotations

import os
import re

# Signals that a line / nearby region is a broker contact block.
_LICENSE_RE = re.compile(
    r"\b(?:CRE\s*Lic|DRE|BRE|Lic(?:ense)?\.?\s*#|CA\s*Lic|broker\s*of\s*record"
    r"|License\s*No|CalDRE|Cal\.?\s*DRE)\b", re.I)
_PHONE_RE = re.compile(r"\b\d{3}[.\-)\s]\s?\d{3}[.\-\s]\d{4}\b")
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
_BROKER_PANEL_RE = re.compile(
    r"\b(exclusively\s+(?:listed|marketed|represented)\s+by|listed\s+by"
    r"|presented\s+by|marketed\s+by|in\s+conjunction\s+with|broker\s+of\s+record"
    r"|advisor(?:s)?\s*:|listing\s+(?:agent|broker)|contact\s*:)\b", re.I)


def line_is_broker_context(line: str) -> bool:
    """True if a single line carries a broker-block signal (license / phone+email
    / panel header)."""
    if _LICENSE_RE.search(line):
        return True
    if _BROKER_PANEL_RE.search(line):
        return True
    if _PHONE_RE.search(line) and _EMAIL_RE.search(line):
        return True
    return False


def broker_block_spans(text: str, pad: int = 160) -> list[tuple[int, int]]:
    """Character spans of probable broker contact blocks in `text`.

    Anchored on license lines, panel headers, and phone+email contact bars. Each
    anchor expands by `pad` chars on each side, since the brokerage street address
    sits one or two lines from its license/contact line.
    """
    spans: list[tuple[int, int]] = []
    for rx in (_LICENSE_RE, _BROKER_PANEL_RE):
        for m in rx.finditer(text):
            spans.append((max(0, m.start() - pad), m.end() + pad))
    # phone AND email close together => a contact bar
    emails = [m.start() for m in _EMAIL_RE.finditer(text)]
    for pm in _PHONE_RE.finditer(text):
        if any(abs(pm.start() - e) <= 120 for e in emails):
            spans.append((max(0, pm.start() - pad), pm.end() + pad))
    if not spans:
        return []
    spans.sort()
    merged = [spans[0]]
    for s, e in spans[1:]:
        if s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


def offset_in_broker_block(text: str, offset: int) -> bool:
    """True if a character offset falls inside a detected broker block."""
    for s, e in broker_block_spans(text):
        if s <= offset < e:
            return True
    return False


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).lower()) if s else ""


def subject_text(doc) -> str:
    """The corroborating subject signal: source filename + cover title region.
    Letter-spacing on covers is stripped so 'C i n c i n n a t i' still matches.
    """
    fn = os.path.basename(getattr(doc, "path", "") or "")
    cover = ""
    if getattr(doc, "pages", None):
        cover = doc.pages[0].text[:400]
    return _norm(fn) + _norm(cover)


_CITY_ST_RE = re.compile(r"([A-Z][A-Za-z.'\- ]{2,22}),\s*([A-Z]{2})\b")


def is_portfolio_cover(doc, threshold: int = 4) -> bool:
    """True if the cover names `threshold`+ distinct City, ST pairs. A multi-
    property portfolio OM has no single subject city, so no city should grade
    HIGH; the listing routes to review for the analyst to split by parcel."""
    if not getattr(doc, "pages", None):
        return False
    cover = doc.pages[0].text[:700]
    cities = {(_norm(m.group(1)), m.group(2)) for m in _CITY_ST_RE.finditer(cover)}
    cities = {c for c in cities if c[0] and len(c[0]) > 2}
    return len(cities) >= threshold


def corroborated_subject(doc, value: str) -> bool:
    """True if `value` (a city or state token) appears in the subject signal AND
    the cover is not a multi-city portfolio. A broker city is absent from the
    filename and cover title, so it fails here; a portfolio cover corroborates
    several cities at once and so corroborates none of them as THE subject.
    """
    nv = _norm(value)
    if not nv or nv not in subject_text(doc):
        return False
    if is_portfolio_cover(doc):
        return False
    return True


def address_from_broker_block(doc, snippet: str | None, page: int | None) -> bool:
    """True if the winning address snippet came from inside a broker block on its
    source page. Used to reject a broker street/zip that out-voted the subject.
    """
    if not snippet:
        return False
    pg_text = doc.page_text(page) if page else doc.full_text
    idx = pg_text.find(snippet[:40]) if snippet else -1
    if idx < 0:
        # snippet was whitespace-collapsed; try a looser anchor on the street number
        m = re.match(r"\s*(\d{2,6})", snippet)
        if m:
            idx = pg_text.find(m.group(1))
    if idx < 0:
        return False
    return offset_in_broker_block(pg_text, idx)

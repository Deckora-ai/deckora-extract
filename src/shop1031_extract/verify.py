"""Multi-path verification layer (liability protection + architectural humility).

Every high-stakes field is produced by more than one independent path, then
reconciled. Agreement across 2+ paths => HIGH confidence. A single path =>
MEDIUM. Disagreement => LOW + review_required (the human review queue picks it
up before any LOI is generated, and downstream surfaces render
"AI-extracted, unverified").

Shop 1031 never asserts accuracy. This layer records HOW a value was reached and
how strongly the document corroborates it; the user and broker verify before
acting. No API calls here either.
"""
from __future__ import annotations

import re

from .model import Field, HIGH, MEDIUM, LOW, VERBATIM, DERIVED, INTERPRETED
from . import fields as F


def _cand(path, value, page=None, snippet=None, method=VERBATIM):
    if value is None:
        return None
    return {"path": path, "value": value, "page": page, "snippet": snippet,
            "method": method}


def reconcile(candidates, eq, priority_value=None) -> Field:
    """Cluster candidate values by an equality function and grade confidence by
    how many independent paths agree."""
    valid = [c for c in candidates if c is not None and c["value"] is not None]
    if not valid:
        return Field.missing("no path produced a value")

    # cluster
    clusters: list[list[dict]] = []
    for c in valid:
        placed = False
        for cl in clusters:
            if eq(cl[0]["value"], c["value"]):
                cl.append(c)
                placed = True
                break
        if not placed:
            clusters.append([c])
    clusters.sort(key=len, reverse=True)
    best = clusters[0]

    paths_meta = [{"path": c["path"], "value": c["value"], "page": c["page"],
                   "snippet": c["snippet"]} for c in valid]
    distinct = len(clusters)

    if len(best) >= 2:
        chosen = best[0]
        return Field(chosen["value"], HIGH, chosen["method"], chosen["page"],
                     chosen["snippet"],
                     notes=f"multi-path verified ({len(best)} of {len(valid)} paths agree)",
                     paths=paths_meta, review_required=False)
    if len(valid) == 1:
        c = valid[0]
        return Field(c["value"], MEDIUM, c["method"], c["page"], c["snippet"],
                     notes="single path (no corroborating path found)",
                     paths=paths_meta, review_required=False)
    # multiple candidates, none agree -> flag
    chosen = valid[0]
    return Field(chosen["value"], LOW, chosen["method"], chosen["page"],
                 chosen["snippet"],
                 notes=f"paths disagree ({distinct} distinct values); flagged for human review",
                 paths=paths_meta, review_required=True)


# ---- equality functions -------------------------------------------------

def _eq_pct(a, b):
    try:
        return abs(float(a) - float(b)) / max(1.0, abs(float(b))) <= 0.01
    except (TypeError, ValueError):
        return a == b


def _eq_cap(a, b):
    return abs(float(a) - float(b)) <= 0.0006


def _eq_str(a, b):
    na = " ".join(str(a).lower().split()).strip(" .,")
    nb = " ".join(str(b).lower().split()).strip(" .,")
    return na == nb or na in nb or nb in na


# ---- per-field path gatherers ------------------------------------------

def verify_cap_rate(doc, base) -> Field:
    cands = []
    # path A: primary extractor (kv / near label)
    if base.found:
        cands.append(_cand("label", base.value, base.source_page, base.source_snippet))
    # path B: page-1 headline "CAP RATE: 5.25%"
    r = F.regex_first(doc, r"cap(?:\s*rate)?\s*[:\-]?\s*(\d{1,2}\.\d{1,3})\s*%")
    if r:
        cands.append(_cand("headline", round(float(r[0].group(1)) / 100, 5), r[1], r[2]))
    # path C: recompute NOI / price
    price = F.extract_price(doc)
    noi = F.extract_noi(doc)
    if price.found and noi.found and price.value:
        cands.append(_cand("noi/price", round(noi.value / price.value, 5),
                           None, "recomputed NOI / price", DERIVED))
    return reconcile(cands, _eq_cap)


def verify_price(doc, base) -> Field:
    cands = []
    if base.found:
        cands.append(_cand("summary", base.value, base.source_page, base.source_snippet))
    # asking/list price anchor
    r = F.regex_near(doc, ["Asking Price", "List Price", "Offering Price",
                           "Purchase Price"], r"\$\s?([\d,]{5,})", window=40)
    if r:
        v = F._money(r[0].group(1))
        if v and v > 50000:
            cands.append(_cand("asking-anchor", v, r[1], r[2]))
    # derived NOI / cap
    noi = F.extract_noi(doc)
    cap = F.extract_cap_rate(doc)
    if noi.found and cap.found and cap.value:
        cands.append(_cand("noi/cap", round(noi.value / cap.value), None,
                           "recomputed NOI / cap", DERIVED))
    return reconcile(cands, _eq_pct)


def verify_noi(doc, base) -> Field:
    cands = []
    if base.found:
        cands.append(_cand("summary", base.value, base.source_page, base.source_snippet))
    # year-1 / annual rent anchor (independent of NOI label)
    r = F.regex_near(doc, ["Annual Rent", "Year 1", "Base Rent", "Current Rent",
                           "Annualized Rent"], r"\$\s?([\d,]{4,})", window=40)
    if r:
        v = F._money(r[0].group(1))
        if v and v > 5000:
            cands.append(_cand("rent-schedule", v, r[1], r[2]))
    # derived price * cap
    price = F.extract_price(doc)
    cap = F.extract_cap_rate(doc)
    if price.found and cap.found:
        cands.append(_cand("price*cap", round(price.value * cap.value), None,
                           "recomputed price x cap", DERIVED))
    return reconcile(cands, _eq_pct)


def verify_tenant(doc, base) -> Field:
    cands = []
    # path A: filename lead (most reliable)
    ft = F.filename_tenant(doc.path)
    if ft:
        cands.append(_cand("filename", ft, None, f"filename:{ft}"))
    # path A2: curated brand on cover page only
    b = F.match_brand(doc, pages=1)
    if b:
        cands.append(_cand("brand-cover", b[0], b[1], b[0], INTERPRETED))
    # path B: kv "Tenant" label
    kv = F.kv_lookup(doc, ["Tenant Trade Name", "Tenant Name", "Tenant"])
    if kv:
        c = F._clean_tenant(kv[0])
        if c:
            cands.append(_cand("tenant-label", c, kv[1], kv[2]))
    # path C: cover-page title
    title = F.get_cover_title(doc)
    if title:
        c = F._clean_tenant(title[0])
        if c and not re.search(r"contents|healthcare|holdings", c, re.I):
            if not re.fullmatch(r"(?:[A-Za-z] ){3,}[A-Za-z]?", c.strip()):
                cands.append(_cand("cover-title", c, title[1], title[0]))
    return reconcile(cands, _eq_str)

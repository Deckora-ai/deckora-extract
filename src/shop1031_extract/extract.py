"""Step 2 orchestrator: Doc -> {field name: Field} + broker, then validation.

Pipeline order: generic pass, then broker-template overrides (template wins
where it has higher confidence), then cross-field validation downgrades.
"""
from __future__ import annotations

from .model import Doc, Field
from . import fields as F
from . import brokers
from . import validate
from . import verify


def run(doc: Doc) -> dict:
    firm = brokers.detect(doc)
    result = F.extract_all(doc)

    tmpl = brokers.template_for(firm)
    if tmpl:
        for k, v in tmpl(doc).items():
            # template overrides only if it found something
            if v.found:
                result[k] = v

    # Cross-derive the cap-rate identity: cap == NOI / price. Given two trusted
    # values, fill or correct the third. Snapshot trust before editing so the
    # three checks don't cascade off each other's derived outputs.
    def val(f):
        return f.value if (f and f.found) else None

    def trusted(f):
        return bool(f and f.found and f.confidence in ("high", "medium")
                    and f.method == "verbatim")

    P, N, C = result.get("price"), result.get("noi"), result.get("capRate")
    p, n, c = val(P), val(N), val(C)
    tp, tn, tc = trusted(P), trusted(N), trusted(C)

    if tn and tc:  # NOI & cap -> price
        pred = round(n / c)
        if not tp or (p and abs(p - pred) / pred > 0.03):
            result["price"] = Field(pred, "medium", "derived", None, None,
                                    notes="price from NOI / cap rate (identity)")
    if tp and tc:  # price & cap -> NOI
        pred = round(p * c)
        if not tn or (n and abs(n - pred) / pred > 0.03):
            result["noi"] = Field(pred, "medium", "derived", None, None,
                                  notes="NOI from price x cap rate (identity)")
    if tp and tn:  # price & NOI -> cap
        pred = round(n / p, 5)
        if not tc or (c and abs(c - pred) > 0.0006):
            result["capRate"] = Field(pred, "medium", "derived", None, None,
                                      notes="cap from NOI / price (identity)")

    # Multi-path verification (liability layer): re-grade high-stakes fields by
    # how many independent paths agree, attach path provenance, and flag
    # disagreements for the human review queue. The post-derivation value is the
    # primary path, so verification never regresses the value; it adds
    # confidence + review_required + paths metadata.
    result["capRate"] = verify.verify_cap_rate(doc, result.get("capRate") or Field.missing())
    result["price"] = verify.verify_price(doc, result.get("price") or Field.missing())
    result["noi"] = verify.verify_noi(doc, result.get("noi") or Field.missing())
    result["tenant"] = verify.verify_tenant(doc, result.get("tenant") or Field.missing())

    # currentRent fallback: for net-lease STNL, year-1 base rent == NOI.
    rent = result.get("currentRent")
    noi = result.get("noi")
    if (not rent or not rent.found) and noi and noi.found:
        result["currentRent"] = Field(
            noi.value, "medium", "derived", noi.source_page, noi.source_snippet,
            notes="year-1 base rent inferred from NOI (net lease: rent == NOI)",
            review_required=noi.review_required)

    # offeringFirm field
    if firm:
        result["offeringFirm"] = Field(firm, "high", "interpreted", None,
                                       notes="broker fingerprint match")
    else:
        result["offeringFirm"] = Field.missing("broker not fingerprinted")

    # OCR confidence cap: when any page text came from the OCR fallback, OCR
    # noise means no field may auto-deploy. Cap every HIGH to MEDIUM unless 2+
    # independent paths corroborate it (a non-OCR corroboration path). Never
    # HIGH on OCR text alone (No-API law: OCR is classical, but it is noisier
    # than an embedded text layer).
    if getattr(doc, "ocr_used", False):
        for f in result.values():
            if isinstance(f, Field) and f.confidence == "high" and len(f.paths) < 2:
                f.confidence = "medium"
                f.notes = (f.notes + "; " if f.notes else "") + "OCR-derived, capped at MEDIUM"

    warnings = validate.check(result)
    return {"firm": firm, "fields": result, "warnings": warnings}

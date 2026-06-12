"""Assemble the pipeline result into a deal_data.json matching the corpus
schema, plus an extraction_report.json carrying per-field provenance.

Document-extractable fields are filled from the Field results. World-knowledge
enrichment (tenant parent/ticker/credit/prose) is left null here and filled by
enrich.py from the maintained tenant table, never fabricated.
"""
from __future__ import annotations

from datetime import date

from .model import Field


def _v(fields, name):
    f = fields.get(name)
    return f.value if f and f.found else None


def _money_display(n):
    return f"${n:,.0f}" if isinstance(n, (int, float)) else None


def build_deal_data(result: dict, source_folder: str, pdf_name: str,
                    pages: int) -> dict:
    fields = result["fields"]
    firm = result["firm"]
    addr = fields.get("address")
    a = addr.value if (addr and addr.found and isinstance(addr.value, dict)) else {}

    price = _v(fields, "price")
    noi = _v(fields, "noi")
    cap = _v(fields, "capRate")
    bsf = _v(fields, "buildingSf")
    tenant = _v(fields, "tenant")
    lot_ac = _v(fields, "lotAcres")

    full_addr = None
    if a.get("address"):
        bits = [a.get("address"), a.get("city"), a.get("state")]
        full_addr = ", ".join(b for b in bits if b)
        if a.get("zip"):
            full_addr += f" {a['zip']}"

    deal = {
        "cover": {
            "price": _money_display(price),
            "tenant": tenant,
            "address": full_addr,
        },
        "cityLine": ", ".join(b for b in [a.get("city"), a.get("state")] if b) or None,
        "project": {
            "name": tenant,
            "address": a.get("address"),
            "city": a.get("city"),
            "state": a.get("state"),
            "zip": a.get("zip"),
            "county": None,
            "msa": None,
            "tenantName": tenant,
            "offeringDate": None,
            "offeringFirm": firm,
        },
        "property": {
            "apn": None,
            "buildingSf": bsf,
            "lotSf": round(lot_ac * 43560) if lot_ac else None,
            "lotAcres": lot_ac,
            "yearBuilt": _v(fields, "yearBuilt"),
            "yearRenovated": None,
            "zoning": None,
            "parking": f"{_v(fields,'parkingCount')} spaces" if _v(fields, "parkingCount") else None,
            "parkingCount": _v(fields, "parkingCount"),
            "driveThru": _v(fields, "driveThru"),
        },
        "offering": {
            "price": price,
            "priceDisplay": _money_display(price),
            "pricePerSf": round(price / bsf, 2) if (price and bsf) else None,
            "noi": noi,
            "noiDisplay": _money_display(noi),
            "capRate": cap,
            "capRateDisplay": f"{cap*100:.2f}%" if cap else None,
        },
        "tenant": {
            "name": tenant,
            "parent": None, "ticker": None, "creditRating": None,
            "creditTier": None, "about": None, "businessModel": None,
            "_enrichment": "pending_tenant_table",
        },
        "lease": {
            "tenantOfRecord": None,
            "type": _v(fields, "leaseType"),
            "commenced": _v(fields, "commenced"),
            "expires": _v(fields, "expires"),
            "currentRent": _v(fields, "currentRent"),
            "escalationPct": _v(fields, "escalationPct"),
        },
        "market": {
            "demoMetrics": {
                "population_5mi": _v(fields, "population_5mi"),
                "trafficCount": _v(fields, "trafficCount"),
            },
        },
        "extraction_metadata": {
            "source_folder": source_folder,
            "primary_om_filename": pdf_name,
            "primary_om_pages": pages,
            "extractor_version": "shop1031_extract-0.1.0",
            "model_used": None,
            "api_calls": 0,
            "extraction_status": "complete",
        },
    }

    # Per-field verification metadata + property review state (liability layer).
    high_stakes = ("price", "noi", "capRate", "tenant", "buildingSf",
                   "commenced", "expires", "currentRent")
    meta = {}
    flagged = []
    for name in high_stakes:
        f = fields.get(name)
        if not f:
            continue
        meta[name] = {
            "confidence": f.confidence,
            "review_required": f.review_required,
            "paths": f.paths,
            "badge": _badge(f),
        }
        if f.review_required:
            flagged.append(name)
    deal["_meta"] = meta
    deal["verification"] = {
        # day-one review state; gates LOI generation, not browsing
        "review_state": "flagged" if flagged else "unreviewed",
        "reviewed_by": None,
        "flagged_fields": flagged,
        "loi_ready": False,  # requires broker_reviewed
        "principle": ("Shop 1031 never asserts accuracy. Values are extracted "
                      "and cross-checked by code; the user and broker verify "
                      "before acting."),
    }
    return deal


def _badge(f) -> str:
    if f.confidence == "high" and len(f.paths) >= 2:
        return "AI-extracted, multi-path verified"
    if f.review_required:
        return "AI-extracted, unverified, flagged for review"
    if f.confidence == "medium":
        return "AI-extracted, single source"
    if not f.found:
        return "not found"
    return "AI-extracted"


def build_report(result: dict) -> dict:
    fields = result["fields"]
    out = {"schema_version": "1.0", "fields": {}, "validation": result.get("warnings", [])}
    for name, f in fields.items():
        if isinstance(f, Field):
            out["fields"][name] = f.to_dict()
    out["api_calls"] = 0
    out["extraction_status"] = "complete"
    return out

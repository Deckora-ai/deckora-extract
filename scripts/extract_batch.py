#!/usr/bin/env python3
"""CLI: extract a whole directory of OM PDFs in one run. No API calls.

For every *.pdf under the input directory, run the same pipeline extract_one.py
runs (intake -> field extraction -> schema) and write one deal_data.json per OM
into its own output subfolder. PDFs whose text layer is too thin route to the
human review queue (an extraction_status.json is written instead of a
deal_data.json), exactly as the single-OM path does. A batch_summary.json records
the per-OM outcome.

Usage:
  python scripts/extract_batch.py <input_dir> --out <output_dir>

Optional:
  --glob   PDF glob, default "*.pdf" (use "**/*.pdf" to recurse)

Each OM lands in <output_dir>/<pdf_stem>/ with deal_data.json +
extraction_report.json, or extraction_status.json when routed to review.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from shop1031_extract import intake, extract, schema, ocr  # noqa: E402


def extract_one(pdf: Path, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = intake.load(pdf)

    if doc.scanned and not doc.ocr_used:
        reason = (f"text layer too thin ({doc.chars_per_page} chars/page); "
                  "routed to human review queue per No-API law")
        if not ocr.available():
            reason += f". OCR fallback unavailable: {ocr.reason_unavailable()}"
        status = {"extraction_status": "pending_broker_review",
                  "reason": reason, "ocr_available": ocr.available(),
                  "api_calls": 0}
        (out_dir / "extraction_status.json").write_text(
            json.dumps(status, indent=2), encoding="utf-8")
        return {"pdf": pdf.name, "status": "pending_broker_review",
                "chars_per_page": doc.chars_per_page, "fields_found": 0}

    result = extract.run(doc)
    deal = schema.build_deal_data(result, pdf.parent.name, pdf.name, doc.page_count)
    report = schema.build_report(result)
    (out_dir / "deal_data.json").write_text(json.dumps(deal, indent=2), encoding="utf-8")
    (out_dir / "extraction_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    found = sum(1 for f in result["fields"].values() if getattr(f, "found", False))
    flagged = deal.get("verification", {}).get("flagged_fields", [])
    return {"pdf": pdf.name, "status": "complete", "firm": result["firm"],
            "fields_found": found, "warnings": len(result["warnings"]),
            "flagged_fields": flagged}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Batch-extract a directory of OM PDFs.")
    ap.add_argument("input_dir", help="directory holding OM PDFs")
    ap.add_argument("--out", required=True, help="output directory")
    ap.add_argument("--glob", default="*.pdf",
                    help='PDF glob (default "*.pdf"; use "**/*.pdf" to recurse)')
    args = ap.parse_args(argv)

    in_dir = Path(args.input_dir)
    out_root = Path(args.out)
    if not in_dir.is_dir():
        print(f"input_dir not found: {in_dir}", file=sys.stderr)
        return 2

    pdfs = sorted(in_dir.glob(args.glob))
    if not pdfs:
        print(f"no PDFs matched {args.glob!r} under {in_dir}", file=sys.stderr)
        return 1

    out_root.mkdir(parents=True, exist_ok=True)
    results = []
    complete = pending = errored = 0
    for pdf in pdfs:
        sub = out_root / pdf.stem
        try:
            rec = extract_one(pdf, sub)
        except Exception as e:  # noqa: BLE001 - one bad OM must not stop the batch
            rec = {"pdf": pdf.name, "status": "error", "error": str(e)}
            (sub).mkdir(parents=True, exist_ok=True)
            (sub / "extraction_status.json").write_text(
                json.dumps(rec, indent=2), encoding="utf-8")
        results.append(rec)
        if rec["status"] == "complete":
            complete += 1
        elif rec["status"] == "pending_broker_review":
            pending += 1
        else:
            errored += 1
        print(f"{rec['status']:<22} {pdf.name}")

    summary = {
        "input_dir": str(in_dir.resolve()),
        "count": len(pdfs),
        "complete": complete,
        "pending_broker_review": pending,
        "errored": errored,
        "api_calls": 0,
        "results": results,
    }
    (out_root / "batch_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\n{len(pdfs)} OM(s): {complete} complete, {pending} review-queued, "
          f"{errored} errored, 0 API calls")
    print(f"  -> {out_root / 'batch_summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""CLI: one OM PDF -> deal_data.json + extraction_report.json. No API calls.

    python packages/extraction/scripts/extract_one.py "<OM.pdf>" --out <dir>
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from shop1031_extract import intake, extract, schema  # noqa: E402


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    pdf = Path(sys.argv[1])
    out = Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv else pdf.parent
    out.mkdir(parents=True, exist_ok=True)

    doc = intake.load(pdf)
    if doc.scanned and not doc.ocr_used:
        from shop1031_extract import ocr
        reason = (f"text layer too thin ({doc.chars_per_page} chars/page); "
                  "routed to human review per No-API law")
        if not ocr.available():
            reason += f". OCR fallback unavailable: {ocr.reason_unavailable()}"
        status = {"extraction_status": "pending_broker_review",
                  "reason": reason, "ocr_available": ocr.available(),
                  "api_calls": 0}
        (out / "extraction_status.json").write_text(json.dumps(status, indent=2))
        print(f"PENDING BROKER REVIEW: {pdf.name} ({doc.chars_per_page} cpp)")
        return

    result = extract.run(doc)
    deal = schema.build_deal_data(result, pdf.parent.name, pdf.name, doc.page_count)
    report = schema.build_report(result)

    (out / "deal_data.json").write_text(json.dumps(deal, indent=2), encoding="utf-8")
    (out / "extraction_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    found = sum(1 for f in result["fields"].values() if getattr(f, "found", False))
    print(f"OK {pdf.name}: firm={result['firm']}, {found} fields found, "
          f"{len(result['warnings'])} warnings, 0 API calls")
    print(f"  -> {out / 'deal_data.json'}")


if __name__ == "__main__":
    main()

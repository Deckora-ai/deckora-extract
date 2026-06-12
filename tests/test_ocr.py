"""OCR fallback tests: graceful degradation when Tesseract is absent, and the
MEDIUM cap on OCR-derived fields. No PDFs, no network.

Run: python packages/extraction/tests/test_ocr.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from shop1031_extract import ocr, extract  # noqa: E402
from shop1031_extract.model import Doc, Page, Field  # noqa: E402


def test_available_returns_bool():
    assert isinstance(ocr.available(), bool)


def test_reason_is_actionable_when_unavailable():
    if not ocr.available():
        r = ocr.reason_unavailable()
        assert "pytesseract" in r or "tesseract" in r.lower()


def test_ocr_cap_downgrades_single_path_high():
    # an OCR-marked doc must have its single-path HIGH fields capped to MEDIUM
    doc = Doc(path="x.pdf", page_count=1,
              pages=[Page(number=1, width=1, height=1, text="cap rate 5.00%")],
              full_text="cap rate 5.00%", ocr_used=True)
    result = extract.run(doc)
    for name, f in result["fields"].items():
        if isinstance(f, Field) and f.confidence == "high":
            assert len(f.paths) >= 2, \
                f"{name} HIGH on OCR text must have 2+ corroborating paths"


def test_no_cap_when_ocr_unused():
    # control: a normal (non-OCR) doc is not blanket-downgraded
    doc = Doc(path="Taco Bell - Decatur, AL - OM.pdf", page_count=1,
              pages=[Page(number=1, width=1, height=1,
                          text="2142 6th Ave SE, Decatur, AL 35601\nCap Rate 5.25%")],
              full_text="2142 6th Ave SE, Decatur, AL 35601\nCap Rate 5.25%",
              ocr_used=False)
    result = extract.run(doc)
    cap = result["fields"].get("capRate")
    assert cap and cap.found and cap.confidence in ("high", "medium")


def _run():
    fns = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        try:
            fn()
            passed += 1
            print(f"  PASS {fn.__name__}")
        except AssertionError as e:
            print(f"  FAIL {fn.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            print(f"  ERR  {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{passed}/{len(fns)} passed")
    return passed == len(fns)


if __name__ == "__main__":
    sys.exit(0 if _run() else 1)

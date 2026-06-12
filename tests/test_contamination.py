"""Broker-block contamination + subject-corroboration guard tests.

Synthetic OMs where a broker contact block (phone, email, brokerage street
address, license line) would contaminate a subject-property field. Each test
asserts the guard rejects or downgrades the broker value so the subject field
never reaches HIGH on contaminated evidence.

No PDFs, no network, no API. Run:
  python packages/extraction/tests/test_contamination.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from shop1031_extract import fields as F  # noqa: E402
from shop1031_extract import contamination as C  # noqa: E402
from shop1031_extract.model import Doc, Page  # noqa: E402


def doc_from_pages(page_texts, path="synthetic"):
    pages = [Page(number=i + 1, width=612, height=792, text=t)
             for i, t in enumerate(page_texts)]
    return Doc(path=path, page_count=len(pages), pages=pages,
               full_text="\n".join(page_texts))


# The canonical near-miss: subject is Cincinnati OH (letter-spaced, so its
# "City, ST" does not parse), broker block is Huntington Beach CA. The guard
# must keep the broker city out of the HIGH lane.
WOOSTER = (
    "PANERA BREAD - NNN Lease\n"
    "7 5 1 0  W o o s t e r  P i k e ,  C i n c i n n a t i ,  O H  4 5 2 2 7\n"
    "Brent L. Hensley | 760-473-0520 | brent@1031nnnsearch.com\n"
    "5831 Lancefield Drive, Huntington Beach, CA 92649\n"
    "CRE Lic. #00841876\n"
)


def test_broker_block_detected():
    spans = C.broker_block_spans(WOOSTER)
    assert spans, "license line + contact bar should be detected as a broker block"
    # the broker street address sits inside a detected block
    idx = WOOSTER.find("5831 Lancefield")
    assert C.offset_in_broker_block(WOOSTER, idx)


def test_broker_city_excluded_from_vote():
    doc = doc_from_pages([WOOSTER], path="Panera Bread Cincinnati OH OM.pdf")
    cs = F._scan_city_state(doc)
    assert cs is not None
    city, st = cs[0], cs[1]
    assert (city, st) != ("Huntington Beach", "CA"), \
        "broker-block city must not win the city vote"


def test_broker_zip_not_high_without_subject_corroboration():
    # filename names the subject city (Cincinnati); the only clean City,ST,ZIP in
    # body text is the broker's. The address must not grade HIGH on the broker zip.
    doc = doc_from_pages([WOOSTER], path="Panera Bread Cincinnati OH OM.pdf")
    a = F.extract_address(doc)
    if a.found and isinstance(a.value, dict):
        if a.value.get("zip") == "92649":
            assert a.confidence != "high", "broker zip must not reach HIGH"
        # whatever city wins, the broker city may not be a HIGH subject value
        if a.confidence == "high":
            assert "huntington" not in str(a.value.get("city", "")).lower()


def test_uncorroborated_city_capped_at_medium():
    # body names only a broker city (Dallas), filename names the subject (Waco).
    # Dallas is not in the filename/cover title, so it cannot grade HIGH.
    body = ("OFFERING MEMORANDUM\n"
            "Exclusively Listed By\n"
            "Jane Broker | 214-555-1212 | jane@brokerfirm.com\n"
            "100 Main St, Dallas, TX 75201\n"
            "License No. 12345678\n")
    doc = doc_from_pages([body], path="Dollar General - Waco, TX - OM.pdf")
    a = F.extract_address(doc)
    if a.found and a.confidence == "high":
        assert "dallas" not in str(a.value.get("city", "")).lower(), \
            "an uncorroborated broker city must not grade HIGH"


def test_portfolio_cover_not_high():
    # multi-city portfolio cover: no single subject city, so none grades HIGH.
    cover = ("Eight-Property Portfolio | Absolute NNN\n"
             "Offering Memorandum\n"
             "Frackville, PA | Pottsville, PA | Hazleton, PA | "
             "St. Clair, PA | Bath, PA\n"
             "1000 Alliance Dr, Hazle Township, PA 17201\n")
    doc = doc_from_pages([cover], path="LVHN Portfolio - PA - OM.pdf")
    assert C.is_portfolio_cover(doc), "5-city cover should read as a portfolio"
    a = F.extract_address(doc)
    if a.found and isinstance(a.value, dict):
        assert a.confidence != "high", \
            "no city in a multi-property portfolio should grade HIGH"


def test_clean_subject_still_high():
    # control: a normal single-tenant OM with the subject address in clean text
    # and a corroborating filename must still reach HIGH (guard is not a blanket
    # downgrade).
    body = ("TACO BELL\n"
            "2142 6th Ave SE, Decatur, AL 35601\n"
            "Offered at $3,529,000\n")
    doc = doc_from_pages([body], path="Taco Bell - Decatur, AL - OM.pdf")
    a = F.extract_address(doc)
    assert a.found and isinstance(a.value, dict)
    assert a.value["city"] == "Decatur" and a.value["state"] == "AL"
    assert a.value["zip"] == "35601"
    assert a.confidence == "high", "a corroborated clean subject must stay HIGH"


def test_license_line_variants_detected():
    for line in ("CRE Lic. #00841876", "CA DRE #01980430", "License No. 12345",
                 "CalDRE# 01234567", "Broker of Record: ACME Realty"):
        assert C.line_is_broker_context(line), f"should flag broker line: {line!r}"


def test_non_broker_line_not_flagged():
    for line in ("2142 6th Ave SE, Decatur, AL 35601", "NOI $185,250",
                 "Cap Rate 5.25%", "Year Built 2026"):
        assert not C.line_is_broker_context(line), f"should NOT flag: {line!r}"


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

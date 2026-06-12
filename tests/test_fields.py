"""Unit tests for the typed extractors. No PDFs, no network, no API.

Build small synthetic Docs from text (and word boxes for the spatial demo
tests) and assert the extractors read them correctly.

Run:  python -m pytest packages/extraction/tests/ -q
  or: python packages/extraction/tests/test_fields.py   (pytest-free runner)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from shop1031_extract import fields as F  # noqa: E402
from shop1031_extract.model import Doc, Page, Word  # noqa: E402


def doc_from_pages(page_texts, words_per_page=None):
    pages = []
    for i, t in enumerate(page_texts):
        pages.append(Page(number=i + 1, width=612, height=792, text=t,
                          words=(words_per_page or {}).get(i + 1, [])))
    return Doc(path="synthetic", page_count=len(pages), pages=pages,
               full_text="\n".join(page_texts))


# ---- low-level helpers -------------------------------------------------

def test_money():
    assert F._money("$3,529,000") == 3529000
    assert F._money("185,250") == 185250
    assert F._money("abc") is None


def test_parse_date():
    assert F._parse_date("December 19, 2025") == "2025-12-19"
    assert F._parse_date("12/19/2025") == "2025-12-19"
    assert F._parse_date("9/30/50") == "2050-09-30"
    assert F._parse_date("September 2050") == "2050-09-01"
    assert F._parse_date("nope") is None


# ---- key-value pass ----------------------------------------------------

INV_SUMMARY = """INVESTMENT SUMMARY
Tenant
Taco Bell
Address
2142 6th Ave SE, Decatur, AL 35601
Price
$3,529,000
Cap Rate
5.25%
NOI
$185,250
Rent Commencement
December 19, 2025
Lease Expiration
September 30, 2050
Rental Increases
1% annual rental increases
"""


def test_kv_price_cap_noi():
    d = doc_from_pages(["cover $3,529,000 | CAP RATE: 5.25%", INV_SUMMARY])
    assert F.extract_price(d).value == 3529000
    assert F.extract_cap_rate(d).value == 0.0525
    assert F.extract_noi(d).value == 185250


def test_kv_dates():
    d = doc_from_pages([INV_SUMMARY])
    assert F.extract_commenced(d).value == "2025-12-19"
    assert F.extract_expires(d).value == "2050-09-30"


def test_address_with_zip():
    d = doc_from_pages([INV_SUMMARY])
    a = F.extract_address(d).value
    assert a["city"] == "Decatur"
    assert a["state"] == "AL"
    assert a["zip"] == "35601"


def test_address_full_state_name():
    d = doc_from_pages(["TACO BELL\nDecatur, Alabama (Huntsville MSA)"])
    a = F.extract_address(d).value
    assert a["city"] == "Decatur" and a["state"] == "AL"


# ---- lease type mapping ------------------------------------------------

def test_lease_types():
    assert F.extract_lease_type(doc_from_pages(["Absolute NNN lease"])).value == "absolute_net"
    assert F.extract_lease_type(doc_from_pages(["a triple net (NNN) lease"])).value == "nnn"
    assert F.extract_lease_type(doc_from_pages(["double net (NN) structure"])).value == "nn"
    assert F.extract_lease_type(doc_from_pages(["subject to a ground lease"])).value == "ground"


def test_escalation():
    assert F.extract_escalation(doc_from_pages(["2.5% annual rent escalations"])).value == 0.025
    assert F.extract_escalation(doc_from_pages(["Rental Increases 1% annually"])).value == 0.01


def test_building_sf_and_acres():
    d = doc_from_pages(["Building Size 1,700 SF on a 0.65 AC parcel"])
    assert F.extract_building_sf(d).value == 1700
    d2 = doc_from_pages(["78,184 square foot industrial facility on approximately 7 acres"])
    assert F.extract_building_sf(d2).value == 78184
    assert F.extract_lot_acres(d2).value == 7.0


def test_drive_thru_and_year():
    assert F.extract_drive_thru(doc_from_pages(["dedicated drive-thru lane"])).value is True
    assert F.extract_year_built(doc_from_pages(["Year Built 2026"])).value == 2026


def test_traffic():
    assert F.extract_traffic(doc_from_pages(["30,220 vehicles per day"])).value == 30220
    assert F.extract_traffic(doc_from_pages(["AVENUE G - 11,952 CPD"])).value == 11952


# ---- spatial demographics (rightmost = 5-mile) -------------------------

def test_demographics_rightmost():
    # header row establishes columns: 1 Mile @200, 3 Mile @300, 5 Mile @400
    def hw(text, x, yy=70.0):
        return Word(text=text, x0=x, y0=yy, x1=x + 18, y1=yy + 10, page=1)
    header = [hw("1", 190), hw("Mile", 210), hw("3", 290), hw("Mile", 310),
              hw("5", 390), hw("Mile", 410)]
    # one visual row: label + 1mi + 3mi + 5mi numbers, increasing left->right
    y = 100.0
    def w(text, x):
        return Word(text=text, x0=x, y0=y, x1=x + 30, y1=y + 10, page=1)
    row = header + [w("Population", 50), w("4,380", 200), w("42,379", 300), w("62,137", 400)]
    inc_y = 130.0
    def w2(text, x):
        return Word(text=text, x0=x, y0=inc_y, x1=x + 30, y1=inc_y + 10, page=1)
    inc_row = [w2("Median", 50), w2("Household", 90), w2("Income", 140),
               w2("$50,000", 200), w2("$60,000", 300), w2("$69,483", 400)]
    text = ("DEMOGRAPHICS  1 Mile 3 Mile 5 Mile\n"
            "Population 4,380 42,379 62,137\n"
            "Median Household Income $50,000 $60,000 $69,483\n")
    d = doc_from_pages([text], words_per_page={1: row + inc_row})
    assert F.extract_population_5mi(d).value == 62137


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

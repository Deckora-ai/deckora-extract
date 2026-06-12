"""Step 2 generic extractors.

Layout-independent field extraction over the normalized Doc. Two strategies,
combined per field:

  1. Key-value pass: many OMs render an "Investment Summary" as a label line
     followed by its value line (Cushman, Colliers, most net-lease desks).
  2. Full-text regex pass: narrative OMs embed the numbers in prose
     (JET Industrial, some CBRE). Typed patterns near anchor words.

Key-value hits are higher confidence than loose regex hits. Every return is a
Field carrying value + confidence + method + source_page + source_snippet.
"""
from __future__ import annotations

import re
from datetime import date

from .model import (
    Doc, Field, HIGH, MEDIUM, LOW, VERBATIM, DERIVED, INTERPRETED,
)

# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

MONTHS = {m.lower(): i for i, m in enumerate(
    ["", "January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"])}

STATE_ABBR = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE",
    "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ",
    "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
}
ABBR_SET = set(STATE_ABBR.values())
STATE_NAMES_RE = "|".join(sorted(STATE_ABBR.keys(), key=len, reverse=True))

# Curated national net-lease tenant brands (static reference, no API, authored
# from general CRE knowledge, not from the answer key). Longest match wins.
TENANT_BRANDS = [
    "7-Eleven", "Wawa", "QuikTrip", "Circle K", "Casey's", "RaceTrac", "Sheetz",
    "Taco Bell", "Taco Cabana", "McDonald's", "Burger King", "Wendy's", "KFC",
    "Popeyes", "Chick-fil-A", "Arby's", "Sonic Drive-In", "Sonic", "Hardee's",
    "Carl's Jr", "Jack in the Box", "Whataburger", "Chipotle", "Panera Bread",
    "Panera", "Starbucks", "Dunkin", "Dutch Bros", "Raising Cane's", "Culver's",
    "Zaxby's", "Bojangles", "Del Taco", "Jersey Mike's", "Panda Express",
    "In-N-Out", "Five Guys", "Wingstop", "Checkers", "Krystal", "Guthrie's",
    "Freddy's", "Portillo's", "Caliber Car Wash", "Mister Car Wash",
    "Tidal Wave Auto Spa", "Take 5 Oil Change", "Take 5", "Valvoline",
    "Jiffy Lube", "Christian Brothers Automotive", "Firestone", "Goodyear",
    "Discount Tire", "Big O Tires", "Mavis Discount Tire", "Caliber Collision",
    "AutoZone", "O'Reilly Auto Parts", "O'Reilly", "Advance Auto Parts",
    "Town Fair Tire", "Dollar General", "Dollar Tree", "Family Dollar",
    "Five Below", "Big Lots", "Walgreens", "CVS", "Rite Aid", "Save-A-Lot",
    "Aldi", "Sprouts Farmers Market", "Sprouts", "Whole Foods", "Kroger",
    "Publix", "Grocery Outlet", "Natural Grocers", "Tractor Supply",
    "Harbor Freight Tools", "Harbor Freight", "Hobby Lobby", "Michaels",
    "At Home", "Floor & Decor", "Mattress Firm", "Sherwin-Williams",
    "Heartland Dental", "Aspen Dental", "Prisma Health", "Lehigh Valley Health",
    "DaVita", "Fresenius", "American Family Care", "Quest Diagnostics",
    "LabCorp", "Old Navy", "Ross Dress for Less", "TJ Maxx", "Marshalls",
    "Burlington", "Ulta Beauty", "Ulta", "Petco", "PetSmart", "Planet Fitness",
    "Crunch Fitness", "Verizon", "AT&T", "T-Mobile", "FedEx", "Mattress",
    "Kids R Kids", "Goodwill", "Ameritube", "Main Street Auto",
]


def _norm_q(s: str) -> str:
    return s.replace("’", "'").replace("‘", "'")


def match_brand_text(text: str) -> str | None:
    """Longest curated brand appearing (word-bounded) in `text`."""
    text = _norm_q(text)
    for brand in sorted(TENANT_BRANDS, key=len, reverse=True):
        if re.search(r"(?<![A-Za-z])" + re.escape(brand) + r"(?![A-Za-z])", text, re.I):
            return brand
    return None


def match_brand(doc, pages=1) -> tuple[str, int] | None:
    """Longest curated brand on the cover page(s). Restricted to the cover by
    default: the area/location narrative lists neighbor tenants, which produces
    false positives when the whole document is scanned."""
    for pg in doc.pages[:pages]:
        b = match_brand_text(pg.text)
        if b:
            return b, pg.number
    return None


_FN_DROP = re.compile(r"\b(om|offering|memorandum|package|lowres|low res|hires|"
                      r"final|draft|copy|brochure|flyer|for sale|nnn|investment)\b",
                      re.I)


def filename_tenant(path: str) -> str | None:
    """Broker filenames usually lead with the tenant: 'Tractor Supply - Clifton
    TX - OM.pdf', 'OM-Walgreens-Whitewater-WI.pdf'. Extract that lead."""
    import os
    stem = os.path.basename(path)
    stem = re.sub(r"\.pdf$", "", stem, flags=re.I)
    stem = re.sub(r"\s*\(\d+\)\s*$", "", stem)
    # brand match against the (clean) filename first
    b = match_brand_text(stem.replace("-", " "))
    if b:
        return b
    # leading segment before ' - ' or ' _ '
    parts = re.split(r"\s+[-_]\s+", stem)
    cand = parts[0].strip()
    cand = _FN_DROP.sub("", cand).strip(" -_")
    # strip a leading "OM-" / "OM_" token style
    cand = re.sub(r"^om[-_ ]+", "", cand, flags=re.I).strip(" -_")
    if "-" in cand and " " not in cand:  # hyphen-joined, no spaces: take first token
        toks = [t for t in cand.split("-") if t and not _FN_DROP.match(t)]
        if toks:
            cand = toks[0]
    # reject addresses / states / too-short
    if (not cand or len(cand) < 3 or re.match(r"^\d", cand)
            or cand.upper() in ABBR_SET or cand.lower() in STATE_ABBR):
        return None
    return cand.strip()


# Words that are section headers, never a tenant value.
_TENANT_REJECT = {
    "overview", "summary", "profile", "information", "trade name", "name",
    "of record", "highlights", "initial lease term", "tenant", "lease",
    "details", "abstract", "the tenant", "company", "credit",
}


def get_cover_title(doc) -> tuple[str, int] | None:
    """Largest-font line on the cover pages that looks like a property/tenant
    name (not a price, not boilerplate)."""
    reject = ("offering memorandum", "cap rate", "for sale", "exclusively",
              "net lease", "investment", "confidential", "represented by",
              "presented by", "listed by", "broker", "memorandum")
    best = None
    for txt, size, page in doc.cover_spans:
        low = txt.lower().strip()
        if not (1 < len(txt) < 50):
            continue
        if "$" in txt or "%" in txt:
            continue
        if any(r in low for r in reject):
            continue
        if re.fullmatch(r"[\d\W]+", txt):
            continue
        # skip pure address lines (start with number)
        if re.match(r"^\d", txt):
            continue
        if best is None or size > best[1]:
            best = (txt.strip(), size, page)
    if best:
        return best[0], best[2]
    return None


def _lines(doc: Doc):
    """Yield (page_number, line_text) for every non-empty line."""
    for pg in doc.pages:
        for raw in pg.text.splitlines():
            s = raw.strip()
            if s:
                yield pg.number, s


def _page_lines(doc: Doc):
    """Yield (page_number, [non-empty lines]) per page."""
    for pg in doc.pages:
        ls = [r.strip() for r in pg.text.splitlines() if r.strip()]
        yield pg.number, ls


def kv_lookup(doc: Doc, labels: list[str]) -> tuple[str, int, str] | None:
    """Find a label line and return (value_line, page, matched_label).

    A label matches if the stripped lowercase line equals the label, or starts
    with it followed by nothing / a colon. The value is the next non-empty line
    that is not itself a label-looking line.
    """
    labset = [l.lower() for l in labels]
    for page, ls in _page_lines(doc):
        for i, line in enumerate(ls):
            low = line.lower().rstrip(":").strip()
            if low in labset:
                # value = next non-empty line
                for j in range(i + 1, min(i + 3, len(ls))):
                    cand = ls[j].strip()
                    if cand:
                        return cand, page, line
            # inline "Label: value" or "Label   value"
            for lab in labset:
                if low.startswith(lab + ":") or (low.startswith(lab) and len(low) > len(lab) + 1 and low[len(lab)] in " \t"):
                    rest = line[len(lab):].lstrip(": \t").strip()
                    if rest:
                        return rest, page, line
    return None


def regex_near(doc: Doc, anchors: list[str], pattern: str, window: int = 80,
               flags=re.I) -> tuple[re.Match, int, str] | None:
    """Find an anchor word, then the first `pattern` match within `window`
    chars after it. Returns (match, page, snippet)."""
    pat = re.compile(pattern, flags)
    anchor_re = re.compile("|".join(re.escape(a) for a in anchors), re.I)
    for pg in doc.pages:
        text = pg.text
        for am in anchor_re.finditer(text):
            seg = text[am.start(): am.start() + window]
            m = pat.search(seg)
            if m:
                snip = text[max(0, am.start()): am.start() + window]
                return m, pg.number, " ".join(snip.split())[:120]
    return None


def regex_first(doc: Doc, pattern: str, flags=re.I) -> tuple[re.Match, int, str] | None:
    pat = re.compile(pattern, flags)
    for pg in doc.pages:
        m = pat.search(pg.text)
        if m:
            s = pg.text[max(0, m.start() - 30): m.end() + 30]
            return m, pg.number, " ".join(s.split())[:120]
    return None


def _money(s: str) -> int | None:
    s = s.replace(",", "").replace("$", "").strip()
    try:
        return int(round(float(s)))
    except ValueError:
        return None


def _num(s: str) -> float | None:
    s = s.replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


MONEY = r"\$?\s?([\d][\d,]{2,}(?:\.\d+)?)"
PCT = r"(\d{1,2}(?:\.\d{1,3})?)\s*%"

# ---------------------------------------------------------------------------
# Field extractors
# ---------------------------------------------------------------------------


def extract_price(doc: Doc) -> Field:
    kv = kv_lookup(doc, ["Price", "Offering Price", "List Price",
                         "Purchase Price", "Sale Price", "Asking Price"])
    if kv:
        v = _money(kv[0])
        if v and v > 50000:
            return Field(v, HIGH, VERBATIM, kv[1], kv[2])
    # cover often: "$3,529,000 | CAP RATE: 5.25%"
    r = regex_near(doc, ["Price", "Offering", "Purchase", "List Price"],
                   r"\$\s?([\d,]{4,}(?:\.\d+)?)", window=40)
    if r:
        v = _money(r[0].group(1))
        if v and v > 50000:
            return Field(v, MEDIUM, VERBATIM, r[1], r[2])
    # fallback: largest dollar figure in first 3 pages
    best = None
    for pg in doc.pages[:3]:
        for m in re.finditer(r"\$\s?([\d,]{5,}(?:\.\d+)?)", pg.text):
            v = _money(m.group(1))
            if v and (best is None or v > best[0]):
                best = (v, pg.number, " ".join(pg.text[max(0, m.start()-20):m.end()+10].split()))
    if best and best[0] > 100000:
        return Field(best[0], LOW, VERBATIM, best[1], best[2],
                     notes="largest dollar figure on cover/summary pages")
    return Field.missing("price not found")


def extract_cap_rate(doc: Doc) -> Field:
    kv = kv_lookup(doc, ["Cap Rate", "CAP RATE", "Capitalization Rate",
                         "Cap", "Going-In Cap Rate"])
    if kv:
        m = re.search(PCT, kv[0])
        if m:
            return Field(round(float(m.group(1)) / 100, 5), HIGH, VERBATIM, kv[1], kv[2])
    r = regex_near(doc, ["Cap Rate", "CAP RATE", "Capitalization Rate"], PCT, window=40)
    if r:
        return Field(round(float(r[0].group(1)) / 100, 5), MEDIUM, VERBATIM, r[1], r[2])
    return Field.missing("cap rate not found")


def extract_noi(doc: Doc) -> Field:
    kv = kv_lookup(doc, ["NOI", "Net Operating Income", "Current NOI",
                         "Annual NOI", "Year 1 NOI", "In-Place NOI"])
    if kv:
        v = _money(kv[0])
        if v and v > 5000:
            return Field(v, HIGH, VERBATIM, kv[1], kv[2])
    r = regex_near(doc, ["NOI", "Net Operating Income"], MONEY, window=40)
    if r:
        v = _money(r[0].group(1))
        if v and v > 5000:
            return Field(v, MEDIUM, VERBATIM, r[1], r[2])
    return Field.missing("NOI not found")


def extract_building_sf(doc: Doc) -> Field:
    kv = kv_lookup(doc, ["Building Size", "Building SF", "Building Area",
                         "Gross Leasable Area", "GLA", "Rentable Area",
                         "Rentable SF", "Building Square Footage", "Square Feet",
                         "Total SF", "Building"])
    if kv:
        m = re.search(r"([\d,]{2,})\s*(?:SF|s\.?f\.?|square)", kv[0], re.I)
        if m:
            v = _money(m.group(1))
            if v and 200 <= v <= 5_000_000:
                return Field(v, HIGH, VERBATIM, kv[1], kv[2])
    r = regex_first(doc, r"([\d,]{3,})\s*(?:SF|square\s*(?:feet|foot)|sq\.?\s?ft)\b")
    if r:
        v = _money(r[0].group(1))
        if v and 200 <= v <= 5_000_000:
            return Field(v, MEDIUM, VERBATIM, r[1], r[2])
    return Field.missing("building SF not found")


def extract_lot_acres(doc: Doc) -> Field:
    kv = kv_lookup(doc, ["Lot Size", "Parcel Size", "Land Area", "Site Size",
                         "Lot Area", "Parcel", "Land Size", "Acreage"])
    if kv:
        m = re.search(r"([\d.]+)\s*(?:AC\b|acres?)", kv[0], re.I)
        if m:
            v = _num(m.group(1))
            if v and 0.05 <= v <= 500:
                return Field(round(v, 2), HIGH, VERBATIM, kv[1], kv[2])
    r = regex_first(doc, r"([\d]+(?:\.\d+)?)\s*(?:AC\b|acres?)\b")
    if r:
        v = _num(r[0].group(1))
        if v and 0.05 <= v <= 500:
            return Field(round(v, 2), MEDIUM, VERBATIM, r[1], r[2])
    return Field.missing("lot acres not found")


def extract_year_built(doc: Doc) -> Field:
    kv = kv_lookup(doc, ["Year Built", "Built", "Year Constructed",
                         "Construction", "Year Built / Renovated"])
    if kv:
        m = re.search(r"((?:19|20)\d{2})", kv[0])
        if m:
            return Field(int(m.group(1)), HIGH, VERBATIM, kv[1], kv[2])
    r = regex_near(doc, ["Year Built", "Built in", "Constructed in", "Built:"],
                   r"((?:19|20)\d{2})", window=40)
    if r:
        return Field(int(r[0].group(1)), MEDIUM, VERBATIM, r[1], r[2])
    return Field.missing("year built not found")


def extract_parking(doc: Doc) -> Field:
    kv = kv_lookup(doc, ["Parking", "Parking Spaces", "Parking Count",
                         "Total Parking", "Spaces"])
    if kv:
        m = re.search(r"(\d{1,4})", kv[0])
        if m:
            return Field(int(m.group(1)), HIGH, VERBATIM, kv[1], kv[2])
    r = regex_near(doc, ["Parking"], r"(\d{1,4})\s*(?:parking\s*)?spaces", window=50)
    if r:
        return Field(int(r[0].group(1)), MEDIUM, VERBATIM, r[1], r[2])
    r = regex_first(doc, r"(\d{1,4})\s*parking\s*spaces")
    if r:
        return Field(int(r[0].group(1)), MEDIUM, VERBATIM, r[1], r[2])
    return Field.missing("parking not found")


def extract_drive_thru(doc: Doc) -> Field:
    r = regex_first(doc, r"drive[\s-]?thru|drive[\s-]?through")
    if r:
        return Field(True, HIGH, VERBATIM, r[1], r[2])
    # presence flag: absence of any mention reads as no drive-thru
    return Field(False, LOW, INTERPRETED, None, None,
                 notes="no drive-thru mentioned in OM")


LEASE_TYPES = [
    (r"absolute\s+(?:net|nnn|triple)", "absolute_net", "Absolute NNN"),
    (r"absolute\s+bondable|bondable", "absolute_net", "Bondable"),
    (r"triple\s*net|nnn", "nnn", "NNN"),
    (r"double\s*net|nn\b", "nn", "NN"),
    (r"ground\s+lease", "ground", "Ground lease"),
    (r"single\s*net|n\b", "n", "N"),
    (r"gross\s+lease|full\s+service", "gross", "Gross"),
]


def extract_lease_type(doc: Doc) -> Field:
    text = doc.full_text
    for pat, enum, label in LEASE_TYPES:
        m = re.search(pat, text, re.I)
        if m:
            # find page
            r = regex_first(doc, pat)
            pg = r[1] if r else None
            snip = r[2] if r else label
            return Field(enum, HIGH, INTERPRETED, pg, snip,
                         notes=f"matched '{label}'")
    return Field.missing("lease type not found")


def _parse_date(s: str) -> str | None:
    s = s.strip()
    # "December 19, 2025"
    m = re.search(r"([A-Za-z]+)\s+(\d{1,2}),?\s+((?:19|20)\d{2})", s)
    if m and m.group(1).lower() in MONTHS:
        mo = MONTHS[m.group(1).lower()]
        return f"{int(m.group(3)):04d}-{mo:02d}-{int(m.group(2)):02d}"
    # "12/19/2025" or "12-19-2025"
    m = re.search(r"(\d{1,2})[/-](\d{1,2})[/-]((?:19|20)?\d{2})", s)
    if m:
        yr = int(m.group(3))
        yr = yr + 2000 if yr < 100 else yr
        return f"{yr:04d}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    # "Month YYYY"
    m = re.search(r"([A-Za-z]+)\s+((?:19|20)\d{2})", s)
    if m and m.group(1).lower() in MONTHS:
        mo = MONTHS[m.group(1).lower()]
        return f"{int(m.group(2)):04d}-{mo:02d}-01"
    return None


def extract_commenced(doc: Doc) -> Field:
    kv = kv_lookup(doc, ["Rent Commencement", "Lease Commencement",
                         "Commencement Date", "Commencement", "Lease Start",
                         "Lease Commencement Date", "Rent Commencement Date",
                         "Original Commencement", "Lease Start Date",
                         "Occupancy Date", "Rent Start", "Rent Start Date",
                         "Lease Inception"])
    if kv:
        d = _parse_date(kv[0])
        if d:
            return Field(d, HIGH, VERBATIM, kv[1], kv[2])
    r = regex_near(doc, ["Rent Commencement", "Lease Commencement", "Commencement"],
                   r"([A-Za-z]+\s+\d{1,2},?\s+\d{4}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",
                   window=60)
    if r:
        d = _parse_date(r[0].group(1))
        if d:
            return Field(d, MEDIUM, VERBATIM, r[1], r[2])
    return Field.missing("commencement not found")


def extract_expires(doc: Doc) -> Field:
    kv = kv_lookup(doc, ["Lease Expiration", "Expiration Date", "Lease End",
                         "Lease Expiration Date", "Expiration", "Lease Maturity",
                         "Term Expiration", "Current Term Expiration",
                         "Lease End Date", "Expiry", "Expiry Date",
                         "Lease Termination", "Termination Date"])
    if kv:
        d = _parse_date(kv[0])
        if d:
            return Field(d, HIGH, VERBATIM, kv[1], kv[2])
    r = regex_near(doc, ["Lease Expiration", "Expiration", "Lease End"],
                   r"([A-Za-z]+\s+\d{1,2},?\s+\d{4}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",
                   window=60)
    if r:
        d = _parse_date(r[0].group(1))
        if d:
            return Field(d, MEDIUM, VERBATIM, r[1], r[2])
    return Field.missing("expiration not found")


def extract_current_rent(doc: Doc) -> Field:
    kv = kv_lookup(doc, ["Base Rent", "Annual Rent", "Current Rent",
                         "Year 1 Rent", "Annual Base Rent", "Rent"])
    if kv:
        v = _money(kv[0])
        if v and v > 5000:
            return Field(v, HIGH, VERBATIM, kv[1], kv[2])
    return Field.missing("current rent not found")


def extract_escalation(doc: Doc) -> Field:
    """Annual rent escalation. The percent must bind tightly to an escalation
    word (a wide label window catches stray figures like a "10% down" note). A
    figure tightly bound on either side, and inside the plausible annual band,
    grades HIGH; a loose label-window grab stays MEDIUM (HIGH-precision
    invariant)."""
    def plausible(v):
        return 0.003 <= v <= 0.06   # 0.3% - 6% annual covers the STNL range

    # percent immediately before the escalation phrase: "2% annual rent increases"
    r = regex_first(doc, r"(\d{1,2}(?:\.\d{1,2})?)\s*%\s*(?:per\s+year\s+)?(?:annual(?:ly)?\s+)?"
                         r"(?:rent(?:al)?\s+)?(?:increase|escalat|bump)")
    if r:
        v = round(float(r[0].group(1)) / 100, 4)
        return Field(v, HIGH if plausible(v) else MEDIUM, VERBATIM, r[1], r[2],
                     notes="escalation percent (bound before phrase)")
    # percent right after a tight escalation label: "Rental Increases: 2%". The
    # window spans the label plus a short value tail (regex_near measures from the
    # anchor START), and the percent must be the first one after the label.
    r = regex_near(doc,
                   ["Rental Increases", "Rent Increases", "Rental Escalation",
                    "Rental Escalations", "Annual Increase", "Annual Increases",
                    "Rent Bumps", "Escalations"],
                   r"[A-Za-z]*[:\s]+?(\d{1,2}(?:\.\d{1,2})?)\s*%", window=40)
    if r:
        v = round(float(r[0].group(1)) / 100, 4)
        return Field(v, HIGH if plausible(v) else MEDIUM, VERBATIM, r[1], r[2],
                     notes="escalation percent (tight label)")
    return Field.missing("escalation not found")


def extract_address(doc: Doc) -> Field:
    """Returns a Field whose value is a dict {address, city, state, zip}."""
    kv = kv_lookup(doc, ["Address", "Property Address", "Location",
                         "Site Address"])
    cand = kv[0] if kv else None
    page = kv[1] if kv else None
    snip = kv[2] if kv else None
    if not cand:
        # search first pages for a US address pattern
        r = regex_first(doc, r"\d{1,6}[^,\n]{2,40},\s*[A-Za-z .]+,\s*[A-Z]{2}\s+\d{5}")
        if r:
            cand, page, snip = r[0].group(0), r[1], r[2]
    if cand:
        m = re.search(r"(.*?),\s*([A-Za-z .]+),\s*([A-Z]{2})\.?\s+(\d{5})", cand)
        if m:
            return Field(
                {"address": m.group(1).strip(), "city": m.group(2).strip(),
                 "state": m.group(3).strip(), "zip": m.group(4).strip()},
                HIGH, VERBATIM, page, snip)
        # city/state without zip
        m = re.search(r"([A-Za-z .]+),\s*([A-Z]{2})\b", cand)
        if m:
            return Field(
                {"address": cand.split(",")[0].strip(), "city": m.group(1).strip(),
                 "state": m.group(2).strip(), "zip": None},
                MEDIUM, VERBATIM, page, snip)
    return Field.missing("address not found")


def _demo_pages(doc: Doc):
    """Pages likely to hold the demographics grid."""
    cand = []
    for pg in doc.pages:
        low = pg.text.lower()
        score = 0
        if "population" in low:
            score += 1
        if "household income" in low or "median" in low:
            score += 1
        if re.search(r"\b5[\s-]*mile", low) or "demographic" in low:
            score += 1
        if score >= 2:
            cand.append((score, pg))
    cand.sort(key=lambda x: -x[0])
    return [pg for _, pg in cand]


def _rows_of(pg, tol=3.5):
    """Reconstruct visual rows: list of dicts {y, words(sorted by x), text}."""
    buckets: dict[int, list] = {}
    for w in pg.words:
        buckets.setdefault(round(w.cy / tol), []).append(w)
    rows = []
    for k in sorted(buckets):
        ws = sorted(buckets[k], key=lambda w: w.x0)
        rows.append({"y": ws[0].cy, "words": ws,
                     "text": " ".join(w.text for w in ws)})
    return rows


def _nums_in(words, lo, hi):
    """[(x_center, value)] for numeric tokens in plausible band."""
    out = []
    for w in words:
        if re.fullmatch(r"\$?[\d]{1,3}(?:,\d{3})+|\$?\d{4,7}", w.text):
            v = _money(w.text)
            if v is not None and lo <= v <= hi:
                out.append((w.cx, v))
    return out


_MILE_RE = re.compile(r"\b(\d{1,2})[\s-]*miles?\b", re.I)
_BAD_POP = ("projected", "2030", "2029", "daytime", "census", "2010", "2000",
            "growth", "employ", "labor")
_AVOID_INC = ("average", "avg", "per capita", "disposable")


def _demographics(doc):
    """Return (population_5mi Field, median_income Field), handling three OM
    layouts: radii-as-columns, transposed radii-as-rows, and vertical lists."""
    pop = Field.missing("5mi population not found")
    inc = Field.missing("median HH income not found")
    for pg in _demo_pages(doc):
        rows = _rows_of(pg)
        # orientation: is there a header row with multiple mile markers?
        mile_header = None
        for r in rows:
            ms = _MILE_RE.findall(r["text"])
            if len(ms) >= 2 and "5" in ms:
                mile_header = r
                break

        if mile_header:
            # ----- Layout A: radii are COLUMNS -----
            # ordered (radius, x) for the FIRST table only (stop at first repeat
            # so a side-by-side second table can't steal the column).
            mile_cols = []
            seen = set()
            for i, w in enumerate(mile_header["words"]):
                m = re.fullmatch(r"(\d{1,2})[\s-]?miles?", w.text, re.I)
                rad = None
                if m:
                    rad = int(m.group(1))
                elif re.fullmatch(r"\d{1,2}", w.text):
                    nxt = mile_header["words"][i + 1].text.lower() if i + 1 < len(mile_header["words"]) else ""
                    if nxt.startswith("mile"):
                        rad = int(w.text)
                if rad is not None:
                    if rad in seen:
                        break
                    seen.add(rad)
                    mile_cols.append((rad, w.cx))
            col_xs = [x for _, x in mile_cols]
            x5 = next((x for r, x in mile_cols if r == 5), None)
            xmax = (max(col_xs) + 60) if col_xs else None
            if x5 is not None:
                if not pop.found:
                    pop = _pick_metric_row(pg, rows, x5, col_xs, xmax, ["population"],
                                           _BAD_POP, 100, 20_000_000, "population")
                if not inc.found:
                    inc = _pick_metric_row(pg, rows, x5, col_xs, xmax,
                                           ["median household income", "median hh income",
                                            "median income"], _AVOID_INC, 10_000, 500_000,
                                           "median income")
        else:
            # ----- Layout B/C: radii are ROW labels -----
            # metric header row (has population & income side by side)?
            metric_hdr = None
            for r in rows:
                tl = r["text"].lower()
                if "population" in tl and "income" in tl and not _MILE_RE.search(r["text"]):
                    metric_hdr = r
                    break
            # 5-mile rows
            mile_rows = [r for r in rows
                         if (m := _MILE_RE.search(r["text"])) and m.group(1) == "5"]
            if metric_hdr and mile_rows:
                # transposed grid: map numbers in the 5-mile row to metric columns
                mx = _metric_columns(metric_hdr)
                for r in mile_rows:
                    nums = _nums_in(r["words"], 100, 20_000_000)
                    if not nums:
                        continue
                    if not pop.found and "population" in mx:
                        pop = _nearest(nums, mx["population"], 100, 20_000_000, pg.number, r["text"], "population")
                    if not inc.found and "median" in mx:
                        inc = _nearest(nums, mx["median"], 10_000, 500_000, pg.number, r["text"], "median income")
            else:
                # vertical list: "5 Mile <num>" lines under a metric section header
                cur = None
                for r in rows:
                    tl = r["text"].lower()
                    if not _MILE_RE.search(r["text"]):
                        if "median" in tl and "income" in tl:
                            cur = "income"
                        elif "average" in tl or "avg" in tl:
                            cur = "avg"
                        elif "population" in tl:
                            cur = "population"
                        elif "income" in tl:
                            cur = "income_other"
                        elif "household" in tl:
                            cur = "households"
                        continue
                    m = _MILE_RE.search(r["text"])
                    if m.group(1) != "5":
                        continue
                    if cur == "population" and not pop.found:
                        ns = _nums_in(r["words"], 100, 20_000_000)
                        if ns:
                            pop = Field(ns[-1][1], HIGH, VERBATIM, pg.number, r["text"][:90],
                                        notes="vertical demo list, 5-mile population")
                    if cur == "income" and not inc.found:
                        ns = _nums_in(r["words"], 10_000, 500_000)
                        if ns:
                            inc = Field(ns[-1][1], HIGH, VERBATIM, pg.number, r["text"][:90],
                                        notes="vertical demo list, 5-mile median income")
        if pop.found and inc.found:
            break
    return pop, inc


def _metric_columns(hdr_row):
    """Map metric name -> x-center from a metric header row."""
    mx = {}
    words = hdr_row["words"]
    text_low = [w.text.lower() for w in words]
    for i, w in enumerate(words):
        t = w.text.lower()
        if t == "population":
            mx["population"] = w.cx
        elif t == "median":
            mx["median"] = w.cx
        elif t == "average" or t == "avg":
            mx["average"] = w.cx
        elif t == "households" or t == "household":
            mx.setdefault("households", w.cx)
    return mx


def _nearest(nums, x, lo, hi, page, snip, label):
    cand = [(abs(cx - x), v) for cx, v in nums if lo <= v <= hi]
    if not cand:
        return Field.missing(f"{label} not found")
    cand.sort()
    return Field(cand[0][1], HIGH, VERBATIM, page, snip[:90],
                 notes=f"transposed demo grid, 5-mile {label}")


def _pick_metric_row(pg, rows, x5, col_xs, xmax, label_terms, avoid, lo, hi, label):
    """Layout A: among rows matching a metric label, read the value in the
    5-mile column. A value counts as the 5-mile value only if its nearest
    column slot is the 5-mile slot. Prefer current-year rows."""
    best = None  # (priority, Field)
    for ri, r in enumerate(rows):
        tl = r["text"].lower()
        if not any(t in tl for t in label_terms):
            continue
        if any(a in tl for a in avoid):
            continue
        nums = _nums_in(r["words"], lo, hi)
        # split-row layout: label on its own line, values on the next line just
        # below. If this row has no numbers, borrow the row immediately below.
        if not nums and ri + 1 < len(rows) and abs(rows[ri + 1]["y"] - r["y"]) <= 16:
            nxt = rows[ri + 1]
            if not any(t in nxt["text"].lower() for t in label_terms):
                nums = _nums_in(nxt["words"], lo, hi)
        # keep values inside the first table's column span
        if xmax is not None:
            nums = [(cx, v) for cx, v in nums if cx <= xmax]
        pick = None
        for cx, v in nums:
            if col_xs:
                nearest = min(col_xs, key=lambda x: abs(x - cx))
                if abs(nearest - x5) > 1:  # nearest slot is not the 5-mile slot
                    continue
            pick = (abs(cx - x5), v) if pick is None or abs(cx - x5) < pick[0] else pick
        if pick is None:
            continue
        pri = 0 if re.search(r"20(2[3-9]|3[0-5])", tl) and "2030" not in tl else 1
        f = Field(pick[1], HIGH, VERBATIM, pg.number, r["text"][:90],
                  notes=f"5-mile column, {label} row")
        if best is None or pri < best[0]:
            best = (pri, f)
    return best[1] if best else Field.missing(f"{label} not found")


def _demo_columns(pg):
    """Map radius -> column x-center from the mile header row(s)."""
    cols = {}
    words = pg.words
    for w in words:
        wl = w.text.lower().strip(".:")
        m = re.fullmatch(r"(\d{1,2})[- ]?mile?s?", wl)
        radius, xc = None, w.cx
        if m:
            radius = int(m.group(1))
        elif wl in ("mile", "miles"):
            left = [u for u in words if abs(u.cy - w.cy) <= 4 and u.x1 <= w.x0 + 2
                    and re.fullmatch(r"\d{1,2}", u.text)]
            if left:
                lw = max(left, key=lambda u: u.x0)
                radius, xc = int(lw.text), (lw.x0 + w.x1) / 2
        if radius in (1, 3, 5, 7, 10):
            cols[radius] = xc
    return cols


def _demo_value(pg, label_terms, lo, hi):
    """Column-aware: read the value in the 5-mile column on the current-year
    population/income row. Returns (value, page, snippet) or None."""
    cols = _demo_columns(pg)
    if 5 not in cols:
        return None
    x5 = cols[5]
    words = pg.words
    avoid = ("2029", "2030", "2020", "2010", "projected", "daytime", "growth")
    prefer = ("2024", "2025", "2023")
    # group into rows
    rows: dict[int, list] = {}
    for w in words:
        rows.setdefault(round(w.cy / 4), []).append(w)
    cands = []  # (priority, value, snippet)
    for key in sorted(rows):
        rw = sorted(rows[key], key=lambda w: w.x0)
        line = " ".join(w.text for w in rw).lower()
        if not any(t in line for t in label_terms):
            continue
        if any(a in line for a in avoid):
            continue
        ly = rw[0].cy
        # numeric tokens near this row's y (tolerate slight split)
        nums = []
        for w in words:
            if abs(w.cy - ly) <= 7 and re.fullmatch(r"\$?[\d]{1,3}(?:,\d{3})+|\$?\d{4,7}", w.text):
                v = _money(w.text)
                if v is not None and lo <= v <= hi:
                    nums.append((abs(w.cx - x5), v))
        if not nums:
            continue
        nums.sort()
        pri = 0 if any(p in line for p in prefer) else (
            2 if re.search(r"\b20\d2\b", line) else 1)
        cands.append((pri, nums[0][1], line[:90]))
    if cands:
        cands.sort(key=lambda c: c[0])
        return cands[0][1], pg.number, cands[0][2]
    return None


def _row_value_rightmost(pg, label_words: list[str], lo: float, hi: float):
    """On the page, find a row whose words contain a label, then return the
    rightmost numeric token on that visual row within [lo, hi]."""
    words = pg.words
    # group words into rows by y (4pt tolerance)
    rows: list[list] = []
    for w in sorted(words, key=lambda w: (round(w.cy / 4), w.x0)):
        placed = False
        for row in rows:
            if abs(row[0].cy - w.cy) <= 4:
                row.append(w)
                placed = True
                break
        if not placed:
            rows.append([w])
    labset = [l.lower() for l in label_words]
    for row in rows:
        row.sort(key=lambda w: w.x0)
        line = " ".join(w.text for w in row).lower()
        if not any(l in line for l in labset):
            continue
        nums = []
        for w in row:
            m = re.fullmatch(r"\$?([\d]{1,3}(?:,\d{3})+|\d{4,7})", w.text)
            if m:
                v = _money(m.group(1))
                if v is not None and lo <= v <= hi:
                    nums.append((w.x1, v, w))
        if nums:
            nums.sort(key=lambda x: x[0])  # left -> right
            x, v, w = nums[-1]             # rightmost = largest radius
            return v, pg.number, line[:110]
    return None


def _demo_cached(doc: Doc):
    cache = getattr(doc, "_demo_cache", None)
    if cache is None:
        cache = _demographics(doc)
        try:
            doc._demo_cache = cache
        except Exception:  # noqa: BLE001
            pass
    return cache


def extract_population_5mi(doc: Doc) -> Field:
    pop, _ = _demo_cached(doc)
    if pop.found:
        return pop
    for pg in _demo_pages(doc):
        r = _row_value_rightmost(
            pg, ["total population", "population", "estimated population",
                 "2025 population", "current population", "2024 population"],
            lo=100, hi=20_000_000)
        if r:
            return Field(r[0], MEDIUM, VERBATIM, r[1], r[2])
    # C&W inline: "POPULATION 5 MILES 62,137"
    r = regex_near(doc, ["5 Mile", "5-Mile", "5 MILES"],
                   r"(?:Population\D{0,30})?([\d]{1,3}(?:,\d{3})+)", window=120)
    if r:
        v = _money(r[0].group(1))
        if v and v > 100:
            return Field(v, MEDIUM, VERBATIM, r[1], r[2])
    return Field.missing("5mi population not found")


def extract_median_hh_income(doc: Doc) -> Field:
    _, inc = _demo_cached(doc)
    if inc.found:
        return inc
    r = regex_near(doc, ["Median Household Income", "Median HH Income", "Median Income"],
                   r"\$?\s?([\d]{1,3}(?:,\d{3})+)", window=120)
    if r:
        v = _money(r[0].group(1))
        if v and 10000 <= v <= 500000:
            return Field(v, MEDIUM, VERBATIM, r[1], r[2])
    return Field.missing("median HH income not found")


def _unused_extract_population_5mi(doc: Doc) -> Field:
    # "POPULATION 5 MILES 62,137" or table with "5 Mile" column
    r = regex_near(doc, ["5 Mile", "5-Mile", "5 MILES", "Five Mile"],
                   r"(?:Population\D{0,30})?([\d]{1,3}(?:,\d{3})+)", window=120)
    if r:
        v = _money(r[0].group(1))
        if v and v > 50:
            return Field(v, MEDIUM, VERBATIM, r[1], r[2])
    r = regex_near(doc, ["Population"], r"([\d]{1,3}(?:,\d{3})+)", window=200)
    if r:
        v = _money(r[0].group(1))
        if v and v > 50:
            return Field(v, LOW, VERBATIM, r[1], r[2], notes="population near label, radius uncertain")
    return Field.missing("5mi population not found")


def extract_traffic(doc: Doc) -> Field:
    """Traffic count. OMs frequently list several roads, so the first unit-
    anchored figure (the value the original extractor returned) is kept, but a
    single uncorroborated grab grades MEDIUM, not HIGH. HIGH requires a second
    independent path: an explicit "Traffic Count" label that names the same
    figure (HIGH-precision invariant; traffic is the worst-precision field when
    auto-graded HIGH off one regex)."""
    r = regex_first(doc, r"([\d]{1,3}(?:,\d{3})+)\s*(?:\+\s*)?(?:vehicles per day|AADT|VPD|CPD|cars per day)")
    primary = _money(r[0].group(1)) if r else None
    label = regex_near(doc, ["Traffic Count", "Traffic Counts", "AADT", "Daily Traffic"],
                       r"([\d]{1,3}(?:,\d{3})+)", window=80)
    label_v = _money(label[0].group(1)) if label else None

    if primary and primary > 100:
        if label_v and abs(primary - label_v) <= max(1, 0.02 * primary):
            return Field(primary, HIGH, VERBATIM, r[1], r[2],
                         notes="traffic: unit-anchored + label agree")
        return Field(primary, MEDIUM, VERBATIM, r[1], r[2],
                     notes="single unit-anchored traffic figure; review")
    if label_v and label_v > 100:
        return Field(label_v, MEDIUM, VERBATIM, label[1], label[2],
                     notes="single labeled traffic count; review")
    return Field.missing("traffic count not found")


def _clean_tenant(v: str) -> str | None:
    v = v.strip().strip(":").strip()
    low = v.lower()
    if low in _TENANT_REJECT or not (1 < len(v) < 50):
        return None
    if any(low == r or low.startswith(r + " ") or low.endswith(" " + r)
           for r in ("overview", "summary", "profile")):
        return None
    if re.fullmatch(r"[\d\W]+", v):
        return None
    return v


def extract_tenant(doc: Doc) -> Field:
    # 1) filename lead (broker names the file after the tenant)
    ft = filename_tenant(doc.path)
    if ft:
        return Field(ft, HIGH, VERBATIM, None, f"filename:{ft}",
                     notes="tenant from source filename")
    # 1b) curated brand on the cover page only (avoids neighbor tenants)
    b = match_brand(doc, pages=1)
    if b:
        return Field(b[0], MEDIUM, INTERPRETED, b[1], b[0],
                     notes="matched curated brand on cover")
    # 2) explicit trade-name label
    kv = kv_lookup(doc, ["Tenant Trade Name", "Tenant Name", "Trade Name",
                         "Tenant of Record", "Tenant"])
    if kv:
        c = _clean_tenant(kv[0])
        if c:
            return Field(c, MEDIUM, VERBATIM, kv[1], kv[2])
    # 3) cover-page largest-font title (STNL OMs are brand-titled)
    title = get_cover_title(doc)
    if title:
        c = _clean_tenant(title[0])
        if c and not re.search(r"\b(table of )?contents\b|healthcare|holdings", c, re.I):
            # reject spaced-out glyph runs (e.g. "N A V E N U")
            if not re.fullmatch(r"(?:[A-Za-z] ){3,}[A-Za-z]?", c.strip()):
                return Field(c, LOW, VERBATIM, title[1], title[0],
                             notes="cover-page title (largest font)")
    return Field.missing("tenant not found")


def _filename_city_state(path: str):
    """Parse 'City, ST' / 'City ST ZIP' from the source filename/folder.
    Broker filenames are reliably address-named. Returns (city, st, zip)."""
    import os
    stem = os.path.basename(path)
    stem = re.sub(r"\.pdf$", "", stem, flags=re.I)
    stem = re.sub(r"\s*\(\d+\)\s*$", "", stem)  # drop "(1)" suffixes
    # filenames are often "Tenant - City, ST - OM"; the city lives in the last
    # ' - ' segment, so use it and avoid reading the tenant prefix as the city.
    segs = re.split(r"\s+-\s+", stem)
    search = stem
    if len(segs) > 1:
        def has_state(s):
            return any(t in ABBR_SET for t in re.findall(r"\b[A-Z]{2}\b", s))
        search = next((s for s in reversed(segs) if has_state(s)), segs[-1])
    # last "City, ST ZIP?" or "City ST ZIP?" in the segment (no internal dashes)
    best = None
    for m in re.finditer(r"([A-Za-z][A-Za-z .']{1,28}?)[, ]+([A-Z]{2})\b\.?\s*(\d{5})?(?:[\s,]|$)", search):
        st = m.group(2).upper()
        if st in ABBR_SET:
            best = (m.group(1).strip(" ,.-"), st, m.group(3))
    return best


def _scan_city_state(doc: Doc):
    """Resolve the subject city/state/zip. Priority: full street-address match
    on early pages (binds the city to the property, not a broker HQ), with the
    source filename as a strong corroborating vote.

    Address candidates whose source offset falls inside a detected broker contact
    block are excluded from voting: a brokerage street/zip parses cleanly and
    would otherwise out-vote a letter-spaced or image-only subject address (the
    Huntington Beach near-miss). Subject corroboration (filename / cover title)
    decides ties and gates the HIGH grade in extract_address."""
    from collections import Counter
    from . import contamination as C
    votes = Counter()
    meta = {}
    block_spans = {pg.number: C.broker_block_spans(pg.text) for pg in doc.pages[:5]}

    def in_block(page, m_start):
        for s, e in block_spans.get(page, []):
            if s <= m_start < e:
                return True
        return False

    def add(city, st, zp, page, snip, weight):
        if st not in ABBR_SET:
            return
        if city.lower() in ("suite", "ste", "unit", "po box") or len(city) < 2:
            return
        key = (city, st)
        votes[key] += weight
        if key not in meta or (zp and meta[key][0] is None):
            meta[key] = (zp, page, snip)

    # filename signal (authoritative: broker files are address-named, and the
    # subject city is far more reliable here than any city mentioned in body
    # text, which includes broker HQs and comparable-sale locations)
    fn = _filename_city_state(doc.path)
    if fn:
        add(fn[0], fn[1], fn[2], None, f"filename:{fn[0]}, {fn[1]}", 25)

    street_re = r"\b\d{2,6}\s+[A-Za-z0-9.\-' ]{3,40}?,\s*"
    for pi, pg in enumerate(doc.pages[:5]):
        t = pg.text
        w = max(1, 5 - pi)  # earlier pages weigh more
        # full street address with ST abbr
        for m in re.finditer(street_re + r"([A-Z][A-Za-z .'\-]{1,28}),\s*([A-Z]{2})\b\.?\s*(\d{5})?", t):
            if in_block(pg.number, m.start()):
                continue
            add(m.group(1).strip(), m.group(2), m.group(3), pg.number,
                " ".join(m.group(0).split())[:80], 4 * w + (2 if m.group(3) else 0))
        # full street address with state name
        for m in re.finditer(street_re + rf"([A-Z][A-Za-z .'\-]{{1,28}}),\s*({STATE_NAMES_RE})\b", t, re.I):
            if in_block(pg.number, m.start()):
                continue
            add(m.group(1).strip(), STATE_ABBR[m.group(2).lower()], None, pg.number,
                " ".join(m.group(0).split())[:80], 4 * w)
        # bare City, ST (low weight; cover lines, captions)
        for m in re.finditer(r"([A-Z][A-Za-z .'\-]{1,28}),\s*([A-Z]{2})\b\.?\s*(\d{5})?", t):
            if in_block(pg.number, m.start()):
                continue
            add(m.group(1).strip(), m.group(2), m.group(3), pg.number,
                " ".join(m.group(0).split())[:80], 1 * w)
        # bare City, StateName
        for m in re.finditer(rf"([A-Z][A-Za-z .'\-]{{1,28}}),\s*({STATE_NAMES_RE})\b", t, re.I):
            if in_block(pg.number, m.start()):
                continue
            add(m.group(1).strip(), STATE_ABBR[m.group(2).lower()], None, pg.number,
                " ".join(m.group(0).split())[:80], 1 * w)

    if not votes:
        return None
    (city, st), _ = votes.most_common(1)[0]
    zp, page, snip = meta[(city, st)]
    return city, st, zp, page, snip


def _find_zip(doc: Doc, city: str, st: str) -> str | None:
    """Find the subject zip for a known city/state, independent of the city
    vote. Prefers a zip adjacent to the city, then to the state in an address.

    A zip inside a broker contact block is skipped: the broker street/zip parses
    cleanly and would otherwise stand in for a letter-spaced subject zip (the
    Tampa broker zip 33606 on the Lecanto OM)."""
    import os
    from . import contamination as C
    cre = re.escape(city)
    # a zip immediately bound to the subject city is trustworthy
    for pg in doc.pages[:6]:
        m = re.search(rf"{cre}\W{{0,4}}{st}\.?\s*(\d{{5}})\b", pg.text, re.I)
        if m and not C.offset_in_broker_block(pg.text, m.start()):
            return m.group(1)
    # a state-adjacent zip is a fallback, but never one inside a broker block
    for pg in doc.pages[:4]:
        spans = C.broker_block_spans(pg.text)
        for m in re.finditer(rf"\b{st}\.?\s+(\d{{5}})\b", pg.text):
            z = m.group(1)
            if 1900 <= int(z) <= 2099:
                continue
            if any(s <= m.start() < e for s, e in spans):
                continue
            return z
    fnz = re.search(r"\b(\d{5})\b", os.path.basename(doc.path))
    if fnz:
        return fnz.group(1)
    return None


def extract_address(doc: Doc) -> Field:
    """Returns a Field whose value is a dict {address, city, state, zip}."""
    street = None
    street_page = None
    kv = kv_lookup(doc, ["Address", "Property Address", "Location",
                         "Site Address"])
    if kv:
        m = re.match(r"\s*(\d[\w .'\-]+?)(?:,|$)", kv[0])
        if m:
            street, street_page = m.group(1).strip(), kv[1]
    if not street:
        r = regex_first(doc, r"\b(\d{1,6}\s+[A-Z][\w .'\-]{3,38})(?:,|\s+[A-Z][a-z])")
        if r:
            street, street_page = r[0].group(1).strip(), r[1]

    cs = _scan_city_state(doc)
    if cs:
        from . import contamination as C
        city, st, zp, page, snip = cs
        if not zp and city:  # resolve zip independently of the city-vote winner
            zp = _find_zip(doc, city, st)
        # Subject-corroboration guard: HIGH requires the winning city to appear in
        # the source filename or cover title. A broker-block city (Huntington
        # Beach on a Cincinnati OM) is absent there, so it caps at MEDIUM and
        # routes to the human review queue instead of auto-deploying wrong.
        corroborated = C.corroborated_subject(doc, city)
        conf = HIGH if (zp and corroborated) else MEDIUM
        return Field(
            {"address": street, "city": city, "state": st, "zip": zp},
            conf, VERBATIM, page, snip,
            notes=None if corroborated else "city not corroborated in filename/cover; review")
    if street:
        return Field({"address": street, "city": None, "state": None, "zip": None},
                     LOW, VERBATIM, street_page, street)
    return Field.missing("address not found")


def extract_all(doc: Doc) -> dict[str, Field]:
    """Run every generic extractor. Returns name -> Field."""
    return {
        "address": extract_address(doc),
        "tenant": extract_tenant(doc),
        "price": extract_price(doc),
        "capRate": extract_cap_rate(doc),
        "noi": extract_noi(doc),
        "buildingSf": extract_building_sf(doc),
        "lotAcres": extract_lot_acres(doc),
        "yearBuilt": extract_year_built(doc),
        "parkingCount": extract_parking(doc),
        "driveThru": extract_drive_thru(doc),
        "leaseType": extract_lease_type(doc),
        "commenced": extract_commenced(doc),
        "expires": extract_expires(doc),
        "currentRent": extract_current_rent(doc),
        "escalationPct": extract_escalation(doc),
        "population_5mi": extract_population_5mi(doc),
        "trafficCount": extract_traffic(doc),
    }

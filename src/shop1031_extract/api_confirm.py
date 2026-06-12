"""Leg 2.5: deterministic confirmation that grows the HIGH lane.

These are NOT generative model calls. They are deterministic data lookups
(Google Geocoding, offline pgeocode, SEC EDGAR) and pure arithmetic. The
No-API-Extraction Law is intact: extraction stays code-only; this layer only
adjusts CONFIDENCE and attaches review suggestions. It never overwrites an
extracted value, and it never fabricates one.

OPTIONAL and env-gated. With no GOOGLE_MAPS_API_KEY and no network, geocoding is
skipped; pgeocode runs offline if installed; EDGAR is skipped without network.
The core extraction path does not import this module, so the pipeline runs to
completion offline with no credentials.

HIGH-precision invariant. Every escalation to HIGH for a location field flows
through the subject-corroboration guard (contamination.corroborated_subject):
the confirmed value must be corroborated as belonging to the SUBJECT property
(present in the filename / cover title and not a portfolio cover), so a broker
office address that geocodes or zip-resolves cleanly can never self-promote a
wrong city/state to HIGH. Disagreement only ever moves an item toward review.

Escalation table (location fields):
  HIGH  + confirm agrees + subject-corroborated -> HIGH (stays)
  HIGH  + confirm disagrees                     -> MEDIUM, conflict logged
  MED   + confirm agrees + subject-corroborated -> HIGH (the volume win)
  MED   + confirm can't verify / not corroborated -> stays MEDIUM (review)
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

from .model import Field, HIGH, MEDIUM, LOW
from . import contamination as C

_OUT = Path(os.environ.get("SHOP1031_EXTRACT_OUT",
                           str(Path(__file__).resolve().parents[2] / "out")))
CACHE = _OUT / ".cache"
EDGAR_TTL = 90 * 86400
UA = os.environ.get("EDGAR_USER_AGENT", "shop1031-extract contact@example.com")


# ---------- key + cache ----------

def _load_key():
    # repo-root .env (parents[4] from this module), then environment
    here = Path(__file__).resolve()
    for env in (here.parents[4] / ".env", here.parents[3] / ".env"):
        if env.exists():
            for line in env.read_text(encoding="utf-8").splitlines():
                if line.startswith("GOOGLE_MAPS_API_KEY="):
                    return line.split("=", 1)[1].strip()
    return os.environ.get("GOOGLE_MAPS_API_KEY")


_KEY = _load_key()


def _cache_path(kind, key):
    h = hashlib.sha256(key.encode()).hexdigest()[:16]
    d = CACHE / kind
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{h}.json"


def _cache_get(kind, key, ttl=None):
    p = _cache_path(kind, key)
    if not p.exists():
        return None
    rec = json.loads(p.read_text(encoding="utf-8"))
    if ttl and (time.time() - rec.get("_fetched", 0)) > ttl:
        return None
    return rec.get("data")


def _cache_put(kind, key, data):
    _cache_path(kind, key).write_text(
        json.dumps({"_fetched": time.time(), "data": data}), encoding="utf-8")


# ---------- Google Geocoding (network, env-gated) ----------

def geocode(address):
    """address -> {lat,lng,formatted,city,state,zip,county} | None. Cached
    forever. No call without GOOGLE_MAPS_API_KEY; returns None offline."""
    if not address:
        return None
    cached = _cache_get("geocode", address)
    if cached is not None:
        return cached or None
    if not _KEY:
        return None
    url = "https://maps.googleapis.com/maps/api/geocode/json?" + urllib.parse.urlencode(
        {"address": address, "key": _KEY})
    try:
        r = json.load(urllib.request.urlopen(url, timeout=15))
    except Exception:  # noqa: BLE001 - network optional
        return None
    if r.get("status") != "OK" or not r.get("results"):
        _cache_put("geocode", address, {})
        return None
    res = r["results"][0]
    short = {c["types"][0]: c["short_name"] for c in res["address_components"]}
    long = {c["types"][0]: c["long_name"] for c in res["address_components"]}
    loc = res["geometry"]["location"]
    out = {
        "lat": loc["lat"], "lng": loc["lng"],
        "formatted": res["formatted_address"],
        "city": long.get("locality") or long.get("sublocality_level_1")
                or long.get("postal_town") or long.get("administrative_area_level_3"),
        "state": short.get("administrative_area_level_1"),
        "zip": long.get("postal_code"),
        "county": long.get("administrative_area_level_2"),
    }
    _cache_put("geocode", address, out)
    return out


# ---------- pgeocode offline zip -> city/state ----------

_NOMI = None


def zip_lookup(zipcode):
    """Offline zip -> {city, state, county} via pgeocode (public-domain GeoNames,
    downloaded once then offline). Deterministic. Returns None if pgeocode is not
    installed."""
    if not zipcode:
        return None
    cached = _cache_get("zip", zipcode)
    if cached is not None:
        return cached or None
    global _NOMI
    try:
        if _NOMI is None:
            import pgeocode
            _NOMI = pgeocode.Nominatim("us")
        r = _NOMI.query_postal_code(str(zipcode))
        city = getattr(r, "place_name", None)
        state = getattr(r, "state_code", None)
        if not city or isinstance(city, float):  # NaN for unknown zip
            _cache_put("zip", zipcode, {})
            return None
        out = {"city": str(city), "state": str(state),
               "county": str(getattr(r, "county_name", "") or "")}
    except Exception:  # noqa: BLE001 - pgeocode optional / offline data may be absent
        return None
    _cache_put("zip", zipcode, out)
    return out


# ---------- SEC EDGAR (network, env-gated by reachability) ----------

def _tickers():
    cached = _cache_get("edgar", "company_tickers", ttl=EDGAR_TTL)
    if cached is not None:
        return cached
    try:
        req = urllib.request.Request(
            "https://www.sec.gov/files/company_tickers.json", headers={"User-Agent": UA})
        data = json.load(urllib.request.urlopen(req, timeout=20))
    except Exception:  # noqa: BLE001 - network optional
        return {}
    table = {v["title"].upper(): {"cik": v["cik_str"], "ticker": v["ticker"],
                                  "title": v["title"]} for v in data.values()}
    _cache_put("edgar", "company_tickers", table)
    return table


_PUBLIC_TENANTS = {
    "walgreens": "WALGREENS", "dollar tree": "DOLLAR TREE", "murphy usa": "MURPHY USA",
    "tractor supply": "TRACTOR SUPPLY", "7-eleven": "7-ELEVEN", "chipotle": "CHIPOTLE",
    "dollar general": "DOLLAR GENERAL", "circle k": "COUCHE", "old navy": "GAP",
    "panera": "PANERA", "natural grocers": "NATURAL GROCERS",
}


def edgar_validate(tenant):
    """For a public tenant brand, confirm an SEC issuer exists (enrichment only,
    never a scored field). Returns {matched, cik, ticker, title} | None."""
    if not tenant:
        return None
    t = tenant.lower()
    frag = next((v for k, v in _PUBLIC_TENANTS.items() if k in t), None)
    if not frag:
        return None
    for title, rec in _tickers().items():
        if frag in title:
            return {"matched": True, **rec}
    return None


# ---------- escalation ----------

def _norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s).lower()) if s else ""


def _city_state_match(a, b):
    na, nb = _norm(a), _norm(b)
    return bool(na and nb) and (na == nb or na in nb or nb in na)


def confirm(doc, fields: dict, tenant=None) -> dict:
    """Apply Leg 2.5 to a pipeline `fields` dict (name -> Field). Mutates
    location-field confidence in place and returns {conflicts, tenantValidation,
    geocode}. Never overwrites a value; every HIGH escalation passes the
    subject-corroboration guard."""
    conflicts = []
    addr_f = fields.get("address")
    if not (addr_f and addr_f.found and isinstance(addr_f.value, dict)):
        return {"conflicts": conflicts,
                "tenantValidation": edgar_validate(tenant), "geocode": None}

    av = addr_f.value
    ecity, estate, ezip = av.get("city"), av.get("state"), av.get("zip")

    full = None
    if av.get("address") and ecity and estate:
        full = f"{av['address']}, {ecity}, {estate}" + (f" {ezip}" if ezip else "")
    geo = geocode(full) if full else None

    def corroborated(value):
        # the SUBJECT guard: the confirmed value must belong to the subject
        return C.corroborated_subject(doc, value)

    # --- geocoding confirms city/state ---
    if geo and geo.get("city") and geo.get("state"):
        city_ok = _city_state_match(geo["city"], ecity)
        state_ok = geo["state"] == estate
        if city_ok and state_ok and corroborated(geo["city"]):
            if addr_f.confidence != HIGH:
                addr_f.confidence = HIGH
                addr_f.notes = "confirmed by geocoding + subject corroboration"
                addr_f.paths = list(addr_f.paths) + [
                    {"path": "geocode", "value": {"city": geo["city"], "state": geo["state"]},
                     "page": None, "snippet": geo.get("formatted")}]
        else:
            # disagreement or uncorroborated: only ever move toward review
            if addr_f.confidence == HIGH and not (city_ok and state_ok):
                addr_f.confidence = MEDIUM
                addr_f.review_required = True
            conflicts.append({"field": "address", "extracted": {"city": ecity, "state": estate},
                              "geocode": {"city": geo["city"], "state": geo["state"],
                                          "zip": geo.get("zip")}})

    # --- offline zip -> city/state (works with no street address) ---
    zinfo = zip_lookup(ezip) if ezip else None
    if zinfo and zinfo.get("city"):
        if zinfo.get("state") and estate and zinfo["state"] != estate:
            # extracted state disagrees with the zip's state: route to review
            if addr_f.confidence == HIGH:
                addr_f.confidence = MEDIUM
                addr_f.review_required = True
            conflicts.append({"field": "address", "extracted": {"city": ecity, "state": estate},
                              "zip_lookup": zinfo})
        elif _city_state_match(zinfo["city"], ecity) and corroborated(ecity):
            # a broker zip resolves to the broker city and would self-confirm;
            # corroboration blocks that (Huntington Beach class)
            if addr_f.confidence != HIGH:
                addr_f.confidence = HIGH
                addr_f.notes = "confirmed by pgeocode zip + subject corroboration"
                addr_f.paths = list(addr_f.paths) + [
                    {"path": "zip(pgeocode)", "value": {"city": zinfo["city"], "state": zinfo["state"]},
                     "page": None, "snippet": str(ezip)}]

    # --- derived arithmetic: price/SF sanity (pure math, no network) ---
    price = fields.get("price")
    sf = fields.get("buildingSf")
    if price and price.found and sf and sf.found and price.value and sf.value:
        psf = price.value / sf.value
        if not (10 <= psf <= 20000):
            conflicts.append({"field": "buildingSf",
                              "detail": f"price/SF {psf:.0f} out of band; check size"})

    return {"conflicts": conflicts,
            "tenantValidation": edgar_validate(tenant), "geocode": geo}


def available() -> bool:
    """True if any confirmation path is wired (geocoding key OR pgeocode)."""
    if _KEY:
        return True
    try:
        import pgeocode  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False

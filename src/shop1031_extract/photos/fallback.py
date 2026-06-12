"""Ordered fallback chain when raw extract and reconstruction both fail.

Per the Phase 2 spec: (a) Google Street View Static, (b) Mapbox satellite,
(c) human-review-queue entry. Every network pass is env-gated and optional;
with no keys present the chain degrades cleanly to the review queue. Tests run
fully offline because the network passes short-circuit when keys are absent.

Caching is mandatory before any fetch (content-addressed per the r2_preserve
pattern) so a re-run never re-bills an API. The fetchers here mirror that
contract but the actual HTTP call is guarded behind a key check; the request
library is imported lazily so importing this module never requires network deps.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

GOOGLE_MAPS_API_KEY = "GOOGLE_MAPS_API_KEY"
MAPBOX_TOKEN = "MAPBOX_TOKEN"

DEFAULT_HEADINGS = (0, 90, 180, 270)
STREETVIEW_BASE_URL = "https://maps.googleapis.com/maps/api/streetview"
MAPBOX_STATIC_URL = "https://api.mapbox.com/styles/v1/mapbox/satellite-v9/static"


def _cache_key(*parts) -> str:
    raw = ":".join(str(p) for p in parts).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _have_key(name: str) -> bool:
    return bool(os.environ.get(name))


def street_view(
    lat: Optional[float],
    lng: Optional[float],
    cache_dir: Path,
    headings=DEFAULT_HEADINGS,
) -> Optional[dict]:
    """Pass 2: Google Street View Static. Returns a photo record or None.

    Returns None (not an error) when the key is absent or coordinates are
    missing, so the chain falls through. Cache is checked before any fetch.
    """
    if not _have_key(GOOGLE_MAPS_API_KEY) or lat is None or lng is None:
        return None
    cache_dir.mkdir(parents=True, exist_ok=True)
    for heading in headings:
        sha = _cache_key(round(lat, 6), round(lng, 6), heading)
        cached = cache_dir / f"{sha}.jpg"
        if cached.exists():
            return _record("street_view", str(cached), {"heading": heading},
                           "Image (c) Google")
        data = _fetch_streetview(lat, lng, heading)
        if data:
            cached.write_bytes(data)
            return _record("street_view", str(cached), {"heading": heading},
                           "Image (c) Google")
    return None


def aerial(
    lat: Optional[float],
    lng: Optional[float],
    cache_dir: Path,
    zoom: int = 18,
) -> Optional[dict]:
    """Pass 3: Mapbox satellite static. Returns a photo record or None."""
    if not _have_key(MAPBOX_TOKEN) or lat is None or lng is None:
        return None
    cache_dir.mkdir(parents=True, exist_ok=True)
    sha = _cache_key(round(lat, 6), round(lng, 6), zoom)
    cached = cache_dir / f"{sha}.jpg"
    if cached.exists():
        return _record("aerial", str(cached), {"zoom": zoom},
                       "(c) Mapbox (c) OpenStreetMap")
    data = _fetch_mapbox(lat, lng, zoom)
    if data:
        cached.write_bytes(data)
        return _record("aerial", str(cached), {"zoom": zoom},
                       "(c) Mapbox (c) OpenStreetMap")
    return None


def _fetch_streetview(lat: float, lng: float, heading: int) -> Optional[bytes]:
    try:
        import requests  # lazy: only when a key is present
    except ImportError:
        return None
    params = {
        "location": f"{lat},{lng}", "heading": heading, "pitch": 0,
        "fov": 90, "size": "1200x800",
        "key": os.environ[GOOGLE_MAPS_API_KEY], "return_error_code": "true",
    }
    try:
        r = requests.get(STREETVIEW_BASE_URL, params=params, timeout=15)
        return r.content if r.status_code == 200 else None
    except Exception:
        return None


def _fetch_mapbox(lat: float, lng: float, zoom: int) -> Optional[bytes]:
    try:
        import requests
    except ImportError:
        return None
    url = f"{MAPBOX_STATIC_URL}/{lng},{lat},{zoom},0/1200x800"
    try:
        r = requests.get(url, params={"access_token": os.environ[MAPBOX_TOKEN]}, timeout=15)
        return r.content if r.status_code == 200 else None
    except Exception:
        return None


def _record(method: str, path: str, extra: dict, attribution: str) -> dict:
    return {
        "method": method, "path": path, "attribution": attribution,
        "captured_at": datetime.now(timezone.utc).isoformat(), **extra,
    }


def review_queue_entry(
    asset_id: str,
    source_pdf: str,
    pass_failures: list[dict],
    out_dir: Path,
) -> dict:
    """Terminal fallback: write a human-review-queue entry with per-pass failure
    reasons. This is what blocks auto-deploy."""
    entry = {
        "asset_id": asset_id,
        "source_pdf": source_pdf,
        "status": "needs_review",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "pass_failures": pass_failures,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{asset_id}.review.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(entry, f, indent=2)
    entry["_path"] = str(path)
    return entry

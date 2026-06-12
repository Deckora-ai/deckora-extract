# APIS

Every external service this stack touches sits on an OPTIONAL path. Extraction,
multi-path verification, and photo de-layering all run offline and free with no
keys set. The services below only enrich verification confidence or provide photo
fallbacks when the local pipeline cannot recover a subject photo.

There are zero LLM API calls anywhere in this stack. No Anthropic, OpenAI, or
hosted vision-model request runs during extraction, verification, or photo
processing. None of the services below are LLMs; they are deterministic data
lookups and static image fetches.

## Summary

| Service | What it does | Required? | Key / auth | Env var |
|---|---|---|---|---|
| Google Maps Geocoding | Address to lat/lng + canonical city/state/zip, for Leg 2.5 confirmation | No | API key, free tier | `GOOGLE_MAPS_API_KEY` |
| Google Street View Static | Fallback subject photo when local de-layering fails | No | same Google key | `GOOGLE_MAPS_API_KEY` |
| Mapbox Static Tiles | Fallback aerial image when Street View is unavailable | No | access token, free tier | `MAPBOX_TOKEN` |
| SEC EDGAR | Confirm a public tenant brand maps to a real SEC issuer (enrichment only) | No | none (just a User-Agent) | n/a |
| Cloudflare R2 | Preserve the source PDF at a stable content-addressed URL | No | wrangler CLI on PATH | `R2_BUCKET`, `R2_PUBLIC_BASE`, `R2_MIRROR` |

## Google Maps Geocoding

- Used by `api_confirm.py` (Leg 2.5). Turns the extracted address into a
  canonical city/state/zip, which can corroborate an extracted location and move
  it toward HIGH confidence. It never overwrites an extracted value and a
  disagreement only moves an item toward review.
- Free-tier key: create a project in Google Cloud Console, enable the Geocoding
  API, and create an API key. Google grants a monthly free usage allotment.
- Rate limits: Google enforces per-second and per-day quotas on the key; the
  default free allotment is generous for per-OM confirmation. Results are cached
  forever on disk, so a re-run never re-bills.
- Env var: `GOOGLE_MAPS_API_KEY`.

## Google Street View Static

- Used by the photo fallback chain (`photos/fallback.py`) only when raw stream
  extraction and overlay reconstruction both fail to produce a subject photo.
  Fetches a street-level image at the geocoded coordinates.
- Free-tier key: the same Google key, with the Street View Static API enabled.
- Rate limits: same per-key Google quotas. Fetches are cached content-addressed,
  so a repeated coordinate never re-bills.
- Env var: `GOOGLE_MAPS_API_KEY`. With no key the chain skips Street View.

## Mapbox Static Tiles

- Used by the photo fallback chain after Street View, fetching a satellite tile
  at the coordinates.
- Free-tier token: create a Mapbox account and use the default public token, or
  mint a scoped one. Mapbox grants a free monthly static-image allotment.
- Rate limits: per-token monthly allotment plus request-rate caps. Cached the
  same way, so a repeated coordinate never re-bills.
- Env var: `MAPBOX_TOKEN`. With no token the chain skips aerial and routes the
  OM to the human review queue.

## SEC EDGAR

- Used by `api_confirm.py` to check whether a public tenant brand matches a real
  SEC issuer. This is enrichment context only; it is never a scored field and
  never changes an extracted value.
- No key. EDGAR asks only for a descriptive User-Agent on requests, which the
  module sets. The company-ticker table is cached for 90 days.
- Rate limits: SEC asks callers to stay under about 10 requests per second and to
  identify themselves via the User-Agent. The cached table means one fetch covers
  many OMs.
- Env var: none. Skipped automatically when offline.

## Cloudflare R2 (preservation)

- Used by `r2_preserve.py` to keep the source PDF as the canonical artifact at a
  stable, content-addressed URL, so re-extraction is always re-runnable against
  the original.
- Auth: preservation shells out to the `wrangler` CLI, so it uses whatever
  Cloudflare credentials wrangler is already configured with. No R2 access key or
  secret is read by this code directly.
- Behavior with nothing set: `preserve()` records the canonical handle offline and
  makes no network call. Upload happens only when `R2_BUCKET` is set and wrangler
  is on PATH. `R2_MIRROR=1` mirrors locally instead.
- Env vars: `R2_BUCKET`, `R2_PUBLIC_BASE`, `R2_MIRROR`, plus `SHOP1031_EXTRACT_OUT`
  for the manifest location.

## What needs nothing

The core path needs no service and no key:

- OM intake and field extraction (PyMuPDF, local).
- Multi-path verification and the contamination guard (pure text heuristics).
- Photo de-layering: raw stream recovery and overlay-detect-then-inpaint
  reconstruction (OpenCV, local).
- OCR fallback (local Tesseract, if installed).
- Offline zip to city/state confirmation (pgeocode, local data after a one-time
  download, if installed).

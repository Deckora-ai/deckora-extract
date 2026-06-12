# deckora-extract

HTTP wrapper around `shop1031_extract` (vendored under `src/`, unmodified from
the extractor-handoff snapshot). One endpoint turns an OM PDF into structured
deal data with per-field provenance + HIGH/MEDIUM/LOW verification grades, and
optionally de-layered subject-property photos. Zero LLM calls; fully offline
except downloading the PDF from the presigned URL it is given.

This repository is published to satisfy the AGPL-3.0 obligations of PyMuPDF
(see `LICENSE.txt`). It contains no Deckora platform code, no credentials, and
no customer data.

## API

```
GET  /healthz                      → { ok, version, ocr_available }

POST /v1/extract
     X-Deckora-Ts:  <unix seconds>
     X-Deckora-Sig: hex(hmac_sha256(EXTRACT_SHARED_SECRET, "<ts>." + raw_body))
     { "pdf_url": "<presigned https GET>", "want_photos": true, "asset_id": "<id>" }
   → {
       "extraction": { "status": "complete", "deal_data": {...}, "report": {...} }
                     | { "status": "pending_review", "reason": "...", "ocr_available": bool },
       "photos": [ { "filename", "method", "quality_score", "overlay_removed_pct",
                     "verification_status", "data_b64" } ],
       "photos_pass_failures": [...], "timings_ms": {...}
     }
```

Failure semantics: 401 bad/stale signature · 400 bad body · 413 PDF over cap
(`MAX_PDF_MB`, default 80) · 422 unparseable PDF · 502 download failed ·
503 secret not configured. Photo-pass failures never fail the request — they
land in `photos_error` / `photos_pass_failures` and extraction still returns.

## Env

- `EXTRACT_SHARED_SECRET` — required. Mint with `openssl rand -hex 32`; the
  same value goes into the Deckora worker via `wrangler secret put`.
- `MAX_PDF_MB` (80), `PHOTO_OVERLAY_THRESHOLD` (0.15) — optional tuning.
- `ALLOW_INSECURE_PDF_URL=1` — test-only, permits http:// fixture servers. Never in prod.
- `GOOGLE_MAPS_API_KEY` — optional. When set, two legs activate: (1) Leg 2.5
  geocode confirmation runs before the schema build, escalating MEDIUM location
  fields to HIGH when geocoding agrees AND the subject-corroboration guard
  passes (results in the `confirm` response key); (2) the geocoded `{lat,lng}`
  arms the Street View photo fallback when de-layering yields nothing. Unset or
  empty = both legs no-op, byte-identical output to offline mode.
- `MAPBOX_TOKEN` — optional, aerial photo fallback after Street View.
- `EDGAR_USER_AGENT` — SEC EDGAR identification header (no key needed); EDGAR
  tenant validation runs inside the confirm leg when reachable.
- `r2_preserve` is never used (the worker owns R2).

## Run locally

```bash
pip install -r requirements.txt -r requirements-service.txt
python tests/make_photo_fixture.py            # regenerate synthetic fixtures
python tests/test_fields.py && python tests/test_contamination.py \
  && python tests/test_ocr.py && python tests/test_photos.py
EXTRACT_SHARED_SECRET=dev uvicorn app.main:app --port 8080
```

## Deploy

Fully online — no local Docker. Push to `main` and GitHub Actions
(`.github/workflows/deploy.yml`) runs the offline test suite, builds the
container image on the runner, and deploys the Worker + Container to
Cloudflare. Requires repo secrets `CLOUDFLARE_API_TOKEN` and
`CLOUDFLARE_ACCOUNT_ID`. Local `npx wrangler deploy` (with Docker running)
remains available as a fallback. See `../PDF_Extractor_Integration_Plan.md` §3
for the hosting decision and the worker-side calling contract.

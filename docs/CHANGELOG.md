# CHANGELOG

## 0.2.0

Consolidated handoff release.

Ships:

- Consolidated `shop1031_extract` package: intake, field extraction, broker
  templates, schema assembly, multi-path verification, and the photo
  de-layering subpackage in one importable package.
- Broker-block contamination guard plus the subject-corroboration guard, with
  the portfolio-cover case (`contamination.py`).
- OCR fallback for scanned / thin-text OMs (`ocr.py`), degrading to the human
  review queue when Tesseract is absent, with OCR-derived fields capped at
  MEDIUM.
- Optional Leg 2.5 deterministic confirmation (`api_confirm.py`): Google
  Geocoding, offline pgeocode, SEC EDGAR, derived arithmetic. Env-gated, off the
  core path, routes every HIGH escalation through the contamination guard.
- Optional R2 source-PDF preservation (`r2_preserve.py`): content-addressed,
  stable URL, offline-by-default.
- Photo de-layering subpackage (`photos/`): raw-stream recovery, overlay
  detection and inpaint reconstruction with quality gates, env-gated photo
  fallback chain, and the manifest writer.
- New handoff scripts: `extract_batch.py` (extract a directory of OMs) and
  `verify_corpus.py` (report HIGH / MEDIUM / LOW grades over a folder of
  extractions, or point at a known-truth corpus).
- Synthetic sample OM, generated sample photos, and an illustrative
  `deal_data.json` showing the output shape.

Standing guarantees (unchanged across versions):

- Zero LLM API calls anywhere in extraction, verification, or photo processing.
- Offline and free by default: the core path needs no key and no network. Every
  external service is optional and env-gated, and only enriches verification
  confidence or provides a photo fallback.

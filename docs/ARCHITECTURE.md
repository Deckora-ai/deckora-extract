# ARCHITECTURE

Code-only intake of an Offering Memorandum PDF into a structured `deal_data.json`,
with every field carrying its own provenance and a verification grade. Zero LLM
API calls; the whole core path runs offline.

## Two-step pipeline

```
   OM PDF
     |
     v
[ Step 1: document understanding ]  intake.py
     |   PyMuPDF parses the PDF into a normalized Doc:
     |   per-page text, word geometry (x0,y0,x1,y1), detected tables,
     |   cover-page font spans, and a section tag per page.
     |   No domain logic here. This layer does not know what a cap rate is.
     |   Thin text layer (under 100 chars/page) -> human review queue.
     |
     v
[ Step 2: field extraction ]  extract.py, fields.py, brokers/
     |   - broker-template overrides where the firm is recognized
     |   - generic anchor-text + typed-regex + spatial-grid extraction elsewhere
     |   - each field returns: value / confidence / method / source_page /
     |     source_snippet
     |
     v
[ Multi-path verification ]  verify.py + contamination.py
     |   each high-stakes field is produced by 2+ independent paths and
     |   reconciled into HIGH / MEDIUM / LOW (see VERIFICATION.md)
     |
     v
[ Cross-field arithmetic ]  validate.py
     |   price / NOI / cap and price-per-SF must agree; inconsistent pulls
     |   are downgraded
     |
     v
[ Schema assembly ]  schema.py
     |   deal_data.json (corpus shape) + extraction_report.json (per-field
     |   provenance), with a _meta confidence block and a verification block
     v
   deal_data.json
```

## Step 1: document understanding (intake.py)

PyMuPDF turns the PDF into a `Doc` of `Page` objects. Each page carries its text,
a list of `Word` boxes with pixel geometry, optional detected tables, and a
section tag (cover, financials, lease, tenant, location, demographics) chosen by
the earliest matching header keyword. Cover-page font spans are captured for
title detection. When a page's embedded text is below the 100-chars-per-page
threshold and Tesseract is available, that page is rasterized and OCR-recovered
(marked `ocr_used`, which caps its fields at MEDIUM). With no Tesseract, a
thin-text OM routes to the human review queue with a clear reason. This step has
no idea what any field means, which keeps it reusable.

## Step 2: field extraction (extract.py, fields.py, brokers/)

Where the listing firm is recognized, a broker template (for example
`brokers/cushman.py`) supplies layout-specific overrides. Everywhere else, generic
extractors work from anchor text, typed regex, and the word-geometry grid. Every
extractor returns a `Field`.

### Field provenance shape

Each field records how it was reached:

```json
{"value": 3529000, "confidence": "high", "method": "verbatim",
 "source_page": 3, "source_snippet": "Price $3,529,000"}
```

`method` is one of:

- `verbatim` — read directly off the page
- `derived` — computed (for example NOI / price)
- `interpreted` — mapped to an enum (for example a lease type)
- `lookup` — from a maintained reference table
- `not_found`

The verification layer also attaches a `paths` list recording every independent
path that produced a candidate value.

## Cross-field arithmetic (validate.py)

Financial fields must be internally consistent. Price, NOI, and cap rate form an
identity (NOI = price x cap), and price-per-SF must sit in a sane band. When the
pulled values disagree, the inconsistent field is downgraded rather than asserted.

## Where contamination.py and api_confirm.py sit

- `contamination.py` runs inside verification. Brokers stamp their own contact
  panel (names, phone, email, brokerage street address, license line) on covers
  and footers, and those parse cleanly enough to out-vote the subject property's
  own address. The contamination guard makes sure a value sourced from inside a
  broker block, or from a multi-property portfolio cover, can never grade HIGH for
  a subject-property field. See VERIFICATION.md.
- `api_confirm.py` is the OPTIONAL Leg 2.5. It runs deterministic confirmations
  (Google Geocoding, offline pgeocode, SEC EDGAR, derived arithmetic) and adjusts
  confidence only. It never overwrites or invents a value, and every escalation to
  HIGH passes back through the contamination guard, so a broker office address that
  geocodes cleanly cannot self-promote a wrong city to HIGH. `extract.run` does not
  import this module; the core path runs to completion offline.

## The zero-LLM guarantee

No Anthropic, OpenAI, or hosted vision-model call runs during extraction,
normalization, field inference, verification, or photo processing. The pipeline
runs to completion on a machine with no model credentials and no network. World
knowledge that an extractor cannot read off the page (tenant parent, ticker,
credit rating, prose) is left null and filled downstream from a maintained
reference table, never fabricated per upload.

## R2 preservation doctrine (r2_preserve.py)

The PDF is canonical; `deal_data.json` is derived. Every source OM is preserved
content-addressed (key = sha256 of the bytes) at a stable URL, so re-extraction is
always re-runnable against the preserved original and never converted-and-lost.
This module is OPTIONAL and env-gated. With nothing set it records the canonical
handle offline and makes no network call; it uploads only when `R2_BUCKET` is set
and wrangler is on PATH.

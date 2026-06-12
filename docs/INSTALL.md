# INSTALL

## Requirements

- Python 3.11 or newer (developed and tested on 3.14).
- The three core packages in `requirements.txt`: PyMuPDF, OpenCV (headless), and
  NumPy. Nothing else is required to extract data, verify it, or de-layer photos.

## Install

From the handoff root:

```bash
pip install -r requirements.txt
```

Or install the package itself (editable) with its core dependencies:

```bash
pip install -e .
```

Optional extras, only if you want the matching optional path:

```bash
pip install -e ".[ocr]"        # Tesseract OCR fallback (also needs the binary, below)
pip install -e ".[fallback]"   # Street View / aerial photo fallback (needs API keys)
pip install -e ".[geo]"        # offline zip -> city/state confirmation
```

## Windows note

Set `PYTHONUTF8=1` so console output does not choke on glyphs in OM text.

PowerShell:

```powershell
$env:PYTHONUTF8 = "1"
```

cmd:

```cmd
set PYTHONUTF8=1
```

## Run the sample

A synthetic sample OM ships at `samples/input/sample_om.pdf`. It is self-generated,
not a real broker document.

```bash
# structured data (writes deal_data.json + extraction_report.json, or an
# extraction_status.json if the OM routes to the human review queue)
python scripts/extract_one.py "samples/input/sample_om.pdf" --out samples/output

# de-layered subject-property photos (writes clean JPEGs + photos_manifest.json)
python scripts/extract_photos.py "samples/input/sample_om.pdf" --out samples/output/photos
```

The shipped `samples/output/` already holds the generated photos plus an
illustrative `deal_data.json` showing the output shape. See
`samples/input/README.md` for why the sample data file is illustrative and how to
bring your own OM.

Batch a folder of OMs:

```bash
python scripts/extract_batch.py path/to/oms_dir --out path/to/out_dir
python scripts/verify_corpus.py path/to/out_dir
```

## Run the tests

Four test files, all offline and deterministic. Run them from the handoff root:

```bash
python tests/test_fields.py
python tests/test_contamination.py
python tests/test_ocr.py
python tests/test_photos.py
```

Or with pytest:

```bash
pip install pytest
pytest tests/ -q
```

## Optional: Tesseract OCR

OCR is a fallback for scanned or thin-text OMs whose embedded text layer is too
sparse to read (under 100 characters per page). It is absent by default. Without
it the pipeline still imports and runs; a thin-text OM routes to the human review
queue with a clear reason instead of being OCR-recovered.

To enable it, install both the Python binding and the Tesseract binary:

```bash
pip install pytesseract pillow
```

Then install the Tesseract engine:

- Windows: install from the UB-Mannheim Tesseract build, then ensure
  `tesseract.exe` is on PATH.
- macOS: `brew install tesseract`
- Debian / Ubuntu: `apt-get install tesseract-ocr`

OCR text is noisier than an embedded text layer, so any field read from an
OCR-recovered page is capped at MEDIUM confidence and never reaches HIGH without
an independent non-OCR corroboration path.

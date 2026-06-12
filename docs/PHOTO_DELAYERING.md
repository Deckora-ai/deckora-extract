# PHOTO DELAYERING

Recovers clean subject-property photos from broker OM PDFs by removing composited
broker branding (contact bars, diagonal watermarks, headshots, logos). Classical
computer vision only. No model, no API on the core path. The public entry point is
`shop1031_extract.photos.delayer_om(pdf_path, out_dir, ...)`, wrapped by
`scripts/extract_photos.py`.

### Use cases

This extraction stack supports the standard buyer-rep / tenant-rep workflow: extracting subject-property photos and structured data from listing-broker OMs and repackaging them for buyer-client analysis. This mirrors industry-standard practice in InDesign, Photoshop, and Acrobat, where the same operations are performed manually every day. The value here is automating the manual workflow.

Users are responsible for their own conduct under applicable law, DRE regulations, and the brokerage code of ethics. The tool does not gate use behind authorization prompts, the same way InDesign does not.

## Two structural cases

A structural survey of the corpus (22 OMs, 434 large image streams) found two
cases that drive the whole pipeline. About a third of corpus photos recover
directly as raw streams; about two-thirds need overlay detection or fall through
to a fallback.

### Case 1: raw base stream under a vector overlay (recovered directly)

The clean photo is its own embedded image stream and the broker overlay is drawn
on top as vector text or separate images. `rawextract.py` enumerates every image
stream, decodes color statistics (color entropy and edge density) to decide which
streams are photographic, and counts how many text spans overlap each image's
placement. When three or more text spans overlap a photographic stream, the base
stream is clean and the overlay lives in the vector layer, so the stream is
extracted directly. `method = raw_extract`, `quality_score = 1.0`,
`overlay_removed_pct = 0.0`.

### Case 2: flattened raster (detect overlay, then inpaint)

No vector text overlaps the image, so the overlay (if any) was flattened into the
raster. `overlay.py` detects the overlay regions and `reconstruct.py` removes
them with `cv2.inpaint` and scores the result.

Detectors in `overlay.py`, all classical CV:

- **contact bar**: a near-uniform full-width color band in the top or bottom rows.
  Detected by per-row dominant-color fraction (most pixels match the row median
  even when white text sits on top), with an inner-edge step check to reject open
  sky masquerading as a bar.
- **banner**: a uniform-color horizontal block away from the edges that also
  carries text-like high-frequency content.
- **diagonal watermark**: low-opacity repeating diagonal text. High-pass the
  luminance, run a text-shaped morphological close, then confirm a diagonal
  periodic structure via off-axis energy in the FFT of the residual. The precise
  per-stroke pixel mask is carried into the inpaint, not just a bounding box.
- **headshot**: a small bordered rectangle near a corner holding one compact
  skin-tone connected component, confirmed by a sharp rectangular border. Skin
  tone alone is rejected (tan facade and pale sky both pass a naive skin test).
- **logo**: template match against `photos/logo_templates/`. That directory ships
  empty; per-firm logos are added as they are licensed. With no templates this
  detector returns nothing.

`reconstruct.py` composes the region masks, inpaints (`cv2.INPAINT_TELEA`, falling
back to `INPAINT_NS`), and computes two quality terms plus a coverage term:

- **overlay_removed_pct**: masked fraction of the frame. The quality gate
  (`DEFAULT_MAX_OVERLAY_PCT = 0.15`) rejects any reconstruction that masks more
  than 15 percent of the frame, because past that the inpaint is guessing too much
  of the photo.
- **quality_score**: `0.4 * (1 - overlay_pct) + 0.3 * edge_continuity +
  0.3 * smear_penalty`, in [0, 1]. Edge continuity checks that texture carries
  across the inpaint seam; the smear penalty flags large flat fills where the
  inpaint gave up. A reconstruction passes only at `quality_score >= 0.55`.

A failed gate never fabricates a score. The photo is dropped from the verified set
and the OM moves to the fallback chain.

## Fallback chain (env-gated) then human review queue

When raw extraction and reconstruction both fail to yield any subject photo,
`fallback.py` runs an ordered chain, each step optional and env-gated:

1. **Street View** (`GOOGLE_MAPS_API_KEY`): a street-level image at the geocoded
   coordinates. Skipped with no key or no coordinates.
2. **Aerial** (`MAPBOX_TOKEN`): a satellite tile at the coordinates. Skipped with
   no token.
3. **Human review queue**: a `<asset_id>.review.json` entry recording each pass's
   failure reason. This is the terminal fallback and what blocks auto-deploy.

Every network fetch is cached content-addressed before it runs, so a re-run never
re-bills an API. With no keys present the chain degrades cleanly to the review
queue, and the package imports and runs with no network dependencies. Fallback
photos are written with `verification_status = needs_review`, never `verified`.

## Manifest shape

`photos_manifest.json` (written by `manifest.py`):

```json
{
  "asset_id": "sample_om",
  "source_pdf": "samples/input/sample_om.pdf",
  "photos": [
    {"filename": "exterior-01.jpg", "method": "raw_extract",
     "quality_score": 1.0, "overlay_removed_pct": 0.0,
     "verification_status": "verified"},
    {"filename": "exterior-02.jpg", "method": "inpainted",
     "quality_score": 0.954, "overlay_removed_pct": 0.1149,
     "verification_status": "verified"}
  ],
  "pass_failures": []
}
```

`method` is one of `raw_extract`, `inpainted`, `street_view`, `aerial`.
`verification_status` is `verified` only when the quality gates passed; a failed
gate or a fallback image yields `needs_review`. Clean photos ship as JPEG quality
85 at original aspect, longest side capped at 1920 px.

## Known limitations (candid)

- Watermark detection is geometry-sensitive: the diagonal-FFT confirmation expects
  a roughly regular diagonal repeat and can miss irregular or curved watermark
  placements.
- Contact-bar detection can miss a bar built on a smooth color gradient rather
  than a flat fill, since the dominant-color test keys on a single row median.
- Headshot detection requires a sharp rectangular border; a borderless or
  feathered headshot can be missed.
- Classical inpaint cannot recover detail that the overlay fully occludes. It
  reconstructs plausible texture across small-to-moderate gaps, not the true
  pixels beneath a large opaque panel.
- The raw-extract-versus-flattened decision rests on vector-text overlap counting.
  An OM that flattens a vector overlay into the raster, or that draws a clean
  photo with no overlapping text, can be classed into the other case.
- No subject-property signage OCR yet, so a building's own signage is not read to
  confirm the photo belongs to the subject.
- No PDF unlock step for encrypted OMs; an encrypted PDF cannot be enumerated and
  routes to the review queue.

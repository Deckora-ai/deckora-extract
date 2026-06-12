"""shop1031_extract.photos: code-only OM photo de-layering. No API calls.

Recovers raw subject-property photos from broker OM PDFs, removing composited
broker branding (watermarks, contact bars, banners, headshots, logos). Two
structural cases: raw base streams under vector overlays (extract directly), and
flattened rasters (detect overlay regions and inpaint). When both fail the
quality gates, an env-gated fallback chain (Street View, aerial) runs, and if
that is unavailable the listing routes to the human review queue.

This automates the standard buyer-rep / tenant-rep workflow of extracting
subject-property photos from a listing-broker OM and repackaging them for
buyer-client analysis, the same operation performed by hand in InDesign,
Photoshop, or Acrobat. The tool does not gate use behind authorization prompts;
the user's professional responsibility under applicable law, DRE regulations,
and the brokerage code of ethics governs its use.

Public entry point: delayer_om(pdf_path, out_dir, ...).
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import fitz
import numpy as np

from . import overlay as _overlay
from . import rawextract as _raw
from . import reconstruct as _reconstruct
from . import fallback as _fallback
from .manifest import PhotoEntry, build_manifest, write_manifest, write_photo_jpeg

__all__ = ["delayer_om"]


def _decode_xref(pdf_path: str, xref: int) -> Optional[np.ndarray]:
    doc = fitz.open(pdf_path)
    try:
        info = doc.extract_image(xref)
    finally:
        doc.close()
    return _raw._decode_to_array(info.get("image", b""))


def delayer_om(
    pdf_path: str,
    out_dir: str | Path,
    asset_id: Optional[str] = None,
    overlay_threshold: float = _reconstruct.DEFAULT_MAX_OVERLAY_PCT,
    geocode: Optional[dict] = None,
) -> dict:
    """De-layer every subject-property photo in an OM PDF.

    Returns the manifest dict; also writes JPEGs + manifest to out_dir.

    geocode: optional {lat, lng} used only by the env-gated fallback chain. With
    no API keys present the fallback degrades to a review-queue entry.
    """
    pdf_path = str(pdf_path)
    out_dir = Path(out_dir)
    asset_id = asset_id or Path(pdf_path).stem

    records = _raw.enumerate_images(pdf_path)
    candidates = _raw.photo_candidates(records)

    photos: list[PhotoEntry] = []
    pass_failures: list[dict] = []
    counter = {"exterior": 0}

    for rec in candidates:
        arr = _decode_xref(pdf_path, rec.xref)
        if arr is None:
            continue

        if rec.method == "raw_extract":
            counter["exterior"] += 1
            fn = f"exterior-{counter['exterior']:02d}.jpg"
            write_photo_jpeg(arr, out_dir / fn)
            photos.append(PhotoEntry(
                filename=fn, method="raw_extract", quality_score=1.0,
                overlay_removed_pct=0.0, verification_status="verified"))
            continue

        regions = _overlay.detect_regions(arr)
        mask = _overlay.build_overlay_mask(arr, regions)
        recon = _reconstruct.reconstruct(arr, mask, max_overlay_pct=overlay_threshold)

        if recon.passed:
            counter["exterior"] += 1
            fn = f"exterior-{counter['exterior']:02d}.jpg"
            write_photo_jpeg(recon.image, out_dir / fn)
            photos.append(PhotoEntry(
                filename=fn, method="inpainted",
                quality_score=recon.quality_score,
                overlay_removed_pct=recon.overlay_removed_pct,
                verification_status="verified"))
        else:
            pass_failures.append({
                "xref": rec.xref, "page": rec.page,
                "pass": "reconstruct", "reason": recon.reason,
                "overlay_removed_pct": recon.overlay_removed_pct,
            })

    if not photos:
        lat = (geocode or {}).get("lat")
        lng = (geocode or {}).get("lng")
        sv = _fallback.street_view(lat, lng, out_dir / "_cache" / "streetview")
        if sv:
            counter["exterior"] += 1
            fn = f"exterior-{counter['exterior']:02d}.jpg"
            _copy_into(sv["path"], out_dir / fn)
            photos.append(PhotoEntry(
                filename=fn, method="street_view", quality_score=0.0,
                overlay_removed_pct=0.0, verification_status="needs_review"))
        else:
            pass_failures.append({"pass": "street_view", "reason": "no key or no pano"})
            ar = _fallback.aerial(lat, lng, out_dir / "_cache" / "aerial")
            if ar:
                counter["exterior"] += 1
                fn = f"exterior-{counter['exterior']:02d}.jpg"
                _copy_into(ar["path"], out_dir / fn)
                photos.append(PhotoEntry(
                    filename=fn, method="aerial", quality_score=0.0,
                    overlay_removed_pct=0.0, verification_status="needs_review"))
            else:
                pass_failures.append({"pass": "aerial", "reason": "no key or no tile"})
                _fallback.review_queue_entry(asset_id, pdf_path, pass_failures, out_dir)

    manifest = build_manifest(asset_id, pdf_path, photos)
    manifest["pass_failures"] = pass_failures
    write_manifest(manifest, out_dir)
    return manifest


def _copy_into(src: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(Path(src).read_bytes())

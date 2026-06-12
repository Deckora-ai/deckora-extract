#!/usr/bin/env python3
"""CLI: de-layer subject-property photos from an OM PDF.

Usage:
  python extract_photos.py path/to/OM.pdf --out outdir

Optional:
  --threshold  max overlay-removed fraction before a reconstruction is rejected
               (default 0.15)
  --asset-id   override the asset id (default: PDF stem)

This automates the standard buyer-rep / tenant-rep workflow of pulling
subject-property photos out of a listing-broker OM for buyer-client analysis,
the same operation done by hand in InDesign or Acrobat. See the README use-cases
section.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from shop1031_extract.photos import delayer_om  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="De-layer OM subject-property photos.")
    ap.add_argument("pdf", help="path to the OM PDF")
    ap.add_argument("--out", required=True, help="output directory")
    ap.add_argument("--threshold", type=float, default=0.15,
                    help="max overlay-removed fraction (default 0.15)")
    ap.add_argument("--asset-id", default=None, help="override asset id")
    args = ap.parse_args(argv)

    manifest = delayer_om(
        pdf_path=args.pdf,
        out_dir=args.out,
        asset_id=args.asset_id,
        overlay_threshold=args.threshold,
    )

    print(json.dumps(manifest, indent=2))
    n = len(manifest["photos"])
    verified = sum(1 for p in manifest["photos"] if p["verification_status"] == "verified")
    print(f"\n{n} photo(s), {verified} verified -> {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

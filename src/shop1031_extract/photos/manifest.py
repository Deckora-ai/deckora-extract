"""Output writing and the manifest schema.

Clean photos ship as JPEG q=85 at original aspect, longest side capped at 1920.
The manifest records, per photo, the recovery method, the computed quality
score, the overlay-removed fraction, and a verification_status that is
"verified" only when the quality gates passed. Values are never fabricated; a
failed gate yields "needs_review".
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

JPEG_QUALITY = 85
MAX_LONG_SIDE = 1920


@dataclass
class PhotoEntry:
    filename: str
    method: str  # raw_extract | inpainted | street_view | aerial
    quality_score: float
    overlay_removed_pct: float
    verification_status: str  # verified | needs_review

    def to_dict(self) -> dict:
        return asdict(self)


def _resize_cap(rgb: np.ndarray, long_side: int = MAX_LONG_SIDE) -> np.ndarray:
    h, w = rgb.shape[:2]
    longest = max(h, w)
    if longest <= long_side:
        return rgb
    scale = long_side / longest
    return cv2.resize(rgb, (int(round(w * scale)), int(round(h * scale))),
                      interpolation=cv2.INTER_AREA)


def write_photo_jpeg(rgb: np.ndarray, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    capped = _resize_cap(rgb)
    bgr = cv2.cvtColor(capped, cv2.COLOR_RGB2BGR)
    ok, buf = cv2.imencode(".jpg", bgr, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
    if not ok:
        raise RuntimeError(f"jpeg encode failed for {out_path}")
    out_path.write_bytes(buf.tobytes())


def build_manifest(asset_id: str, source_pdf: str, photos: list[PhotoEntry]) -> dict:
    return {
        "asset_id": asset_id,
        "source_pdf": source_pdf,
        "photos": [p.to_dict() for p in photos],
    }


def write_manifest(manifest: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "photos_manifest.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    return path

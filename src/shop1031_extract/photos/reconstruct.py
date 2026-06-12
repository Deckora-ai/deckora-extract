"""Pass 1c: inpaint masked overlay regions and score the reconstruction.

Given a flattened RGB image and the binary overlay mask from overlay.py, remove
the overlay with cv2.inpaint and decide whether the result is fit to show. The
quality gate is deliberately conservative: a large masked fraction means we are
guessing too much of the frame, so the reconstruction is rejected and the
listing routes to fallback.

No model is involved. quality_score and overlay_removed_pct are computed from
pixels; when a gate fails the caller emits needs_review, never a fabricated
score.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

DEFAULT_MAX_OVERLAY_PCT = 0.15
INPAINT_RADIUS = 3


@dataclass
class Reconstruction:
    image: np.ndarray
    overlay_removed_pct: float
    quality_score: float
    passed: bool
    reason: str


def overlay_removed_pct(mask: np.ndarray) -> float:
    total = mask.shape[0] * mask.shape[1]
    return float((mask > 0).sum()) / total if total else 0.0


def _inpaint(rgb: np.ndarray, mask: np.ndarray) -> np.ndarray:
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    m = (mask > 0).astype(np.uint8) * 255
    try:
        out = cv2.inpaint(bgr, m, INPAINT_RADIUS, cv2.INPAINT_TELEA)
    except cv2.error:
        out = cv2.inpaint(bgr, m, INPAINT_RADIUS, cv2.INPAINT_NS)
    return cv2.cvtColor(out, cv2.COLOR_BGR2RGB)


def _edge_continuity(original_outside: np.ndarray, result: np.ndarray, mask: np.ndarray) -> float:
    """How well edge density inside the inpainted region matches the rest of the
    frame. A clean inpaint carries comparable texture across the seam; a smear
    drops to near zero. Returns [0,1], 1 = matched."""
    g = cv2.cvtColor(result, cv2.COLOR_RGB2GRAY)
    edges = cv2.Canny(g, 60, 160) > 0
    inside = mask > 0
    outside = ~inside
    if inside.sum() == 0 or outside.sum() == 0:
        return 1.0
    e_in = float(edges[inside].mean())
    e_out = float(edges[outside].mean())
    if e_out <= 1e-6:
        return 1.0
    ratio = e_in / e_out
    return float(max(0.0, min(1.0, ratio)))


def _flat_smear_penalty(result: np.ndarray, mask: np.ndarray) -> float:
    """Penalize large flat smears: local variance inside the inpainted region
    that is far below the frame's. Returns [0,1], 1 = no smear."""
    g = cv2.cvtColor(result, cv2.COLOR_RGB2GRAY).astype(np.float32)
    mean = cv2.blur(g, (9, 9))
    var = cv2.blur(g * g, (9, 9)) - mean * mean
    inside = mask > 0
    outside = ~inside
    if inside.sum() == 0 or outside.sum() == 0:
        return 1.0
    v_in = float(np.maximum(var, 0)[inside].mean())
    v_out = float(np.maximum(var, 0)[outside].mean())
    if v_out <= 1e-6:
        return 1.0
    return float(max(0.0, min(1.0, v_in / v_out)))


def reconstruct(
    rgb: np.ndarray,
    mask: np.ndarray,
    max_overlay_pct: float = DEFAULT_MAX_OVERLAY_PCT,
) -> Reconstruction:
    pct = overlay_removed_pct(mask)
    if pct > max_overlay_pct:
        return Reconstruction(
            image=rgb, overlay_removed_pct=round(pct, 4), quality_score=0.0,
            passed=False,
            reason=f"overlay {pct:.1%} exceeds threshold {max_overlay_pct:.0%}",
        )
    if pct == 0.0:
        return Reconstruction(
            image=rgb, overlay_removed_pct=0.0, quality_score=1.0, passed=True,
            reason="no overlay regions; image used as-is",
        )

    result = _inpaint(rgb, mask)
    edge = _edge_continuity(rgb, result, mask)
    smear = _flat_smear_penalty(result, mask)
    coverage_term = 1.0 - pct
    quality = round(0.4 * coverage_term + 0.3 * edge + 0.3 * smear, 4)
    passed = quality >= 0.55
    reason = "ok" if passed else f"quality_score {quality:.2f} below 0.55"
    return Reconstruction(
        image=result, overlay_removed_pct=round(pct, 4),
        quality_score=quality, passed=passed, reason=reason,
    )

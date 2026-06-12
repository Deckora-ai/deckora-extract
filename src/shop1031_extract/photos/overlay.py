"""Pass 1b: detect broker overlay regions on a flattened raster and build a mask.

Input is an RGB uint8 image where the broker overlay has been baked into the
pixels (case 2 flattened). Output is a list of labeled regions plus a single
binary mask (255 = overlay, 0 = keep) that reconstruct.py inpaints.

Detectors, classical CV only:
  contact_bar  near-uniform full-width color band at top or bottom N rows
  banner       uniform-color block (any vertical position) with text inside it
  watermark    low-opacity repeating diagonal text, found via high-pass + text
               morphology, confirmed by a diagonal peak in the FFT of the
               residual
  headshot     small bordered rectangle near a corner with skin-tone pixels
  logo         template match against photos/logo_templates/ (per-firm, opt-in)

Every detector returns confidence in [0,1]. Nothing here calls a model.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

LOGO_TEMPLATE_DIR = Path(__file__).resolve().parent / "logo_templates"

# A row/column counts as "uniform" when its per-channel std is below this.
UNIFORM_STD = 18.0
# Bars are scanned within this fraction of image height from each edge.
BAR_EDGE_FRACTION = 0.22
# Minimum run of uniform rows to call a contact bar.
MIN_BAR_ROWS = 6


@dataclass
class OverlayRegion:
    kind: str  # contact_bar | banner | watermark | headshot | logo
    bbox: tuple  # (x0, y0, x1, y1) in pixel coords
    confidence: float


def _to_gray(rgb: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)


def _row_uniformity(rgb: np.ndarray) -> np.ndarray:
    """Per-row mean of the per-channel standard deviation across width."""
    return rgb.std(axis=1).mean(axis=1)


def _row_dominant_fraction(rgb: np.ndarray, tol: float = 28.0) -> np.ndarray:
    """Per-row fraction of pixels close to that row's median color. A contact
    bar is a flat color block, so most of its pixels match the row median even
    when white text or a logo sits on top (the text is a minority of pixels).
    This is robust where a plain std test breaks once text is composited."""
    med = np.median(rgb, axis=1, keepdims=True)
    dist = np.abs(rgb.astype(np.float32) - med).mean(axis=2)
    return (dist < tol).mean(axis=1)


def detect_contact_bars(rgb: np.ndarray) -> list[OverlayRegion]:
    h, w = rgb.shape[:2]
    dominant = _row_dominant_fraction(rgb)
    band = max(1, int(h * BAR_EDGE_FRACTION))
    regions: list[OverlayRegion] = []
    row_mean = rgb.reshape(h, -1, 3).mean(axis=1)

    # Fraction of width matching the row median that qualifies a row as part of
    # a flat color block (allows minority text/logo pixels on top of the bar).
    DOMINANT_MIN = 0.70

    def scan(rows, top_down: bool):
        run = []
        ordered = rows if top_down else rows[::-1]
        for r in ordered:
            if dominant[r] >= DOMINANT_MIN:
                run.append(r)
            else:
                break
        if len(run) < MIN_BAR_ROWS:
            return
        y0, y1 = min(run), max(run) + 1
        # Reject a smooth photo region (e.g. open sky) masquerading as a bar:
        # a real contact bar differs sharply from the adjacent photo content at
        # its inner edge.
        inner = y1 if top_down else y0 - 1
        if 0 <= inner < h:
            step = float(np.abs(row_mean[inner] - row_mean[(y0 + y1) // 2]).mean())
            if step < 25.0:
                return
        conf = round(0.6 + 0.4 * float(dominant[run].mean()), 3)
        regions.append(OverlayRegion("contact_bar", (0, y0, w, y1), conf))

    scan(list(range(0, band)), top_down=True)
    scan(list(range(h - band, h)), top_down=False)
    return regions


def detect_banner(rgb: np.ndarray) -> list[OverlayRegion]:
    """Uniform-color horizontal block anywhere in the frame that also contains
    text-like high-frequency content. Distinct from a contact bar by position
    (not edge-anchored) and by carrying text."""
    h, w = rgb.shape[:2]
    row_std = _row_uniformity(rgb)
    uniform = row_std < UNIFORM_STD
    regions: list[OverlayRegion] = []
    r = 0
    while r < h:
        if uniform[r]:
            start = r
            while r < h and uniform[r]:
                r += 1
            if (r - start) >= MIN_BAR_ROWS and start > h * BAR_EDGE_FRACTION \
                    and r < h * (1 - BAR_EDGE_FRACTION):
                strip = rgb[start:r]
                edges = cv2.Canny(_to_gray(strip), 60, 160)
                text_ratio = float((edges > 0).mean())
                if text_ratio > 0.01:
                    conf = round(min(1.0, 0.5 + text_ratio * 8), 3)
                    regions.append(OverlayRegion("banner", (0, start, w, r), conf))
        else:
            r += 1
    return regions


def detect_diagonal_watermark(rgb: np.ndarray) -> list[OverlayRegion]:
    """Low-opacity repeating diagonal text. High-pass the luminance, run a
    text-shaped morphological close, then confirm a diagonal periodic structure
    via the FFT magnitude of the residual (off-axis energy)."""
    g = _to_gray(rgb).astype(np.float32)
    blur = cv2.GaussianBlur(g, (0, 0), 3)
    residual = g - blur  # high-pass: keeps thin strokes, drops the photo base
    mag = np.abs(residual)
    norm = cv2.normalize(mag, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    _, binr = cv2.threshold(norm, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 3))
    closed = cv2.morphologyEx(binr, cv2.MORPH_CLOSE, kernel)
    stroke_ratio = float((closed > 0).mean())

    # FFT diagonal-energy confirmation: a repeating diagonal pattern puts energy
    # off both the horizontal and vertical axes of the spectrum.
    diag_score = _diagonal_fft_energy(residual)

    if stroke_ratio > 0.04 and stroke_ratio < 0.5 and diag_score > 0.10:
        contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return []
        xs0 = min(int(cv2.boundingRect(c)[0]) for c in contours)
        ys0 = min(int(cv2.boundingRect(c)[1]) for c in contours)
        xs1 = max(int(cv2.boundingRect(c)[0] + cv2.boundingRect(c)[2]) for c in contours)
        ys1 = max(int(cv2.boundingRect(c)[1] + cv2.boundingRect(c)[3]) for c in contours)
        conf = round(min(1.0, 0.4 + diag_score), 3)
        # Return the morphological mask region by bbox plus a precise pixel mask
        # carried separately through build_overlay_mask.
        reg = OverlayRegion("watermark", (xs0, ys0, xs1, ys1), conf)
        reg.pixel_mask = closed  # type: ignore[attr-defined]
        return [reg]
    return []


def _diagonal_fft_energy(residual: np.ndarray) -> float:
    f = np.fft.fftshift(np.fft.fft2(residual))
    mag = np.abs(f)
    h, w = mag.shape
    cy, cx = h // 2, w // 2
    mag[cy - 2:cy + 3, :] = 0
    mag[:, cx - 2:cx + 3] = 0
    total = mag.sum()
    if total <= 0:
        return 0.0
    yy, xx = np.indices(mag.shape)
    dy = np.abs(yy - cy)
    dx = np.abs(xx - cx)
    diag = (np.abs(dy - dx) < (0.25 * np.maximum(dy, dx) + 1))
    return float(mag[diag].sum() / total)


def detect_headshot(rgb: np.ndarray) -> list[OverlayRegion]:
    """Small bordered rectangle near a corner holding a compact skin-tone region.

    Skin-tone alone is not enough (tan facade and pale sky can both pass a YCrCb
    skin test), so we additionally require: a single compact connected component
    (not skin scattered across the corner) and a sharp rectangular border around
    it. The returned bbox is the component's bounding box, not the loose extent
    of every skin pixel in the corner."""
    h, w = rgb.shape[:2]
    ycrcb = cv2.cvtColor(rgb, cv2.COLOR_RGB2YCrCb)
    cr, cb = ycrcb[..., 1], ycrcb[..., 2]
    skin = (((cr > 135) & (cr < 180) & (cb > 85) & (cb < 135)).astype(np.uint8)) * 255
    gray = _to_gray(rgb)
    regions: list[OverlayRegion] = []
    qh, qw = h // 3, w // 3
    corners = [
        (0, 0, qh, qw), (0, w - qw, qh, w),
        (h - qh, 0, h, qw), (h - qh, w - qw, h, w),
    ]
    for (y0, x0, y1, x1) in corners:
        sub = skin[y0:y1, x0:x1]
        if sub.size == 0 or sub.max() == 0:
            continue
        n, labels, stats, _ = cv2.connectedComponentsWithStats(sub, connectivity=8)
        if n <= 1:
            continue
        # largest non-background component
        areas = stats[1:, cv2.CC_STAT_AREA]
        li = 1 + int(np.argmax(areas))
        cx0, cy0 = int(stats[li, cv2.CC_STAT_LEFT]), int(stats[li, cv2.CC_STAT_TOP])
        cw, ch = int(stats[li, cv2.CC_STAT_WIDTH]), int(stats[li, cv2.CC_STAT_HEIGHT])
        carea = int(stats[li, cv2.CC_STAT_AREA])
        box_area = max(1, cw * ch)
        fill = carea / box_area
        frame_frac = box_area / (h * w)
        # a headshot fills most of its bounding box, is small, and roughly square
        if fill < 0.6 or frame_frac > 0.05 or cw < 24 or ch < 24:
            continue
        ar = cw / ch
        if ar < 0.5 or ar > 2.0:
            continue
        bx0, by0 = x0 + cx0, y0 + cy0
        bx1, by1 = bx0 + cw, by0 + ch
        # sharp border check: a frame of strong edges hugging the box
        if not _has_rect_border(gray, bx0, by0, bx1, by1):
            continue
        regions.append(OverlayRegion("headshot", (bx0, by0, bx1, by1), round(0.4 + 0.5 * fill, 3)))
    return regions


def _has_rect_border(gray: np.ndarray, x0: int, y0: int, x1: int, y1: int) -> bool:
    h, w = gray.shape
    pad = 6
    ox0, oy0 = max(0, x0 - pad), max(0, y0 - pad)
    ox1, oy1 = min(w, x1 + pad), min(h, y1 + pad)
    ring = gray[oy0:oy1, ox0:ox1]
    if ring.size == 0:
        return False
    edges = cv2.Canny(ring, 50, 150)
    border = np.zeros_like(edges)
    border[:pad, :] = edges[:pad, :]
    border[-pad:, :] = edges[-pad:, :]
    border[:, :pad] = edges[:, :pad]
    border[:, -pad:] = edges[:, -pad:]
    edge_pixels = border.shape[0] * 2 * pad + border.shape[1] * 2 * pad
    return (float((border > 0).sum()) / max(1, edge_pixels)) > 0.12


def detect_logos(rgb: np.ndarray, template_dir: Path = LOGO_TEMPLATE_DIR) -> list[OverlayRegion]:
    """Template match against per-firm logo templates. The directory ships
    empty; broker logos are added per firm as they are licensed. With no
    templates present this returns nothing."""
    regions: list[OverlayRegion] = []
    if not template_dir.exists():
        return regions
    gray = _to_gray(rgb)
    for tpl_path in sorted(template_dir.glob("*.png")):
        tpl = cv2.imread(str(tpl_path), cv2.IMREAD_GRAYSCALE)
        if tpl is None or tpl.shape[0] >= gray.shape[0] or tpl.shape[1] >= gray.shape[1]:
            continue
        res = cv2.matchTemplate(gray, tpl, cv2.TM_CCOEFF_NORMED)
        _, maxval, _, maxloc = cv2.minMaxLoc(res)
        if maxval >= 0.7:
            x0, y0 = maxloc
            regions.append(OverlayRegion(
                "logo", (x0, y0, x0 + tpl.shape[1], y0 + tpl.shape[0]), round(float(maxval), 3)))
    return regions


def detect_regions(rgb: np.ndarray) -> list[OverlayRegion]:
    regions: list[OverlayRegion] = []
    regions += detect_contact_bars(rgb)
    regions += detect_banner(rgb)
    regions += detect_diagonal_watermark(rgb)
    regions += detect_headshot(rgb)
    regions += detect_logos(rgb)
    return regions


def build_overlay_mask(rgb: np.ndarray, regions: list[OverlayRegion]) -> np.ndarray:
    """Compose a single binary mask (255 = overlay) from labeled regions. For
    watermarks the precise per-stroke pixel mask is used; for bars/banners/
    headshots/logos the bbox is filled."""
    h, w = rgb.shape[:2]
    mask = np.zeros((h, w), dtype=np.uint8)
    for reg in regions:
        pm = getattr(reg, "pixel_mask", None)
        if pm is not None and pm.shape == (h, w):
            mask = cv2.bitwise_or(mask, pm)
        else:
            x0, y0, x1, y1 = (int(v) for v in reg.bbox)
            x0, y0 = max(0, x0), max(0, y0)
            x1, y1 = min(w, x1), min(h, y1)
            mask[y0:y1, x0:x1] = 255
    return mask

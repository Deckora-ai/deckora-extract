"""Generate a deterministic synthetic OM PDF for the photo de-layering tests.

No randomness without a fixed seed. Produces:
  fixtures/synthetic_om.pdf       the OM
  fixtures/ground_truth_scene.png the clean base scene (case 1 + case 2 truth)

PDF structure:
  page 1  cover text (price, NOI, cap rate, address, tenant)
  page 2  case 1: clean scene embedded as a separate image stream, with vector
          text drawn on top (so rawextract picks method = raw_extract)
  page 3  case 2: the SAME scene flattened with a bottom contact bar, a diagonal
          semi-transparent "SAMPLE BROKERAGE" watermark, and a corner headshot
          box baked into one raster

A second helper builds an oversized-overlay variant (overlay > 15%) used by the
quality-gate rejection test.
"""
from __future__ import annotations

from pathlib import Path

import cv2
import fitz
import numpy as np

SEED = 1031
FIX_DIR = Path(__file__).resolve().parent / "fixtures"
PDF_PATH = FIX_DIR / "synthetic_om.pdf"
GT_PATH = FIX_DIR / "ground_truth_scene.png"

SCENE_W, SCENE_H = 1200, 800


def build_scene() -> np.ndarray:
    """A photo-like exterior: gradient sky, building blocks, a road, plus fine
    deterministic texture so it reads as photographic (high color entropy and
    edge density), not a flat fill."""
    rng = np.random.default_rng(SEED)
    img = np.zeros((SCENE_H, SCENE_W, 3), dtype=np.float32)

    # gradient sky: blue at top easing to pale at the horizon
    horizon = int(SCENE_H * 0.55)
    for y in range(horizon):
        t = y / horizon
        img[y, :, 0] = 120 + 110 * t   # R
        img[y, :, 1] = 160 + 80 * t    # G
        img[y, :, 2] = 220 - 20 * t    # B (RGB order)

    # ground / road
    img[horizon:, :, :] = np.array([90, 95, 88], dtype=np.float32)
    road_top = int(SCENE_H * 0.80)
    img[road_top:, :, :] = np.array([60, 60, 64], dtype=np.float32)
    # lane dashes
    for x in range(40, SCENE_W, 120):
        img[road_top + 40:road_top + 52, x:x + 60, :] = 210

    # building blocks of varied tone with window grids
    blocks = [(120, 180, 320, horizon, (150, 120, 100)),
              (360, 120, 600, horizon, (110, 130, 150)),
              (640, 220, 880, horizon, (170, 160, 120)),
              (900, 160, 1120, horizon, (130, 140, 160))]
    for x0, y0, x1, y1, col in blocks:
        img[y0:y1, x0:x1, :] = np.array(col, dtype=np.float32)
        for wy in range(y0 + 14, y1 - 10, 34):
            for wx in range(x0 + 12, x1 - 16, 38):
                img[wy:wy + 18, wx:wx + 24, :] = np.array(
                    [210, 220, 235], dtype=np.float32) - rng.uniform(0, 40)

    # photographic texture: fine per-pixel noise so entropy/edges read as a photo
    noise = rng.normal(0, 9, size=img.shape).astype(np.float32)
    img = img + noise
    return np.clip(img, 0, 255).astype(np.uint8)


def _draw_watermark(rgb: np.ndarray, text: str = "SAMPLE BROKERAGE") -> np.ndarray:
    """Low-opacity repeating diagonal text baked into the raster."""
    h, w = rgb.shape[:2]
    layer = np.zeros((h, w, 3), dtype=np.uint8)
    for row in range(-h, h, 150):
        for col in range(-w, w, 460):
            cv2.putText(layer, text, (col, row + h // 2),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.6, (255, 255, 255), 3,
                        cv2.LINE_AA)
    # rotate 30 degrees for a diagonal sweep
    M = cv2.getRotationMatrix2D((w / 2, h / 2), 30, 1.0)
    layer = cv2.warpAffine(layer, M, (w, h))
    alpha = 0.22
    out = rgb.astype(np.float32)
    mask = layer.astype(np.float32) / 255.0
    out = out * (1 - alpha * mask) + 255 * (alpha * mask)
    return np.clip(out, 0, 255).astype(np.uint8)


def build_flattened(scene: np.ndarray, big_overlay: bool = False) -> np.ndarray:
    """Scene with broker overlays baked in: bottom contact bar, diagonal
    watermark, corner headshot box. big_overlay inflates the bar so the masked
    fraction exceeds the 15% gate (for the rejection test)."""
    img = scene.copy()
    h, w = img.shape[:2]

    bar_h = int(h * 0.30) if big_overlay else int(h * 0.085)
    img[h - bar_h:, :, :] = np.array([28, 40, 70], dtype=np.uint8)
    cv2.putText(img, "Listed by Sample Brokerage  |  (555) 010-1031",
                (24, h - bar_h // 2 + 8), cv2.FONT_HERSHEY_SIMPLEX, 0.9,
                (235, 235, 235), 2, cv2.LINE_AA)

    img = _draw_watermark(img)

    # corner headshot box (top-right): a skin-tone fill with a dark border
    hs = int(min(h, w) * 0.14)
    x0, y0 = w - hs - 16, 16
    cv2.rectangle(img, (x0 - 3, y0 - 3), (x0 + hs + 3, y0 + hs + 3), (20, 20, 20), 3)
    img[y0:y0 + hs, x0:x0 + hs, :] = np.array([200, 150, 120], dtype=np.uint8)
    # a couple of darker features so the skin region is not perfectly flat
    cv2.circle(img, (x0 + hs // 3, y0 + hs // 3), 6, (60, 50, 45), -1)
    cv2.circle(img, (x0 + 2 * hs // 3, y0 + hs // 3), 6, (60, 50, 45), -1)
    return img


def _embed_image_page(doc: fitz.Document, rgb: np.ndarray, overlay_text: bool):
    """Add a page with rgb as a separate image stream; optionally draw vector
    text over it (case 1)."""
    page = doc.new_page(width=SCENE_W, height=SCENE_H)
    ok, buf = cv2.imencode(".png", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
    page.insert_image(fitz.Rect(0, 0, SCENE_W, SCENE_H), stream=buf.tobytes())
    if overlay_text:
        # vector text spans over the photo -> rawextract sees text overlap.
        for i, line in enumerate([
            "EXCLUSIVELY LISTED BY SAMPLE BROKERAGE",
            "Subject Property  |  100 Example Way",
            "Contact: agent@sample.example  (555) 010-1031",
            "Offering Price $4,250,000   Cap Rate 7.00%",
        ]):
            page.insert_text(fitz.Point(60, 120 + i * 60), line, fontsize=26,
                             color=(1, 1, 1))
    return page


def _flattened_page(doc: fitz.Document, rgb: np.ndarray):
    """Add a page that is ONE flattened raster (no separate vector text)."""
    page = doc.new_page(width=SCENE_W, height=SCENE_H)
    ok, buf = cv2.imencode(".png", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
    page.insert_image(fitz.Rect(0, 0, SCENE_W, SCENE_H), stream=buf.tobytes())
    return page


def build(big_overlay_variant: bool = False) -> Path:
    FIX_DIR.mkdir(parents=True, exist_ok=True)
    scene = build_scene()
    cv2.imwrite(str(GT_PATH), cv2.cvtColor(scene, cv2.COLOR_RGB2BGR))

    doc = fitz.open()
    # page 1: cover text
    cover = doc.new_page(width=612, height=792)
    cover_lines = [
        "OFFERING MEMORANDUM",
        "100 Example Way, Springfield IL",
        "Tenant: Sample Tenant Co",
        "Offering Price $4,250,000",
        "NOI $297,500",
        "Cap Rate 7.00%",
    ]
    for i, line in enumerate(cover_lines):
        cover.insert_text(fitz.Point(72, 100 + i * 40), line, fontsize=20)

    # page 2: case 1 (separate stream + vector overlay)
    _embed_image_page(doc, scene, overlay_text=True)

    # page 3: case 2 (flattened with overlays)
    flat = build_flattened(scene, big_overlay=False)
    _flattened_page(doc, flat)

    doc.save(str(PDF_PATH), deflate=True)
    doc.close()

    if big_overlay_variant:
        variant = FIX_DIR / "synthetic_om_big_overlay.pdf"
        d2 = fitz.open()
        d2.new_page(width=612, height=792).insert_text(
            fitz.Point(72, 100), "OFFERING MEMORANDUM", fontsize=20)
        _flattened_page(d2, build_flattened(scene, big_overlay=True))
        d2.save(str(variant), deflate=True)
        d2.close()
        return variant

    return PDF_PATH


if __name__ == "__main__":
    p = build()
    build(big_overlay_variant=True)
    print(f"wrote {p}")
    print(f"wrote {GT_PATH}")

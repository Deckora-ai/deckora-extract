"""Tests for the photo de-layering subpackage. Offline, deterministic, no API.

Style mirrors test_fields.py: plain asserts, a pytest-free runner, but
pytest-compatible. Every fixture is synthetic and generated here; no real
broker OM is read or written.

Run:  python packages/extraction/tests/test_photos.py
  or:  python -m pytest packages/extraction/tests/test_photos.py -q
"""
import json
import os
import sys
import tempfile
from pathlib import Path

import cv2
import fitz
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import make_photo_fixture as FIX  # noqa: E402
from shop1031_extract.photos import delayer_om  # noqa: E402
from shop1031_extract.photos import rawextract as RAW  # noqa: E402
from shop1031_extract.photos import overlay as OV  # noqa: E402
from shop1031_extract.photos import reconstruct as RC  # noqa: E402


def _ensure_fixtures():
    if not FIX.PDF_PATH.exists() or not FIX.GT_PATH.exists():
        FIX.build()
    variant = FIX.FIX_DIR / "synthetic_om_big_overlay.pdf"
    if not variant.exists():
        FIX.build(big_overlay_variant=True)
    return FIX.PDF_PATH, FIX.GT_PATH, variant


def _psnr(a: np.ndarray, b: np.ndarray) -> float:
    a = a.astype(np.float64)
    b = b.astype(np.float64)
    if a.shape != b.shape:
        h = min(a.shape[0], b.shape[0])
        w = min(a.shape[1], b.shape[1])
        a, b = a[:h, :w], b[:h, :w]
    mse = float(np.mean((a - b) ** 2))
    if mse <= 1e-9:
        return 99.0
    return float(10.0 * np.log10((255.0 ** 2) / mse))


# carries the measured numbers so the runner can print them
METRICS = {}


# ---- (a) runs on any OM with no flag, writes output --------------------

def test_runs_without_any_flag():
    pdf, _, _ = _ensure_fixtures()
    out = Path(tempfile.mkdtemp())
    m = delayer_om(str(pdf), out, asset_id="no-flag-test")
    assert m["asset_id"] == "no-flag-test"
    assert (out / "photos_manifest.json").exists()
    assert m["photos"], "the run must produce at least one photo entry"


def test_cli_runs_without_authorization_flag():
    pdf, _, _ = _ensure_fixtures()
    sys.path.insert(0, str(ROOT / "scripts"))
    import importlib
    cli = importlib.import_module("extract_photos")
    out = Path(tempfile.mkdtemp())
    import io
    from contextlib import redirect_stdout
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = cli.main([str(pdf), "--out", str(out)])
    assert code == 0, "the CLI must run with no authorization flag"
    manifest = json.loads(buf.getvalue())
    assert manifest["photos"]


# ---- (c) case 1 raw_extract near-exact match ---------------------------

def test_case1_raw_extract_psnr():
    pdf, gt_path, _ = _ensure_fixtures()
    out = Path(tempfile.mkdtemp())
    m = delayer_om(str(pdf), out)
    raw = [p for p in m["photos"] if p["method"] == "raw_extract"]
    assert raw, "case 1 must yield a raw_extract photo"
    gt = cv2.cvtColor(cv2.imread(str(gt_path)), cv2.COLOR_BGR2RGB)

    # The recovered base stream is bit-exact to ground truth: this is the core
    # case-1 claim (the clean photo IS its own stream, the overlay was vector).
    doc = fitz.open(str(pdf))
    stream = None
    for rec in RAW.enumerate_images(str(pdf)):
        if rec.method == "raw_extract":
            stream = RAW._decode_to_array(doc.extract_image(rec.xref)["image"])
            break
    doc.close()
    assert stream is not None
    stream_psnr = _psnr(stream, gt)
    METRICS["case1_raw_stream_psnr_db"] = round(stream_psnr, 2)
    assert stream_psnr >= 45.0, f"raw stream PSNR {stream_psnr:.1f} dB not near-exact"

    # The shipped JPEG q85 deliverable is lossy on a noise-heavy frame (q85 on
    # high-frequency noise caps around 29 dB), still far above a contaminated
    # pick (~22 dB), so the threshold separates a correct extract from a wrong
    # one with margin.
    extracted = cv2.cvtColor(cv2.imread(str(out / raw[0]["filename"])), cv2.COLOR_BGR2RGB)
    jpeg_psnr = _psnr(extracted, gt)
    METRICS["case1_jpeg_deliverable_psnr_db"] = round(jpeg_psnr, 2)
    assert jpeg_psnr >= 28.0, f"raw_extract JPEG PSNR {jpeg_psnr:.1f} dB below 28"


# ---- (d) case 2 inpainted, gates, overlay removed ----------------------

def test_case2_inpainted_overlay_removed():
    pdf, gt_path, _ = _ensure_fixtures()
    out = Path(tempfile.mkdtemp())
    m = delayer_om(str(pdf), out)
    inp = [p for p in m["photos"] if p["method"] == "inpainted"]
    assert inp, "case 2 must yield an inpainted photo"
    entry = inp[0]
    assert entry["overlay_removed_pct"] < 0.15, entry["overlay_removed_pct"]
    assert 0.0 <= entry["quality_score"] <= 1.0
    assert entry["verification_status"] == "verified"

    # masked regions no longer carry the overlay: mean abs diff to ground truth
    # inside the former overlay regions improves vs the contaminated raster.
    doc = fitz.open(str(pdf))
    flat = None
    for rec in RAW.enumerate_images(str(pdf)):
        if rec.method == "needs_overlay_pass" and rec.is_photo_candidate:
            flat = RAW._decode_to_array(doc.extract_image(rec.xref)["image"])
            break
    doc.close()
    assert flat is not None
    regions = OV.detect_regions(flat)
    mask = OV.build_overlay_mask(flat, regions)
    recon = RC.reconstruct(flat, mask)
    gt = cv2.cvtColor(cv2.imread(str(gt_path)), cv2.COLOR_BGR2RGB)
    inside = mask > 0
    contaminated = float(np.abs(flat[inside].astype(float) - gt[inside].astype(float)).mean())
    cleaned = float(np.abs(recon.image[inside].astype(float) - gt[inside].astype(float)).mean())
    METRICS["case2_overlay_removed_pct"] = round(entry["overlay_removed_pct"], 4)
    METRICS["case2_quality_score"] = entry["quality_score"]
    METRICS["case2_inside_diff_contaminated"] = round(contaminated, 2)
    METRICS["case2_inside_diff_cleaned"] = round(cleaned, 2)
    assert cleaned < contaminated, (
        f"inpaint must reduce inside-overlay diff: {cleaned:.1f} !< {contaminated:.1f}")
    kinds = {r.kind for r in regions}
    METRICS["case2_region_kinds"] = sorted(kinds)
    assert {"contact_bar", "watermark", "headshot"} & kinds, kinds


# ---- (e) manifest schema exact -----------------------------------------

def test_manifest_schema_exact():
    pdf, _, _ = _ensure_fixtures()
    out = Path(tempfile.mkdtemp())
    m = delayer_om(str(pdf), out)
    assert set(m.keys()) >= {"asset_id", "source_pdf", "photos"}
    on_disk = json.loads((out / "photos_manifest.json").read_text(encoding="utf-8"))
    assert on_disk["asset_id"] == m["asset_id"]
    for p in m["photos"]:
        assert set(p.keys()) == {
            "filename", "method", "quality_score",
            "overlay_removed_pct", "verification_status",
        }, set(p.keys())
        assert p["method"] in {"raw_extract", "inpainted", "street_view", "aerial"}
        assert p["verification_status"] in {"verified", "needs_review"}
        assert isinstance(p["quality_score"], (int, float))
        assert isinstance(p["overlay_removed_pct"], (int, float))


# ---- (f) oversized overlay routes to needs_review/fallback -------------

def test_oversized_overlay_routes_to_review():
    _, _, variant = _ensure_fixtures()
    out = Path(tempfile.mkdtemp())
    # no GOOGLE_MAPS_API_KEY / MAPBOX_TOKEN in test env -> chain ends at review
    os.environ.pop("GOOGLE_MAPS_API_KEY", None)
    os.environ.pop("MAPBOX_TOKEN", None)
    m = delayer_om(str(variant), out)
    verified = [p for p in m["photos"] if p["verification_status"] == "verified"]
    assert not verified, "oversized overlay must not pass as verified"
    failures = m["pass_failures"]
    assert any(f.get("pass") == "reconstruct" for f in failures), failures
    review = list(out.glob("*.review.json"))
    assert review, "a review-queue entry must be written when all passes fail"
    entry = json.loads(review[0].read_text(encoding="utf-8"))
    assert entry["status"] == "needs_review"
    assert entry["pass_failures"]
    METRICS["case_f_first_failure_reason"] = failures[0]["reason"]


def _run():
    fns = [(k, v) for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    passed = 0
    for name, fn in fns:
        try:
            fn()
            passed += 1
            print(f"  PASS {name}")
        except AssertionError as e:
            print(f"  FAIL {name}: {e}")
        except Exception as e:  # noqa: BLE001
            import traceback
            print(f"  ERR  {name}: {type(e).__name__}: {e}")
            traceback.print_exc()
    print(f"\n{passed}/{len(fns)} passed")
    if METRICS:
        print("metrics:", json.dumps(METRICS))
    return passed == len(fns)


if __name__ == "__main__":
    sys.exit(0 if _run() else 1)

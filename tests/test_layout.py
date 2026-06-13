"""Tests for the /v1/layout geometry endpoint. Offline, deterministic, no API.

Style mirrors test_fields.py: plain asserts, a pytest-free runner, but
pytest-compatible. The fixture is the synthetic OM produced by
make_photo_fixture.py (3 pages: text cover, image+vector-text, flattened
raster). Direct-call tests exercise extract_layout(); one HTTP-level test
boots uvicorn + a local http.server fixture host and exercises the full
auth + download path (ALLOW_INSECURE_PDF_URL=1 + a dev secret, exactly how
the repo was smoke-tested).

Run:  python tests/test_layout.py
  or: python -m pytest tests/test_layout.py -q

NOTE: fixture generation saves a PDF — run from a writable copy (/tmp), not
from a mount that rejects PyMuPDF save operations.
"""
import hashlib
import hmac
import json
import os
import re
import sys
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# Env must be set BEFORE app.main is imported (module-load-time reads).
os.environ.setdefault("EXTRACT_SHARED_SECRET", "dev-test-secret")
os.environ["ALLOW_INSECURE_PDF_URL"] = "1"

TESTS_DIR = Path(__file__).resolve().parent
ROOT = TESTS_DIR.parents[0]
sys.path.insert(0, str(ROOT))           # `app` package
sys.path.insert(0, str(ROOT / "src"))   # vendored shop1031_extract
sys.path.insert(0, str(TESTS_DIR))      # make_photo_fixture

import make_photo_fixture as FIX  # noqa: E402
from app.layout import extract_layout  # noqa: E402

HEX_RE = re.compile(r"^#[0-9a-f]{6}$")
TOL = 1.0  # pt tolerance on page-bounds checks


def _pdf() -> Path:
    if not FIX.PDF_PATH.exists():
        FIX.build()
    return FIX.PDF_PATH


def _full_layout():
    return extract_layout(_pdf(), asset_id="t-layout")


# ---- direct-call tests --------------------------------------------------

def test_page_count_and_pages():
    lay = _full_layout()
    assert lay["page_count"] == 3, lay["page_count"]
    assert len(lay["pages"]) == 3
    assert [p["number"] for p in lay["pages"]] == [1, 2, 3]
    assert lay["asset_id"] == "t-layout"
    for p in lay["pages"]:
        assert "error" not in p, p.get("error")
        assert p["width_pt"] > 0 and p["height_pt"] > 0


def test_page1_has_cover_spans():
    lay = _full_layout()
    texts = [s["text"] for s in lay["pages"][0]["spans"]]
    assert texts, "page 1 has no spans"
    assert any(("4,250,000" in t) or ("Offering Price" in t) for t in texts), texts


def test_page2_has_image_inside_page():
    lay = _full_layout()
    p2 = lay["pages"][1]
    assert len(p2["images"]) >= 1, p2
    w, h = p2["width_pt"], p2["height_pt"]
    for im in p2["images"]:
        x0, y0, x1, y1 = im["bbox"]
        assert x0 < x1 and y0 < y1, im["bbox"]
        assert -TOL <= x0 and x1 <= w + TOL, im["bbox"]
        assert -TOL <= y0 and y1 <= h + TOL, im["bbox"]
        assert im["width_px"] > 0 and im["height_px"] > 0
        assert isinstance(im["xref"], int)


def test_all_bboxes_sane_and_colors_hex():
    lay = _full_layout()
    for p in lay["pages"]:
        w, h = p["width_pt"], p["height_pt"]
        boxes = ([s["bbox"] for s in p["spans"]]
                 + [i["bbox"] for i in p["images"]]
                 + [f["bbox"] for f in p["fills"]])
        for b in boxes:
            x0, y0, x1, y1 = b
            assert x0 < x1 and y0 < y1, (p["number"], b)
            assert -TOL <= x0 and x1 <= w + TOL, (p["number"], b)
            assert -TOL <= y0 and y1 <= h + TOL, (p["number"], b)
        for s in p["spans"]:
            assert HEX_RE.match(s["color"]), s["color"]
            assert isinstance(s["bold"], bool) and isinstance(s["italic"], bool)
            assert isinstance(s["line_dir"], list) and len(s["line_dir"]) == 2
            assert s["text"] == s["text"].strip() and s["text"]
        for f in p["fills"]:
            assert HEX_RE.match(f["color"]), f["color"]


def test_max_pages_truncates_but_page_count_total():
    lay = extract_layout(_pdf(), max_pages=1)
    assert lay["page_count"] == 3
    assert len(lay["pages"]) == 1
    assert lay["pages"][0]["number"] == 1


# ---- HTTP-level test (uvicorn + local fixture server) --------------------

def test_http_roundtrip_auth():
    import requests
    import uvicorn
    from app.main import app

    _pdf()  # ensure the fixture exists before serving it

    # Fixture host on an OS-assigned port.
    handler = partial(SimpleHTTPRequestHandler, directory=str(FIX.FIX_DIR))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    fix_port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    # Service under test.
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.1)
    assert server.started, "uvicorn failed to start"
    svc_port = server.servers[0].sockets[0].getsockname()[1]

    try:
        body = json.dumps({
            "pdf_url": f"http://127.0.0.1:{fix_port}/synthetic_om.pdf",
            "asset_id": "http-test",
            "max_pages": 2,
        })
        ts = str(int(time.time()))
        secret = os.environ["EXTRACT_SHARED_SECRET"]
        sig = hmac.new(secret.encode(), f"{ts}.{body}".encode(),
                       hashlib.sha256).hexdigest()
        url = f"http://127.0.0.1:{svc_port}/v1/layout"

        r = requests.post(url, data=body, timeout=60, headers={
            "Content-Type": "application/json",
            "X-Deckora-Ts": ts, "X-Deckora-Sig": sig,
        })
        assert r.status_code == 200, (r.status_code, r.text[:300])
        out = r.json()
        assert out["page_count"] == 3
        assert len(out["pages"]) == 2          # max_pages honored
        assert out["asset_id"] == "http-test"
        assert out["pages"][0]["spans"], "no spans over HTTP"

        # Bad signature -> 401, same auth path as /v1/extract.
        r2 = requests.post(url, data=body, timeout=30, headers={
            "Content-Type": "application/json",
            "X-Deckora-Ts": ts, "X-Deckora-Sig": "0" * 64,
        })
        assert r2.status_code == 401, (r2.status_code, r2.text[:200])
    finally:
        server.should_exit = True
        httpd.shutdown()


def _run():
    fns = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        try:
            fn()
            passed += 1
            print(f"  PASS {fn.__name__}")
        except AssertionError as e:
            print(f"  FAIL {fn.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            print(f"  ERR  {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{passed}/{len(fns)} passed")
    return passed == len(fns)


if __name__ == "__main__":
    sys.exit(0 if _run() else 1)

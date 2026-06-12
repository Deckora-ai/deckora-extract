"""R2 PDF preservation (source-of-truth doctrine).

The PDF is canonical; the deal_data.json is derived. Every OM PDF entering the
pipeline is preserved and addressable by a stable URL, so re-extraction is
re-runnable anytime against the preserved source and never "converted and lost."

Content-addressed: key = sha256(pdf bytes), so the same PDF always maps to the
same URL and re-preserving is idempotent. If Cloudflare R2 is wired (wrangler on
PATH + R2_BUCKET env), the object uploads there; otherwise the canonical handle
is recorded (default) or mirrored locally (R2_MIRROR=1) under the same URL scheme.

This module is OPTIONAL and fully env-gated. Nothing here runs during core
extraction. No network call happens unless R2_BUCKET is set and wrangler is on
PATH; with no env set, preserve() records the handle and returns, fully offline.

Provenance contract: every preserved PDF yields {source_pdf_url, sha256, ...};
the extractor supplies page_number + word_bbox to complete a field's provenance.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

# Output lives beside the package data root, overridable so callers can point it
# at a build artifacts dir. Default: packages/extraction/out.
_DEFAULT_OUT = Path(__file__).resolve().parents[2] / "out"
OUT = Path(os.environ.get("SHOP1031_EXTRACT_OUT", str(_DEFAULT_OUT)))
MIRROR = OUT / "r2-store"
MANIFEST = OUT / "r2-manifest.json"

PUBLIC_BASE = os.environ.get("R2_PUBLIC_BASE", "https://oms.shop1031.com").rstrip("/")
R2_BUCKET = os.environ.get("R2_BUCKET")           # set => attempt wrangler upload
LOCAL_MIRROR = os.environ.get("R2_MIRROR") == "1"


def sha256_of(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def url_for(sha: str) -> str:
    return f"{PUBLIC_BASE}/oms/{sha}.pdf"


def _load_manifest() -> dict:
    if MANIFEST.exists():
        return json.loads(MANIFEST.read_text(encoding="utf-8"))
    return {}


def _save_manifest(m: dict):
    OUT.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(m, indent=2), encoding="utf-8")


def _r2_put(sha: str, path: str | Path) -> bool:
    if not (R2_BUCKET and shutil.which("wrangler")):
        return False
    try:
        subprocess.run(
            ["wrangler", "r2", "object", "put", f"{R2_BUCKET}/oms/{sha}.pdf",
             "--file", str(path), "--content-type", "application/pdf"],
            check=True, capture_output=True, timeout=120)
        return True
    except Exception:  # noqa: BLE001 - upload is best-effort; handle is recorded regardless
        return False


def preserve(pdf_path: str | Path) -> dict:
    """Store the PDF (idempotent) and return its stable provenance handle.

    Backends, in order of preference: R2 upload (env-gated), local mirror
    (R2_MIRROR=1), or recorded handle (default, no copy, no network).
    """
    pdf_path = Path(pdf_path)
    sha = sha256_of(pdf_path)
    manifest = _load_manifest()
    if sha in manifest:
        return manifest[sha]
    if _r2_put(sha, pdf_path):
        backend = "r2"
    elif LOCAL_MIRROR:
        MIRROR.mkdir(parents=True, exist_ok=True)
        dest = MIRROR / f"{sha}.pdf"
        if not dest.exists():
            shutil.copy2(pdf_path, dest)
        backend = "local-mirror"
    else:
        backend = "recorded"          # canonical handle recorded; bytes at original
    rec = {
        "sha256": sha,
        "url": url_for(sha),
        "backend": backend,
        "size": pdf_path.stat().st_size,
        "original_name": pdf_path.name,
        "original_path": str(pdf_path.resolve()),
    }
    manifest[sha] = rec
    _save_manifest(manifest)
    return rec


def provenance(pdf_url: str, citation: dict | None) -> dict:
    """Assemble the {source_pdf_url, page_number, word_bbox} provenance block."""
    return {
        "source_pdf_url": pdf_url,
        "page_number": (citation or {}).get("page"),
        "word_bbox": (citation or {}).get("bbox"),
    }

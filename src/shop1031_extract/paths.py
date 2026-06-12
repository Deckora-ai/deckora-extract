"""Single resolver for the known-truth OM corpus location.

The corpus moved off the old `C:/Users/matth/Deckora/Dark Shell Scoring/OMs`
path. Resolution order:

  1. SHOP1031_OM_CORPUS environment variable, if set.
  2. The current OneDrive location (default below).
  3. Error with a clear message naming both the env var and the expected path.

`extracted/` under the returned root holds the ground-truth deal folders; the
source PDFs live in sibling folders named to match each extracted folder.
"""
from __future__ import annotations

import os
from pathlib import Path

ENV_VAR = "SHOP1031_OM_CORPUS"

DEFAULT_CORPUS = Path(
    "C:/Users/matth/OneDrive - Arbor Realty Capital Advisors, Inc"
    "/Matt-Mazur-1/CLAUDE SHARE/et cetera/dark-shell-scoring/OMs"
)


def corpus_root() -> Path:
    env = os.environ.get(ENV_VAR)
    if env:
        p = Path(env)
        if p.is_dir():
            return p
        raise FileNotFoundError(
            f"{ENV_VAR} is set to {env!r} but that directory does not exist."
        )
    if DEFAULT_CORPUS.is_dir():
        return DEFAULT_CORPUS
    raise FileNotFoundError(
        "OM corpus not found. Set the "
        f"{ENV_VAR} environment variable to the OMs folder, or place the "
        f"corpus at {DEFAULT_CORPUS}. The folder must contain an extracted/ "
        "subfolder of ground-truth deal_data.json folders."
    )


def extracted_root() -> Path:
    return corpus_root() / "extracted"

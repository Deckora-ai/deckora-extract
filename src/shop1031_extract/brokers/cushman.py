"""Cushman & Wakefield template.

C&W net-lease OMs (YAFC / leased investment team) render an "INVESTMENT
SUMMARY" as clean label-line / value-line pairs, which the generic key-value
pass already handles well. This template adds C&W-specific refinements on top
of the generic result. It is filled in iteratively as corpus failures surface;
until then it returns no overrides and the generic pass stands.
"""
from __future__ import annotations

from ..model import Doc, Field


def extract(doc: Doc) -> dict[str, Field]:
    overrides: dict[str, Field] = {}
    # Refinements added here as C&W-specific failure modes are found in
    # validation. Empty dict => rely entirely on the generic extractors.
    return overrides

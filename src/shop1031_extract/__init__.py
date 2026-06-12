"""shop1031_extract: code-only OM PDF intake. No LLM API calls anywhere.

Core (offline, no credentials): intake, extract, fields, validate, verify,
contamination, schema. Optional and env-gated (never imported by the core path):
r2_preserve (PDF preservation) and api_confirm (Leg 2.5 deterministic
confirmation: geocoding / pgeocode / EDGAR, escalation routed through the
subject-corroboration guard).
"""
from . import intake, extract, fields, validate, verify, contamination  # noqa: F401

__all__ = ["intake", "extract", "fields", "validate", "verify", "contamination"]
__version__ = "0.2.0"

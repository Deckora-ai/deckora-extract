"""Cross-field arithmetic validation. Catches bad pulls and downgrades them.

Mirrors the validation.warnings block in the corpus extraction_report.json:
cap == NOI/price, lease dates ordered, lot/building ratio sane, etc.
"""
from __future__ import annotations

from .model import Field, LOW, MEDIUM


def check(fields: dict[str, Field]) -> list[dict]:
    warnings: list[dict] = []

    def g(name):
        f = fields.get(name)
        return f.value if f and f.found else None

    price = g("price")
    noi = g("noi")
    cap = g("capRate")
    bsf = g("buildingSf")
    rent = g("currentRent")

    # cap == NOI / price
    if price and noi and cap:
        implied = noi / price
        if abs(implied - cap) > 0.005:  # >50bp off
            warnings.append({"check": "CAP_RATE_INTEGRITY", "status": "warn",
                             "detail": f"stated cap {cap:.4f} vs NOI/price {implied:.4f}"})
            for n in ("capRate", "noi", "price"):
                if fields.get(n) and fields[n].confidence not in (LOW,):
                    fields[n].confidence = MEDIUM
        else:
            warnings.append({"check": "CAP_RATE_INTEGRITY", "status": "ok",
                             "detail": f"cap {cap:.4f} ~= NOI/price {implied:.4f}"})

    # NOI vs rent (STNL: often equal)
    if noi and rent and abs(noi - rent) / max(noi, rent) > 0.5:
        warnings.append({"check": "RENT_NOI_DELTA", "status": "info",
                         "detail": f"rent {rent} vs NOI {noi} differ >50%"})

    # lease dates ordered
    c = g("commenced")
    e = g("expires")
    if c and e and c >= e:
        warnings.append({"check": "LEASE_DATES", "status": "warn",
                         "detail": f"commenced {c} not before expires {e}"})
        for n in ("commenced", "expires"):
            if fields.get(n):
                fields[n].confidence = LOW

    # lot/building ratio sane (2-60x typical for STNL pads)
    lot_ac = g("lotAcres")
    if bsf and lot_ac:
        lot_sf = lot_ac * 43560
        ratio = lot_sf / bsf
        if not (1.2 <= ratio <= 80):
            warnings.append({"check": "LOT_RATIO_OUTLIER", "status": "warn",
                             "detail": f"lot/building ratio {ratio:.1f} out of band"})

    # price sanity
    if price and bsf:
        psf = price / bsf
        if not (20 <= psf <= 20000):
            warnings.append({"check": "PRICE_PSF_OUTLIER", "status": "warn",
                             "detail": f"price/SF {psf:.0f} out of band"})

    return warnings

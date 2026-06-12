"""Broker detection + template registry.

A "template" is a function (Doc) -> dict[str, Field] that overrides generic
extraction for a known firm's layout. Detection is text-fingerprint based.
"""
from __future__ import annotations

from ..model import Doc

# substring (lowercase) -> canonical firm name
FINGERPRINTS = {
    "cushman": "Cushman & Wakefield",
    "cushwake": "Cushman & Wakefield",
    "cbre": "CBRE",
    "colliers": "Colliers",
    "jll": "JLL",
    "newmark": "Newmark",
    "marcus & millichap": "Marcus & Millichap",
    "marcus and millichap": "Marcus & Millichap",
    "stan johnson": "Stan Johnson Company",
    "northmarq": "NorthMarq",
    "hanley": "Hanley Investment Group",
    "faris lee": "Faris Lee Investments",
    "lee & associates": "Lee & Associates",
    "sands investment": "Sands Investment Group",
    "sab capital": "SAB Capital",
    "matthews": "Matthews Real Estate",
    "avison young": "Avison Young",
    "kidder": "Kidder Mathews",
    "tscg": "TSCG",
    "encore": "Encore Real Estate",
    "b+e": "B+E",
    "the boulder group": "The Boulder Group",
    "jet industrial": "JET Industrial",
}


# email domain (lowercased, after @) -> canonical firm
EMAIL_DOMAINS = {
    "cushwake.com": "Cushman & Wakefield",
    "cbre.com": "CBRE",
    "colliers.com": "Colliers",
    "jll.com": "JLL",
    "am.jll.com": "JLL",
    "nmrk.com": "Newmark",
    "ngkf.com": "Newmark",
    "marcusmillichap.com": "Marcus & Millichap",
    "stanjohnson.com": "Stan Johnson Company",
    "northmarq.com": "NorthMarq",
    "hanleyinvestment.com": "Hanley Investment Group",
    "farislee.com": "Faris Lee Investments",
    "lee-associates.com": "Lee & Associates",
    "srsre.com": "SRS Real Estate Partners",
    "matthews.com": "Matthews Real Estate",
    "avisonyoung.com": "Avison Young",
    "kidder.com": "Kidder Mathews",
    "tscg.com": "TSCG",
    "encorereip.com": "Encore Real Estate",
    "bpluse.com": "B+E",
    "boultongroup.com": "The Boulder Group",
    "bouldergroup.com": "The Boulder Group",
    "sigsor.com": "Sands Investment Group",
    "sabcap.com": "SAB Capital",
    # boutique / regional net-lease brokers (cores chosen to match OM bylines)
    "blacktreeinc.com": "Blacktree",
    "surmount.com": "SURMOUNT",
    "bangrealty.com": "Bang Realty",
    "brockman.group": "Brockman Group",
    "crownpoint.co": "Crown Point",
    "parasellinc.com": "Parasell",
    "securenetlease.com": "Secure Net Lease",
    "svn.com": "SVN",
    "txinvestco.com": "Texas Invest",
    "nnnpro.com": "NNN Pro",
    "draketexas.com": "Drake",
    "sperrycga.com": "Sperry",
    "steveeustisrealestate.com": "Steve Eustis",
    "wedgewoodcp.com": "Wedgewood",
}


def _domain_detect(text: str) -> str | None:
    import re
    for m in re.finditer(r"@([A-Za-z0-9.\-]+\.[A-Za-z]{2,})", text):
        dom = m.group(1).lower()
        if dom in EMAIL_DOMAINS:
            return EMAIL_DOMAINS[dom]
    return None


def detect(doc: Doc) -> str | None:
    """Return canonical firm name from the first few pages, or None."""
    head = " ".join(pg.text for pg in doc.pages[:4]).lower()
    for sign, firm in FINGERPRINTS.items():
        if sign in head:
            return firm
    # email domains (very reliable: brokers list contact emails)
    d = _domain_detect(doc.full_text)
    if d:
        return d
    # broader fingerprint pass over whole doc
    low = doc.full_text.lower()
    for sign, firm in FINGERPRINTS.items():
        if sign in low:
            return firm
    return None


def template_for(firm: str | None):
    """Return a template callable for the firm, or None for generic-only."""
    if not firm:
        return None
    from . import cushman
    table = {
        "Cushman & Wakefield": cushman.extract,
    }
    return table.get(firm)

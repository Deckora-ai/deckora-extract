# VERIFICATION

Every high-stakes field ships with verification baked in. A value is produced by
more than one independent path through the source document, the paths are
reconciled, and the field carries a HIGH / MEDIUM / LOW grade plus its full path
list. The stack never asserts accuracy; it records how a value was reached and how
strongly the document corroborates it, and the user and broker verify before
acting. No API calls run in this layer.

## Multi-path grading (verify.py)

For each high-stakes field, several independent gatherers each propose a candidate
value. For example, cap rate is gathered from the labeled value, from the page-1
headline, and recomputed from NOI / price. The candidates are clustered by an
equality function and graded by how many independent paths agree:

- **HIGH**: two or more independent paths agree.
- **MEDIUM**: exactly one path produced a value (no corroborating path found).
- **LOW**: multiple paths produced values but they disagree. The field is flagged
  `review_required` and routes to the human review queue; downstream surfaces
  render it as unverified, and no LOI ships against an unreviewed record.

Each grade surfaces as a provenance badge on the deliverable, and the full `paths`
list (every candidate value, its page, and snippet) ships with the field so a
reviewer can audit how it was reached.

## The subject-corroboration guard (contamination.py)

A value may only be promoted toward HIGH when the corroborating evidence is
confirmed to belong to the SUBJECT property, not to a broker contact block. The
corroborating signal is the source filename and the cover-title region: the broker
file is reliably named for the subject, and the subject city and state appear
there. Letter-spacing on covers is stripped first, so a letter-spaced
`C i n c i n n a t i` still matches. A broker city does not appear in the OM title,
so it cannot corroborate, and a multi-property portfolio cover (four or more
distinct City, ST pairs) corroborates several cities at once and therefore
corroborates none of them as THE subject; that OM routes to review.

## Broker-block contamination detector (the canonical near-miss class)

Brokers stamp their own contact panel on covers and footers: names, phone, email,
brokerage street address, and a license line. That street address and zip parse
cleanly and can out-vote the subject property's own address, which is often
letter-spaced or image-only on the cover. The detector finds broker blocks by
their license lines, contact-panel headers (for example "exclusively listed by"),
and phone-plus-email contact bars, then rejects any subject-field value sourced
from inside a broker block.

The canonical near-miss this stops: an OM whose broker block reads
"5831 Lancefield Drive, Huntington Beach, CA 92649 / CRE Lic." would otherwise
promote Huntington Beach, CA to HIGH over the subject property's actual city. The
offline zip self-confirm (a broker zip resolving cleanly to the broker city) is the
same class of error and is blocked by the same corroboration requirement.

## Optional Leg 2.5 deterministic confirmation (api_confirm.py)

Leg 2.5 grows the HIGH lane with deterministic confirmations, never with a model.
It runs Google Geocoding (env-gated by `GOOGLE_MAPS_API_KEY`), offline pgeocode
zip lookup, SEC EDGAR issuer matching, and derived arithmetic (price-per-SF
sanity, cross-section checks). It is OPTIONAL and env-gated; `extract.run` does not
import it, so the core path runs offline with no credentials.

Every escalation to HIGH for a location field routes back through the
subject-corroboration guard. The confirmed value must be corroborated as belonging
to the subject before it can move to HIGH, so a broker office address that geocodes
or zip-resolves cleanly can never self-promote a wrong city or state to HIGH.
Disagreement only ever moves an item toward review:

- HIGH + confirm agrees + subject-corroborated -> stays HIGH
- HIGH + confirm disagrees -> MEDIUM, conflict logged, flagged for review
- MEDIUM + confirm agrees + subject-corroborated -> HIGH (the volume win)
- MEDIUM + confirm cannot verify or is not corroborated -> stays MEDIUM (review)

Leg 2.5 never overwrites an extracted value and never fabricates one; it only
adjusts confidence and attaches review suggestions.

## Measured numbers

These are measured on the internal 61-OM known-truth corpus, not asserted. They
are produced by `score_corpus.py` against ground-truth `deal_data.json` files:

- Overall document-extractable accuracy: 85.8 percent.
- HIGH precision (share of HIGH-graded values that are correct): 97.0 percent.
- The invariant goal is 100 percent HIGH precision on the contamination classes
  (broker-block and portfolio): a HIGH that is wrong on those classes is the worst
  regression and the gate the guard exists to protect. `score_corpus.py` prints
  any wrong-HIGH per run.

A partner engineer without that labeled corpus cannot reproduce the accuracy
numbers, but can read the verification grades the pipeline assigns over their own
extractions with `scripts/verify_corpus.py` (HIGH / MEDIUM / LOW counts; see its
`--help`).

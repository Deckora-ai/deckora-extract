#!/usr/bin/env python3
"""CLI: report multi-path verification grades over a folder of extractions.

What this needs, and what it does NOT need
------------------------------------------
A true accuracy checker (the internal score_corpus.py) needs a known-truth
corpus: a folder of human-labeled ground-truth deal_data.json files plus the
matching source PDFs, so each extracted value can be compared to truth and HIGH
precision can be measured. A partner engineer does not have that labeled corpus,
so this wrapper does not attempt to measure accuracy.

Instead it does the part that needs no ground truth: it reads the per-field
verification grades that the pipeline already wrote into each deal_data.json
(the `_meta` confidence block and the `verification.flagged_fields` list) and
reports HIGH / MEDIUM / LOW / flagged counts across the folder. Run extract_batch.py
first to produce the deal_data.json files, then point this at that output folder.

To run the full known-truth checker instead, set SHOP1031_OM_CORPUS to a folder
that holds an extracted/ subfolder of ground-truth deal_data.json folders with
matching source PDFs in sibling folders, then run the internal score_corpus.py.
This wrapper prints that pointer when --corpus is requested but no corpus is set.

Usage:
  python scripts/verify_corpus.py <extractions_dir>
  python scripts/verify_corpus.py --corpus

<extractions_dir> is the --out folder produced by extract_batch.py (one
subfolder per OM, each holding deal_data.json). HIGH/MEDIUM/LOW are read from
the deal_data.json the pipeline already wrote; nothing is re-extracted.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

CORPUS_ENV = "SHOP1031_OM_CORPUS"


def _iter_deal_data(root: Path):
    for p in sorted(root.rglob("deal_data.json")):
        try:
            yield p, json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue


def report_extractions(root: Path) -> int:
    if not root.is_dir():
        print(f"not a directory: {root}", file=sys.stderr)
        return 2

    per_grade = defaultdict(int)
    per_field = defaultdict(lambda: defaultdict(int))
    flagged_oms = []
    n = 0

    for path, deal in _iter_deal_data(root):
        n += 1
        meta = deal.get("_meta", {})
        for field, m in meta.items():
            grade = (m.get("confidence") or "unknown").lower()
            per_grade[grade] += 1
            per_field[field][grade] += 1
        flagged = deal.get("verification", {}).get("flagged_fields", [])
        if flagged:
            flagged_oms.append((path.parent.name, flagged))

    if n == 0:
        print(f"no deal_data.json found under {root}", file=sys.stderr)
        print("run scripts/extract_batch.py first to produce extractions.",
              file=sys.stderr)
        return 1

    total = sum(per_grade.values()) or 1
    print("=" * 60)
    print(f"VERIFICATION GRADES over {n} extraction(s) in {root}")
    print("=" * 60)
    for grade in ("high", "medium", "low", "unknown"):
        c = per_grade.get(grade, 0)
        print(f"  {grade.upper():<10} {c:>4}  {c/total*100:5.1f}%")
    print(f"  {'TOTAL':<10} {total:>4}")

    print("\nPER FIELD (high / medium / low):")
    for field in sorted(per_field):
        g = per_field[field]
        print(f"  {field:<14} {g.get('high',0):>3} / {g.get('medium',0):>3} / {g.get('low',0):>3}")

    print(f"\nFLAGGED-FOR-REVIEW OMs: {len(flagged_oms)}")
    for name, flagged in flagged_oms[:20]:
        print(f"  {name[:44]:<46} {', '.join(flagged)}")

    print("\nNote: these are verification grades the pipeline assigned, not an")
    print("accuracy measurement. Accuracy requires a known-truth corpus; see")
    print("--corpus and the module docstring.")
    return 0


def corpus_pointer() -> int:
    env = os.environ.get(CORPUS_ENV)
    if env:
        print(f"{CORPUS_ENV} is set to: {env}")
        print("Point the internal score_corpus.py at it to run the full")
        print("known-truth accuracy checker (needs labeled ground truth).")
        return 0
    print(f"{CORPUS_ENV} is not set.")
    print("To run the full accuracy checker you need a labeled known-truth corpus:")
    print("  - a folder with an extracted/ subfolder of ground-truth deal_data.json")
    print("    folders, and the matching source PDFs in sibling folders.")
    print(f"  - set {CORPUS_ENV} to that folder.")
    print("Without labeled ground truth, run this script against an extractions")
    print("folder instead to get HIGH/MEDIUM/LOW verification counts.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Report multi-path verification grades over a folder of "
                    "already-extracted deal_data.json files. Does not need ground truth.")
    ap.add_argument("extractions_dir", nargs="?",
                    help="folder of extractions (extract_batch.py --out)")
    ap.add_argument("--corpus", action="store_true",
                    help=f"print how to wire a known-truth corpus via {CORPUS_ENV}")
    args = ap.parse_args(argv)

    if args.corpus:
        return corpus_pointer()
    if not args.extractions_dir:
        ap.print_help()
        return 1
    return report_extractions(Path(args.extractions_dir))


if __name__ == "__main__":
    raise SystemExit(main())

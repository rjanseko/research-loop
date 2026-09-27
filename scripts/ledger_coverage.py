"""Count how much of a case's expected set a run's research found, without a judge or a model call.

    .venv/bin/python scripts/ledger_coverage.py runs/set-screen-*/run.json

The counting is `research_loop.coverage`; `research study run` reports it too.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from research_loop.coverage import coverage


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("runs", nargs="+", type=Path, help="run.json files written with --out")
    args = parser.parse_args()
    for path in args.runs:
        try:
            row = coverage(json.loads(path.read_text()))
        except ValueError as exc:
            print(f"{path}: {exc}", file=sys.stderr)
            continue
        print(f"{path.parent.name}: {row['case']} {str(row['run_id'])[:8]} found {len(row['found'])}/{row['expected']}"
              f", named only {len(row['named_only'])}; found {', '.join(row['found']) or '-'}"
              f"; named only {', '.join(row['named_only']) or '-'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Count how much of a case's expected set a run's research found, without a judge or a model call.

    .venv/bin/python scripts/ledger_coverage.py runs/set-screen-*/run.json

Each development case lists the members its rubric expects, such as the databases drb2-task8 asks for.
A member counts as found when a claim or a conclusion names it, and as named only when it appears
nowhere but in `unresolved`: the research saw it but did not establish it. Both are free, fast signals for
screening a change before paying for synthesis and grading; neither replaces the rubric grade.

Held-out cases have no expected sets here on purpose: tuning against them would spend their value.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# Members each development case's rubric expects, as patterns over lowercased text.
EXPECTED: dict[str, dict[str, str]] = {
    "drb2-task8": {
        "Materials Project": r"materials ?project", "OQMD": r"oqmd|open quantum materials",
        "NOMAD": r"nomad", "ICSD": r"icsd|inorganic crystal structure",
        "CSD": r"\bcsd\b|cambridge structural", "ASM Alloy Center": r"asm (alloy|international)|alloy center",
        "DDSE": r"ddse|solid[- ]state electrolyte",
    },
    "drb2-task68-plus": {
        "threshold rules": r"threshold", "queuing theory": r"queu", "reinforcement learning": r"reinforcement",
        "fuzzy logic": r"fuzzy", "machine learning": r"machine learning|neural", "time series": r"time[- ]series|arima",
    },
}


def coverage(record: dict) -> dict:
    """The run's case, and which expected members its research found or only named."""
    case_id = ((record.get("config") or {}).get("case") or {}).get("id")
    members = EXPECTED.get(case_id or "")
    if members is None:
        raise ValueError(f"no expected set for case {case_id!r}")
    results = [result for results in (record.get("ledger") or {}).values() for result in results]
    established = " ".join([result.get("conclusion", "") for result in results]
                           + [claim.get("statement", "") for result in results for claim in result.get("claims", [])])
    unresolved = " ".join(item for result in results for item in result.get("unresolved", []))
    found = [name for name, pattern in members.items() if re.search(pattern, established.lower())]
    named = [name for name, pattern in members.items()
             if name not in found and re.search(pattern, unresolved.lower())]
    return {"case": case_id, "run_id": record.get("run_id"), "found": found, "named_only": named,
            "missing": [name for name in members if name not in found and name not in named],
            "expected": len(members)}


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

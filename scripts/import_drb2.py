"""Freeze DeepResearch Bench II tasks as study cases, from the pinned snapshot.

    .venv/bin/python scripts/import_drb2.py task82 task59 --role held-out

It downloads the pinned file (or reads --file), refuses it unless its SHA-256 matches, and appends one
case per task to src/research_loop/study_cases.jsonl. It never replaces a case that is already frozen.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request
from pathlib import Path

from research_loop.evals import DRB2_SHA256, DRB2_URL, drb2_case

CASES = Path(__file__).resolve().parents[1] / "src" / "research_loop" / "study_cases.jsonl"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("tasks", nargs="+", help="Official task IDs, such as task82 or task68+")
    parser.add_argument("--role", help="Metadata role for the new cases, such as held-out")
    parser.add_argument("--file", type=Path, help="A local copy of the pinned tasks_and_rubrics.jsonl")
    args = parser.parse_args()

    data = args.file.read_bytes() if args.file else urllib.request.urlopen(DRB2_URL, timeout=60).read()
    if hashlib.sha256(data).hexdigest() != DRB2_SHA256:
        print("The tasks file does not match the pinned DeepResearch Bench II snapshot.", file=sys.stderr)
        return 1
    rows = {row["id"]: row for row in map(json.loads, data.decode().splitlines())}
    frozen = {json.loads(line)["id"] for line in CASES.read_text(encoding="utf-8").splitlines() if line.strip()}
    missing = [task for task in args.tasks if task not in rows]
    if missing:
        print(f"Not in the snapshot: {', '.join(missing)}", file=sys.stderr)
        return 1
    cases = [drb2_case(rows[task], args.role) for task in args.tasks]
    if taken := [case.id for case in cases if case.id in frozen]:
        print(f"Already frozen, not replaced: {', '.join(taken)}", file=sys.stderr)
        return 1
    with CASES.open("a", encoding="utf-8") as out:
        for case in cases:
            out.write(json.dumps(case.model_dump(exclude_defaults=True), ensure_ascii=False) + "\n")
            print(f"Froze {case.id}: {sum(map(len, case.rubrics.values()))} rubric points, "
                  f"{len(case.blocked_urls)} blocked URLs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

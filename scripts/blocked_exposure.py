"""List what stored runs' research tools showed that the current source policy blocks, without a model call.

    .venv/bin/python scripts/blocked_exposure.py --study serper-rescout-task8
    .venv/bin/python scripts/blocked_exposure.py 710c5d4e-fba5-414e-92f6-85cf647d6c65

Each run is replayed with its own blocked addresses and its frozen case's blocked title
(`research_loop.tools.blocked_shown`). Run it on a study of a case with blocked sources before reading the
study's results: fetch versions 15 to 18 let drb2-task8's blocked review reach scouts under shortened titles
(docs/study-log.md, 29 September 2026). It reads Postgres (`DATABASE_URL`) and nothing else.
"""
from __future__ import annotations

import argparse
from collections import Counter

import psycopg
from pydantic_ai.messages import ModelMessagesTypeAdapter

from research_loop.acquisition import SourcePolicy
from research_loop.config import Settings
from research_loop.evals import case_blocked_titles
from research_loop.tools import blocked_shown


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run_ids", nargs="*", help="run IDs, whole")
    parser.add_argument("--study", action="append", default=[], help="every run of this study label")
    parser.add_argument("--all", action="store_true", help="list every blocked item, not only the most frequent")
    args = parser.parse_args()
    if not args.run_ids and not args.study:
        parser.error("name run IDs or --study")
    exposed = 0
    with psycopg.connect(Settings().database_dsn) as conn:
        rows = conn.execute(
            "select id, study_id, arm, config from runs where id::text = any(%s) or study_id = any(%s) "
            "order by started_at", (args.run_ids, args.study)).fetchall()
        for run_id, study, arm, config in rows:
            config = config or {}
            policy = SourcePolicy(tuple(config.get("blocked_urls") or []),
                                  titles=tuple(config.get("blocked_titles") or case_blocked_titles(config.get("case"))))
            shown: list[str] = []
            for (messages,) in conn.execute("select messages from run_calls where run_id = %s and messages is not null "
                                            "order by started_at", (run_id,)):
                shown += blocked_shown(ModelMessagesTypeAdapter.validate_python(messages), policy)
            exposed += bool(shown)
            label = " ".join(part for part in (study, arm) if part)
            print(f"{str(run_id)[:8]} {label}: {len(shown)} blocked item(s) shown, fetch version "
                  f"{config.get('fetch_version', '?')}")
            for item, count in Counter(shown).most_common(None if args.all else 5):
                print(f"    {count}x {item[:140]}")
    print(f"{exposed} of {len(rows)} run(s) were shown something the current policy blocks.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

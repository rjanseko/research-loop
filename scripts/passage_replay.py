"""Replay stored fetch results through the passage splitter, without a model call.

    .venv/bin/python scripts/passage_replay.py [--limit 2000] [--show pypdf --samples 3]

For every distinct page window scouts read (run_calls messages in Postgres, `DATABASE_URL`), it splits the text
with `research_loop.passages.split` and checks the splitter's invariants: passages in order, inside the text,
without overlap, trimmed, covering every non-whitespace character once, and within their size limits. It reports,
by extraction method, how many passages a window makes and how long they are, so the size limits can be tuned on
real text (docs/passage-evidence-plan.md, build step 1). It also estimates what storing each run's full extracted
texts would take, from the `total_chars` its fetches report. `--show` prints passages of a few windows extracted
one way, for reading by eye. It reads the database and prints; it writes nothing.
"""
from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from typing import Any

import psycopg

from research_loop.config import Settings
from research_loop.passages import MIN_CHARS, TABLE_CHARS, TARGET_CHARS, display, split


def problems(text: str, passages: list[Any]) -> list[str]:
    """The splitter invariants `passages` of `text` break."""
    found, position = [], 0
    covered = bytearray(len(text))
    for passage in passages:
        if not (position <= passage.start < passage.end <= len(text)):
            found.append(f"passage {passage.ordinal} at {passage.start}-{passage.end} is out of order or bounds")
            continue
        body = text[passage.start:passage.end]
        if body != body.strip():
            found.append(f"passage {passage.ordinal} is not trimmed")
        limit = TABLE_CHARS if passage.kind == "table" else TARGET_CHARS + MIN_CHARS
        if passage.end - passage.start > limit and " " in body:
            found.append(f"passage {passage.ordinal} is {passage.end - passage.start} characters")
        covered[passage.start:passage.end] = b"\x01" * (passage.end - passage.start)
        position = passage.end
    if any(not covered[i] and not char.isspace() for i, char in enumerate(text)):
        found.append("a non-whitespace character is in no passage")
    return found


def windows(limit: int) -> tuple[list[dict[str, Any]], dict[str, dict[str, int]]]:
    """Distinct fetched windows, and each run's documents with the length their fetches reported."""
    seen, found = set(), []
    documents: dict[str, dict[str, int]] = defaultdict(dict)
    with psycopg.connect(Settings().database_dsn) as conn:
        rows = conn.execute("select run_id, messages from run_calls where role in ('scout', 'deep_dive') "
                            "and messages is not null order by started_at desc limit %s", (limit,))
        for run_id, messages in rows:
            for message in messages or []:
                for part in message.get("parts", []):
                    if part.get("part_kind") != "tool-return" or part.get("tool_name") != "fetch":
                        continue
                    data = part.get("content")
                    if isinstance(data, str):
                        try:
                            data = json.loads(data)
                        except ValueError:
                            continue
                    if not isinstance(data, dict) or not data.get("text"):
                        continue
                    url = str(data.get("url"))
                    documents[str(run_id)][url] = max(documents[str(run_id)].get(url, 0),
                                                      int(data.get("total_chars") or len(data["text"])))
                    if (url, data.get("start", 0)) not in seen:
                        seen.add((url, data.get("start", 0)))
                        found.append(data)
    return found, documents


def _percentiles(values: list[int]) -> str:
    values = sorted(values)
    return " / ".join(str(values[int(len(values) * q)]) for q in (0.1, 0.5, 0.9)) if values else "-"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--limit", type=int, default=2000, help="scout calls to read, newest first")
    parser.add_argument("--show", help="print passages of windows extracted this way, such as pypdf")
    parser.add_argument("--samples", type=int, default=2)
    args = parser.parse_args()
    found, documents = windows(args.limit)
    by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for data in found:
        by[str(data.get("extraction") or "unknown")].append(data)
    print("| Extraction | Windows | Passages per 10k chars | Length p10 / p50 / p90 | Under MIN | Tables | "
          "Invariant failures |")
    print("|---|---:|---:|---|---:|---:|---:|")
    failures: list[tuple[str, str]] = []
    for extraction, datas in sorted(by.items(), key=lambda item: -len(item[1])):
        lengths, chars, count, short, tables = [], 0, 0, 0, 0
        for data in datas:
            text = data["text"]
            passages = split(text, extraction)
            chars += len(text)
            count += len(passages)
            lengths += [p.end - p.start for p in passages]
            short += sum(p.end - p.start < MIN_CHARS for p in passages)
            tables += sum(p.kind == "table" for p in passages)
            failures += [(str(data.get("url")), problem) for problem in problems(text, passages)]
        print(f"| {extraction} | {len(datas)} | {count / max(chars, 1) * 10_000:.1f} | {_percentiles(lengths)} | "
              f"{short / max(count, 1):.0%} | {tables} | "
              f"{sum(1 for url, _ in failures if any(d.get('url') == url for d in datas))} |")
    for url, problem in failures[:20]:
        print(f"- {url}: {problem}")
    per_run = [sum(lengths.values()) for lengths in documents.values() if lengths]
    if per_run:
        print(f"\nFull extracted text per run, from reported lengths: median {statistics.median(per_run) / 1e6:.2f} MB, "
              f"p90 {sorted(per_run)[int(len(per_run) * 0.9)] / 1e6:.2f} MB, max {max(per_run) / 1e6:.2f} MB "
              f"over {len(per_run)} runs; {sum(per_run) / 1e6:.0f} MB in all before deduplication and compression.")
    if args.show:
        for data in by.get(args.show, [])[:args.samples]:
            text = data["text"]
            print(f"\n## {data.get('url')}")
            for passage in split(text, args.show):
                print(f"\n[{passage.ordinal} {passage.kind} {passage.end - passage.start}] {display(text, passage)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

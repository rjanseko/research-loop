"""Replay stored scout searches through each web search engine, without a model call.

    .venv/bin/python scripts/search_replay.py [--sample 40] [--engines duckduckgo,serper,brave] [--max-usd 0.25]

It samples distinct queries that production scouts sent to DuckDuckGo, half from searches that found nothing
or failed and half from searches that found results, and sends each query to every engine now, through the
same `WebSearch` a run uses, so a result counts as found, empty, or failed by a run's own rules. DuckDuckGo
is searched again because its failures come and go: the stored outcome alone would credit a paid engine
with queries DuckDuckGo answers on a second try. This measures search availability on paired queries; it
says nothing about which engine's results make better reports.

The paid engines' list prices (web.py) bound the spend before any search; the script refuses a sample
whose worst case exceeds --max-usd. Per-query results go to benchmark_outputs/search-replay/, since the
queries come from benchmark cases; only counts are printed.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
import time
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import psycopg

from research_loop.config import Settings
from research_loop.reading import ExternalSpend
from research_loop.web import (
    BRAVE_SEARCH_USD,
    SERPER_SEARCH_USD,
    WebSearch,
    brave_engine,
    search_cache_key,
    serper_engine,
)

OUT = Path("benchmark_outputs/search-replay")
PRICES = {"duckduckgo": Decimal(0), "serper": SERPER_SEARCH_USD, "brave": BRAVE_SEARCH_USD}


def outcome(content: dict[str, Any]) -> str:
    """found, empty, or failed, as the study summary counts a web search (study.tool_counts)."""
    return "found" if content.get("results") else "failed" if content.get("error") else "empty"


def stored_searches(dsn: str) -> dict[str, str]:
    """Each distinct query production scouts sent to DuckDuckGo alone, with its first stored outcome."""
    rows = psycopg.connect(dsn).execute(
        "select c.messages from run_calls c join runs r on r.id = c.run_id "
        "where r.config->>'search_engine' = 'duckduckgo' and r.config->'models'->>'scout' not like '%fake%' "
        "order by c.started_at").fetchall()
    searches: dict[str, str] = {}
    seen: set[str] = set()
    for (messages,) in rows:
        queries: dict[str, str] = {}
        for message in messages or []:
            for part in message.get("parts") or []:
                if part.get("tool_name") != "web_search":
                    continue
                if part.get("part_kind") == "tool-call":
                    args = part.get("args")
                    args = json.loads(args) if isinstance(args, str) else args or {}
                    if args.get("query"):
                        queries[part.get("tool_call_id")] = str(args["query"])
                elif part.get("part_kind") == "tool-return" and isinstance(part.get("content"), dict):
                    query = queries.get(part.get("tool_call_id"))
                    if query and search_cache_key(query) not in seen:
                        seen.add(search_cache_key(query))
                        searches[query] = outcome(part["content"])
    return searches


def sample(searches: dict[str, str], size: int, seed: int) -> list[tuple[str, str]]:
    """Half missed (empty or failed) and half found, so a paid engine is tried on both."""
    rng = random.Random(seed)
    missed = sorted(q for q, o in searches.items() if o != "found")
    found = sorted(q for q, o in searches.items() if o == "found")
    picked = rng.sample(missed, min(len(missed), size // 2))
    picked += rng.sample(found, min(len(found), size - len(picked)))
    return [(query, searches[query]) for query in picked]


async def replay(queries: list[tuple[str, str]], engines: list[str], settings: Settings) -> list[dict[str, Any]]:
    spend = ExternalSpend()
    records = []
    async with httpx.AsyncClient() as client:
        searchers = {"duckduckgo": WebSearch()}
        if "serper" in engines:
            key = settings.serper_api_key.get_secret_value()  # type: ignore[union-attr]
            searchers["serper"] = WebSearch(engine=serper_engine(client, key, spend), name="serper")
        if "brave" in engines:
            key = settings.brave_api_key.get_secret_value()  # type: ignore[union-attr]
            searchers["brave"] = WebSearch(engine=brave_engine(client, key, spend), name="brave")
        for index, (query, stored) in enumerate(queries, 1):
            record: dict[str, Any] = {"query": query, "stored": stored}
            for name in engines:
                started = time.monotonic()
                content = await searchers[name].search(query)
                record[name] = {"outcome": outcome(content), "results": len(content.get("results") or []),
                                "error": content.get("error"), "seconds": round(time.monotonic() - started, 2),
                                "domains": [urlparse(r["url"]).netloc for r in content.get("results") or []][:10]}
            records.append(record)
            print(f"\r{index}/{len(queries)} queries, ${spend.usd:.3f} spent", end="", file=sys.stderr)
    print(file=sys.stderr)
    return records


def table(records: list[dict[str, Any]], engines: list[str]) -> str:
    lines = ["| Stored DuckDuckGo outcome | Queries | " + " | ".join(f"{e} found / empty / failed" for e in engines)
             + " |", "|---|---|" + "---|" * len(engines)]
    for stratum, keep in (("missed", lambda o: o != "found"), ("found", lambda o: o == "found")):
        group = [r for r in records if keep(r["stored"])]
        cells = []
        for engine in engines:
            counts = Counter(r[engine]["outcome"] for r in group)
            cells.append(f"{counts['found']} / {counts['empty']} / {counts['failed']}")
        lines.append(f"| {stratum} | {len(group)} | " + " | ".join(cells) + " |")
    lines += ["", "Median seconds a search: " + ", ".join(
        f"{e} {sorted(r[e]['seconds'] for r in records)[len(records) // 2]:.1f}" for e in engines)]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sample", type=int, default=40)
    parser.add_argument("--seed", type=int, default=20260928)
    parser.add_argument("--engines", default="duckduckgo,serper,brave")
    parser.add_argument("--max-usd", type=Decimal, default=Decimal("0.25"))
    args = parser.parse_args()
    engines = args.engines.split(",")
    if unknown := set(engines) - set(PRICES):
        sys.exit(f"unknown engines: {', '.join(sorted(unknown))}")
    worst = args.sample * sum(PRICES[e] for e in engines)
    if worst > args.max_usd:
        sys.exit(f"{args.sample} queries could cost ${worst:.3f}, over the ${args.max_usd} cap")
    settings = Settings()
    searches = stored_searches(settings.database_dsn)
    queries = sample(searches, args.sample, args.seed)
    print(f"{len(searches)} distinct stored queries; replaying {len(queries)} (worst case ${worst:.3f})",
          file=sys.stderr)
    records = asyncio.run(replay(queries, engines, settings))
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"replay-{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps({"seed": args.seed, "engines": engines, "records": records}, indent=1))
    print(table(records, engines))
    print(f"\nPer-query results: {path}")


if __name__ == "__main__":
    main()

"""Re-check stored scouts' quotes under the current evidence rules, without a model call.

    .venv/bin/python scripts/rescore_quotes.py

For every scout and deep-dive call in the database, it rebuilds the labeled texts the call's tools
returned from its stored messages, checks its stored result again, and counts quotes whose verdict
changes: above all, quotes stored as `verified` that the current rules call `misattributed`, because
they appear only in another source's text. `same site` counts those whose real source shares the cited
source's host, such as a publisher's PDF cited as its landing page, which is likely the same work.
"""
from __future__ import annotations

import asyncio
import sys
from collections import Counter
from contextlib import AsyncExitStack
from urllib.parse import urlparse

from pydantic_ai.messages import ModelMessagesTypeAdapter

from research_loop.config import Settings
from research_loop.db import open_migrated_pool
from research_loop.evidence import check_result
from research_loop.schemas import ResearchResult
from research_loop.tools import labeled_texts


def _host(url: str | None) -> str:
    return (urlparse(url).hostname or "").removeprefix("www.") if url else ""


async def main() -> int:
    settings = Settings()
    if not settings.database_dsn:
        print("Set DATABASE_URL.", file=sys.stderr)
        return 2
    totals: Counter[str] = Counter()
    by_run: dict[str, Counter[str]] = {}
    async with AsyncExitStack() as stack:
        pool = await open_migrated_pool(stack, settings.database_dsn)
        async with pool.connection() as conn:
            cursor = await conn.execute(
                "select run_id, output, messages from run_calls "
                "where role in ('scout', 'deep_dive') and status = 'succeeded' and output is not null")
            rows = await cursor.fetchall()
    for run_id, output, messages in rows:
        stored = ResearchResult.model_validate(output)
        texts = labeled_texts(ModelMessagesTypeAdapter.validate_python(messages or []))
        again = check_result(stored, texts)
        counts: Counter[str] = Counter()
        for claim_before, claim_after in zip(stored.claims, again.claims, strict=True):
            for before, after in zip(claim_before.evidence, claim_after.evidence, strict=True):
                if before.quote_check is None:
                    continue
                counts["quotes"] += 1
                counts[f"{before.quote_check} -> {after.quote_check}"] += 1
                if before.quote_check == "verified" and after.quote_check == "misattributed":
                    cited = _host(str(before.source.url) if before.source.url else None)
                    counts["same site" if cited and cited == _host("https://" + (after.quote_found_in or ""))
                           else "other site"] += 1
        totals.update(counts)
        by_run.setdefault(str(run_id), Counter()).update(counts)
    for run_id, counts in sorted(by_run.items()):
        if counts["verified -> misattributed"]:
            print(f"{run_id[:8]}: {counts['verified -> misattributed']} of {counts['verified -> verified'] + counts['verified -> misattributed']} "
                  f"verified quotes now misattributed ({counts['same site']} same site)")
    verified = totals["verified -> verified"] + totals["verified -> misattributed"]
    print(f"\n{len(rows)} scout calls, {totals['quotes']} quotes; {verified} stored as verified, of which "
          f"{totals['verified -> misattributed']} are misattributed now ({totals['same site']} same site, "
          f"{totals['other site']} another site).")
    for key in sorted(k for k in totals if "->" in k):
        print(f"  {key}: {totals[key]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

"""Run the README's worked-example question under a capped budget and save the result.

The question and notes are the ones "Example: one question through the graph" in the README
shows. Without --paid, the synthetic policy runs the real graph with scripted outputs and no
model or web calls, which checks the script end to end for free. With --paid, the quality
policy runs under a total USD cap, with fewer questions, deep dives, and verification rounds
than its defaults. Every run writes a JSON record (question, date, policy snapshot with each
route's model and the caps, run configuration, report, verification, review reasons, cost,
and job ID) under RESEARCH_BENCHMARK_OUTPUT, which git ignores.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from contextlib import AsyncExitStack
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from research_loop import ResearchConfig, ResearchConstraints, ResearchLoop, get_policy
from research_loop.db import open_migrated_pool
from research_loop.observability import configure_logfire
from research_loop.repository import (
    InMemoryResearchRepository,
    PostgresResearchRepository,
)
from research_loop.settings import ResearchSettings
from research_loop.synthetic import SyntheticResearchLoop

QUESTION = "Is SWE-bench Verified still a trustworthy measure of coding-agent progress?"
NOTES = [
    "Keep preprints and published papers distinct.",
    "Label scores a vendor reports about its own model as vendor claims.",
]


def build(paid: bool, budget: float, reserve: float, settings: ResearchSettings) -> tuple[Any, ResearchConfig]:
    """The capped quality policy and trimmed run configuration, or the synthetic policy unchanged."""
    if not paid:
        return get_policy("synthetic"), ResearchConfig(scholarly_cache_mode=settings.scholarly_cache_mode)
    policy = get_policy("quality", model_overrides=settings.model_overrides)
    # The built-in policies set no total cap; this one keeps the whole run near `budget`.
    policy.job_cost_limit = budget
    # Held back from research so synthesis and verification can still run once it is spent.
    policy.job_reserve_usd = reserve
    policy.planner_question_range = (3, 5)
    policy.validate()
    config = ResearchConfig(
        max_verification_rounds=1,
        max_deep_dives_per_round=2,
        scholarly_cache_mode=settings.scholarly_cache_mode,
    )
    return policy, config


async def run(*, paid: bool, persist: bool, budget: float, reserve: float,
              settings: ResearchSettings, output: Path) -> dict[str, Any]:
    policy, config = build(paid, budget, reserve, settings)
    configure_logfire(settings)
    started = datetime.now(UTC)
    async with AsyncExitStack() as stack:
        if persist:
            # Refuses before any model call if migrations are pending or changed.
            repo = PostgresResearchRepository(await open_migrated_pool(stack, settings.database_dsn))
        else:
            repo = InMemoryResearchRepository()
        loop_class = ResearchLoop if paid else SyntheticResearchLoop
        loop = loop_class(policy, config, repository=repo, settings=settings)
        outcome = await loop.run(QUESTION, constraints=ResearchConstraints(notes=NOTES))

    record = {
        "question": QUESTION,
        "notes": NOTES,
        "started_at": started.isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
        "paid": paid,
        "policy": policy.snapshot(),
        "config": asdict(config),
        "job_id": str(outcome.job_id),
        "persisted": persist,
        "cost_usd": None if outcome.cost_usd is None else float(outcome.cost_usd),
        "review_reasons": outcome.review_reasons,
        "plan": outcome.plan.model_dump(mode="json"),
        "report": outcome.report.model_dump(mode="json"),
        "verification": outcome.verification.model_dump(mode="json"),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(record, indent=2, default=str) + "\n", encoding="utf-8")
    return record


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the README's example question under a capped budget")
    parser.add_argument("--paid", action="store_true", help="Call real models (the quality policy, capped)")
    parser.add_argument("--budget", type=float, default=4.0, help="Total USD cap for the run (default 4.00)")
    parser.add_argument("--reserve", type=float, default=1.5,
                        help="USD of the budget held for synthesis and verification (default 1.50)")
    parser.add_argument("--persist", action="store_true", help="Also store the job in Postgres at DATABASE_URL")
    parser.add_argument("--output", type=Path, help="Where to write the JSON record")
    args = parser.parse_args(argv)
    # Resolve settings first: it loads .env, which carries the model overrides and DATABASE_URL.
    settings = ResearchSettings.from_env()
    if args.persist and not settings.database_dsn:
        parser.error("--persist needs DATABASE_URL; see docs/setup.md#postgres")
    try:
        build(args.paid, args.budget, args.reserve, settings)
    except ValueError as exc:  # a budget or reserve no policy can run under
        parser.error(str(exc))
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output = args.output or settings.benchmark_output / "readme_example" / f"{stamp}.json"
    record = asyncio.run(run(paid=args.paid, persist=args.persist, budget=args.budget,
                             reserve=args.reserve, settings=settings, output=output))

    print(record["report"]["answer"])
    print(f"\njob_id={record['job_id']} cost_usd={record['cost_usd']}")
    if args.paid and record["cost_usd"] is None:
        print("cost unknown: a model had no pricing data, so the budget could not be enforced for it")
    for reason in record["review_reasons"]:
        print(f"needs review: {reason}")
    print(f"record: {output}")


if __name__ == "__main__":
    main()

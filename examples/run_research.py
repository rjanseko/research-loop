"""Run one research objective and print the report.

The default synthetic policy runs the real graph without model or web calls; any
other policy calls paid model providers and needs --paid.
"""
from __future__ import annotations

import argparse
import asyncio

from research_loop import POLICY_PRESETS, ResearchConfig, ResearchLoop, ResearchToolMode, get_policy
from research_loop.observability import configure_logfire
from research_loop.repository import InMemoryResearchRepository
from research_loop.settings import ResearchSettings
from research_loop.synthetic import SyntheticResearchLoop


async def run(objective: str, policy_name: str, tool_mode: str) -> None:
    # Resolve settings first: it loads .env, which carries the model overrides.
    settings = ResearchSettings.from_env()
    configure_logfire(settings)
    repo = InMemoryResearchRepository()
    loop_class = SyntheticResearchLoop if policy_name == "synthetic" else ResearchLoop
    loop = loop_class(
        get_policy(policy_name, model_overrides=settings.model_overrides),
        ResearchConfig(
            tool_mode=ResearchToolMode(tool_mode),
            scholarly_cache_mode=settings.scholarly_cache_mode,
        ),
        repository=repo,
        settings=settings,
    )
    outcome = await loop.run(objective)

    print("\n=== FINAL REPORT ===\n")
    print(outcome.report.answer)
    print("\n=== VERIFICATION ===")
    print(f"needs_research={outcome.verification.needs_research}")
    print(f"claims={len(outcome.verification.checks)}")
    print(f"tasks={len(repo.tasks)} tool_events={len(repo.tool_events)} cost_usd={outcome.cost_usd}")
    for reason in outcome.review_reasons:
        print(f"needs review: {reason}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one research objective and print the report")
    parser.add_argument("objective")
    parser.add_argument("--policy", default="synthetic", choices=sorted(POLICY_PRESETS))
    parser.add_argument("--tool-mode", default="adaptive", choices=[mode.value for mode in ResearchToolMode])
    parser.add_argument("--paid", action="store_true", help="Allow policies that call paid model providers")
    args = parser.parse_args()
    if args.policy != "synthetic" and not args.paid:
        parser.error("real model policies require --paid")
    asyncio.run(run(args.objective, args.policy, args.tool_mode))


if __name__ == "__main__":
    main()

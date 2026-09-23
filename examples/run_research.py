from __future__ import annotations

import argparse
import asyncio

from research_loop import ResearchConfig, ResearchLoop, ResearchToolMode, get_policy
from research_loop.repository import InMemoryResearchRepository


async def run(objective: str, policy_name: str, tool_mode: str) -> None:
    repo = InMemoryResearchRepository()
    loop = ResearchLoop(
        get_policy(policy_name),
        ResearchConfig(tool_mode=ResearchToolMode(tool_mode)),
        repository=repo,
    )
    outcome = await loop.run(objective)

    print("\n=== FINAL REPORT ===\n")
    print(outcome.report.answer)
    print("\n=== VERIFICATION ===")
    print(f"needs_research={outcome.verification.needs_research}")
    print(f"claims={len(outcome.verification.checks)}")
    print(f"tasks={len(repo.tasks)} tool_events={len(repo.tool_events)}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("objective")
    parser.add_argument("--policy", default="quality", choices=["quality", "breadth", "glm-heavy"])
    parser.add_argument("--tool-mode", default="adaptive", choices=["adaptive", "normalized"])
    args = parser.parse_args()
    asyncio.run(run(args.objective, args.policy, args.tool_mode))


if __name__ == "__main__":
    main()

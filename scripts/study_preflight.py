"""Whether a stored job's research fits the settings study's finishing limits, before a paid step.

A finishing call (gap analysis, synthesis, verification) refuses its prompt before any request when
one validation retry would not fit its token limit (`PromptExceedsRetryBudget`). The prompts grow
with the research, so the study's deeper limits make larger prompts than the presets were sized for:
the second pilot's gap analysis was refused after $1.67 of research.

This rebuilds each finishing prompt from a stored job's objective, plan, constraints, and evidence
ledger, with the functions the orchestrator uses, and checks it against the study policy's route:

- gap analysis and synthesis: the prompts the job's ledger makes;
- verification: the job's own report if it has one; otherwise an upper bound, the synthesis prompt's
  evidence plus a report as long as the synthesizer's assumed answer.

`headroom` is the token limit over the tokens one retry needs. Deep dives add to the ledger after gap
analysis, so synthesis and verification in a later run see more than a job stopped at gap analysis
shows; a headroom of 2 or more leaves room for that. Output holds sizes, not prompt text.

    .venv/bin/python scripts/study_preflight.py <job_id> [--policy value]
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from research_loop.async_orchestrator import (
    gap_analysis_prompt,
    synthesis_prompt,
    verification_prompt,
)
from research_loop.ledger import EvidenceLedger
from research_loop.policy import ModelPolicy, retry_output_tokens, retry_token_budget
from research_loop.schemas import FinalReport, ResearchPlan, ResearchRole

_STUDY = Path(__file__).parents[1] / "examples" / "settings_study.py"


@dataclass
class Check:
    role: ResearchRole
    prompt_chars: int
    needed: int
    limit: int
    note: str = ""

    @property
    def fits(self) -> bool:
        return self.needed <= self.limit


def study_policy(policy_name: str) -> ModelPolicy:
    """The policy examples/settings_study.py runs paid steps with."""
    from research_loop.settings import ResearchSettings

    spec = importlib.util.spec_from_file_location("settings_study", _STUDY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    policy, _config = module.build(True, 5.0, 1.0, ResearchSettings.from_env(), policy_name=policy_name)
    return policy


def check_job(policy: ModelPolicy, objective: str, plan: ResearchPlan, ledger: EvidenceLedger,
              constraints: dict[str, Any], report: FinalReport | None) -> list[Check]:
    """Each finishing role's prompt size and the tokens one retry of it needs, against its route's limit."""
    checks = []
    for role, prompt in ((ResearchRole.GAP_ANALYST, gap_analysis_prompt(objective, plan, ledger, constraints)),
                         (ResearchRole.SYNTHESIZER, synthesis_prompt(objective, ledger, constraints))):
        route = policy.for_role(role)
        checks.append(Check(role, len(prompt), retry_token_budget(prompt, route, role), route.total_tokens_limit))
    route = policy.for_role(ResearchRole.VERIFIER)
    if report is not None:
        prompt = verification_prompt(objective, report, ledger, constraints)
        checks.append(Check(ResearchRole.VERIFIER, len(prompt), retry_token_budget(prompt, route, ResearchRole.VERIFIER),
                            route.total_tokens_limit, "the job's report"))
    else:
        # Sent twice, as the prompt is: once in the first request and again in the retry.
        report_tokens = retry_output_tokens(policy.for_role(ResearchRole.SYNTHESIZER), ResearchRole.SYNTHESIZER)
        prompt = synthesis_prompt(objective, ledger, constraints)
        needed = retry_token_budget(prompt, route, ResearchRole.VERIFIER) + 2 * report_tokens
        checks.append(Check(ResearchRole.VERIFIER, len(prompt), needed, route.total_tokens_limit,
                            "upper bound: every claim, and a report of the synthesizer's assumed length"))
    return checks


async def load_job(dsn: str, job_id: UUID) -> tuple[str, ResearchPlan, EvidenceLedger, dict[str, Any], FinalReport | None]:
    import psycopg

    async with await psycopg.AsyncConnection.connect(dsn) as conn:
        row = await (await conn.execute(
            "select objective, plan, evidence_ledger, effective_config, final_report from research_jobs where id = %s",
            (job_id,))).fetchone()
    if row is None:
        raise SystemExit(f"no job {job_id}")
    objective, plan, ledger, config, report = row
    if plan is None or ledger is None:
        raise SystemExit(f"job {job_id} stored no plan or evidence ledger")
    stored = (config or {}).get("constraints") or {}
    # The fields the orchestrator's constraints payload sends; attachments are not stored with the job.
    constraints = {"blocked_urls": stored.get("blocked_urls", []), "benchmark_id": stored.get("benchmark_id"),
                   "notes": stored.get("notes", []), "attachments": []}
    return (objective, ResearchPlan.model_validate(plan), EvidenceLedger.from_json(ledger), constraints,
            FinalReport.model_validate(report) if report else None)


def render(checks: list[Check]) -> str:
    lines = ["role         prompt_chars  retry_tokens   limit  headroom  fits"]
    for c in checks:
        headroom = c.limit / c.needed if c.needed else math.inf
        lines.append(f"{c.role.value:<12} {c.prompt_chars:>12,} {c.needed:>13,} {c.limit:>7,} {headroom:>9.1f}"
                     f"  {'yes' if c.fits else 'NO'}" + (f"  ({c.note})" if c.note else ""))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    from research_loop.settings import ResearchSettings

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("job_id", type=UUID)
    parser.add_argument("--policy", default="value", help="Preset the study runs on (default value)")
    args = parser.parse_args(argv)
    settings = ResearchSettings.from_env()
    if not settings.database_dsn:
        parser.error("reads the job from Postgres; set DATABASE_URL (see docs/setup.md#postgres)")
    checks = check_job(study_policy(args.policy), *asyncio.run(load_job(settings.database_dsn, args.job_id)))
    print(render(checks))
    if not all(c.fits for c in checks):
        raise SystemExit(1)


if __name__ == "__main__":
    main()

"""Try a candidate planner or synthesizer prompt against the current one on the settings study's inputs.

A prompt change is a behavior change, so it is compared on the same inputs before it replaces the
current instructions in `agents.INSTRUCTIONS`. Both modes call the orchestrator's own role calls
(`_plan`, `_synthesize`, `_verify`) with the study policy's routes, so the prompts, output schemas,
validators, and limits are the ones a study run uses; only the role's instructions differ.

- `planner`: plans each study question with the current and the candidate planner instructions.
  No research runs; the comparison is the shape of the plans.
- `synthesis`: for each stored job, writes a report from its final evidence ledger with the current
  and the candidate synthesizer instructions, and verifies both with the current verifier, as one job
  each. The comparison is the verifier's counts: statements checked, unsupported, and rated major.
  One verifier sample per report, so a difference of a few statements is within its variation.

`--max-usd` is required and is a hard cap on the trial's spend: no unit starts once the units that
finished have spent it, and at most `--concurrency` units run at once, so the trial can pass the cap by
no more than the units running when it is reached. A unit's model requests are recorded, so a
validation retry, which resends the whole prompt, shows up. Estimate a trial from the costliest
call seen, retries included, not the average.

Every unit is a job in Postgres, with its calls, transcripts, and cost, as a study run is, and its
config names the trial and the arm (`trial`) so it is never taken for a study run. A synthesis unit's
job keeps its report and verification, so `research-report` renders it and `research-grade` grades it.
The trial record, with the job IDs, goes to benchmark_outputs/settings_study/prompt-trial/; the
terminal gets counts and costs.

    .venv/bin/python scripts/prompt_trial.py --max-usd 1 planner
    .venv/bin/python scripts/prompt_trial.py --max-usd 3 synthesis <job_id> [<job_id> ...]
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import sys
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from research_loop.agents import INSTRUCTIONS, planner_agent, synthesizer_agent
from research_loop.async_orchestrator import AsyncResearchLoop
from research_loop.policy import ModelPolicy
from research_loop.repository import (
    CapturingResearchRepository,
    InMemoryResearchRepository,
)
from research_loop.schemas import FinalReport, ResearchConstraints, VerificationReport

_SCRIPTS = Path(__file__).parent
_STUDY = _SCRIPTS.parent / "examples" / "settings_study.py"

# The candidates, each the current instructions plus one addition aimed at what the pilots showed.
# Three planners split the seven-country task three ways, bundling countries into questions whose
# scouts all ran into their limits, while the one single-country scout finished by itself.
PLANNER_ADDITION = (
    " When the objective asks the same things about several parallel subjects, such as countries, "
    "products, or periods, give each subject its own question as far as the question range allows, "
    "and put any comparison across them in a question of its own."
)
# The verifier's major findings on the fourth and fifth pilots' reports were mostly statements broader
# than their evidence: facts the ledger itself marked unverified or outside the requested period, stated
# as established; special cases stated as rules; conditions dropped; and tables resting on one secondary
# or later source.
SYNTHESIZER_ADDITION = (
    " State each fact no more broadly than its evidence: keep the conditions, exceptions, dates, and scope "
    "the evidence gives, and do not turn a special case into a general rule. Where a claim, its evidence, or "
    "its result marks something unverified, unresolved, or outside the period the objective asks about, say "
    "so where the fact appears, in tables as well as prose. Say when a figure rests only on a secondary or "
    "later source. Where the evidence does not cover part of the objective, say it was not established "
    "rather than filling it in."
)
CANDIDATES = {
    "planner": INSTRUCTIONS["planner"] + PLANNER_ADDITION,
    "synthesizer": INSTRUCTIONS["synthesizer"] + SYNTHESIZER_ADDITION,
}
VARIANTS = ("current", "candidate")


def _load(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


CAP_REACHED = "cap_reached"


@dataclass
class TrialBudget:
    """The trial's hard dollar cap, what its finished units spent, and how many units may run at once."""

    max_usd: float
    concurrency: int = 2
    spent: float = 0.0
    _slots: asyncio.Semaphore | None = field(default=None, repr=False)

    @property
    def slots(self) -> asyncio.Semaphore:
        # Made on first use, inside the trial's event loop.
        if self._slots is None:
            self._slots = asyncio.Semaphore(self.concurrency)
        return self._slots


@dataclass
class Unit:
    result: Any
    cost: float
    requests: int
    error: str | None
    job_id: UUID | None = None
    # Dollars by role, such as a scout run's scouting apart from its synthesis.
    cost_by_role: dict[str, float] = field(default_factory=dict)


def _usage(repo: Any, job_id: UUID) -> tuple[float, int, dict[str, float]]:
    """Dollars, model requests, and dollars by role of one unit's job; units run at once, so it goes by job."""
    tasks = [task for task in repo.tasks.values() if task.get("job_id") == job_id]
    by_role: dict[str, float] = {}
    for task in tasks:
        role = str(getattr(task.get("role"), "value", task.get("role")))
        by_role[role] = by_role.get(role, 0.0) + float((task.get("usage") or {}).get("cost") or 0)
    requests = sum(int((task.get("usage") or {}).get("requests") or 0) for task in tasks)
    return sum(by_role.values()), requests, by_role


async def _unit(loop: AsyncResearchLoop, budget: TrialBudget, constraints: ResearchConstraints,
                body: Callable[[UUID], Awaitable[Any]], *, objective: str, trial: dict[str, Any],
                finish: Callable[[Any], dict[str, Any]] | None = None) -> Unit:
    """Run `body` as one stored job under the study's job cap and the trial's cap.

    The job's config records `trial`. When `body` returns, the job is finished as succeeded with the
    fields `finish` makes from its result (a report, verification, and ledger); when it raises, the job
    scope records it failed. A failed unit, such as a refusal, is recorded rather than stopping the
    trial's other units; one that would start after the trial's cap is spent is skipped as `cap_reached`.
    """
    async with budget.slots:
        if budget.spent >= budget.max_usd:
            return Unit(None, 0.0, 0, CAP_REACHED)
        repo = loop.repository
        job_id = await loop._create_job(objective, constraints, session_id=None, root_run_id=None,
                                        kind="trial", trial=trial)
        result, error = None, None
        try:
            async with loop._job_scope(job_id, constraints):
                result = await body(job_id)
                await repo.finish_job(job_id, status="succeeded", **(finish(result) if finish else {}))
        except Exception as exc:  # noqa: BLE001 - one unit's failure must not stop the others; the type only, as bodies can leak
            error = type(exc).__name__
        cost, requests, by_role = _usage(repo, job_id)
        budget.spent += cost
        return Unit(result, cost, requests, error, job_id, by_role)


def _loop(policy: ModelPolicy, settings: Any, repository: Any = None) -> AsyncResearchLoop:
    """A loop writing to `repository` (the trial's Postgres, captured) or, in tests, to memory.

    The study's job cap stays: the planner is shown it, and it shapes the plan.
    """
    return AsyncResearchLoop(policy, repository=repository or CapturingResearchRepository(InMemoryResearchRepository()),
                             settings=settings)


async def trial_plans(loop: AsyncResearchLoop, budget: TrialBudget, cases: list[Any],
                      candidate: str) -> list[dict[str, Any]]:
    """Each case planned with the current and the candidate planner instructions."""
    async def plan(case: Any, variant: str) -> dict[str, Any]:
        constraints = ResearchConstraints(blocked_urls=case.blocked_urls, benchmark_id=case.benchmark_id,
                                          benchmark_case_id=case.case_id)
        with planner_agent.override(instructions=candidate if variant == "candidate" else INSTRUCTIONS["planner"]):
            objective = case.render_objective()
            unit = await _unit(loop, budget, constraints, lambda job_id: loop._plan(job_id, objective, constraints, None),
                               objective=objective,
                               trial={"name": "prompt", "mode": "planner", "variant": variant, "case_id": case.case_id})
        return {"case_id": case.case_id, "variant": variant, "cost_usd": unit.cost, "requests": unit.requests,
                "error": unit.error, "job_id": unit.job_id,
                "questions": [q.question for q in unit.result.questions] if unit.result else []}

    # An override is held in a context variable, so it applies only to the task that set it.
    return list(await asyncio.gather(*(plan(case, variant) for variant in VARIANTS for case in cases)))


def _counts(verification: VerificationReport, report: FinalReport) -> dict[str, Any]:
    checks = verification.checks
    return {"statements": len(report.claims), "checked": len(checks),
            "unsupported": sum(not c.supported for c in checks),
            "major": sum(c.severity == "major" for c in checks),
            "follow_ups": len(verification.followups)}


async def trial_synthesis(loop: AsyncResearchLoop, budget: TrialBudget, jobs: list[tuple[str, tuple[Any, ...]]],
                          candidate: str) -> list[dict[str, Any]]:
    """Each job's ledger synthesized with the current and the candidate instructions and verified, one job each."""
    async def unit(job_id: str, job: tuple[Any, ...], variant: str) -> dict[str, Any]:
        objective, _plan, ledger, stored, _report = job
        constraints = ResearchConstraints(blocked_urls=stored["blocked_urls"], benchmark_id=stored["benchmark_id"],
                                          notes=stored["notes"])

        async def body(trial_id: UUID) -> tuple[FinalReport, VerificationReport]:
            report = await loop._synthesize(trial_id, objective, ledger, constraints, None)
            return report, await loop._verify(trial_id, objective, report, ledger, constraints, None)

        # Only the synthesizer's instructions change; the verifier is the current one for both variants.
        with synthesizer_agent.override(
                instructions=candidate if variant == "candidate" else INSTRUCTIONS["synthesizer"]):
            done = await _unit(loop, budget, constraints, body, objective=objective,
                               trial={"name": "prompt", "mode": "synthesis", "variant": variant, "source_job": job_id},
                               finish=lambda result: {"final_report": result[0].model_dump(mode="json"),
                                                      "verification": result[1].model_dump(mode="json"),
                                                      "evidence_ledger": ledger.to_json()})
        entry = {"job_id": job_id, "variant": variant, "trial_job_id": done.job_id, "error": done.error,
                 "synthesis_usd": done.cost_by_role.get("synthesizer", 0.0),
                 "verification_usd": done.cost_by_role.get("verifier", 0.0), "requests": done.requests}
        if done.result is None:
            return entry
        report, verification = done.result
        return entry | {**_counts(verification, report), "report": report.model_dump(mode="json"),
                        "verification": verification.model_dump(mode="json")}

    return list(await asyncio.gather(*(unit(job_id, job, variant) for variant in VARIANTS for job_id, job in jobs)))


def render_plans(rows: list[dict[str, Any]]) -> str:
    lines = ["case                     variant    questions requests  cost_usd"]
    for row in sorted(rows, key=lambda r: (r["case_id"], VARIANTS.index(r["variant"]))):
        count = row["error"] or len(row["questions"])
        lines.append(f"{row['case_id']:<24} {row['variant']:<10} {count:>9} {row['requests']:>8} {row['cost_usd']:>9.3f}")
    return "\n".join(lines)


def render_synthesis(rows: list[dict[str, Any]], stored: dict[str, dict[str, Any] | None]) -> str:
    lines = ["job       variant    statements checked unsupported major follow_ups synth_usd verify_usd requests"]
    for row in sorted(rows, key=lambda r: (r["job_id"], VARIANTS.index(r["variant"]))):
        if row.get("error"):
            lines.append(f"{row['job_id'][:8]}  {row['variant']:<10} failed: {row['error']}")
            continue
        lines.append(f"{row['job_id'][:8]}  {row['variant']:<10} {row['statements']:>10} {row['checked']:>7} "
                     f"{row['unsupported']:>11} {row['major']:>5} {row['follow_ups']:>10} "
                     f"{row['synthesis_usd']:>9.3f} {row['verification_usd']:>10.3f} {row['requests']:>8}")
    for job_id, counts in stored.items():
        if counts:
            lines.append(f"{job_id[:8]}  {'stored':<10} {counts['statements']:>10} {counts['checked']:>7} "
                         f"{counts['unsupported']:>11} {counts['major']:>5} {counts['follow_ups']:>10}")
    return "\n".join(lines)


async def _stored_verification(dsn: str, job_id: UUID) -> dict[str, Any] | None:
    """The job's own final report and verification counts, a second sample of the current prompt."""
    import psycopg

    async with await psycopg.AsyncConnection.connect(dsn) as conn:
        row = await (await conn.execute(
            "select final_report, verification from research_jobs where id = %s", (job_id,))).fetchone()
    if not row or not row[0] or not row[1]:
        return None
    return _counts(VerificationReport.model_validate(row[1]), FinalReport.model_validate(row[0]))


async def open_trial_repository(stack: AsyncExitStack, dsn: str) -> CapturingResearchRepository:
    """The trial's store: Postgres, transcripts included, with a local copy for the trial's own measures."""
    from research_loop.db import open_migrated_pool
    from research_loop.repository import PostgresResearchRepository

    # Refuses before any model call if migrations are pending or changed.
    pool = await open_migrated_pool(stack, dsn)
    return CapturingResearchRepository(PostgresResearchRepository(pool, capture_transcripts=True))


async def _run(args: argparse.Namespace, settings: Any, budget: TrialBudget,
               record: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    from research_loop.benchmarks import load_suite

    preflight = _load("study_preflight", _SCRIPTS / "study_preflight.py")
    async with AsyncExitStack() as stack:
        loop = _loop(preflight.study_policy("value", settings), settings,
                     await open_trial_repository(stack, settings.database_dsn))
        if args.mode == "planner":
            study = _load("settings_study", _STUDY)
            _manifest, specs = load_suite(study.SUITE)
            cases = [spec for spec in specs if not spec.metadata.get("retired")]
            rows = await trial_plans(loop, budget, cases, CANDIDATES["planner"])
            return rows, render_plans(rows)
        jobs = [(str(job_id), await preflight.load_job(settings.database_dsn, job_id)) for job_id in args.job_ids]
        rows = await trial_synthesis(loop, budget, jobs, CANDIDATES["synthesizer"])
        stored = {str(job_id): await _stored_verification(settings.database_dsn, job_id) for job_id in args.job_ids}
        record["stored"] = stored
        return rows, render_synthesis(rows, stored)


def main(argv: list[str] | None = None) -> None:
    from research_loop.settings import ResearchSettings

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="mode", required=True)
    sub.add_parser("planner", help="Plan every study question with both planner prompts")
    synthesis = sub.add_parser("synthesis", help="Synthesize and verify stored jobs' ledgers with both prompts")
    synthesis.add_argument("job_ids", nargs="+", type=UUID)
    parser.add_argument("--max-usd", type=float, required=True,
                        help="Hard cap on the trial's spend: no unit starts once finished units have spent it")
    parser.add_argument("--concurrency", type=int, default=2,
                        help="Units run at once, and so how far past the cap the trial can go (default 2)")
    parser.add_argument("--output", type=Path, help="Trial record (default: benchmark_outputs/settings_study/prompt-trial/)")
    args = parser.parse_args(argv)
    if args.max_usd <= 0 or args.concurrency < 1:
        parser.error("--max-usd must be positive and --concurrency at least 1")
    budget = TrialBudget(args.max_usd, args.concurrency)
    settings = ResearchSettings.from_env()
    if not settings.database_dsn:
        parser.error("trials store their jobs in Postgres; set DATABASE_URL (see docs/setup.md#postgres)")
    record: dict[str, Any] = {"mode": args.mode, "started_at": datetime.now(UTC).isoformat(),
                              "candidate_addition": PLANNER_ADDITION if args.mode == "planner" else SYNTHESIZER_ADDITION}
    rows, summary = asyncio.run(_run(args, settings, budget, record))
    record["rows"] = rows
    output = args.output or settings.benchmark_output / "settings_study" / "prompt-trial" / (
        f"{args.mode}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    print(summary)
    record["spent_usd"] = budget.spent
    output.write_text(json.dumps(record, indent=2, default=str) + "\n", encoding="utf-8")
    skipped = sum(r.get("error") == CAP_REACHED for r in rows)
    print(f"total ${budget.spent:.2f} of the ${budget.max_usd:.2f} cap"
          + (f"; {skipped} units skipped at the cap" if skipped else "") + f"; record: {output}")


if __name__ == "__main__":
    main()

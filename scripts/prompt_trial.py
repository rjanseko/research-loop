"""Try a candidate planner or synthesizer prompt against the current one on the settings study's inputs.

A prompt change is a behavior change, so it is compared on the same inputs before it replaces the
current instructions in `agents.INSTRUCTIONS`. Both modes call the orchestrator's own role calls
(`_plan`, `_synthesize`, `_verify`) with the study policy's routes, so the prompts, output schemas,
validators, and limits are the ones a study run uses; only the role's instructions differ.

- `planner`: plans each study question with the current and the candidate planner instructions.
  No research runs; the comparison is the shape of the plans.
- `synthesis`: for each stored job, writes a report from its final evidence ledger with the current
  and the candidate synthesizer instructions, and verifies both with the current verifier. The
  comparison is the verifier's counts: statements checked, unsupported, and rated major. One
  verifier sample per report, so a difference of a few statements is within its variation.

`--max-usd` is required and is a hard cap on the trial's spend: no unit starts once the units that
finished have spent it, and at most `--concurrency` units run at once, so the trial can pass the cap by
no more than the units running when it is reached. A unit's model requests are recorded, so a
validation retry, which resends the whole prompt, shows up. Estimate a trial from the costliest
call seen, retries included, not the average.

Nothing is stored in Postgres. The trial record, with plans and reports, goes to
benchmark_outputs/settings_study/prompt-trial/; the terminal gets counts and costs.

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
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from research_loop.agents import INSTRUCTIONS, planner_agent, synthesizer_agent
from research_loop.async_orchestrator import AsyncResearchLoop
from research_loop.policy import ModelPolicy
from research_loop.repository import InMemoryResearchRepository
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


def _usage(repo: InMemoryResearchRepository, job_id: UUID) -> tuple[float, int]:
    """Dollars and model requests of one unit's job; units run at once, so it goes by job, not by order."""
    usages = [task.get("usage") or {} for task in repo.tasks.values() if task.get("job_id") == job_id]
    return sum(float(u.get("cost") or 0) for u in usages), sum(int(u.get("requests") or 0) for u in usages)


async def _unit(loop: AsyncResearchLoop, budget: TrialBudget, constraints: ResearchConstraints,
                body: Callable[[UUID], Awaitable[Any]]) -> Unit:
    """Run `body` as one job under the study's job cap and the trial's cap.

    A failed unit, such as a refusal, is recorded rather than stopping the trial's other units; one
    that would start after the trial's cap is spent is skipped as `cap_reached`.
    """
    async with budget.slots:
        if budget.spent >= budget.max_usd:
            return Unit(None, 0.0, 0, CAP_REACHED)
        repo = loop.repository
        job_id = await repo.create_job(objective="prompt trial")
        result, error = None, None
        try:
            async with loop._job_scope(job_id, constraints):
                result = await body(job_id)
        except Exception as exc:  # noqa: BLE001 - one unit's failure must not stop the others; the type only, as bodies can leak
            error = type(exc).__name__
        cost, requests = _usage(repo, job_id)
        budget.spent += cost
        return Unit(result, cost, requests, error)


def _loop(policy: ModelPolicy, settings: Any) -> AsyncResearchLoop:
    # The study's job cap stays: the planner is shown it, and it shapes the plan.
    return AsyncResearchLoop(policy, repository=InMemoryResearchRepository(), settings=settings)


async def trial_plans(loop: AsyncResearchLoop, budget: TrialBudget, cases: list[Any],
                      candidate: str) -> list[dict[str, Any]]:
    """Each case planned with the current and the candidate planner instructions."""
    async def plan(case: Any, variant: str) -> dict[str, Any]:
        constraints = ResearchConstraints(blocked_urls=case.blocked_urls, benchmark_id=case.benchmark_id,
                                          benchmark_case_id=case.case_id)
        with planner_agent.override(instructions=candidate if variant == "candidate" else INSTRUCTIONS["planner"]):
            unit = await _unit(loop, budget, constraints, lambda job_id: loop._plan(
                job_id, case.render_objective(), constraints, None))
        return {"case_id": case.case_id, "variant": variant, "cost_usd": unit.cost, "requests": unit.requests,
                "error": unit.error, "questions": [q.question for q in unit.result.questions] if unit.result else []}

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
    """Each job's ledger synthesized with the current and the candidate instructions, each report verified."""
    async def synthesize(job_id: str, job: tuple[Any, ...], variant: str) -> dict[str, Any]:
        objective, _plan, ledger, stored, _report = job
        constraints = ResearchConstraints(blocked_urls=stored["blocked_urls"], benchmark_id=stored["benchmark_id"],
                                          notes=stored["notes"])
        with synthesizer_agent.override(
                instructions=candidate if variant == "candidate" else INSTRUCTIONS["synthesizer"]):
            unit = await _unit(loop, budget, constraints, lambda trial_id: loop._synthesize(
                trial_id, objective, ledger, constraints, None))
        return {"job_id": job_id, "variant": variant, "report": unit.result, "synthesis_usd": unit.cost,
                "synthesis_requests": unit.requests, "error": unit.error,
                "objective": objective, "ledger": ledger, "constraints": constraints}

    reports = await asyncio.gather(*(synthesize(job_id, job, variant) for variant in VARIANTS for job_id, job in jobs))

    async def verify(row: dict[str, Any]) -> dict[str, Any]:
        entry = {"job_id": row["job_id"], "variant": row["variant"], "synthesis_usd": row["synthesis_usd"],
                 "synthesis_requests": row["synthesis_requests"], "verification_usd": 0.0,
                 "verification_requests": 0, "error": row["error"]}
        if row["report"] is None:
            return entry
        unit = await _unit(loop, budget, row["constraints"], lambda trial_id: loop._verify(
            trial_id, row["objective"], row["report"], row["ledger"], row["constraints"], None))
        entry |= {"verification_usd": unit.cost, "verification_requests": unit.requests}
        if unit.result is None:
            return entry | {"error": unit.error, "report": row["report"].model_dump(mode="json")}
        verification = unit.result
        return entry | {**_counts(verification, row["report"]),
                "report": row["report"].model_dump(mode="json"),
                "verification": verification.model_dump(mode="json")}

    return list(await asyncio.gather(*(verify(row) for row in reports)))


def render_plans(rows: list[dict[str, Any]]) -> str:
    lines = ["case                     variant    questions requests  cost_usd"]
    for row in sorted(rows, key=lambda r: (r["case_id"], VARIANTS.index(r["variant"]))):
        count = row["error"] or len(row["questions"])
        lines.append(f"{row['case_id']:<24} {row['variant']:<10} {count:>9} {row['requests']:>8} {row['cost_usd']:>9.3f}")
    return "\n".join(lines)


def render_synthesis(rows: list[dict[str, Any]], stored: dict[str, dict[str, Any] | None]) -> str:
    lines = ["job       variant    statements checked unsupported major follow_ups synth_usd (requests) verify_usd"]
    for row in sorted(rows, key=lambda r: (r["job_id"], VARIANTS.index(r["variant"]))):
        if row.get("error"):
            lines.append(f"{row['job_id'][:8]}  {row['variant']:<10} failed: {row['error']}")
            continue
        lines.append(f"{row['job_id'][:8]}  {row['variant']:<10} {row['statements']:>10} {row['checked']:>7} "
                     f"{row['unsupported']:>11} {row['major']:>5} {row['follow_ups']:>10} "
                     f"{row['synthesis_usd']:>9.3f} ({row['synthesis_requests']:>8}) {row['verification_usd']:>10.3f}")
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


def main(argv: list[str] | None = None) -> None:
    from research_loop.benchmarks import load_suite
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
    preflight = _load("study_preflight", _SCRIPTS / "study_preflight.py")
    loop = _loop(preflight.study_policy("value", settings), settings)
    record: dict[str, Any] = {"mode": args.mode, "started_at": datetime.now(UTC).isoformat(),
                              "candidate_addition": PLANNER_ADDITION if args.mode == "planner" else SYNTHESIZER_ADDITION}
    if args.mode == "planner":
        study = _load("settings_study", _STUDY)
        _manifest, specs = load_suite(study.SUITE)
        cases = [spec for spec in specs if not spec.metadata.get("retired")]
        rows = asyncio.run(trial_plans(loop, budget, cases, CANDIDATES["planner"]))
        summary = render_plans(rows)
    else:
        if not settings.database_dsn:
            parser.error("reads the jobs from Postgres; set DATABASE_URL (see docs/setup.md#postgres)")
        jobs = [(str(job_id), asyncio.run(preflight.load_job(settings.database_dsn, job_id))) for job_id in args.job_ids]
        rows = asyncio.run(trial_synthesis(loop, budget, jobs, CANDIDATES["synthesizer"]))
        stored = {str(job_id): asyncio.run(_stored_verification(settings.database_dsn, job_id)) for job_id in args.job_ids}
        record["stored"] = stored
        summary = render_synthesis(rows, stored)
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

"""Synthesizes and verifies a stored job's evidence ledger again, on the study's current finishing routes, and grades it.

For a job whose research finished but whose report did not, as pilot 8's synthesis failed on a provider
error, or to see what a change to synthesis does to a report without paying for its research again. The
stored job gives the objective, the constraints, and the ledger; the study's policy (examples/settings_study.py)
gives the synthesizer and verifier. It runs one synthesis and one verification, as prompt_trial.py's synthesis
mode does: a verifier's follow-ups are recorded, not researched, so a pilot's follow-up round is not repeated.

The result is a job in Postgres marked with the trial (`trial` in its config), holding the report, the
verification, and the ledger, so `research-report` renders it and the study's judge grades it as
`research-grade` would. The record adds what the synthesis call did: its model or models (a fallback shows
as a failed task on the route's model and a task on the fallback), how long it took, its tokens, and its
cost. `--max-usd` caps synthesis and verification, checked before the job starts, as in prompt_trial.py; the
judge's call is not in it, about $0.05. The record goes to benchmark_outputs/settings_study/resynthesis/.

    .venv/bin/python scripts/resynthesize.py <job_id> --max-usd 1.50

`--synthesizer MODEL` swaps the synthesizer's model, keeping the study route's limits and fallback; its effort
is then the model's own under policy.MODEL_EFFORT (Opus 5.5 at `medium`), or the study route's.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from research_loop.async_orchestrator import AsyncResearchLoop
from research_loop.ledger import EvidenceLedger
from research_loop.policy import apply_model_effort
from research_loop.schemas import (
    FinalReport,
    ResearchConstraints,
    ResearchRole,
    VerificationReport,
)

_SCRIPTS = Path(__file__).parent
_STUDY = _SCRIPTS.parent / "examples" / "settings_study.py"
JUDGE = "openai:gpt-6-sol"


def _load(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def task_rows(repo: Any, job_id: UUID) -> list[dict[str, Any]]:
    """Each finishing call of the job, in order: role, model, status, seconds, tokens, and cost."""
    rows = []
    for task in sorted((t for t in repo.tasks.values() if t.get("job_id") == job_id), key=lambda t: t["started_at"]):
        usage = task.get("usage") or {}
        finished = task.get("finished_at")
        rows.append({"role": str(getattr(task["role"], "value", task["role"])), "model": task["model_id"],
                     "status": task["status"], "error": (task.get("error") or {}).get("type"),
                     "seconds": round((finished - task["started_at"]).total_seconds(), 1) if finished else None,
                     "input_tokens": usage.get("input_tokens"), "output_tokens": usage.get("output_tokens"),
                     "cost_usd": float(usage.get("cost") or 0)})
    return rows


async def resynthesize(loop: AsyncResearchLoop, budget: Any, prompt_trial: Any, objective: str, ledger: EvidenceLedger,
                       constraints: ResearchConstraints, trial: dict[str, Any]) -> dict[str, Any]:
    """One trial job: the ledger synthesized and verified by `loop`'s routes, with what each call did."""
    async def body(job_id: UUID) -> tuple[FinalReport, VerificationReport]:
        report = await loop._synthesize(job_id, objective, ledger, constraints, None)
        return report, await loop._verify(job_id, objective, report, ledger, constraints, None)

    unit = await prompt_trial._unit(loop, budget, constraints, body, objective=objective, trial=trial,
                                    finish=lambda result: {"final_report": result[0].model_dump(mode="json"),
                                                           "verification": result[1].model_dump(mode="json"),
                                                           "evidence_ledger": ledger.to_json()})
    entry = {"trial_job_id": unit.job_id, "error": unit.error, "cost_usd": unit.cost,
             "calls": task_rows(loop.repository, unit.job_id) if unit.job_id else []}
    if unit.result is not None:
        report, verification = unit.result
        entry |= prompt_trial._counts(verification, report)
    return entry


async def run(job_id: UUID, max_usd: float, settings: Any, synthesizer_model: str | None = None) -> dict[str, Any]:
    from contextlib import AsyncExitStack

    from research_loop.benchmarks import load_suite
    from research_loop.evals import parse_judge
    from research_loop.grading import grade_rows, grade_stored, load_stored_output

    prompt_trial = _load("prompt_trial", _SCRIPTS / "prompt_trial.py")
    preflight = _load("study_preflight", _SCRIPTS / "study_preflight.py")
    scout_trial = _load("scout_trial", _SCRIPTS / "scout_trial.py")
    study = _load("settings_study", _STUDY)
    objective, _plan, ledger, stored, _report = await preflight.load_job(settings.database_dsn, job_id)
    case_id = await scout_trial._case_id(settings.database_dsn, job_id)
    manifest, specs = load_suite(study.SUITE)
    case = next(spec for spec in specs if spec.case_id == case_id)
    constraints = ResearchConstraints(blocked_urls=stored["blocked_urls"], benchmark_id=stored["benchmark_id"],
                                      benchmark_case_id=case_id, benchmark_suite=manifest.name, notes=stored["notes"])
    policy, config = study.build(True, 5.0, 1.0, settings, cache_mode="reuse")
    if synthesizer_model:
        policy.routes[ResearchRole.SYNTHESIZER] = replace(policy.routes[ResearchRole.SYNTHESIZER], model=synthesizer_model)
        apply_model_effort(policy)
    synthesizer = policy.routes[ResearchRole.SYNTHESIZER]
    trial = {"name": "resynthesis", "source_job": str(job_id), "synthesizer": synthesizer.model,
             "thinking": synthesizer.thinking, "verifier": policy.routes[ResearchRole.VERIFIER].model,
             "started": datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")}
    budget = prompt_trial.TrialBudget(max_usd, concurrency=1)
    async with AsyncExitStack() as stack:
        repo = await prompt_trial.open_trial_repository(stack, settings.database_dsn)
        entry = await resynthesize(AsyncResearchLoop(policy, config, repo, settings=settings), budget, prompt_trial,
                                   objective, ledger, constraints, trial)
    record = {"job_id": str(job_id), "case_id": case_id, "trial": trial, "max_usd": max_usd, **entry}
    if entry["error"] is None:
        output = await load_stored_output(settings.database_dsn, case, entry["trial_job_id"])
        judge = parse_judge(JUDGE)
        rows = grade_rows(await grade_stored([(case, output)], judges=[judge], name="resynthesis"), [judge])
        record["grade"] = dict(rows[0]["scores"]) if rows else None
        record["judge"] = JUDGE
    return record


def render(record: dict[str, Any]) -> str:
    lines = ["role         model                 status     seconds  input_tok output_tok  cost_usd"]
    for call in record["calls"]:
        lines.append(f"{call['role']:<12} {call['model']:<21} {call['status']:<10} {call['seconds'] or 0:>7} "
                     f"{call['input_tokens'] or 0:>10} {call['output_tokens'] or 0:>10} {call['cost_usd']:>9.4f}"
                     + (f"  {call['error']}" if call["error"] else ""))
    if record["error"]:
        lines.append(f"failed: {record['error']}")
    else:
        lines.append(f"statements {record['statements']}, checked {record['checked']}, unsupported "
                     f"{record['unsupported']}, major {record['major']}, follow-ups {record['follow_ups']}")
        grade = record.get("grade") or {}
        if isinstance(grade.get("rubric"), int | float):
            lines.append(f"rubric {grade['rubric']:.2f} ({record['judge']}, ${float(grade.get('rubric_cost_usd') or 0):.3f})")
    lines.append(f"spent ${record['cost_usd']:.2f} of the ${record['max_usd']:.2f} cap on synthesis and verification;"
                 f" trial job {record['trial_job_id']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    from research_loop.settings import ResearchSettings

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("job_id", type=UUID, help="A stored job with a plan and an evidence ledger")
    parser.add_argument("--max-usd", type=float, required=True, help="Hard cap on synthesis and verification")
    parser.add_argument("--synthesizer", metavar="MODEL", help="Synthesize on this model in place of the study's")
    parser.add_argument("--output", type=Path, help="Record (default: benchmark_outputs/settings_study/resynthesis/)")
    args = parser.parse_args(argv)
    if args.max_usd <= 0:
        parser.error("--max-usd must be positive")
    settings = ResearchSettings.from_env()
    if not settings.database_dsn:
        parser.error("reads the job from Postgres; set DATABASE_URL (see docs/setup.md#postgres)")
    settings = settings.model_copy(update={"benchmark_cache": settings.benchmark_output / "settings_study" / "cache"})
    record = asyncio.run(run(args.job_id, args.max_usd, settings, args.synthesizer))
    output = args.output or settings.benchmark_output / "settings_study" / "resynthesis" / (
        f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(record, indent=2, default=str) + "\n", encoding="utf-8")
    print(render(record))
    print(f"record: {output}")


if __name__ == "__main__":
    main()

"""Can a cheaper scout do the study's scouting? Scouts a stored job's plan again on other models, on the same web.

The stored job, a settings-study run recorded with `record` caching, gives the plan, the constraints,
and the baseline: its own scouts' results. Each arm scouts every planned question again, with the study's
scout limits and budget notes, reusing the recorded searches and fetches (`reuse`), so a call the baseline
made gets the result it got and only differences between the models remain:

- `flash-1`, `flash-2`, `flash-3`: `zai:glm-5.3-flash`, three independent runs, whose spread is how
  much Flash varies from run to run;
- `flash-merged`: the three Flash runs' results together, no new calls, for what running it several
  times adds;
- `glm-low`: the baseline model, `zai:glm-5.3`, at `low` effort in place of `high`.

Every arm, the baseline included, is scored the same two ways:

- from its results, with no model: questions answered, claims, distinct cited sources, and quotes and
  sources the checks could not find in the tool output (a sign of invented evidence);
- by a report: the fixed synthesizer (`openai:gpt-6-luna`, docs/settings-study.md) writes one from the
  arm's scout results alone, and the study's judge grades it against the case's rubric.

The decision rules, set before the trial ran, are in docs/settings-study.md. `--max-usd` is a hard cap
on scouting and synthesis, as in prompt_trial.py; the judge's calls are not in it, about $0.05 a report.
Nothing is stored in Postgres; the record, with results and reports, goes to
benchmark_outputs/settings_study/scout-trial/.

    .venv/bin/python scripts/scout_trial.py <job_id> --max-usd 3.50
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import importlib.util
import json
import sys
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from research_loop.async_orchestrator import AsyncResearchLoop, _dead_result
from research_loop.ledger import EvidenceLedger
from research_loop.policy import ModelPolicy
from research_loop.schemas import (
    ResearchConstraints,
    ResearchResult,
    ResearchRole,
    VerificationReport,
)

_SCRIPTS = Path(__file__).parent
_STUDY = _SCRIPTS.parent / "examples" / "settings_study.py"
FIXED_SYNTHESIZER = "openai:gpt-6-luna"
JUDGE = "openai:gpt-6-sol"


@dataclass(frozen=True)
class Arm:
    name: str
    model: str
    thinking: str | None = None  # None keeps the study scout route's effort


ARMS = (Arm("flash-1", "zai:glm-5.3-flash"), Arm("flash-2", "zai:glm-5.3-flash"),
        Arm("flash-3", "zai:glm-5.3-flash"), Arm("glm-low", "zai:glm-5.3", "low"))
MERGED = ("flash-merged", ("flash-1", "flash-2", "flash-3"))


def _load(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _arm_policy(policy: ModelPolicy, arm: Arm) -> ModelPolicy:
    """The study policy with its scout routes on the arm's model and effort, and nothing else changed."""
    policy = copy.copy(policy)
    policy.routes = dict(policy.routes)
    scout = policy.routes[ResearchRole.SCOUT]
    route = replace(scout, model=arm.model, thinking=arm.thinking if arm.thinking else scout.thinking)
    policy.routes[ResearchRole.SCOUT] = route
    policy.cheap_scout = route
    return policy


def _synthesis_policy(policy: ModelPolicy) -> ModelPolicy:
    policy = copy.copy(policy)
    policy.routes = dict(policy.routes)
    synthesizer = policy.routes[ResearchRole.SYNTHESIZER]
    policy.routes[ResearchRole.SYNTHESIZER] = replace(synthesizer, model=FIXED_SYNTHESIZER, refusal_fallback=None)
    return policy


def baseline_results(ledger: EvidenceLedger, question_ids: list[str]) -> dict[str, ResearchResult]:
    """The stored job's scout result for each question: the first result filed under it."""
    return {qid: ledger.for_question(qid)[0] for qid in question_ids if ledger.for_question(qid)}


def ledger_of(results: list[ResearchResult]) -> EvidenceLedger:
    ledger = EvidenceLedger()
    for result in results:
        ledger.add(result)
    return ledger


def evidence_metrics(results: list[ResearchResult], plan_questions: int) -> dict[str, Any]:
    """What an arm's scout results hold, computed without a model."""
    evidence = [item for result in results for claim in result.claims for item in claim.evidence]
    quoted = [item for item in evidence if item.quote]
    return {
        "questions_answered": len({result.question_id for result in results if result.claims}),
        "questions": plan_questions,
        "claims": sum(len(result.claims) for result in results),
        "cited_sources": len({str(item.source.url) for item in evidence if item.source.url}),
        "quotes": len(quoted),
        "quotes_not_found": sum(item.quote_check == "not_found" for item in quoted),
        "sources_not_found": sum(item.source_check == "not_found" for item in evidence),
    }


def run_metrics(repo: Any, job_id: UUID) -> dict[str, Any]:
    """An arm run's calls: cost, requests, tool calls, dead tool results, and salvage calls."""
    tasks = [task for task in repo.tasks.values() if task.get("job_id") == job_id]
    usage = [task.get("usage") or {} for task in tasks]
    task_ids = {task["id"] for task in tasks}
    events = [event for event in repo.tool_events if event["task_id"] in task_ids]
    return {
        "cost_usd": sum(float(u.get("cost") or 0) for u in usage),
        "requests": sum(int(u.get("requests") or 0) for u in usage),
        "tool_calls": len(events),
        "dead_tool_calls": sum(_dead_result(event.get("result")) for event in events),
        "salvage_calls": sum(bool((task.get("effective_config") or {}).get("salvage")) for task in tasks),
    }


async def scout_arm(loop: AsyncResearchLoop, budget: Any, prompt_trial: Any, plan: Any,
                    constraints: ResearchConstraints) -> tuple[list[ResearchResult], dict[str, Any], str | None]:
    """Every planned question scouted once on the loop's scout route, as one capped trial unit."""
    semaphore = asyncio.Semaphore(loop.config.max_parallel_scouts)
    job: dict[str, UUID] = {}

    async def body(job_id: UUID) -> list[ResearchResult]:
        job["id"] = job_id
        return list(await asyncio.gather(*(loop._run_scout(job_id, question, semaphore, constraints, None)
                                           for question in plan.questions)))

    unit = await prompt_trial._unit(loop, budget, constraints, body)
    metrics = run_metrics(loop.repository, job["id"]) if "id" in job else {"cost_usd": unit.cost}
    return unit.result or [], metrics, unit.error


async def run_trial(job_id: UUID, max_usd: float, settings: Any) -> dict[str, Any]:
    prompt_trial = _load("prompt_trial", _SCRIPTS / "prompt_trial.py")
    preflight = _load("study_preflight", _SCRIPTS / "study_preflight.py")
    study = _load("settings_study", _STUDY)
    from research_loop.benchmark import benchmark_output
    from research_loop.benchmarks import load_suite
    from research_loop.evals import parse_judge
    from research_loop.grading import grade_rows, grade_stored
    from research_loop.repository import InMemoryResearchRepository

    objective, plan, ledger, stored, _report = await preflight.load_job(settings.database_dsn, job_id)
    case_id = await _case_id(settings.database_dsn, job_id)
    _, specs = load_suite(study.SUITE)
    case = next(spec for spec in specs if spec.case_id == case_id)
    constraints = ResearchConstraints(blocked_urls=stored["blocked_urls"], benchmark_id=stored["benchmark_id"],
                                      notes=stored["notes"])
    study_policy, config = study.build(True, 5.0, 1.0, settings, cache_mode="reuse",
                                       budget_notes=(ResearchRole.SCOUT,))
    budget = prompt_trial.TrialBudget(max_usd, concurrency=2)
    question_ids = [question.id for question in plan.questions]

    arms: dict[str, dict[str, Any]] = {}
    baseline = list(baseline_results(ledger, question_ids).values())
    arms["baseline"] = {"model": study_policy.routes[ResearchRole.SCOUT].model, "results": baseline,
                        "run": await _stored_scout_metrics(settings.database_dsn, job_id)}

    async def one(arm: Arm) -> None:
        loop = AsyncResearchLoop(_arm_policy(study_policy, arm), config,
                                 InMemoryResearchRepository(), settings=settings)
        results, metrics, error = await scout_arm(loop, budget, prompt_trial, plan, constraints)
        arms[arm.name] = {"model": arm.model, "thinking": arm.thinking, "results": results, "run": metrics,
                          "error": error}

    await asyncio.gather(*(one(arm) for arm in ARMS))
    merged_name, parts = MERGED
    arms[merged_name] = {"model": "zai:glm-5.3-flash x3", "results": [r for part in parts for r in arms[part]["results"]],
                         "run": {"cost_usd": sum(arms[part]["run"]["cost_usd"] for part in parts)}}

    # One report per arm from its scout results alone, by the fixed synthesizer, graded by the judge.
    writer = AsyncResearchLoop(_synthesis_policy(study_policy), config, InMemoryResearchRepository(),
                               settings=settings)
    outputs = []
    for name, arm in arms.items():
        arm["evidence"] = evidence_metrics(arm["results"], len(question_ids))
        if not arm["results"]:
            continue
        arm_ledger = ledger_of(arm["results"])
        unit = await prompt_trial._unit(writer, budget, constraints, lambda trial_id, led=arm_ledger: writer._synthesize(
            trial_id, objective, led, constraints, None))
        arm["synthesis_usd"], arm["synthesis_error"] = unit.cost, unit.error
        if unit.result is None:
            continue
        arm["report"] = unit.result
        outputs.append((case, benchmark_output(
            case, job_id=name, root_run_id=name, report=unit.result, verification=VerificationReport(),
            ledger=arm_ledger, tool_events=[], tool_calls=0, total_tokens=0, cost_usd=None, attachment_count=0,
            review_reasons=[], tool_args_known=False)))
    judge = parse_judge(JUDGE)
    graded = grade_rows(await grade_stored(outputs, judges=[judge], name="scout-trial"), [judge]) if outputs else []
    for row in graded:
        if row.get("job_id") in arms:
            arms[row["job_id"]]["grade"] = {key: value for key, value in row["scores"].items()}
    judge_usd = sum(float((arm.get("grade") or {}).get("rubric_cost_usd") or 0) for arm in arms.values())
    return {"job_id": str(job_id), "case_id": case_id, "spent_usd": budget.spent, "max_usd": max_usd, "judge_usd": judge_usd,
            "judge": JUDGE, "synthesizer": FIXED_SYNTHESIZER, "arms": arms}


async def _case_id(dsn: str, job_id: UUID) -> str:
    import psycopg

    async with await psycopg.AsyncConnection.connect(dsn) as conn:
        row = await (await conn.execute(
            "select effective_config->'constraints'->>'benchmark_case_id' from research_jobs where id = %s",
            (job_id,))).fetchone()
    if not row or not row[0]:
        raise SystemExit(f"job {job_id} names no benchmark case to grade against")
    return row[0]


async def _stored_scout_metrics(dsn: str, job_id: UUID) -> dict[str, Any]:
    """The stored job's scout calls, salvage included, as run_metrics counts an arm's."""
    import psycopg

    async with await psycopg.AsyncConnection.connect(dsn) as conn:
        row = await (await conn.execute(
            "select coalesce(sum((usage->>'cost')::numeric), 0), coalesce(sum((usage->>'requests')::int), 0),"
            " count(*) filter (where effective_config->>'salvage' = 'true')"
            " from research_tasks where job_id = %s and role = 'scout'", (job_id,))).fetchone()
    return {"cost_usd": float(row[0]), "requests": int(row[1]), "salvage_calls": int(row[2])}


def render(record: dict[str, Any]) -> str:
    lines = ["arm           answered claims sources quotes_nf sources_nf salvage requests  scout_usd  grade"]
    for name, arm in record["arms"].items():
        e, r = arm["evidence"], arm["run"]
        grade = arm.get("grade") or {}
        score = f"{grade['rubric']:.2f}" if isinstance(grade.get("rubric"), int | float) else "-"
        lines.append(f"{name:<13} {e['questions_answered']:>4}/{e['questions']:<3} {e['claims']:>6} {e['cited_sources']:>7} "
                     f"{e['quotes_not_found']:>4}/{e['quotes']:<4} {e['sources_not_found']:>10} "
                     f"{r.get('salvage_calls', '-'):>7} {r.get('requests', '-'):>8} {r['cost_usd']:>10.3f}  {score}"
                     + (f"  error: {arm['error']}" if arm.get("error") else ""))
    lines.append(f"spent ${record['spent_usd']:.2f} of the ${record['max_usd']:.2f} cap on scouting and synthesis,"
                 f" and ${record['judge_usd']:.2f} on the judge")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    from research_loop.settings import ResearchSettings

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("job_id", type=UUID, help="A settings-study job recorded with `record` caching")
    parser.add_argument("--max-usd", type=float, required=True, help="Hard cap on scouting and synthesis")
    parser.add_argument("--output", type=Path, help="Trial record (default: benchmark_outputs/settings_study/scout-trial/)")
    args = parser.parse_args(argv)
    if args.max_usd <= 0:
        parser.error("--max-usd must be positive")
    settings = ResearchSettings.from_env()
    if not settings.database_dsn:
        parser.error("reads the job from Postgres; set DATABASE_URL (see docs/setup.md#postgres)")
    settings = settings.model_copy(update={"benchmark_cache": settings.benchmark_output / "settings_study" / "cache"})
    record = asyncio.run(run_trial(args.job_id, args.max_usd, settings))
    output = args.output or settings.benchmark_output / "settings_study" / "scout-trial" / (
        f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(record, indent=2, default=lambda value: value.model_dump(mode="json")
                                 if hasattr(value, "model_dump") else str(value)) + "\n", encoding="utf-8")
    print(render(record))
    print(f"record: {output}")


if __name__ == "__main__":
    main()

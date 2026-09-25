"""Grade finished research jobs stored in Postgres, without running anything again.

`research-bench` grades each run as it finishes. The settings study (docs/settings-study.md) also
grades runs made earlier, repeatedly, and with judges added later: reference runs, replays, and
their reports. `load_stored_output` rebuilds from Postgres the `BenchmarkOutput` a live run would
have produced, through the same `benchmark.benchmark_output`, and `grade_stored` scores those
outputs with pydantic-evals: the default metrics plus any `RubricJudge`s, `repeat` times each.

A job's tool arguments are hashes in `research_tool_events`. A job whose tasks all kept a transcript
(`--capture`) is rebuilt from its real tool calls; otherwise the metrics that need them are skipped.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic_evals import Case, Dataset
from pydantic_evals.reporting import EvaluationReport

from .benchmark import benchmark_output
from .benchmarks import BenchmarkCaseSpec, load_suite
from .evals import (
    BenchmarkOutput,
    RubricJudge,
    default_evaluators,
    judge_records,
    parse_judge,
)
from .ledger import EvidenceLedger
from .orchestrator import RESEARCH_GRAPH_VERSION
from .schemas import FinalReport, VerificationReport
from .telemetry import extract_tool_events

# Case metadata key naming the stored job a case grades; several jobs can grade one question.
JOB_KEY = "stored_job_id"


@dataclass
class StoredJob:
    """The rows a finished job left in Postgres that grading reads."""

    job_id: str
    root_run_id: str
    status: str
    effective_config: dict[str, Any]
    report: dict[str, Any] | None
    verification: dict[str, Any] | None
    ledger: dict[str, Any] | None
    review_reasons: list[str] | None
    # Per task: its usage, whether it has a transcript, and its stored tool events.
    task_usage: list[dict[str, Any]]
    tool_events: list[dict[str, Any]]
    transcripts: dict[str, list[Any]]
    tasks_with_tools: set[str]
    attachment_count: int


async def fetch_stored_job(dsn: str, job_id: UUID) -> StoredJob:
    import psycopg

    async with await psycopg.AsyncConnection.connect(dsn) as conn:
        row = await (await conn.execute(
            "select root_run_id, status, effective_config, final_report, verification, evidence_ledger, review_reasons"
            " from research_jobs where id = %s", (job_id,))).fetchone()
        if row is None:
            raise LookupError(f"no research job {job_id}")
        usage = await (await conn.execute(
            "select usage from research_tasks where job_id = %s", (job_id,))).fetchall()
        events = await (await conn.execute(
            "select e.task_id, e.tool_name, e.args from research_tool_events e"
            " join research_tasks t on t.id = e.task_id where t.job_id = %s order by t.started_at, e.call_index",
            (job_id,))).fetchall()
        transcripts = await (await conn.execute(
            "select m.task_id, m.messages from research_task_messages m"
            " join research_tasks t on t.id = m.task_id where t.job_id = %s", (job_id,))).fetchall()
        attachments = await (await conn.execute(
            "select count(*) from research_attachments where job_id = %s", (job_id,))).fetchone()
    root_run_id, status, config, report, verification, ledger, reasons = row
    return StoredJob(
        job_id=str(job_id), root_run_id=str(root_run_id), status=status, effective_config=config or {},
        report=report, verification=verification, ledger=ledger, review_reasons=reasons,
        task_usage=[u or {} for (u,) in usage],
        tool_events=[{"tool_name": name, "args": args} for _, name, args in events],
        transcripts={str(task_id): messages for task_id, messages in transcripts},
        tasks_with_tools={str(task_id) for task_id, _, _ in events},
        attachment_count=attachments[0],
    )


def stored_output(case: BenchmarkCaseSpec, job: StoredJob) -> BenchmarkOutput:
    """The `BenchmarkOutput` a live run of `case` would have produced, from its stored rows."""
    from pydantic_ai.messages import ModelMessagesTypeAdapter

    if job.status != "succeeded" or not all((job.report, job.verification, job.ledger is not None)):
        raise LookupError(f"research job {job.job_id} is {job.status}; only a finished job with a report can be graded")
    report = FinalReport.model_validate(job.report)
    verification = VerificationReport.model_validate(job.verification)
    ledger = EvidenceLedger.from_json(job.ledger or {})
    # Real tool arguments only when every task that called a tool kept its transcript.
    tool_args_known = job.tasks_with_tools <= set(job.transcripts)
    if tool_args_known:
        events = [event.model_dump(mode="json")
                  for messages in job.transcripts.values()
                  for event in extract_tool_events(ModelMessagesTypeAdapter.validate_python(messages))]
    else:
        events = job.tool_events
    # As the live job's spend ledger: unknown once any billed call could not be priced.
    costs = [u.get("cost") for u in job.task_usage if u.get("requests")]
    cost = None if any(c is None for c in costs) else float(sum(Decimal(str(c)) for c in costs))
    return benchmark_output(
        case,
        job_id=job.job_id,
        root_run_id=job.root_run_id,
        report=report,
        verification=verification,
        ledger=ledger,
        tool_events=events,
        tool_calls=sum(int(u.get("tool_calls") or 0) for u in job.task_usage),
        total_tokens=sum(int(u.get("total_tokens") or 0) for u in job.task_usage),
        cost_usd=cost,
        attachment_count=job.attachment_count,
        review_reasons=job.review_reasons or [],
        tool_args_known=tool_args_known,
        graph_version=(job.effective_config.get("orchestrator") or {}).get("graph_version", RESEARCH_GRAPH_VERSION),
    )


async def load_stored_output(dsn: str, case: BenchmarkCaseSpec, job_id: UUID) -> BenchmarkOutput:
    return stored_output(case, await fetch_stored_job(dsn, job_id))


async def grade_stored(
    outputs: Sequence[tuple[BenchmarkCaseSpec, BenchmarkOutput]],
    *,
    judges: Sequence[RubricJudge] = (),
    repeat: int = 1,
    name: str = "stored",
    max_concurrency: int | None = 4,
) -> EvaluationReport:
    """Score outputs already produced; the task only hands each case its stored output back."""
    cases = [
        Case(name=f"{spec.benchmark_id}:{spec.case_id}:{output.job_id}",
             inputs=spec.model_copy(update={"metadata": {**spec.metadata, JOB_KEY: output.job_id}}),
             expected_output=spec.expected_answer,
             metadata={"benchmark": spec.benchmark_id, "job_id": output.job_id})
        for spec, output in outputs
    ]
    by_job = {output.job_id: output for _, output in outputs}

    async def task(spec: BenchmarkCaseSpec) -> BenchmarkOutput:
        return by_job[spec.metadata[JOB_KEY]]

    dataset = Dataset(name="research_loop", cases=cases, evaluators=[*default_evaluators(), *judges])
    return await dataset.evaluate(task, name=name, repeat=repeat, max_concurrency=max_concurrency, progress=False)


def grade_rows(report: EvaluationReport, judges: Sequence[RubricJudge]) -> list[dict[str, Any]]:
    """One JSON row per graded case and repeat: IDs and scores, with rubric reasons (point numbers only)."""
    judge_info = judge_records(judges)
    rows = []
    for case in report.cases:
        rows.append({
            "case": getattr(case, "source_case_name", None) or case.name,
            "run": case.name,
            "job_id": (case.metadata or {}).get("job_id"),
            "scores": {key: result.value for key, result in case.scores.items()},
            "reasons": {key: result.reason for key, result in case.scores.items()
                        if result.reason and any(key == j.name for j in judges)},
            "judges": judge_info,
            **({"evaluator_failures": sorted(f.name for f in case.evaluator_failures)} if case.evaluator_failures else {}),
        })
    for failure in report.failures:
        # The exception type only: messages can carry provider response bodies.
        rows.append({"case": failure.name, "error": failure.error_message.split(":")[0]})
    return rows


def jobs_from_record(path: Path) -> list[tuple[str, UUID]]:
    """(case ID, job ID) for each succeeded run in a study step record; failed runs have no job to grade."""
    record = json.loads(path.read_text(encoding="utf-8"))
    return [(run["case_id"], UUID(run["job_id"])) for run in record["runs"] if run.get("status") == "succeeded"]


def _parse_job(value: str) -> tuple[str, UUID]:
    case_id, sep, job_id = value.partition("=")
    if not sep or not case_id:
        raise argparse.ArgumentTypeError("expected CASE_ID=JOB_ID")
    try:
        return case_id, UUID(job_id)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"not a job ID: {job_id!r}") from exc


async def _grade(args: argparse.Namespace, dsn: str) -> list[dict[str, Any]]:
    _, specs = load_suite(args.suite)
    by_id = {spec.case_id: spec for spec in specs}
    if unknown := sorted({case_id for case_id, _ in args.job} - set(by_id)):
        raise SystemExit(f"not in {args.suite}: {', '.join(unknown)}")
    outputs = [(by_id[case_id], await load_stored_output(dsn, by_id[case_id], job_id)) for case_id, job_id in args.job]
    report = await grade_stored(outputs, judges=args.judge, repeat=args.repeat, name=args.suite.stem)
    # Scores and averages only: inputs, outputs, and reasons can hold benchmark text.
    report.print(include_input=False, include_output=False, include_durations=False, include_averages=True,
                 include_errors=False, include_evaluator_failures=False)
    return grade_rows(report, args.judge)


def main(argv: list[str] | None = None) -> None:
    from .settings import ResearchSettings

    parser = argparse.ArgumentParser(
        prog="research-grade",
        description="Grade finished research jobs stored in Postgres against a suite's cases, without rerunning them",
    )
    parser.add_argument("suite", type=Path, help="Suite TOML whose cases the jobs answered")
    parser.add_argument("--job", type=_parse_job, action="append", default=[], metavar="CASE_ID=JOB_ID",
                        help="A stored job and the case it answered; repeat for more")
    parser.add_argument("--jobs-from", type=Path, action="append", default=[], metavar="RECORD",
                        help="A study step record (examples/settings_study.py) whose succeeded runs to grade")
    parser.add_argument("--judge", type=parse_judge, action="append", default=[], metavar="[NAME=]MODEL",
                        help="Add a rubric judge (calls the model); NAME keeps a second judge's scores apart")
    parser.add_argument("--repeat", type=int, default=1, help="Grade each job this many times (default 1)")
    parser.add_argument("--output", type=Path, help="JSONL of scores (default: benchmark_outputs/grades/<time>.jsonl)")
    args = parser.parse_args(argv)
    try:
        args.job += [pair for record in args.jobs_from for pair in jobs_from_record(record)]
    except (OSError, ValueError, KeyError) as exc:
        parser.error(f"cannot read a step record: {type(exc).__name__}: {exc}")
    if not args.job:
        parser.error("name jobs to grade with --job or --jobs-from")
    if args.repeat < 1:
        parser.error("--repeat must be at least 1")
    if len({judge.name for judge in args.judge}) != len(args.judge):
        parser.error("two judges share a name; give the second one NAME=MODEL")
    settings = ResearchSettings.from_env()
    if not settings.database_dsn:
        parser.error("research-grade reads jobs from Postgres; set DATABASE_URL (see docs/setup.md#postgres)")
    rows = asyncio.run(_grade(args, settings.database_dsn))
    output = args.output or settings.benchmark_output / "grades" / f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}.jsonl"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("".join(json.dumps(row, ensure_ascii=False, default=str) + "\n" for row in rows), encoding="utf-8")
    print(f"wrote {len(rows)} rows to {output}")


if __name__ == "__main__":
    main()

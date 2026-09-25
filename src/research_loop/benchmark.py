from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Callable
from contextlib import AsyncExitStack
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from typing import Any
from uuid import UUID, uuid4

from pydantic_evals import Case

from .acquisition import SourcePolicy
from .attachments import AttachmentMode
from .benchmarks import BenchmarkCaseSpec, BenchmarkOutputMode, load_suite
from .db import open_migrated_pool
from .evals import (
    EVALUATOR_VERSION,
    BenchmarkOutput,
    RubricJudge,
    judge_records,
    make_dataset,
    parse_judge,
)
from .experiment import build_manifest, write_manifest
from .ledger import EvidenceLedger, sources_markdown, strip_inline_citations
from .observability import configure_logfire
from .orchestrator import RESEARCH_GRAPH_VERSION, ResearchConfig, ResearchLoop
from .policy import POLICY_PRESETS, get_policy
from .quotes import find_urls
from .repository import (
    CapturingResearchRepository,
    InMemoryResearchRepository,
    PostgresResearchRepository,
)
from .schemas import (
    FinalReport,
    ResearchConstraints,
    ResearchRole,
    SourceRef,
    VerificationReport,
    is_research_tool,
)
from .settings import ResearchSettings
from .synthetic import SyntheticResearchLoop
from .tools import ResearchToolMode

EXACT_ANSWER_RE = re.compile(r"(?im)^\s*Exact Answer\s*:\s*(.+?)\s*$")
EVAL_AWARENESS_TERMS = (
    "browsecomp",
    "simple-evals",
    "simple_evals",
    "browse_comp_test_set",
    "canary string",
    "decrypt benchmark",
    "benchmark answer",
    "ground truth dataset",
)


def _load_legacy_cases(path: Path) -> list[BenchmarkCaseSpec]:
    data = json.loads(path.read_text(encoding="utf-8"))
    cases: list[BenchmarkCaseSpec] = []
    for index, item in enumerate(data):
        if isinstance(item, str):
            cases.append(
                BenchmarkCaseSpec(
                    benchmark_id="legacy",
                    case_id=f"case-{index + 1}",
                    objective=item,
                )
            )
        else:
            cases.append(
                BenchmarkCaseSpec(
                    benchmark_id=str(item.get("benchmark_id", "legacy")),
                    case_id=str(item.get("name", f"case-{index + 1}")),
                    objective=item["objective"],
                    expected_answer=item.get("expected_answer"),
                    output_mode=item.get("output_mode", "report"),
                    metadata=item.get("metadata") or {},
                )
            )
    return cases


def _load_cases(path: Path) -> tuple[str, list[BenchmarkCaseSpec]]:
    if path.suffix.lower() == ".toml":
        manifest, cases = load_suite(path)
        return manifest.name, cases
    return path.stem, _load_legacy_cases(path)


def _sum_usage(repo: CapturingResearchRepository) -> tuple[int, int]:
    tool_calls = 0
    total_tokens = 0
    for task in repo.tasks.values():
        usage = task.get("usage") or {}
        tool_calls += int(usage.get("tool_calls") or 0)
        total_tokens += int(usage.get("total_tokens") or 0)
    return tool_calls, total_tokens


def _export_component(value: str) -> str:
    """Use benchmark identifiers as one safe path component."""
    if len(value) <= 80 and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value):
        return value
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-")[:80] or "item"
    suffix = hashlib.sha256(value.encode()).hexdigest()[:8]
    return f"{cleaned}-{suffix}"


def _extract_exact_answer(answer: str, mode: BenchmarkOutputMode) -> str | None:
    if mode is not BenchmarkOutputMode.SHORT_ANSWER:
        return None
    # Reports cite sources inline as [sN]; a graded answer must not carry them.
    matches = EXACT_ANSWER_RE.findall(answer)
    if matches:
        return strip_inline_citations(matches[-1]).strip()
    stripped = strip_inline_citations(answer).strip()
    if "\n" not in stripped and len(stripped) <= 300:
        return stripped
    return None


def _flatten_event(event: dict[str, Any]) -> str:
    return json.dumps(
        {"args": event.get("args"), "result": event.get("result")},
        ensure_ascii=False,
        default=str,
    ).casefold()


def _refused_entry(result: Any) -> str | None:
    """The blocked-source entry a fetch tool named when it refused a request, if it did."""
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except ValueError:
            return None
    if not isinstance(result, dict):
        return None
    return result.get("blocked") if result.get("error") == "BlockedSource" else result.get("blocked_source")


def _audit_blocked_sources(events: list[dict[str, Any]], sources: list[SourceRef],
                           policy: SourcePolicy) -> dict[str, list[str]]:
    """Sort a case's contact with blocked sources by kind, as blocked-source entries.

    Refused fetches were stopped by the tools, and blocked sources in search or metadata results
    were only seen; neither breaks compliance. Completed fetches (possible only with tools the
    application does not control) and blocked sources cited as evidence do. Raw tool arguments
    and results are read here, in memory; Postgres keeps only their hashes.
    """
    refused, completed, seen = set(), set(), set()
    for event in events:
        name = str(event.get("tool_name", "")).lower()
        if entry := _refused_entry(event.get("result")):
            refused.add(entry)
        elif any(token in name for token in ("fetch", "page", "url")):
            args = event.get("args")
            url = args.get("url") if isinstance(args, dict) else None
            if isinstance(url, str) and (entry := policy.blocks(url)):
                completed.add(entry)
        else:
            text = json.dumps(event.get("result"), ensure_ascii=False, default=str)
            seen.update(entry for url in find_urls(text) if (entry := policy.blocks(url)))
    cited = {entry for source in sources if source.url and (entry := policy.blocks(str(source.url)))}
    return {"blocked_fetches_refused": sorted(refused), "blocked_fetches_completed": sorted(completed),
            "blocked_sources_in_search": sorted(seen), "blocked_sources_cited": sorted(cited)}


def _integrity_flags(
    case: BenchmarkCaseSpec,
    queries: list[str],
    events: list[dict[str, Any]],
) -> list[str]:
    if not case.leakage_sensitive:
        return []
    flags: list[str] = []
    haystacks = [q.casefold() for q in queries]
    haystacks.extend(_flatten_event(event) for event in events)
    for term in EVAL_AWARENESS_TERMS:
        if any(term in value for value in haystacks):
            flags.append(f"benchmark-integrity trace contains {term!r}")
    return flags


async def _run_policy_case(
    policy_name: str,
    case: BenchmarkCaseSpec,
    *,
    export_dir: Path | None = None,
    attachment_mode: AttachmentMode = AttachmentMode.NORMALIZED,
    repository_mode: str = "memory",
    pool: Any | None = None,
    suite_name: str | None = None,
    on_job_created: Callable[[UUID, UUID], None] | None = None,
    settings: ResearchSettings | None = None,
    budget_notes: tuple[ResearchRole, ...] = (),
) -> BenchmarkOutput:
    if repository_mode == "postgres" and pool is None:
        raise ValueError("Postgres benchmark mode requires a connection pool")
    backend = PostgresResearchRepository(pool) if repository_mode == "postgres" else InMemoryResearchRepository()
    root_run_id = uuid4()
    repo = CapturingResearchRepository(
        backend,
        on_job_created=(lambda job_id: on_job_created(job_id, root_run_id)) if on_job_created else None,
    )
    loop_class = SyntheticResearchLoop if policy_name == "synthetic" else ResearchLoop
    loop = loop_class(
        get_policy(policy_name, model_overrides=settings.model_overrides if settings else None),
        _benchmark_config(attachment_mode, budget_notes),
        repository=repo,
        settings=settings,
    )
    outcome = await loop.run(
        case.render_objective(),
        root_run_id=root_run_id,
        constraints=ResearchConstraints(
            blocked_urls=case.blocked_urls,
            attachment_paths=case.attachments,
            benchmark_id=case.benchmark_id,
            benchmark_case_id=case.case_id,
            benchmark_suite=suite_name,
            notes=[
                "Do not use benchmark-answer datasets, evaluator artifacts, or decrypted benchmark mirrors as research sources."
            ] if case.leakage_sensitive else [],
        ),
    )

    if export_dir is not None:
        policy_dir = (
            export_dir / _export_component(policy_name) / _export_component(case.benchmark_id)
        )
        policy_dir.mkdir(parents=True, exist_ok=True)
        idx = case.metadata.get("idx")
        stem = f"idx-{idx}" if idx is not None else case.case_id
        filename = f"{_export_component(str(stem))}.md"
        (policy_dir / filename).write_text(outcome.report.answer + sources_markdown(outcome.report, outcome.ledger),
                                           encoding="utf-8")

    tool_calls, total_tokens = _sum_usage(repo)
    return benchmark_output(
        case,
        job_id=str(outcome.job_id),
        root_run_id=str(root_run_id),
        report=outcome.report,
        verification=outcome.verification,
        ledger=outcome.ledger,
        tool_events=repo.tool_events,
        tool_calls=tool_calls,
        total_tokens=total_tokens,
        # The job's own spend ledger: None once any billed call could not be priced.
        cost_usd=None if outcome.cost_usd is None else float(outcome.cost_usd),
        attachment_count=len(outcome.attachments.records) if outcome.attachments else 0,
        review_reasons=outcome.review_reasons,
    )


def reader_text(report: FinalReport, ledger: EvidenceLedger) -> str:
    """The report as a reader reads it, in Markdown: answer, key statements, caveats, and cited sources.

    Leaves out the verifier's verdicts, so a grader judges what the report says, not how it was checked.
    """
    parts = [report.answer]
    if report.claims:
        parts.append("## Key statements\n\n" + "\n".join(f"{n}. {c.statement}" for n, c in enumerate(report.claims, 1)))
    if report.caveats:
        parts.append("## Caveats\n\n" + "\n".join(f"- {caveat}" for caveat in report.caveats))
    return "\n\n".join(parts) + sources_markdown(report, ledger)


def benchmark_output(
    case: BenchmarkCaseSpec,
    *,
    job_id: str,
    root_run_id: str,
    report: FinalReport,
    verification: VerificationReport,
    ledger: EvidenceLedger,
    tool_events: list[dict[str, Any]],
    tool_calls: int,
    total_tokens: int,
    cost_usd: float | None,
    attachment_count: int,
    review_reasons: list[str],
    tool_args_known: bool = True,
    graph_version: str = RESEARCH_GRAPH_VERSION,
) -> BenchmarkOutput:
    """What the evaluators score, from a finished run: a live one, or one reloaded from Postgres (grading.py).

    `tool_events` are dicts with `tool_name` and `args`. With `tool_args_known` false, the arguments
    are hashes, as Postgres keeps them without a transcript, so search queries, fetched URLs, and the
    checks built on them are left out rather than scored from hashes.
    """
    sources = ledger.sources()
    source_urls = [str(s.url) for s in sources if s.url is not None]
    primary_urls = [
        str(s.url)
        for s in sources
        if s.url is not None and s.source_type in {"primary", "official", "paper", "documentation"}
    ]
    attachment_ids_cited = sorted({s.attachment_id for s in sources if s.attachment_id})
    evidence = [item for claim in ledger.claims() for item in claim.evidence]
    quote_checks = [item.quote_check for item in evidence if item.quote_check]
    source_checks = [item.source_check for item in evidence if item.source_check]
    checks = verification.checks
    unsupported = [c for c in checks if not c.supported]
    major = [c for c in unsupported if c.severity == "major"]
    research_tool_calls = sum(1 for event in tool_events if is_research_tool(str(event.get("tool_name", ""))))
    attachment_tool_calls = sum(1 for event in tool_events if _is_attachment_event(event))
    search_queries = _extract_search_queries(tool_events) if tool_args_known else []
    audit = _audit_blocked_sources(tool_events if tool_args_known else [], sources, SourcePolicy(tuple(case.blocked_urls)))

    return BenchmarkOutput(
        benchmark_id=case.benchmark_id,
        case_id=case.case_id,
        graph_version=graph_version,
        job_id=job_id,
        root_run_id=root_run_id,
        answer=report.answer,
        extracted_answer=_extract_exact_answer(report.answer, case.output_mode),
        source_urls=source_urls,
        primary_source_urls=primary_urls,
        unsupported_claims=len(unsupported),
        major_unsupported_claims=len(major),
        total_claims=len(checks),
        tool_calls=tool_calls,
        research_tool_calls=research_tool_calls,
        total_tokens=total_tokens,
        cost_usd=cost_usd,
        search_queries=search_queries,
        **audit,
        integrity_flags=_integrity_flags(case, search_queries, tool_events) if tool_args_known else [],
        attachment_count=attachment_count,
        attachment_tool_calls=attachment_tool_calls,
        attachment_ids_cited=attachment_ids_cited,
        quotes=len(quote_checks),
        quotes_not_found=quote_checks.count("not_found"),
        sources=len(source_checks),
        sources_not_found=source_checks.count("not_found"),
        review_reasons=review_reasons,
        report_text=reader_text(report, ledger),
        tool_args_known=tool_args_known,
    )


def _extract_search_queries(events: list[dict[str, Any]]) -> list[str]:
    queries: list[str] = []
    for event in events:
        if "search" not in str(event.get("tool_name", "")).lower():
            continue
        args = event.get("args")
        if isinstance(args, dict):
            for key in ("query", "q"):
                value = args.get(key)
                if isinstance(value, str):
                    queries.append(value)
            values = args.get("queries")
            if isinstance(values, list):
                queries.extend(str(v) for v in values if isinstance(v, str))
        elif isinstance(args, str):
            queries.append(args)
    return queries


def _is_attachment_event(event: dict[str, Any]) -> bool:
    return "attachment" in str(event.get("tool_name", "")).lower()


# Safe numbers behind a case's scores: counts, tokens, and cost, never prompts, answers, or URLs.
_MEASURES = ("total_claims", "unsupported_claims", "major_unsupported_claims", "tool_calls", "research_tool_calls",
             "total_tokens", "cost_usd", "quotes", "quotes_not_found", "sources", "sources_not_found",
             "attachment_count", "attachment_tool_calls")


def _benchmark_config(attachment_mode: AttachmentMode,
                      budget_notes: tuple[ResearchRole, ...] = ()) -> ResearchConfig:
    """The run configuration of every benchmark case: normalized tools, caches off."""
    return ResearchConfig(tool_mode=ResearchToolMode.NORMALIZED, attachment_mode=attachment_mode,
                          scholarly_cache_mode="off", budget_notes=budget_notes)


def _case_name(spec: BenchmarkCaseSpec) -> str:
    return f"{spec.benchmark_id}:{spec.case_id}"


def _measures(output: BenchmarkOutput) -> dict[str, Any]:
    # Integrity flags are this module's own fixed phrases, safe to keep verbatim.
    return {name: getattr(output, name) for name in _MEASURES} | {"integrity_flags": output.integrity_flags}


def _policy_summary(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """One policy's results: case outcomes, spend, and each metric's mean over the cases it applied to."""
    succeeded = [run for run in runs if run.get("status") == "succeeded"]
    costs = [run["measures"]["cost_usd"] for run in succeeded if "measures" in run]
    scores: dict[str, list[float]] = defaultdict(list)
    for run in succeeded:
        for name, value in run.get("scores", {}).items():
            scores[name].append(value)
    return {
        "cases": len(runs),
        "succeeded": len(succeeded),
        "failed": sum(run.get("status") == "failed" for run in runs),
        "needs_review": sum(bool(run.get("review_reasons")) for run in succeeded),
        # Spend of succeeded cases; unknown once any of them had an unpriced call.
        "succeeded_cost_usd": None if not costs or None in costs else round(sum(costs), 6),
        "scores": {name: {"mean": round(mean(values), 6), "cases": len(values)} for name, values in sorted(scores.items())},
    }


async def run_benchmark(
    suite_path: Path,
    *,
    policies: list[str],
    max_concurrency: int,
    export_dir: Path | None = None,
    attachment_mode: AttachmentMode = AttachmentMode.NORMALIZED,
    repository_mode: str = "memory",
    manifest_path: Path | None = None,
    settings: ResearchSettings | None = None,
    max_cases: int | None = None,
    budget_notes: tuple[ResearchRole, ...] = (),
    judges: tuple[RubricJudge, ...] = (),
) -> Path:
    settings = settings or ResearchSettings.from_env()
    configure_logfire(settings)
    suite_name, specs = _load_cases(suite_path)
    if not specs:
        raise ValueError(f"suite {suite_name!r} contains no cases")
    if max_cases is not None:
        if max_cases < 1:
            raise ValueError("max_cases must be at least 1")
        specs = specs[:max_cases]
    if max_concurrency < 1:
        raise ValueError("max_concurrency must be at least 1")
    if "synthetic" in policies and len(policies) > 1:
        raise ValueError("run synthetic separately from real model policies")
    if repository_mode not in {"memory", "postgres"}:
        raise ValueError("repository_mode must be memory or postgres")
    if repository_mode == "postgres" and not settings.database_dsn:
        raise ValueError("DATABASE_URL is required for Postgres benchmark mode")

    if manifest_path is None:
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        safe_name = re.sub(r"[^A-Za-z0-9_-]+", "-", suite_name)
        manifest_path = settings.benchmark_output / f"{safe_name}-{timestamp}.json"
    manifest = build_manifest(
        suite_path, suite_name, specs, policies,
        attachment_mode=attachment_mode.value,
        tool_mode=ResearchToolMode.NORMALIZED.value,
        repository_mode=repository_mode,
        evaluator_version=EVALUATOR_VERSION,
        run_config=_benchmark_config(attachment_mode, budget_notes).snapshot(),
        model_overrides=settings.model_overrides,
    )
    manifest["summary"] = {}
    if judges:  # rubric scores are comparable only under one judge model and prompt version
        manifest["judges"] = judge_records(judges)
    write_manifest(manifest_path, manifest)

    cases = [
        Case(
            name=_case_name(spec),
            inputs=spec,
            expected_output=spec.expected_answer,
            metadata={"benchmark": spec.benchmark_id, **spec.metadata},
        )
        for spec in specs
    ]
    dataset = make_dataset(cases, judges=judges)
    baseline = None
    failed_cases = 0
    run_records: dict[tuple[str, str], dict[str, Any]] = {}  # (policy, case name) -> manifest run record

    try:
        async with AsyncExitStack() as stack:
            pool = None
            if repository_mode == "postgres":
                pool = await open_migrated_pool(stack, settings.database_dsn)
            for policy_name in policies:
                async def task(case: BenchmarkCaseSpec, _policy: str = policy_name) -> BenchmarkOutput:
                    run_record: dict[str, str] | None = None

                    def record_job(job_id: UUID, root_run_id: UUID) -> None:
                        nonlocal run_record
                        run_record = {
                            "policy": _policy,
                            "benchmark_id": case.benchmark_id,
                            "case_id": case.case_id,
                            "job_id": str(job_id),
                            "root_run_id": str(root_run_id),
                            "status": "running",
                        }
                        manifest["runs"].append(run_record)
                        run_records[(_policy, _case_name(case))] = run_record
                        write_manifest(manifest_path, manifest)

                    try:
                        output = await _run_policy_case(
                            _policy, case, export_dir=export_dir,
                            attachment_mode=attachment_mode,
                            repository_mode=repository_mode,
                            pool=pool,
                            suite_name=suite_name,
                            on_job_created=record_job,
                            settings=settings,
                            budget_notes=budget_notes,
                        )
                        if run_record:
                            run_record["status"] = "succeeded"
                            run_record["measures"] = _measures(output)
                            run_record["review_reasons"] = output.review_reasons
                            if case.blocked_urls:  # counts only: the entries are case inputs
                                run_record["blocked_sources"] = {
                                    kind: len(getattr(output, f"blocked_{kind}"))
                                    for kind in ("fetches_refused", "fetches_completed", "sources_in_search", "sources_cited")
                                }
                        return output
                    except (Exception, asyncio.CancelledError) as exc:
                        if run_record is None:  # failed before a job existed
                            run_record = {
                                "policy": _policy,
                                "benchmark_id": case.benchmark_id,
                                "case_id": case.case_id,
                                "job_id": None,
                                "root_run_id": None,
                            }
                            manifest["runs"].append(run_record)
                            run_records[(_policy, _case_name(case))] = run_record
                        run_record["status"] = "failed"
                        run_record["error"] = type(exc).__name__
                        raise
                    finally:
                        write_manifest(manifest_path, manifest)

                report = await dataset.evaluate(
                    task,
                    name=f"{suite_name}-{policy_name}",
                    max_concurrency=max_concurrency,
                    metadata={
                        "suite": suite_name,
                        "policy": policy_name,
                        "graph_version": RESEARCH_GRAPH_VERSION,
                        "tool_mode": ResearchToolMode.NORMALIZED.value,
                        "attachment_mode": attachment_mode.value,
                    },
                )
                # Failure messages can carry provider response bodies; the manifest keeps the error type.
                report.print(
                    baseline=baseline,
                    include_output=False,
                    include_input=False,
                    include_durations=True,
                    include_averages=True,
                    include_errors=False,
                )
                # Keep every score after the process exits: a metric that did not apply has no entry.
                for case in report.cases:
                    if record := run_records.get((policy_name, case.name)):
                        record["scores"] = {name: result.value for name, result in case.scores.items()}
                        record["duration_seconds"] = round(case.task_duration, 3)
                        if case.evaluator_failures:
                            record["evaluator_failures"] = sorted(failure.name for failure in case.evaluator_failures)
                manifest["summary"][policy_name] = _policy_summary(
                    [run for run in manifest["runs"] if run["policy"] == policy_name]
                )
                write_manifest(manifest_path, manifest)
                if report.failures:
                    print(f"{len(report.failures)} of {len(specs)} cases failed for policy {policy_name}.")
                failed_cases += len(report.failures)
                if baseline is None:
                    baseline = report
        manifest["failed_cases"] = failed_cases
        if not failed_cases:
            manifest["status"] = "completed"
        elif failed_cases == len(specs) * len(policies):
            manifest["status"] = "failed"
        else:
            manifest["status"] = "completed_with_failures"
    except (Exception, asyncio.CancelledError) as exc:  # Ctrl-C arrives as cancellation
        manifest["status"] = "failed"
        manifest["error"] = type(exc).__name__
        raise
    finally:
        manifest["finished_at"] = datetime.now(UTC).isoformat()
        write_manifest(manifest_path, manifest)
    return manifest_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark research-loop model policies")
    parser.add_argument(
        "suite",
        type=Path,
        help="TOML benchmark suite manifest or legacy JSON case list",
    )
    parser.add_argument(
        "--policies",
        nargs="+",
        default=["synthetic"],
        choices=sorted(POLICY_PRESETS),
    )
    parser.add_argument("--max-concurrency", type=int, default=None)
    parser.add_argument("--paid", action="store_true", help="Allow policies that call paid model providers")
    parser.add_argument("--max-cases", type=int, default=None, help="Limit selected suite cases")
    parser.add_argument("--all-cases", action="store_true", help="Run the entire selected suite")
    parser.add_argument(
        "--repository", choices=("memory", "postgres"), default="memory",
        help="Persist jobs and task telemetry to Postgres or keep disposable in-memory runs",
    )
    parser.add_argument("--manifest-output", type=Path, default=None)
    parser.add_argument("--persist", action="store_true", help="Alias for --repository postgres")
    parser.add_argument(
        "--attachment-mode",
        choices=[mode.value for mode in AttachmentMode],
        default=AttachmentMode.NORMALIZED.value,
        help="normalized = deterministic file extraction; multimodal additionally sends image bytes",
    )
    parser.add_argument(
        "--export-reports",
        type=Path,
        default=None,
        help="Write model reports by policy/benchmark for official external evaluators",
    )
    parser.add_argument(
        "--budget-notes", nargs="+", default=[],
        choices=[ResearchRole.SCOUT.value, ResearchRole.DEEP_DIVE.value],
        help="Experimental: these tool-loop roles end each request with the requests and tool calls they have left",
    )
    parser.add_argument(
        "--judge", type=parse_judge, action="append", default=[], metavar="[NAME=]MODEL",
        help="Also grade each report against its case's rubric with this model; repeat for a second judge (needs --paid)",
    )
    args = parser.parse_args()
    if args.judge and not args.paid:
        parser.error("--judge calls a model; add --paid")
    if len({judge.name for judge in args.judge}) != len(args.judge):
        parser.error("two judges share a name; give the second one NAME=MODEL")
    if any(policy != "synthetic" for policy in args.policies) and not args.paid:
        parser.error("real model policies require --paid")
    if args.persist:
        args.repository = "postgres"
    if args.max_cases is not None and args.max_cases < 1:
        parser.error("--max-cases must be at least 1")
    if args.all_cases and args.max_cases is not None:
        parser.error("--all-cases and --max-cases cannot be combined")
    max_cases = None if args.all_cases else args.max_cases
    if max_cases is None and not args.all_cases and args.paid:
        max_cases = 1
    try:
        settings = ResearchSettings.from_env()
        concurrency = args.max_concurrency if args.max_concurrency is not None else settings.benchmark_concurrency
        manifest_path = asyncio.run(
            run_benchmark(
                args.suite,
                policies=args.policies,
                max_concurrency=concurrency,
                export_dir=args.export_reports,
                attachment_mode=AttachmentMode(args.attachment_mode),
                repository_mode=args.repository,
                manifest_path=args.manifest_output,
                settings=settings,
                max_cases=max_cases,
                budget_notes=tuple(ResearchRole(role) for role in args.budget_notes),
                judges=tuple(args.judge),
            )
        )
    except Exception as exc:  # noqa: BLE001 - report the type only; provider errors can carry response bodies
        parser.exit(1, f"Benchmark failed ({type(exc).__name__}). Check configuration and the experiment manifest.\n")
    print(f"Experiment manifest: {manifest_path}")
    status = json.loads(manifest_path.read_text(encoding="utf-8"))["status"]
    if status != "completed":
        parser.exit(1, f"Benchmark finished with status {status}; failed cases are listed in the manifest.\n")


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
import asyncio
import json
import re
from contextlib import AsyncExitStack
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping
from uuid import UUID, uuid4

from pydantic_evals import Case

from .attachments import AttachmentMode
from .benchmarks import BenchmarkCaseSpec, BenchmarkOutputMode, load_suite
from .evals import BenchmarkOutput, make_dataset
from .experiment import build_manifest, write_manifest
from .db import migration_files, migration_status
from .orchestrator import RESEARCH_GRAPH_VERSION, ResearchConfig, ResearchLoop
from .observability import configure_logfire
from .policy import POLICY_PRESETS, get_policy
from .repository import CapturingResearchRepository, InMemoryResearchRepository, PostgresResearchRepository
from .schemas import ResearchConstraints
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


def _sum_usage(repo: CapturingResearchRepository) -> tuple[int, int, float]:
    tool_calls = 0
    total_tokens = 0
    cost = 0.0
    for task in repo.tasks.values():
        usage = task.get("usage") or {}
        tool_calls += int(usage.get("tool_calls") or 0)
        total_tokens += int(usage.get("total_tokens") or 0)
        raw_cost = usage.get("cost")
        if isinstance(raw_cost, (int, float)):
            cost += float(raw_cost)
        elif isinstance(raw_cost, dict):
            for key in ("total_price", "price", "cost"):
                if isinstance(raw_cost.get(key), (int, float)):
                    cost += float(raw_cost[key])
                    break
    return tool_calls, total_tokens, cost


def _extract_exact_answer(answer: str, mode: BenchmarkOutputMode) -> str | None:
    if mode is not BenchmarkOutputMode.SHORT_ANSWER:
        return None
    matches = EXACT_ANSWER_RE.findall(answer)
    if matches:
        return matches[-1].strip()
    stripped = answer.strip()
    if "\n" not in stripped and len(stripped) <= 300:
        return stripped
    return None


def _flatten_event(event: dict[str, Any]) -> str:
    return json.dumps(
        {"args": event.get("args"), "result": event.get("result")},
        ensure_ascii=False,
        default=str,
    ).casefold()


def _blocked_accesses(events: list[dict[str, Any]], blocked_urls: list[str]) -> list[str]:
    if not blocked_urls:
        return []
    flattened = [_flatten_event(event) for event in events]
    hits: list[str] = []
    for url in blocked_urls:
        needle = url.casefold().rstrip("/")
        if any(needle in value for value in flattened):
            hits.append(url)
    return hits


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
    model_overrides: Mapping[str, str] | None = None,
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
        get_policy(policy_name, model_overrides=model_overrides),
        ResearchConfig(tool_mode=ResearchToolMode.NORMALIZED, attachment_mode=attachment_mode, scholarly_cache_mode="off"),
        repository=repo,
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

    sources = outcome.ledger.sources()
    source_urls = [str(s.url) for s in sources if s.url is not None]
    primary_urls = [
        str(s.url)
        for s in sources
        if s.url is not None and s.source_type in {"primary", "official", "paper", "documentation"}
    ]
    attachment_ids_cited = sorted({s.attachment_id for s in sources if s.attachment_id})
    checks = outcome.verification.checks
    unsupported = [c for c in checks if not c.supported]
    major = [c for c in unsupported if c.severity == "major"]
    tool_calls, total_tokens, cost = _sum_usage(repo)
    research_tool_calls = sum(1 for event in repo.tool_events if _is_research_event(event))
    attachment_tool_calls = sum(1 for event in repo.tool_events if _is_attachment_event(event))
    search_queries = _extract_search_queries(repo.tool_events)

    if export_dir is not None:
        policy_dir = export_dir / policy_name / case.benchmark_id
        policy_dir.mkdir(parents=True, exist_ok=True)
        idx = case.metadata.get("idx")
        filename = f"idx-{idx}.md" if idx else f"{case.case_id}.md"
        (policy_dir / filename).write_text(outcome.report.answer, encoding="utf-8")

    return BenchmarkOutput(
        benchmark_id=case.benchmark_id,
        case_id=case.case_id,
        graph_version=RESEARCH_GRAPH_VERSION,
        job_id=str(outcome.job_id),
        root_run_id=str(root_run_id),
        answer=outcome.report.answer,
        extracted_answer=_extract_exact_answer(outcome.report.answer, case.output_mode),
        source_urls=source_urls,
        primary_source_urls=primary_urls,
        unsupported_claims=len(unsupported),
        major_unsupported_claims=len(major),
        total_claims=len(checks),
        tool_calls=tool_calls,
        research_tool_calls=research_tool_calls,
        total_tokens=total_tokens,
        cost_usd=cost,
        search_queries=search_queries,
        blocked_source_accesses=_blocked_accesses(repo.tool_events, case.blocked_urls),
        integrity_flags=_integrity_flags(case, search_queries, repo.tool_events),
        attachment_count=len(outcome.attachments.records) if outcome.attachments else 0,
        attachment_tool_calls=attachment_tool_calls,
        attachment_ids_cited=attachment_ids_cited,
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


def _is_research_event(event: dict[str, Any]) -> bool:
    name = str(event.get("tool_name", "")).lower()
    return any(token in name for token in ("search", "fetch", "page", "url", "attachment"))


def _verify_database_schema(dsn: str) -> None:
    """Fail before any paid model call when durable benchmark storage is not ready."""
    import psycopg

    with psycopg.connect(dsn, autocommit=True, connect_timeout=5) as conn:
        pending = [m.name for m, state in migration_status(conn, migration_files()) if state != "applied"]
    if pending:
        raise RuntimeError("database migrations are pending or changed; run research-db migrate")


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
        model_overrides=settings.model_overrides,
    )
    write_manifest(manifest_path, manifest)

    cases = [
        Case(
            name=f"{spec.benchmark_id}:{spec.case_id}",
            inputs=spec,
            expected_output=spec.expected_answer,
            metadata={"benchmark": spec.benchmark_id, **spec.metadata},
        )
        for spec in specs
    ]
    dataset = make_dataset(cases)
    baseline = None

    try:
        async with AsyncExitStack() as stack:
            pool = None
            if repository_mode == "postgres":
                await asyncio.to_thread(_verify_database_schema, settings.database_dsn)
                from psycopg_pool import AsyncConnectionPool

                pool = await stack.enter_async_context(
                    AsyncConnectionPool(conninfo=settings.database_dsn, open=False)
                )
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
                        write_manifest(manifest_path, manifest)

                    try:
                        output = await _run_policy_case(
                            _policy, case, export_dir=export_dir,
                            attachment_mode=attachment_mode,
                            repository_mode=repository_mode,
                            pool=pool,
                            suite_name=suite_name,
                            on_job_created=record_job,
                            model_overrides=settings.model_overrides,
                        )
                        if run_record:
                            run_record["status"] = "succeeded"
                        return output
                    except Exception:
                        if run_record:
                            run_record["status"] = "failed"
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
                report.print(
                    baseline=baseline,
                    include_output=False,
                    include_input=False,
                    include_durations=True,
                    include_averages=True,
                )
                if baseline is None:
                    baseline = report
        manifest["status"] = "completed"
    except Exception:
        manifest["status"] = "failed"
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
    args = parser.parse_args()
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
            )
        )
    except Exception as exc:
        parser.exit(1, f"Benchmark failed ({type(exc).__name__}). Check configuration and the experiment manifest.\n")
    print(f"Experiment manifest: {manifest_path}")


if __name__ == "__main__":
    main()

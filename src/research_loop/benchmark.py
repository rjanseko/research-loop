from __future__ import annotations

import argparse
import asyncio
import json
import re
from pathlib import Path
from typing import Any

from pydantic_evals import Case

from .attachments import AttachmentMode
from .benchmarks import BenchmarkCaseSpec, BenchmarkOutputMode, load_suite
from .evals import BenchmarkOutput, make_dataset
from .orchestrator import RESEARCH_GRAPH_VERSION, ResearchConfig, ResearchLoop
from .policy import POLICY_PRESETS, get_policy
from .repository import InMemoryResearchRepository
from .schemas import ResearchConstraints
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


def _sum_usage(repo: InMemoryResearchRepository) -> tuple[int, int, float]:
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
) -> BenchmarkOutput:
    repo = InMemoryResearchRepository()
    loop = ResearchLoop(
        get_policy(policy_name),
        ResearchConfig(tool_mode=ResearchToolMode.NORMALIZED, attachment_mode=attachment_mode),
        repository=repo,
    )
    outcome = await loop.run(
        case.render_objective(),
        constraints=ResearchConstraints(
            blocked_urls=case.blocked_urls,
            attachment_paths=case.attachments,
            benchmark_id=case.benchmark_id,
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


async def run_benchmark(
    suite_path: Path,
    *,
    policies: list[str],
    max_concurrency: int,
    export_dir: Path | None = None,
    attachment_mode: AttachmentMode = AttachmentMode.NORMALIZED,
) -> None:
    suite_name, specs = _load_cases(suite_path)
    if not specs:
        raise ValueError(f"suite {suite_name!r} contains no cases")

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

    for policy_name in policies:
        async def task(case: BenchmarkCaseSpec, _policy: str = policy_name) -> BenchmarkOutput:
            return await _run_policy_case(_policy, case, export_dir=export_dir, attachment_mode=attachment_mode)

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
        # Inputs may contain anti-contamination benchmark material; never print them by default.
        report.print(
            baseline=baseline,
            include_output=False,
            include_input=False,
            include_durations=True,
            include_averages=True,
        )
        if baseline is None:
            baseline = report


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
        default=["quality", "breadth", "glm-heavy"],
        choices=sorted(POLICY_PRESETS),
    )
    parser.add_argument("--max-concurrency", type=int, default=1)
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
    asyncio.run(
        run_benchmark(
            args.suite,
            policies=args.policies,
            max_concurrency=args.max_concurrency,
            export_dir=args.export_reports,
            attachment_mode=AttachmentMode(args.attachment_mode),
        )
    )


if __name__ == "__main__":
    main()

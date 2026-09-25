from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from research_loop.benchmark import main, run_benchmark
from research_loop.evals import (
    AttachmentCitationCoverage,
    BlockedSourceCompliance,
    CostEfficiency,
    EvalIntegrity,
    MajorErrorFreeRate,
    ObservedSourceRate,
    ReferenceAnswerMatch,
    SupportedClaimRate,
    VerbatimQuoteRate,
)
from research_loop.settings import ResearchSettings


@pytest.mark.asyncio
async def test_synthetic_benchmark_writes_safe_manifest(tmp_path: Path) -> None:
    suite = tmp_path / "cases.json"
    suite.write_text(json.dumps([{"name": "synthetic-case", "objective": "Private fixture prompt"}]))
    output = tmp_path / "manifest.json"
    result = await run_benchmark(
        suite,
        policies=["synthetic"],
        max_concurrency=1,
        manifest_path=output,
        settings=ResearchSettings.from_env({"RESEARCH_BENCHMARK_OUTPUT": str(tmp_path)}),
    )
    assert result == output
    manifest = json.loads(output.read_text())
    assert manifest["status"] == "completed"
    assert manifest["graph_version"] == "research-graph-v1"
    assert manifest["schema_version"] == 3
    assert manifest["experiment_id"]
    assert len(manifest["config_fingerprint"]) == 64
    assert manifest["git"]["commit"]
    assert isinstance(manifest["git"]["dirty"], bool)
    assert manifest["policy_schema_version"] == 1
    assert manifest["acquisition"]["search_backend"] == "duckduckgo"
    assert manifest["acquisition"]["fetch_version"] == 5
    assert manifest["evidence_version"] == 4
    assert manifest["python_version"]
    assert manifest["policies"]["synthetic"]["routes"]["scout"]["model"] == "synthetic:fake"
    assert manifest["runs"][0]["status"] == "succeeded"
    assert manifest["runs"][0]["review_reasons"] == []  # the synthetic verifier supports its one claim
    # Scores survive the process: metrics that applied, the counts behind them, and a per-policy summary.
    assert manifest["evaluator_version"] == 1
    run = manifest["runs"][0]
    assert run["scores"] == {"SupportedClaimRate": 1.0, "MajorErrorFreeRate": 1.0, "PrimarySourceRate": 1.0,
                             "ToolEfficiency": 1.0, "UniqueSearchRate": 1.0}
    assert run["measures"]["total_claims"] == 1 and run["measures"]["cost_usd"] == 0.0
    assert run["duration_seconds"] >= 0 and "evaluator_failures" not in run
    summary = manifest["summary"]["synthetic"]
    assert (summary["cases"], summary["succeeded"], summary["failed"], summary["needs_review"]) == (1, 1, 0, 0)
    assert summary["scores"]["SupportedClaimRate"] == {"mean": 1.0, "cases": 1}
    assert "ReferenceAnswerMatch" not in summary["scores"]  # no reference answer: not applicable, not zero
    assert manifest["runs"][0]["job_id"]
    assert "Private fixture prompt" not in output.read_text()
    assert str(tmp_path) not in output.read_text()


def test_inapplicable_evaluators_return_no_score() -> None:
    reference_ctx = SimpleNamespace(expected_output=None)
    attachment_ctx = SimpleNamespace(output=SimpleNamespace(attachment_count=0))
    assert ReferenceAnswerMatch().evaluate(reference_ctx) == {}
    assert AttachmentCitationCoverage().evaluate(attachment_ctx) == {}
    # No checked claims, no priced spend, no blocked URLs, not leakage-sensitive: nothing to score.
    unassessed = SimpleNamespace(
        inputs=SimpleNamespace(blocked_urls=[], leakage_sensitive=False),
        output=SimpleNamespace(total_claims=0, unsupported_claims=0, major_unsupported_claims=0,
                               cost_usd=None, blocked_fetches_completed=[], blocked_sources_cited=[], integrity_flags=[],
                               quotes=0, quotes_not_found=0, sources=0, sources_not_found=0),
    )
    for evaluator in (SupportedClaimRate(), MajorErrorFreeRate(), CostEfficiency(),
                      BlockedSourceCompliance(), EvalIntegrity(), VerbatimQuoteRate(), ObservedSourceRate()):
        assert evaluator.evaluate(unassessed) == {}, type(evaluator).__name__


def test_verbatim_quote_rate_counts_quotes_found_in_tool_output() -> None:
    ctx = SimpleNamespace(output=SimpleNamespace(quotes=4, quotes_not_found=1))
    assert VerbatimQuoteRate().evaluate(ctx) == pytest.approx(0.75)


def test_observed_source_rate_counts_sources_found_in_tool_output() -> None:
    ctx = SimpleNamespace(output=SimpleNamespace(sources=5, sources_not_found=2))
    assert ObservedSourceRate().evaluate(ctx) == pytest.approx(0.6)


@pytest.mark.asyncio
async def test_benchmark_max_cases_limits_manifest_and_runs(tmp_path: Path) -> None:
    suite = tmp_path / "cases.json"
    suite.write_text(json.dumps([
        {"name": "first", "objective": "First fixture"},
        {"name": "second", "objective": "Second fixture"},
    ]))
    output = tmp_path / "manifest.json"
    await run_benchmark(
        suite,
        policies=["synthetic"],
        max_concurrency=1,
        max_cases=1,
        manifest_path=output,
        settings=ResearchSettings.from_env({"RESEARCH_BENCHMARK_OUTPUT": str(tmp_path)}),
    )
    manifest = json.loads(output.read_text())
    assert len(manifest["benchmark_manifest"]["cases"]) == 1
    assert len(manifest["runs"]) == 1


def test_real_policy_requires_paid_flag(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(sys, "argv", ["research-bench", "unused.toml", "--policies", "quality"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2
    assert "require --paid" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_report_export_contains_untrusted_case_ids(tmp_path: Path) -> None:
    suite = tmp_path / "cases.json"
    suite.write_text(
        json.dumps(
            [
                {
                    "benchmark_id": "../../outside",
                    "name": "../../escaped",
                    "objective": "First fixture",
                },
                {
                    "benchmark_id": "../../outside",
                    "name": "zero",
                    "objective": "Second fixture",
                    "metadata": {"idx": 0},
                },
            ]
        )
    )
    export_dir = tmp_path / "reports"
    await run_benchmark(
        suite,
        policies=["synthetic"],
        max_concurrency=1,
        export_dir=export_dir,
        manifest_path=tmp_path / "manifest.json",
        settings=ResearchSettings.from_env({"RESEARCH_BENCHMARK_OUTPUT": str(tmp_path)}),
    )
    reports = list(export_dir.rglob("*.md"))
    assert len(reports) == 2
    escaped_stem = "escaped-" + hashlib.sha256(b"../../escaped").hexdigest()[:8]
    assert {path.stem for path in reports} == {"idx-0", escaped_stem}
    assert all(path.is_relative_to(export_dir) for path in reports)
    assert not (tmp_path / "outside").exists()
    assert not (tmp_path / "escaped.md").exists()


def _two_case_suite(tmp_path: Path, names: list[str]) -> Path:
    suite = tmp_path / f"{'-'.join(names)}.json"
    suite.write_text(json.dumps([{"name": name, "objective": f"Fixture {name}"} for name in names]))
    return suite


@pytest.fixture
def broken_case(monkeypatch: pytest.MonkeyPatch) -> str:
    """Make the case named 'broken' fail with a provider-style error body."""
    from research_loop import benchmark

    marker = "PRIVATE-PROVIDER-BODY"
    real_case = benchmark._run_policy_case

    async def flaky(policy_name, case, **kwargs):
        if case.case_id == "broken":
            raise RuntimeError(f"HTTP 400: {marker}")
        return await real_case(policy_name, case, **kwargs)

    monkeypatch.setattr(benchmark, "_run_policy_case", flaky)
    return marker


@pytest.mark.asyncio
async def test_failed_cases_set_manifest_status_without_printing_errors(
    tmp_path: Path, broken_case: str, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = ResearchSettings.from_env({"RESEARCH_BENCHMARK_OUTPUT": str(tmp_path)})
    partial = await run_benchmark(
        _two_case_suite(tmp_path, ["working", "broken"]), policies=["synthetic"], max_concurrency=1,
        manifest_path=tmp_path / "partial.json", settings=settings,
    )
    manifest = json.loads(partial.read_text())
    assert manifest["status"] == "completed_with_failures"
    assert manifest["failed_cases"] == 1
    runs = {run["case_id"]: run for run in manifest["runs"]}
    assert runs["working"]["status"] == "succeeded"
    # The case failed before a job existed; it still gets a terminal record, with only the error type.
    assert runs["broken"]["status"] == "failed"
    assert runs["broken"]["error"] == "RuntimeError"
    assert runs["broken"]["job_id"] is None
    assert "scores" in runs["working"] and "scores" not in runs["broken"]
    summary = manifest["summary"]["synthetic"]
    assert (summary["cases"], summary["succeeded"], summary["failed"]) == (2, 1, 1)
    assert summary["scores"]["SupportedClaimRate"]["cases"] == 1

    failed = await run_benchmark(
        _two_case_suite(tmp_path, ["broken"]), policies=["synthetic"], max_concurrency=1,
        manifest_path=tmp_path / "failed.json", settings=settings,
    )
    assert json.loads(failed.read_text())["status"] == "failed"
    captured = capsys.readouterr()
    assert broken_case not in captured.out + captured.err
    assert broken_case not in partial.read_text() + failed.read_text()


def test_cli_exits_nonzero_when_a_case_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, broken_case: str, capsys: pytest.CaptureFixture[str]
) -> None:
    from research_loop import benchmark

    settings = ResearchSettings.from_env({"RESEARCH_BENCHMARK_OUTPUT": str(tmp_path)})
    monkeypatch.setattr(benchmark, "ResearchSettings", SimpleNamespace(from_env=lambda: settings))
    suite = _two_case_suite(tmp_path, ["working", "broken"])
    monkeypatch.setattr(sys, "argv", ["research-benchmark", str(suite), "--all-cases",
                                      "--manifest-output", str(tmp_path / "manifest.json")])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert "completed_with_failures" in captured.err
    assert broken_case not in captured.out + captured.err


@pytest.mark.asyncio
async def test_unpriced_model_call_leaves_benchmark_cost_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    from research_loop import benchmark
    from research_loop.benchmarks import BenchmarkCaseSpec
    from research_loop.synthetic import SyntheticResearchLoop

    class UnpricedLoop(SyntheticResearchLoop):
        async def _run_agent(self, **kwargs):
            output = await super()._run_agent(**kwargs)
            self._job_spend[kwargs["job_id"]] = None  # what a billed call without pricing data leaves
            return output

    monkeypatch.setattr(benchmark, "SyntheticResearchLoop", UnpricedLoop)
    output = await benchmark._run_policy_case(
        "synthetic", BenchmarkCaseSpec(benchmark_id="fixture", case_id="unpriced", objective="Fixture")
    )
    assert output.cost_usd is None


@pytest.mark.asyncio
async def test_cancelled_benchmark_marks_its_runs_and_manifest_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio
    from uuid import uuid4

    from research_loop import benchmark

    started = asyncio.Event()

    async def hanging(_policy_name, _case, *, on_job_created, **_kwargs):
        on_job_created(uuid4(), uuid4())
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(benchmark, "_run_policy_case", hanging)
    manifest_path = tmp_path / "manifest.json"
    running = asyncio.create_task(run_benchmark(
        _two_case_suite(tmp_path, ["slow"]), policies=["synthetic"], max_concurrency=1,
        manifest_path=manifest_path, settings=ResearchSettings.from_env({"RESEARCH_BENCHMARK_OUTPUT": str(tmp_path)}),
    ))
    await asyncio.wait_for(started.wait(), timeout=15)
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    manifest = json.loads(manifest_path.read_text())
    assert (manifest["status"], manifest["error"]) == ("failed", "CancelledError")
    assert [(run["status"], run["error"]) for run in manifest["runs"]] == [("failed", "CancelledError")]


def test_policy_summary_averages_applicable_scores_and_keeps_unknown_cost_unknown() -> None:
    from research_loop.benchmark import _policy_summary

    runs = [
        {"status": "succeeded", "scores": {"A": 1.0, "B": 0.0}, "measures": {"cost_usd": 0.5}, "review_reasons": []},
        {"status": "succeeded", "scores": {"A": 0.5}, "measures": {"cost_usd": 0.25}, "review_reasons": ["x"]},
        {"status": "failed"},
    ]
    assert _policy_summary(runs) == {
        "cases": 3, "succeeded": 2, "failed": 1, "needs_review": 1, "succeeded_cost_usd": 0.75,
        "scores": {"A": {"mean": 0.75, "cases": 2}, "B": {"mean": 0.0, "cases": 1}},
    }
    runs[1]["measures"]["cost_usd"] = None  # one unpriced case makes the total unknown
    assert _policy_summary(runs)["succeeded_cost_usd"] is None


@pytest.mark.asyncio
async def test_benchmark_runs_use_the_settings_they_were_given(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from research_loop import benchmark

    seen = []

    class Recording(benchmark.SyntheticResearchLoop):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            seen.append(self.settings)

    monkeypatch.setattr(benchmark, "SyntheticResearchLoop", Recording)
    settings = ResearchSettings.from_env({"RESEARCH_BENCHMARK_OUTPUT": str(tmp_path),
                                          "RESEARCH_BENCHMARK_CACHE": str(tmp_path / "cache")})
    await run_benchmark(_two_case_suite(tmp_path, ["one"]), policies=["synthetic"], max_concurrency=1,
                        manifest_path=tmp_path / "manifest.json", settings=settings)
    assert seen == [settings]



@pytest.mark.parametrize(("answer", "expected"), [
    ("Reasoning [s2].\nExact Answer: 1997 [s4]", "1997"),
    ("1997 [s4, s5]", "1997"),
    ("Exact Answer: 1997", "1997"),
])
def test_graded_short_answers_drop_inline_source_citations(answer, expected) -> None:
    from research_loop.benchmark import _extract_exact_answer
    from research_loop.benchmarks import BenchmarkOutputMode

    assert _extract_exact_answer(answer, BenchmarkOutputMode.SHORT_ANSWER) == expected

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

from research_loop.benchmark import main, run_benchmark
from research_loop.evals import AttachmentCitationCoverage, ReferenceAnswerMatch
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
    assert manifest["schema_version"] == 2
    assert manifest["experiment_id"]
    assert len(manifest["config_fingerprint"]) == 64
    assert manifest["git"]["commit"]
    assert isinstance(manifest["git"]["dirty"], bool)
    assert manifest["policy_schema_version"] == 1
    assert manifest["acquisition"]["search_backend"] == "duckduckgo"
    assert manifest["python_version"]
    assert manifest["policies"]["synthetic"]["routes"]["scout"]["model"] == "synthetic:fake"
    assert manifest["runs"][0]["status"] == "succeeded"
    assert manifest["runs"][0]["job_id"]
    assert "Private fixture prompt" not in output.read_text()
    assert str(tmp_path) not in output.read_text()


def test_inapplicable_evaluators_return_no_score() -> None:
    reference_ctx = SimpleNamespace(expected_output=None)
    attachment_ctx = SimpleNamespace(output=SimpleNamespace(attachment_count=0))
    assert ReferenceAnswerMatch().evaluate(reference_ctx) == {}
    assert AttachmentCitationCoverage().evaluate(attachment_ctx) == {}


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

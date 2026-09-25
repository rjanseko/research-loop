"""examples/settings_study.py: the study's limits, its step record, and grading from that record."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from research_loop.grading import jobs_from_record
from research_loop.schemas import ResearchRole
from research_loop.settings import ResearchSettings

_SPEC = importlib.util.spec_from_file_location("settings_study", Path(__file__).parents[1] / "examples" / "settings_study.py")
settings_study = importlib.util.module_from_spec(_SPEC)
sys.modules["settings_study"] = settings_study
_SPEC.loader.exec_module(settings_study)


def test_paid_setup_gives_every_research_route_the_study_limits_and_keeps_dollar_caps() -> None:
    preset = settings_study.get_policy("value")
    policy, config = settings_study.build(True, 4.0, 1.5, ResearchSettings.from_env({}), cache_mode="reuse")
    assert (policy.name, policy.job_cost_limit, policy.job_reserve_usd, policy.planner_question_range) == (
        "value", 4.0, 1.5, (3, 5))
    for route, preset_route in ((policy.routes[ResearchRole.SCOUT], preset.routes[ResearchRole.SCOUT]),
                                (policy.cheap_scout, preset.cheap_scout)):
        assert (route.max_requests, route.max_tool_calls, route.total_tokens_limit) == (24, 48, 2_000_000)
        assert route.cost_limit == preset_route.cost_limit and route.model == preset_route.model
    for route in (policy.routes[ResearchRole.DEEP_DIVE], policy.alternate_deep_dive):
        assert (route.max_requests, route.max_tool_calls, route.total_tokens_limit) == (12, 80, 2_000_000)
    assert config.salvage_exhausted_research and config.scholarly_cache_mode == "reuse"
    assert config.tool_mode.value == "normalized"


@pytest.mark.asyncio
async def test_a_synthetic_step_records_each_case_and_carries_on_past_a_failure(tmp_path, monkeypatch) -> None:
    _, specs = settings_study.load_suite(settings_study.SUITE)
    original = settings_study.SyntheticResearchLoop.run

    async def fail_one(self, objective, **kwargs):
        if kwargs["constraints"].benchmark_case_id == specs[0].case_id:
            raise RuntimeError("provider body that must not be recorded")
        return await original(self, objective, **kwargs)

    monkeypatch.setattr(settings_study.SyntheticResearchLoop, "run", fail_one)
    output = tmp_path / "step.json"
    record = await settings_study.run(
        cases=specs[:3], paid=False, persist=False, budget=4.0, reserve=1.5,
        settings=ResearchSettings.from_env({}), policy_name="value", cache_mode="record", step="rehearsal",
        output=output, suite_name="settings-study")
    assert json.loads(output.read_text()) == json.loads(json.dumps(record, default=str))
    assert [run["status"] for run in record["runs"]] == ["failed", "succeeded", "succeeded"]
    assert record["runs"][0]["error"] == "RuntimeError" and "provider body" not in output.read_text()
    assert record["step"] == "rehearsal" and record["cache"]["mode"] == "record" and record["git"]["commit"]
    # research-grade --jobs-from takes the succeeded runs only.
    assert [case for case, _ in jobs_from_record(output)] == [specs[1].case_id, specs[2].case_id]


@pytest.mark.parametrize(("argv", "env", "message"), [
    (["--step", "s", "--cases", "st99"], {}, "not in settings_study.toml: st99"),
    (["--step", "s", "--policy", "quality"], {}, "add --paid"),
    (["--step", "s", "--persist"], {}, "set DATABASE_URL"),
    (["--step", "s", "--paid"], {}, "set DATABASE_URL"),
    (["--step", "s", "--paid", "--budget", "1", "--reserve", "1.5"], {"DATABASE_URL": "postgresql://x/y"}, "reserve"),
])
def test_unrunnable_options_are_refused_before_any_run(argv, env, message, monkeypatch, capsys) -> None:
    monkeypatch.setattr(settings_study, "ResearchSettings", SimpleNamespace(from_env=lambda: ResearchSettings.from_env(env)))
    with pytest.raises(SystemExit):
        settings_study.main(argv)
    assert message in capsys.readouterr().err


def test_research_grade_needs_jobs_and_reads_step_records(tmp_path, capsys) -> None:
    from research_loop.grading import main

    with pytest.raises(SystemExit):
        main(["suite.toml"])
    assert "--job or --jobs-from" in capsys.readouterr().err
    record = tmp_path / "step.json"
    job = uuid4()
    record.write_text(json.dumps({"runs": [{"case_id": "st07", "status": "succeeded", "job_id": str(job)},
                                           {"case_id": "st05", "status": "failed", "error": "X"}]}))
    assert jobs_from_record(record) == [("st07", job)]


def test_default_step_skips_retired_cases_and_passes_blocked_urls(tmp_path, monkeypatch) -> None:
    seen: dict[str, list[str]] = {}

    async def record(self, objective, **kwargs):
        constraints = kwargs["constraints"]
        seen[constraints.benchmark_case_id] = constraints.blocked_urls
        raise RuntimeError("stop before running")

    monkeypatch.setattr(settings_study.SyntheticResearchLoop, "run", record)
    monkeypatch.setattr(settings_study, "ResearchSettings",
                        SimpleNamespace(from_env=lambda: ResearchSettings.from_env({"RESEARCH_BENCHMARK_OUTPUT": str(tmp_path)})))
    settings_study.main(["--step", "s", "--output", str(tmp_path / "step.json")])
    assert "st07-swebench-trust" not in seen and "st06-cot-small-models" not in seen
    assert "st01-transformer-venue" in seen and seen["st01-transformer-venue"] == []
    drb2 = [case for case in seen if not case.startswith("st")]
    assert set(drb2) == {"task2+", "task8", "task17+", "task26"} and all(seen[case] for case in drb2)

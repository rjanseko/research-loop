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

# The suite's DRB-II tasks, by their position among the English tasks (examples/settings_study.toml).
_DRB2_PICKS = {1: "task2+", 7: "task8", 20: "task17+", 31: "task26"}


# The study's alternate deep dive is on Z.ai, set by override as in its .env.
_STUDY_ENV = ResearchSettings.from_env({"RESEARCH_ALT_DEEP_MODEL": "zai:glm-5.3"})


@pytest.fixture(autouse=True)
def drb2_offline(tmp_path, monkeypatch):
    """A stand-in for DRB-II's tasks file in the benchmark cache, so loading the suite never downloads it."""
    rows = []
    for position in range(32):
        task_id = _DRB2_PICKS.get(position, f"filler{position}")
        rows.append({"id": task_id, "idx": position, "language": "en", "prompt": f"Research task {task_id}",
                     "content": {"rubric": {"info_recall": [f"{task_id} point"]},
                                 "blocked": {"urls": [f"https://example.org/{task_id}"]}}})
        rows.append({"id": f"zh{position}", "idx": 100 + position, "language": "zh", "prompt": "x",
                     "content": {"rubric": {}}})
    cache = tmp_path / "benchmark-cache"
    cache.mkdir()
    (cache / "drb2_tasks_and_rubrics.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    monkeypatch.setenv("RESEARCH_BENCHMARK_CACHE", str(cache))


def test_paid_setup_gives_every_research_route_the_study_limits_and_keeps_dollar_caps() -> None:
    preset = settings_study.get_policy("value")
    policy, config = settings_study.build(True, 4.0, 1.5, _STUDY_ENV, cache_mode="reuse")
    assert (policy.name, policy.job_cost_limit, policy.job_reserve_usd, policy.planner_question_range) == (
        "value", 4.0, 1.5, (3, 8))
    for route, preset_route in ((policy.routes[ResearchRole.SCOUT], preset.routes[ResearchRole.SCOUT]),
                                (policy.cheap_scout, preset.cheap_scout)):
        assert (route.max_requests, route.max_tool_calls, route.total_tokens_limit) == (24, 48, 2_000_000)
        assert route.cost_limit == preset_route.cost_limit and route.model == "zai:glm-5.3-flash"
    for route in (policy.routes[ResearchRole.DEEP_DIVE], policy.alternate_deep_dive):
        assert (route.max_requests, route.max_tool_calls, route.total_tokens_limit) == (12, 80, 2_000_000)
    for role in (ResearchRole.GAP_ANALYST, ResearchRole.VERIFIER):
        route = policy.routes[role]
        assert route.total_tokens_limit == 600_000 and route.model == preset.routes[role].model
        assert route.settings == preset.routes[role].settings
    # The study's synthesizer: glm-5.3 at Z.ai's highest effort, with room for its reasoning.
    synthesizer = policy.routes[ResearchRole.SYNTHESIZER]
    assert (synthesizer.model, synthesizer.thinking, synthesizer.settings["max_tokens"]) == ("zai:glm-5.3", "xhigh", 64_000)
    assert synthesizer.total_tokens_limit == 600_000 and synthesizer.refusal_fallback == "openai:gpt-6-sol"
    assert config.salvage_exhausted_research and config.scholarly_cache_mode == "reuse"
    assert config.tool_mode.value == "normalized"
    assert config.budget_notes == ()


def test_budget_notes_reach_the_run_config() -> None:
    _policy, config = settings_study.build(True, 4.0, 1.5, _STUDY_ENV,
                                           budget_notes=(ResearchRole.SCOUT, ResearchRole.DEEP_DIVE))
    assert config.budget_notes == (ResearchRole.SCOUT, ResearchRole.DEEP_DIVE)



def test_a_paid_step_refuses_an_environment_that_moves_the_study_models() -> None:
    env = {"RESEARCH_ALT_DEEP_MODEL": "zai:glm-5.3", "RESEARCH_VALUE_VERIFY_MODEL": "zai:glm-5.3"}
    with pytest.raises(ValueError, match="verifier is zai:glm-5.3, not openai:gpt-6-sol"):
        settings_study.build(True, 5.0, 1.0, ResearchSettings.from_env(env))
    # The scout and synthesizer are the study's own, whatever the environment says.
    moved = {"RESEARCH_ALT_DEEP_MODEL": "zai:glm-5.3", "RESEARCH_VALUE_SYNTH_MODEL": "openai:gpt-6-luna"}
    policy, _ = settings_study.build(True, 5.0, 1.0, ResearchSettings.from_env(moved))
    assert policy.routes[ResearchRole.SYNTHESIZER].model == "zai:glm-5.3"
    with pytest.raises(ValueError, match="alternate_deep_dive is xai:grok-4.5"):
        settings_study.build(True, 5.0, 1.0, ResearchSettings.from_env({}))
    settings_study.build(False, 5.0, 1.0, ResearchSettings.from_env({}))  # the synthetic rehearsal has no models to move

@pytest.mark.asyncio
async def test_a_synthetic_step_records_each_case_and_carries_on_past_a_failure(tmp_path, monkeypatch) -> None:
    _, specs = settings_study.load_suite(settings_study.SUITE)
    original = settings_study.SyntheticResearchLoop.run

    async def fail_one(self, objective, **kwargs):
        outcome = await original(self, objective, **kwargs)
        if kwargs["constraints"].benchmark_case_id == specs[0].case_id:
            raise RuntimeError("provider body that must not be recorded")
        return outcome

    monkeypatch.setattr(settings_study.SyntheticResearchLoop, "run", fail_one)
    output = tmp_path / "step.json"
    record = await settings_study.run(
        cases=specs[:3], paid=False, persist=False, budget=4.0, reserve=1.5,
        settings=ResearchSettings.from_env({}), policy_name="value", cache_mode="record", step="rehearsal",
        output=output, suite_name="settings-study")
    assert json.loads(output.read_text()) == json.loads(json.dumps(record, default=str))
    assert [run["status"] for run in record["runs"]] == ["failed", "succeeded", "succeeded"]
    assert record["runs"][0]["error"] == "RuntimeError" and "provider body" not in output.read_text()
    assert record["runs"][0]["job_id"] not in {run["job_id"] for run in record["runs"][1:]}  # the failed run's own job
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

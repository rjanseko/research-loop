"""examples/readme_example.py: the capped paid setup, and a synthetic run end to end."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from research_loop.schemas import ResearchRole
from research_loop.settings import ResearchSettings

_SPEC = importlib.util.spec_from_file_location(
    "readme_example", Path(__file__).parents[1] / "examples" / "readme_example.py"
)
readme_example = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(readme_example)
# No .env, DATABASE_URL, or keys: the settings a fresh checkout has.
_empty_settings = SimpleNamespace(from_env=lambda: ResearchSettings.from_env({}))


def test_paid_setup_caps_the_quality_policy_and_trims_the_run() -> None:
    policy, config = readme_example.build(True, 4.0, 1.5, ResearchSettings.from_env({}))
    assert (policy.name, policy.job_cost_limit, policy.job_reserve_usd) == ("quality", 4.0, 1.5)
    assert policy.planner_question_range == (3, 5)
    assert (config.max_verification_rounds, config.max_deep_dives_per_round) == (1, 2)
    # Running out of tokens or budget degrades a scout or deep dive instead of failing the run.
    assert config.salvage_exhausted_research
    assert policy.routes[ResearchRole.SCOUT].total_tokens_limit == policy.cheap_scout.total_tokens_limit == 400_000
    assert policy.routes[ResearchRole.SCOUT].cost_limit == 0.80  # the dollar cap is unchanged


@pytest.mark.parametrize("argv", [
    ["--paid", "--budget", "1", "--reserve", "1.5"],  # the reserve must stay below the cap
    ["--paid", "--budget", "0"],
    ["--persist"],  # no DATABASE_URL
])
def test_unrunnable_options_are_refused_before_any_run(argv, monkeypatch) -> None:
    monkeypatch.setattr(readme_example, "ResearchSettings", _empty_settings)
    with pytest.raises(SystemExit) as exit_info:
        readme_example.main(argv)
    assert exit_info.value.code == 2


def test_synthetic_run_writes_the_record(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(readme_example, "ResearchSettings", _empty_settings)
    output = tmp_path / "record.json"
    readme_example.main(["--output", str(output)])

    record = json.loads(output.read_text(encoding="utf-8"))
    assert record["question"] == readme_example.QUESTION and record["notes"] == readme_example.NOTES
    assert record["paid"] is False and record["policy"]["name"] == "synthetic"
    assert record["report"]["answer"] and record["verification"] is not None
    assert record["review_reasons"] == [] and record["cost_usd"] == 0.0
    assert f"record: {output}" in capsys.readouterr().out

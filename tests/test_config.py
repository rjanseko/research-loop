from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from research_loop.config import ScoutLimits, Settings
from research_loop.scout import run_config


def test_defaults_are_the_settings_study_lineup_and_scout_limits() -> None:
    settings = Settings()
    assert (settings.models.planner, settings.models.scout, settings.models.synthesizer, settings.models.fallback) == (
        "openai:gpt-6-sol@high", "openai:gpt-6-luna@high", "anthropic:claude-opus-5-5@medium", "openai:gpt-6-sol@high")
    limits = settings.limits
    assert (limits.cost_usd, limits.deadline_seconds, limits.max_questions) == (1.75, 1320, 4)
    assert (limits.scout_requests, limits.scout_productive_calls, limits.scout_misses) == (30, 128, 16)
    assert limits.scout_usd(4) == 0.275 and limits.scout_usd(1) == 1.1
    assert settings.logfire is True and settings.cache_mode == "live"


def test_environment_beats_dotenv_and_arguments_beat_both(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("RESEARCH_MODELS__SCOUT=zai:glm-5.3@xhigh\nRESEARCH_LIMITS__COST_USD=1.5\nZAI_API_KEY=from-dotenv\n")
    from_file = Settings(_env_file=env_file)
    assert (from_file.models.scout, from_file.limits.cost_usd) == ("zai:glm-5.3@xhigh", 1.5)
    assert from_file.api_key("zai") == "from-dotenv"

    monkeypatch.setenv("RESEARCH_MODELS__SCOUT", "openai:gpt-6-luna@high")
    monkeypatch.setenv("ZAI_API_KEY", "from-environment")
    from_env = Settings(_env_file=env_file)
    assert from_env.models.scout == "openai:gpt-6-luna@high" and from_env.api_key("zai") == "from-environment"
    assert from_env.limits.cost_usd == 1.5  # other nested values still come from the file

    assert Settings(_env_file=env_file, cache_mode="replay").cache_mode == "replay"


def test_keys_are_secret_and_never_exported(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    import os

    env_file = tmp_path / ".env"
    env_file.write_text("OPENAI_API_KEY=sk-dotenv\n")
    settings = Settings(_env_file=env_file)
    assert "sk-dotenv" not in repr(settings) and "sk-dotenv" not in settings.model_dump_json()
    assert "OPENAI_API_KEY" not in os.environ


@pytest.mark.parametrize(("name", "value", "message"), [
    ("RESEARCH_MODELS__SCOUT", "glm-5.3-flash", "not provider:model"),
    ("RESEARCH_MODELS__PLANNER", "openrouter:vendor/model", "not provider:model"),
    ("RESEARCH_MODELS__AUDIT", "", "not provider:model"),
    ("RESEARCH_MODELS__DRY", "openai:gpt-6-luna@low", "must use the fake: provider"),
    ("RESEARCH_ENABLED_PROVIDERS", "openai,xai", "unknown providers: xai"),
    ("RESEARCH_LIMITS__SYNTHESIS_USD", "1.8", "must leave part of cost_usd"),
    ("RESEARCH_CACHE_MODE", "sometimes", "cache_mode"),
])
def test_bad_values_fail_at_startup(monkeypatch: pytest.MonkeyPatch, name: str, value: str, message: str) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError, match=message):
        Settings()


def test_limits_must_leave_time_to_synthesize() -> None:
    with pytest.raises(ValidationError, match="research_seconds must end before deadline_seconds"):
        ScoutLimits(deadline_seconds=200, research_seconds=200)


def test_route_problems_name_missing_keys_and_disabled_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    assert Settings().route_problems() == []

    monkeypatch.setenv("RESEARCH_MODELS__SCOUT", "zai:glm-5.3-flash@high")
    assert Settings().route_problems() == ["scout: zai:glm-5.3-flash needs ZAI_API_KEY"]

    monkeypatch.setenv("ZAI_API_KEY", "k")
    assert Settings().route_problems() == []
    monkeypatch.setenv("RESEARCH_ENABLED_PROVIDERS", "openai, zai")
    assert Settings().enabled_providers == ("openai", "zai")
    assert Settings().route_problems() == [
        "synthesizer: anthropic:claude-opus-5-5 needs anthropic, which is not enabled (RESEARCH_ENABLED_PROVIDERS)"]


def test_a_study_gets_its_own_reused_cache_unless_the_cache_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RESEARCH_CACHE_MODE", raising=False)
    monkeypatch.delenv("RESEARCH_CACHE_DIR", raising=False)
    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.setenv(name, "test-key")
    study = Settings().for_study("set-coverage")
    assert (study.cache_mode, study.cache_dir) == ("reuse", Path(".cache/studies/set-coverage"))
    assert run_config(study, [], [])["cache_dir"] == ".cache/studies/set-coverage"
    monkeypatch.setenv("RESEARCH_CACHE_MODE", "replay")
    assert Settings().for_study("set-coverage").cache_mode == "replay"
    monkeypatch.setenv("RESEARCH_CACHE_DIR", "/tmp/elsewhere")
    assert Settings().for_study("set-coverage").cache_dir == Path("/tmp/elsewhere")
    with pytest.raises(ValueError, match="study name"):
        Settings().for_study("../escape")


def test_each_depth_changes_only_what_it_sets() -> None:
    limits = ScoutLimits()
    assert limits.question_caps() == {"quick": 2, "standard": 4, "deep": 8}
    quick = limits.for_depth("quick")
    assert (quick.max_questions, quick.cost_usd, quick.synthesis_usd, quick.deadline_seconds) == (2, 0.30, 0.12, 360)
    assert quick.scout_requests == limits.scout_requests and limits.for_depth("standard") is limits
    assert limits.follows_up("deep") and not limits.follows_up("quick") and not limits.follows_up("standard")
    assert limits.followup_scout_usd(8) == 0.0875 and limits.for_depth("deep").followup_scout_usd(8) == 0.275
    with pytest.raises(ValidationError, match="quick depth"):
        ScoutLimits(quick={"cost_usd": 0.10})


def test_setting_one_depth_limit_keeps_the_rest_of_that_depth(monkeypatch: pytest.MonkeyPatch) -> None:
    # One variable used to replace the whole tier, so RESEARCH_LIMITS__DEEP__MAX_QUESTIONS turned the
    # deep follow-up off and reset every other deep limit.
    monkeypatch.setenv("RESEARCH_LIMITS__DEEP__MAX_QUESTIONS", "6")
    monkeypatch.setenv("RESEARCH_LIMITS__QUICK__COST_USD", "0.40")
    limits = Settings().limits
    assert limits.follows_up("deep") and limits.question_caps()["deep"] == 6
    assert limits.for_depth("deep").research_seconds == ScoutLimits().for_depth("deep").research_seconds
    quick = limits.for_depth("quick")
    assert (quick.max_questions, quick.cost_usd, quick.deadline_seconds) == (2, 0.40, 360)


def test_a_deep_run_gets_more_time_tool_calls_and_scout_money() -> None:
    limits = ScoutLimits()
    deep = limits.for_depth("deep")
    assert deep.research_seconds > limits.research_seconds and deep.deep_dive_seconds > limits.deep_dive_seconds
    assert deep.followup_deadline_seconds >= deep.research_seconds + deep.gap_seconds + deep.deep_dive_seconds + 90
    assert deep.scout_productive_calls > limits.scout_productive_calls
    assert (deep.deep_dive_requests, deep.deep_dive_productive_calls, deep.deep_dive_misses) == (
        limits.scout_requests, limits.scout_productive_calls, limits.scout_misses)
    # Only the scouts' part of the envelope grows: $1.70 for eight scouts instead of $0.70.
    assert (deep.followup_cost_usd, limits.followup_cost_usd) == (4.00, 2.50)
    assert (deep.deep_dive_usd, deep.synthesis_usd, deep.gap_usd) == (limits.deep_dive_usd, limits.synthesis_usd, limits.gap_usd)
    assert deep.followup_scout_usd(8) == 0.275

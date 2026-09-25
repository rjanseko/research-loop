from __future__ import annotations

import pytest
from pydantic import ValidationError

from research_loop.config import ScoutLimits, Settings


def test_defaults_are_the_settings_study_lineup_and_scout_limits() -> None:
    settings = Settings()
    assert (settings.models.planner, settings.models.scout, settings.models.synthesizer, settings.models.fallback) == (
        "openai:gpt-6-sol", "zai:glm-5.3-flash", "anthropic:claude-opus-5-5", "openai:gpt-6-sol")
    limits = settings.limits
    assert (limits.cost_usd, limits.deadline_seconds, limits.max_questions) == (0.75, 360, 4)
    assert (limits.scout_requests, limits.scout_productive_calls, limits.scout_misses) == (12, 16, 12)
    assert limits.scout_usd(4) == 0.075 and limits.scout_usd(1) == 0.3
    assert settings.logfire is True and settings.cache_mode == "live"


def test_environment_beats_dotenv_and_arguments_beat_both(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("RESEARCH_MODELS__SCOUT=zai:glm-5.3\nRESEARCH_LIMITS__COST_USD=1.5\nZAI_API_KEY=from-dotenv\n")
    from_file = Settings(_env_file=env_file)
    assert (from_file.models.scout, from_file.limits.cost_usd) == ("zai:glm-5.3", 1.5)
    assert from_file.api_key("zai") == "from-dotenv"

    monkeypatch.setenv("RESEARCH_MODELS__SCOUT", "openai:gpt-6-luna")
    monkeypatch.setenv("ZAI_API_KEY", "from-environment")
    from_env = Settings(_env_file=env_file)
    assert from_env.models.scout == "openai:gpt-6-luna" and from_env.api_key("zai") == "from-environment"
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
    ("RESEARCH_ENABLED_PROVIDERS", "openai,xai", "unknown providers: xai"),
    ("RESEARCH_LIMITS__SYNTHESIS_USD", "0.9", "must leave part of cost_usd"),
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
    assert Settings().route_problems() == ["scout: zai:glm-5.3-flash needs ZAI_API_KEY"]

    monkeypatch.setenv("ZAI_API_KEY", "k")
    assert Settings().route_problems() == []
    monkeypatch.setenv("RESEARCH_ENABLED_PROVIDERS", "openai, zai")
    assert Settings().enabled_providers == ("openai", "zai")
    assert Settings().route_problems() == [
        "synthesizer: anthropic:claude-opus-5-5 needs anthropic, which is not enabled (RESEARCH_ENABLED_PROVIDERS)"]

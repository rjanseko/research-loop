from __future__ import annotations

import pytest

from research_loop.settings import ResearchSettings, model_for_call, openrouter_model


def test_settings_reads_environment_without_exposing_secrets() -> None:
    settings = ResearchSettings.from_env({
        "DATABASE_URL": "postgresql://user:password@localhost/db",
        "OPENROUTER_API_KEY": "private-key",
        "OPENAI_API_KEY": "ignored-provider-key",
        "RESEARCH_BENCHMARK_CONCURRENCY": "2",
        "RESEARCH_SCOUT_MODEL": "openrouter:openai/configured-scout",
        "RESEARCH_LOGFIRE_ENABLED": "true",
    })
    assert settings.database_dsn == "postgresql://user:password@localhost/db"
    assert settings.openrouter_api_key is not None
    assert settings.openrouter_api_key.get_secret_value() == "private-key"
    assert settings.benchmark_concurrency == 2
    assert settings.logfire_enabled is True
    assert settings.model_overrides["RESEARCH_SCOUT_MODEL"] == "openrouter:openai/configured-scout"
    assert "private-key" not in repr(settings)
    assert "ignored-provider-key" not in repr(settings)
    assert "password" not in repr(settings)


def test_model_key_comes_only_from_the_global_environment(tmp_path, monkeypatch) -> None:
    import os
    from unittest.mock import patch

    import dotenv

    monkeypatch.setattr("research_loop.settings.load_dotenv", dotenv.load_dotenv)  # conftest turns it off

    (tmp_path / ".env").write_text(
        "OPENROUTER_API_KEY=file-key\nOPENAI_API_KEY=file-openai\nDATABASE_URL=postgresql://from-file\n"
    )
    monkeypatch.chdir(tmp_path)
    with patch.dict(os.environ, {}, clear=True):
        settings = ResearchSettings.from_env()
        assert settings.openrouter_api_key is None
        assert "OPENROUTER_API_KEY" not in os.environ
        assert settings.database_dsn == "postgresql://from-file"
        assert "file-key" not in repr(settings)

    with patch.dict(os.environ, {"OPENROUTER_API_KEY": " global-key ", "DATABASE_URL": "postgresql://exported"}, clear=True):
        settings = ResearchSettings.from_env()
        assert settings.openrouter_api_key is not None
        assert settings.openrouter_api_key.get_secret_value() == "global-key"
        assert os.environ["OPENROUTER_API_KEY"] == "global-key"
        assert settings.database_dsn == "postgresql://exported"
        assert "file-key" not in repr(settings)


def test_openrouter_model_uses_the_passed_key(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "env-key")
    model = openrouter_model("openrouter:openai/gpt-5.6-sol", "given-key")
    assert model.model_name == "openai/gpt-5.6-sol"
    assert model._provider.client.api_key == "given-key"


def test_settings_refuse_model_overrides_that_are_not_openrouter() -> None:
    # Values an old .env.example set; they fail at load, before a job is created.
    with pytest.raises(ValueError) as raised:
        ResearchSettings.from_env({
            "RESEARCH_SYNTH_MODEL": "anthropic:claude-opus-5",
            "RESEARCH_GAP_MODEL": "test",
            "RESEARCH_SCOUT_MODEL": "openrouter:z-ai/glm-5.3",
        })
    message = str(raised.value)
    assert "RESEARCH_SYNTH_MODEL=anthropic:claude-opus-5" in message
    assert "RESEARCH_GAP_MODEL=test" in message
    assert "RESEARCH_SCOUT_MODEL" not in message


def test_model_for_call_uses_the_key_or_passes_the_id_through() -> None:
    from pydantic import SecretStr

    assert model_for_call("openrouter:openai/gpt-5.6-sol", None) == "openrouter:openai/gpt-5.6-sol"
    model = model_for_call("openrouter:openai/gpt-5.6-sol", SecretStr("given-key"))
    assert model.model_name == "openai/gpt-5.6-sol"
    assert model._provider.client.api_key == "given-key"
    with pytest.raises(ValueError, match="openrouter:"):
        model_for_call("anthropic:claude-opus-5", SecretStr("given-key"))


async def test_a_run_without_a_key_takes_an_override_or_asks_for_the_key() -> None:
    from pydantic_ai import Agent
    from pydantic_ai.exceptions import UserError
    from pydantic_ai.models.test import TestModel

    agent = Agent()
    model = model_for_call("openrouter:openai/gpt-5.6-sol", None)
    with agent.override(model=TestModel(custom_output_text="scripted")):
        assert (await agent.run("hi", model=model)).output == "scripted"
    with pytest.raises(UserError, match="OPENROUTER_API_KEY"):
        await agent.run("hi", model=model)

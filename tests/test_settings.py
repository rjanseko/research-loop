from __future__ import annotations

from research_loop.policy import get_policy
from research_loop.schemas import ResearchRole
from research_loop.settings import ResearchSettings


def test_settings_reads_environment_without_exposing_secrets() -> None:
    settings = ResearchSettings.from_env({
        "DATABASE_URL": "postgresql://user:password@localhost/db",
        "OPENAI_API_KEY": "private-key",
        "RESEARCH_ENABLED_PROVIDERS": "openai,anthropic",
        "RESEARCH_BENCHMARK_CONCURRENCY": "2",
        "RESEARCH_SCOUT_MODEL": "openai:configured-scout",
        "RESEARCH_LOGFIRE_ENABLED": "true",
    })
    assert settings.database_dsn == "postgresql://user:password@localhost/db"
    assert settings.enabled_providers == ("openai", "anthropic")
    assert settings.benchmark_concurrency == 2
    assert settings.logfire_enabled is True
    assert settings.model_overrides["RESEARCH_SCOUT_MODEL"] == "openai:configured-scout"
    assert "private-key" not in repr(settings)
    assert "password" not in repr(settings)


def test_policy_uses_current_nonempty_override(monkeypatch) -> None:
    monkeypatch.setenv("RESEARCH_SCOUT_MODEL", "openai:configured-scout")
    assert get_policy("quality").for_role(ResearchRole.SCOUT).model == "openai:configured-scout"
    monkeypatch.setenv("RESEARCH_SCOUT_MODEL", "")
    assert get_policy("quality").for_role(ResearchRole.SCOUT).model != ""


def test_policy_applies_typed_settings_override() -> None:
    settings = ResearchSettings.from_env({"RESEARCH_SCOUT_MODEL": "openai:typed-scout"})
    policy = get_policy("quality", model_overrides=settings.model_overrides)
    assert policy.for_role(ResearchRole.SCOUT).model == "openai:typed-scout"


def test_local_dotenv_loads_with_exported_environment_precedence(tmp_path, monkeypatch) -> None:
    import os
    from unittest.mock import patch

    (tmp_path / ".env").write_text("OPENAI_API_KEY=file-key\nANTHROPIC_API_KEY=file-anthropic\n")
    monkeypatch.chdir(tmp_path)
    with patch.dict(os.environ, {"OPENAI_API_KEY": "exported-key"}, clear=True):
        settings = ResearchSettings.from_env()
        assert settings.provider_keys["openai"].get_secret_value() == "exported-key"
        assert settings.provider_keys["anthropic"].get_secret_value() == "file-anthropic"
        assert "file-anthropic" not in repr(settings)

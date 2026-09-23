from __future__ import annotations

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


def test_local_dotenv_loads_with_exported_environment_precedence(tmp_path, monkeypatch) -> None:
    import os
    from unittest.mock import patch

    import dotenv

    monkeypatch.setattr("research_loop.settings.load_dotenv", dotenv.load_dotenv)  # conftest turns it off

    (tmp_path / ".env").write_text("OPENAI_API_KEY=file-key\nANTHROPIC_API_KEY=file-anthropic\n")
    monkeypatch.chdir(tmp_path)
    with patch.dict(os.environ, {"OPENAI_API_KEY": "exported-key"}, clear=True):
        settings = ResearchSettings.from_env()
        assert settings.provider_keys["openai"].get_secret_value() == "exported-key"
        assert settings.provider_keys["anthropic"].get_secret_value() == "file-anthropic"
        assert "file-anthropic" not in repr(settings)

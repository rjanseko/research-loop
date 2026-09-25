from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, Field, SecretStr

PROVIDER_KEY_ENV = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "google": "GOOGLE_API_KEY",
    "xai": "XAI_API_KEY",
    "zai": "ZAI_API_KEY",
    # One key for the models OpenRouter serves, as `openrouter:vendor/model`.
    "openrouter": "OPENROUTER_API_KEY",
}

MODEL_OVERRIDE_ENV = (
    "RESEARCH_PLANNER_MODEL",
    "RESEARCH_SCOUT_MODEL",
    "RESEARCH_CHEAP_SCOUT_MODEL",
    "RESEARCH_GAP_MODEL",
    "RESEARCH_DEEP_MODEL",
    "RESEARCH_SYNTH_MODEL",
    "RESEARCH_VERIFY_MODEL",
    "RESEARCH_MULTIMODAL_MODEL",
    "RESEARCH_ALT_DEEP_MODEL",
    "RESEARCH_BREADTH_SCOUT_MODEL",
    "RESEARCH_GLM_GAP_MODEL",
    "RESEARCH_GLM_CHEAP_MODEL",
    "RESEARCH_VALUE_PLANNER_MODEL",
    "RESEARCH_VALUE_GAP_MODEL",
    "RESEARCH_VALUE_DEEP_MODEL",
    "RESEARCH_VALUE_SYNTH_MODEL",
    "RESEARCH_VALUE_VERIFY_MODEL",
)


def model_provider(model_id: str) -> str | None:
    """The provider of a `provider:model` id, or None when it names no known provider or no model."""
    provider, separator, model = model_id.partition(":")
    return provider if separator and model.strip() and provider in PROVIDER_KEY_ENV else None


class ResearchSettings(BaseModel):
    """Typed local configuration. Exported variables override an optional local .env."""

    database_url: SecretStr | None = None
    provider_keys: dict[str, SecretStr] = Field(default_factory=dict)
    enabled_providers: tuple[str, ...] = ()
    model_overrides: dict[str, str] = Field(default_factory=dict)
    benchmark_cache: Path = Path(".cache/research-loop")
    benchmark_output: Path = Path("benchmark_outputs")
    benchmark_concurrency: int = Field(default=1, ge=1)
    logfire_enabled: bool = False
    scholarly_cache_mode: Literal["off", "live", "record", "replay"] = "live"
    openalex_api_key: SecretStr | None = None
    crossref_mailto: str | None = None
    semantic_scholar_api_key: SecretStr | None = None
    grobid_url: str | None = None

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> ResearchSettings:
        """Load configuration; a `RESEARCH_*_MODEL` override that is not `provider:model` for a
        provider in PROVIDER_KEY_ENV is refused here, before any job starts."""
        if environ is None:
            load_dotenv(dotenv_path=Path.cwd() / ".env", override=False)
        source = os.environ if environ is None else environ
        keys = {
            provider: SecretStr(source[name].strip())
            for provider, name in PROVIDER_KEY_ENV.items()
            if source.get(name, "").strip()
        }
        explicit = source.get("RESEARCH_ENABLED_PROVIDERS", "").strip()
        enabled = (
            tuple(part.strip().lower() for part in explicit.split(",") if part.strip())
            if explicit
            else tuple(keys)
        )
        unknown = set(enabled) - set(PROVIDER_KEY_ENV)
        if unknown:
            raise ValueError(f"unknown providers in RESEARCH_ENABLED_PROVIDERS: {', '.join(sorted(unknown))}")
        overrides = {name: source[name].strip() for name in MODEL_OVERRIDE_ENV if source.get(name, "").strip()}
        invalid = [f"{name}={value}" for name, value in overrides.items() if model_provider(value) is None]
        if invalid:
            raise ValueError(
                f"model overrides must be provider:model with a provider from {', '.join(sorted(PROVIDER_KEY_ENV))}: "
                + ", ".join(invalid)
            )
        return cls(
            database_url=SecretStr(source["DATABASE_URL"].strip()) if source.get("DATABASE_URL", "").strip() else None,
            provider_keys=keys,
            enabled_providers=enabled,
            model_overrides=overrides,
            benchmark_cache=Path(source.get("RESEARCH_BENCHMARK_CACHE") or ".cache/research-loop").expanduser(),
            benchmark_output=Path(source.get("RESEARCH_BENCHMARK_OUTPUT") or "benchmark_outputs").expanduser(),
            benchmark_concurrency=int(source.get("RESEARCH_BENCHMARK_CONCURRENCY") or "1"),
            logfire_enabled=source.get("RESEARCH_LOGFIRE_ENABLED", "false").strip().lower(),
            scholarly_cache_mode=source.get("RESEARCH_SCHOLAR_CACHE_MODE", "live").strip().lower(),
            openalex_api_key=SecretStr(source["OPENALEX_API_KEY"].strip()) if source.get("OPENALEX_API_KEY", "").strip() else None,
            crossref_mailto=source.get("CROSSREF_MAILTO", "").strip() or None,
            semantic_scholar_api_key=(
                SecretStr(source["SEMANTIC_SCHOLAR_API_KEY"].strip())
                if source.get("SEMANTIC_SCHOLAR_API_KEY", "").strip() else None
            ),
            grobid_url=source.get("GROBID_URL", "").strip() or None,
        )

    @property
    def database_dsn(self) -> str | None:
        return self.database_url.get_secret_value() if self.database_url else None

    def has_credential(self, provider: str) -> bool:
        return provider in self.provider_keys

    def provider_enabled(self, provider: str) -> bool:
        return provider in self.enabled_providers

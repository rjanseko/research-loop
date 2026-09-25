"""Typed configuration from the environment and an optional `.env`, validated at startup.

Values come from, highest first: arguments passed to `Settings(...)`, environment variables, `.env`
in the working directory, then the defaults below. Research Loop's own variables start with
`RESEARCH_`; nested ones use a double underscore, such as `RESEARCH_MODELS__SCOUT=openai:gpt-6-luna`
or `RESEARCH_LIMITS__COST_USD=1.5`. Provider keys, `DATABASE_URL`, and `LOGFIRE_TOKEN` keep their
usual names. Keys are read into `SecretStr` values and handed to each provider directly; the
process environment is never modified.
"""
from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import (
    AliasChoices,
    BaseModel,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from .acquisition import CacheMode

# The providers Research Loop can call, and the variable each one's API key is read from.
PROVIDER_KEYS = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "zai": "ZAI_API_KEY",
    "google": "GOOGLE_API_KEY",
}


def model_provider(model_id: str) -> str | None:
    """The provider of a `provider:model` ID, or None when it names no known provider or no model."""
    provider, separator, model = model_id.partition(":")
    return provider if separator and model.strip() and provider in PROVIDER_KEYS else None


def _model_id(value: str) -> str:
    value = value.strip()
    if model_provider(value) is None:
        raise ValueError(f"{value!r} is not provider:model with a provider from {', '.join(PROVIDER_KEYS)}")
    return value


class ScoutModels(BaseModel):
    """The model each Scout role runs on. Workflow and model choice stay separate: change these freely.

    The planner, synthesizer, and fallback are the settings study's lineup (docs/lessons.md). The
    scout is `gpt-6-luna`: on the screened cases it finished inside the deadline, and Flash at max did
    not. `fallback` takes a planner or synthesizer call when its model refuses it or its provider fails;
    Opus 5.5 refused to plan one ordinary research question as a biological risk.
    """

    planner: str = "openai:gpt-6-sol"
    scout: str = "openai:gpt-6-luna"
    synthesizer: str = "anthropic:claude-opus-5-5"
    fallback: str | None = "openai:gpt-6-sol"
    # None keeps effort_for's default, which is xhigh for every GLM scout.
    scout_effort: Literal["low", "medium", "high", "xhigh"] | None = None

    @field_validator("planner", "scout", "synthesizer", "fallback")
    @classmethod
    def _provider_model(cls, value: str | None) -> str | None:
        return None if value is None or not value.strip() else _model_id(value)


class ScoutLimits(BaseModel):
    """What one Scout run may spend. Dollar shares are allocated before the run starts (scout.py)."""

    cost_usd: float = Field(0.75, gt=0, description="Total cap for the run")
    planner_usd: float = Field(0.05, gt=0)
    # Opus 5.5 synthesizes a Scout-sized ledger for about $0.25; this leaves room for one validation retry.
    synthesis_usd: float = Field(0.40, gt=0)
    # Opt-in gap analysis and one targeted follow-up use a separate envelope, keeping scout-v1 unchanged.
    followup_cost_usd: float = Field(1.25, gt=0)
    gap_usd: float = Field(0.10, gt=0)
    deep_dive_usd: float = Field(0.25, gt=0)
    followup_deadline_seconds: float = Field(600, gt=0)
    gap_seconds: float = Field(45, gt=0)
    deep_dive_seconds: float = Field(180, gt=0)
    deep_dive_requests: int = Field(8, ge=2)
    deep_dive_productive_calls: int = Field(10, ge=1)
    deep_dive_misses: int = Field(6, ge=1)
    max_questions: int = Field(4, ge=1, le=8)
    parallel_scouts: int = Field(4, ge=1)
    # The settings study's scout budget: requests, productive calls, and misses (budget_notes.py).
    scout_requests: int = Field(12, ge=2)
    scout_productive_calls: int = Field(16, ge=1)
    scout_misses: int = Field(12, ge=1)
    scout_tokens: int = Field(400_000, ge=1_000)
    synthesis_tokens: int = Field(200_000, ge=1_000)
    synthesis_max_output_tokens: int = Field(32_000, ge=1_000)
    deadline_seconds: float = Field(360, gt=0, description="Wall-clock limit for the whole run")
    research_seconds: float = Field(270, gt=0, description="Scouts still running after this are stopped")
    request_timeout_seconds: float = Field(120, gt=0, description="One model request, or between streamed chunks")

    @model_validator(mode="after")
    def _consistent(self) -> ScoutLimits:
        if self.planner_usd + self.synthesis_usd >= self.cost_usd:
            raise ValueError("planner_usd + synthesis_usd must leave part of cost_usd for scouts")
        if self.research_seconds >= self.deadline_seconds:
            raise ValueError("research_seconds must end before deadline_seconds")
        if self.planner_usd + self.synthesis_usd + self.gap_usd + self.deep_dive_usd >= self.followup_cost_usd:
            raise ValueError("followup_cost_usd must leave part of the budget for initial scouts")
        if self.research_seconds + self.gap_seconds + self.deep_dive_seconds + 90 > self.followup_deadline_seconds:
            raise ValueError("followup_deadline_seconds must reserve 90 seconds for synthesis")
        return self

    def followup_scout_usd(self, questions: int) -> float:
        return round((self.followup_cost_usd - self.planner_usd - self.synthesis_usd
                      - self.gap_usd - self.deep_dive_usd) / max(questions, 1), 4)

    def scout_usd(self, questions: int) -> float:
        """Each scout's share when `questions` scouts run."""
        return round((self.cost_usd - self.planner_usd - self.synthesis_usd) / max(questions, 1), 4)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="RESEARCH_", env_file=".env", env_file_encoding="utf-8",
        env_nested_delimiter="__", extra="ignore",
    )

    env: str = "development"
    models: ScoutModels = Field(default_factory=ScoutModels)
    limits: ScoutLimits = Field(default_factory=ScoutLimits)

    openai_api_key: SecretStr | None = Field(None, validation_alias="OPENAI_API_KEY")
    anthropic_api_key: SecretStr | None = Field(None, validation_alias="ANTHROPIC_API_KEY")
    zai_api_key: SecretStr | None = Field(None, validation_alias="ZAI_API_KEY")
    google_api_key: SecretStr | None = Field(None, validation_alias="GOOGLE_API_KEY")
    # Comma-separated; empty means every provider with a key.
    enabled_providers: Annotated[tuple[str, ...], NoDecode] = ()

    database_url: SecretStr | None = Field(None, validation_alias=AliasChoices("DATABASE_URL", "RESEARCH_DATABASE_URL"))

    # Tracing is on by default and sends to Logfire only when a token is set (telemetry.py).
    logfire: bool = True
    logfire_token: SecretStr | None = Field(None, validation_alias="LOGFIRE_TOKEN")

    cache_dir: Path = Path(".cache/research-loop")
    cache_mode: CacheMode = "live"
    openalex_api_key: SecretStr | None = Field(None, validation_alias="OPENALEX_API_KEY")
    crossref_mailto: str | None = Field(None, validation_alias="CROSSREF_MAILTO")

    @field_validator("enabled_providers", mode="before")
    @classmethod
    def _split(cls, value: object) -> object:
        if isinstance(value, str):
            value = tuple(part.strip().lower() for part in value.split(",") if part.strip())
        if unknown := sorted(set(value or ()) - set(PROVIDER_KEYS)):  # type: ignore[arg-type]
            raise ValueError(f"unknown providers: {', '.join(unknown)}; choose from {', '.join(PROVIDER_KEYS)}")
        return value

    def api_key(self, provider: str) -> str | None:
        secret: SecretStr | None = getattr(self, f"{provider}_api_key", None)
        return secret.get_secret_value() if secret else None

    def provider_enabled(self, provider: str) -> bool:
        return provider in self.enabled_providers if self.enabled_providers else self.api_key(provider) is not None

    @property
    def database_dsn(self) -> str | None:
        return self.database_url.get_secret_value() if self.database_url else None

    def route_problems(self) -> list[str]:
        """Why the configured models cannot run, before any call is made; empty when they can."""
        problems = []
        roles = {"planner": self.models.planner, "scout": self.models.scout, "synthesizer": self.models.synthesizer}
        if self.models.fallback:
            roles["fallback"] = self.models.fallback
        for role, model_id in roles.items():
            provider = model_provider(model_id)
            if provider is None:
                problems.append(f"{role}: {model_id} names no known provider")
            elif self.api_key(provider) is None:
                problems.append(f"{role}: {model_id} needs {PROVIDER_KEYS[provider]}")
            elif not self.provider_enabled(provider):
                problems.append(f"{role}: {model_id} needs {provider}, which is not enabled (RESEARCH_ENABLED_PROVIDERS)")
        return problems

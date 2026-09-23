from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal, Mapping

from dotenv import load_dotenv
from pydantic import BaseModel, Field, SecretStr

# Model calls authenticate only with this variable, and only when it is already
# exported. A project .env must not supply it.
OPENROUTER_API_KEY_ENV = "OPENROUTER_API_KEY"

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
)


class ResearchSettings(BaseModel):
    """Typed local configuration. Exported variables override an optional local .env.

    `openrouter_api_key` is only the process environment's `OPENROUTER_API_KEY`.
    """

    database_url: SecretStr | None = None
    openrouter_api_key: SecretStr | None = None
    model_overrides: dict[str, str] = Field(default_factory=dict)
    benchmark_cache: Path = Path(".cache/research-loop")
    benchmark_output: Path = Path("benchmark_outputs")
    benchmark_concurrency: int = Field(default=1, ge=1)
    logfire_enabled: bool = False
    scholarly_cache_mode: Literal["off", "live", "record", "replay"] = "live"
    openalex_api_key: SecretStr | None = None
    crossref_mailto: str | None = None
    grobid_url: str | None = None

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> ResearchSettings:
        """Load configuration.

        With no mapping, values come from the process environment, then an optional
        `.env` fills gaps. `OPENROUTER_API_KEY` is the exception: only a value already
        in the process environment is kept, and a `.env` entry is removed so model
        calls cannot see it. A passed mapping replaces both and may include the key;
        tests use that to avoid the developer's environment. A `RESEARCH_*_MODEL`
        override that is not `openrouter:<author>/<slug>` is refused here, before any job starts.
        """
        if environ is None:
            global_key = os.environ.get(OPENROUTER_API_KEY_ENV, "").strip()
            load_dotenv(dotenv_path=Path.cwd() / ".env", override=False)
            if global_key:
                os.environ[OPENROUTER_API_KEY_ENV] = global_key
            else:
                os.environ.pop(OPENROUTER_API_KEY_ENV, None)
            source: Mapping[str, str] = os.environ
            openrouter_key = global_key or None
        else:
            source = environ
            openrouter_key = source.get(OPENROUTER_API_KEY_ENV, "").strip() or None
        model_overrides = {name: source[name].strip() for name in MODEL_OVERRIDE_ENV if source.get(name, "").strip()}
        if invalid := [f"{name}={value}" for name, value in model_overrides.items() if not _is_openrouter_id(value)]:
            raise ValueError(
                "model overrides must be openrouter:<author>/<slug>, as in .env.example: " + ", ".join(invalid)
            )
        return cls(
            database_url=SecretStr(source["DATABASE_URL"].strip()) if source.get("DATABASE_URL", "").strip() else None,
            openrouter_api_key=SecretStr(openrouter_key) if openrouter_key else None,
            model_overrides=model_overrides,
            benchmark_cache=Path(source.get("RESEARCH_BENCHMARK_CACHE") or ".cache/research-loop").expanduser(),
            benchmark_output=Path(source.get("RESEARCH_BENCHMARK_OUTPUT") or "benchmark_outputs").expanduser(),
            benchmark_concurrency=int(source.get("RESEARCH_BENCHMARK_CONCURRENCY") or "1"),
            logfire_enabled=source.get("RESEARCH_LOGFIRE_ENABLED", "false").strip().lower(),
            scholarly_cache_mode=source.get("RESEARCH_SCHOLAR_CACHE_MODE", "live").strip().lower(),
            openalex_api_key=SecretStr(source["OPENALEX_API_KEY"].strip()) if source.get("OPENALEX_API_KEY", "").strip() else None,
            crossref_mailto=source.get("CROSSREF_MAILTO", "").strip() or None,
            grobid_url=source.get("GROBID_URL", "").strip() or None,
        )

    @property
    def database_dsn(self) -> str | None:
        return self.database_url.get_secret_value() if self.database_url else None


def openrouter_slug(model_id: str) -> str:
    """Return the `author/slug` of an `openrouter:` model id."""
    provider, separator, slug = model_id.partition(":")
    if provider != "openrouter" or not separator or "/" not in slug:
        raise ValueError(f"model must be openrouter:<author>/<slug>, got {model_id!r}")
    return slug


def _is_openrouter_id(model_id: str) -> bool:
    try:
        openrouter_slug(model_id)
    except ValueError:
        return False
    return True


def openrouter_model(model_id: str, api_key: str) -> Any:
    """An OpenRouter model that authenticates with `api_key`, not the environment."""
    from pydantic_ai.models.openrouter import OpenRouterModel
    from pydantic_ai.providers.openrouter import OpenRouterProvider

    return OpenRouterModel(openrouter_slug(model_id), provider=OpenRouterProvider(api_key=api_key))


def model_for_call(model_id: str, api_key: SecretStr | None) -> Any:
    """The model a run passes to PydanticAI.

    With a key, the id becomes an OpenRouter model that authenticates with it. Without one
    the id passes through unchanged: an `Agent.override(model=...)` in force wins, as in
    scripted tests, and otherwise PydanticAI's OpenRouter provider refuses the call for
    lack of OPENROUTER_API_KEY, which `from_env` leaves in the environment only when it was
    exported.
    """
    if api_key is None:
        return model_id
    return openrouter_model(model_id, api_key.get_secret_value())

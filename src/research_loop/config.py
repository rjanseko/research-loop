"""Typed configuration from the environment and an optional `.env`, validated at startup.

Values come from, highest first: arguments passed to `Settings(...)`, environment variables, `.env`
in the working directory, then the defaults below. Research Loop's own variables start with
`RESEARCH_`; nested ones use a double underscore, such as `RESEARCH_MODELS__SCOUT=openai:gpt-6-luna@high`
or `RESEARCH_LIMITS__COST_USD=1.5`. Provider keys, `DATABASE_URL`, and `LOGFIRE_TOKEN` keep their
usual names. Keys are read into `SecretStr` values and handed to each provider directly; the
process environment is never modified.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Any, Literal, get_args

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
from .schemas import Depth

# The providers Research Loop can call, and the variable each one's API key is read from.
PROVIDER_KEYS = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "zai": "ZAI_API_KEY",
    "google": "GOOGLE_API_KEY",
}


# Reasoning effort, named with every model as `provider:model@effort`. GLM's `xhigh` is sent as `max`.
Effort = Literal["low", "medium", "high", "xhigh"]
EFFORTS: tuple[str, ...] = get_args(Effort)


def split_model(spec: str) -> tuple[str, str]:
    """The `provider:model` ID and the effort of a `provider:model@effort` setting; the effort is empty
    when the setting names none."""
    model_id, separator, effort = spec.strip().rpartition("@")
    return (model_id, effort) if separator else (spec.strip(), "")


def model_spec_problem(spec: str) -> str | None:
    """Why `spec` is not a usable `provider:model@effort`, or None when it is."""
    model_id, effort = split_model(spec)
    if model_provider(model_id) is None:
        return f"{spec!r} is not provider:model@effort with a provider from {', '.join(PROVIDER_KEYS)}"
    if effort not in EFFORTS:
        return f"{spec!r} must name its effort: {model_id}@<{'|'.join(EFFORTS)}>"
    return None


# The bug-finding harness's scripted models (dryrun.py): no key, priced as Luna, and usable only with
# the offline world, so neither can reach a real run.
FAKE_PROVIDER = "fake"


def model_provider(model_id: str) -> str | None:
    """The provider of a `provider:model` ID, or None when it names no known provider or no model."""
    provider, separator, model = model_id.partition(":")
    known = provider in PROVIDER_KEYS or provider == FAKE_PROVIDER
    return provider if separator and model.strip() and known else None


def _model_spec(value: str) -> str:
    value = value.strip()
    if problem := model_spec_problem(value):
        raise ValueError(problem)
    return value


class ScoutModels(BaseModel):
    """The model each Scout role runs on. Workflow and model choice stay separate: change these freely.

    The planner, synthesizer, and fallback are the settings study's lineup (docs/lessons.md). The
    scout is `gpt-6-luna`: on the screened cases it finished inside the deadline, and Flash at max did
    not. `fallback` takes a planner or synthesizer call when its model refuses it or its provider fails;
    Opus 5.5 refused to plan one ordinary research question as a biological risk.

    Each is `provider:model@effort`, and a model without its effort is refused, so a model and the
    reasoning effort it runs at are always chosen together.
    """

    planner: str = "openai:gpt-6-sol@high"
    scout: str = "openai:gpt-6-luna@high"
    # A second scout model for deep runs, such as zai:glm-5.3@xhigh: when set, a deep run's even-numbered
    # questions and deep dives use it, so its scouts draw on a second provider's rate limit. It was built
    # when a deep run's scouts sent only about 115,000 tokens a minute (d8c8198e, 2c66e8bd); that ceiling
    # turned out to be the pacer's stale 200,000 limit, a tenth of the account's real one.
    scout_alt: str | None = None
    synthesizer: str = "anthropic:claude-opus-5-5@medium"
    fallback: str | None = "openai:gpt-6-sol@high"
    # Grades rubric points and assesses quality (evals.py, quality.py); each grade records it.
    judge: str = "openai:gpt-6-sol@high"

    @model_validator(mode="before")
    @classmethod
    def _no_separate_effort(cls, data: Any) -> Any:
        if isinstance(data, dict) and "scout_effort" in data:
            raise ValueError("RESEARCH_MODELS__SCOUT_EFFORT was removed; put the effort in the model, "
                             "such as RESEARCH_MODELS__SCOUT=openai:gpt-6-luna@high")
        return data

    @field_validator("planner", "scout", "scout_alt", "synthesizer", "fallback", "judge")
    @classmethod
    def _provider_model(cls, value: str | None) -> str | None:
        return None if value is None or not value.strip() else _model_spec(value)


class DepthTier(BaseModel):
    """How one depth changes the standard limits: each field it sets replaces ScoutLimits' own."""

    max_questions: int | None = Field(None, ge=1, le=8)
    cost_usd: float | None = Field(None, gt=0)
    synthesis_usd: float | None = Field(None, gt=0)
    research_seconds: float | None = Field(None, gt=0)
    deadline_seconds: float | None = Field(None, gt=0)
    followup_cost_usd: float | None = Field(None, gt=0)
    followup_deadline_seconds: float | None = Field(None, gt=0)
    deep_dive_seconds: float | None = Field(None, gt=0)
    scout_productive_calls: int | None = Field(None, ge=1)
    deep_dive_requests: int | None = Field(None, ge=2)
    deep_dive_productive_calls: int | None = Field(None, ge=1)
    deep_dive_misses: int | None = Field(None, ge=1)
    # A run at this depth adds the gap follow-up, with the follow-up envelope, as `--follow-up` does.
    follow_up: bool = False


# Each depth's own limits. A setting for one of them, such as RESEARCH_LIMITS__DEEP__MAX_QUESTIONS, replaces
# only that one; the rest of the depth keeps these.
_QUICK = {"max_questions": 2, "cost_usd": 0.30, "synthesis_usd": 0.12, "research_seconds": 240,
          "deadline_seconds": 360}
# In the first deep example run (d8c8198e), three of four Luna scouts were cut off at the 480-second research
# deadline with over 90% of their dollar share unspent, and the fourth scout and a deep dive stopped on their
# productive calls. Paced under Luna's token rate, eight scouts need far longer than four, so a deep run gets
# more time and tool calls, and each deep dive a full scout's loop budget. Its $3.00 leaves eight scouts about
# $0.21 each, room for a GLM-5.3 second scout model (ScoutModels.scout_alt), which costs about seven times Luna.
_DEEP = {"max_questions": 8, "follow_up": True, "followup_cost_usd": 3.00, "research_seconds": 1200,
         "deadline_seconds": 1920,
         "followup_deadline_seconds": 1920, "deep_dive_seconds": 480, "scout_productive_calls": 48,
         "deep_dive_requests": 20, "deep_dive_productive_calls": 32, "deep_dive_misses": 16}


class ScoutLimits(BaseModel):
    """What one Scout run may spend. Dollar shares are allocated before the run starts (scout.py)."""

    cost_usd: float = Field(0.75, gt=0, description="Total cap for the run")
    planner_usd: float = Field(0.05, gt=0)
    # Opus 5.5 synthesizes a Scout-sized ledger for about $0.25; this leaves room for one validation retry.
    synthesis_usd: float = Field(0.40, gt=0)
    # Opt-in gap analysis and up to `max_gaps` parallel deep dives use a separate envelope, keeping the plain
    # Scout envelope unchanged. Each deep dive gets `deep_dive_usd`.
    followup_cost_usd: float = Field(2.00, gt=0)
    gap_usd: float = Field(0.10, gt=0)
    max_gaps: int = Field(3, ge=1, le=6)
    deep_dive_usd: float = Field(0.25, gt=0)
    followup_deadline_seconds: float = Field(900, gt=0)
    gap_seconds: float = Field(45, gt=0)
    deep_dive_seconds: float = Field(240, gt=0)
    deep_dive_requests: int = Field(12, ge=2)
    deep_dive_productive_calls: int = Field(16, ge=1)
    deep_dive_misses: int = Field(8, ge=1)
    max_questions: int = Field(4, ge=1, le=8)
    # Enough for a deep plan's questions at once; the token pacer (rate_limit.py) keeps them under the
    # provider's rate limit.
    parallel_scouts: int = Field(8, ge=1)
    # The planner chooses a depth (`ResearchPlan.depth`); standard is these limits unchanged. A quick
    # question gets two scouts and a small report; a deep one up to eight scouts, the gap follow-up, and
    # more time and tool calls.
    quick: DepthTier = DepthTier(**_QUICK)
    deep: DepthTier = DepthTier(**_DEEP)
    # A scout's loop budget: requests, productive calls, and misses (budget_notes.py). The settings study set
    # 12, 16, and 12 to stop Flash loops that only failed; with Luna, 39 of 68 scouts stopped on the 16
    # productive calls after reading one to eight pages, while spending about 15% of their dollar share.
    scout_requests: int = Field(20, ge=2)
    scout_productive_calls: int = Field(32, ge=1)
    scout_misses: int = Field(16, ge=1)
    # Billed input across a scout's requests; each request resends the loop's history.
    scout_tokens: int = Field(1_000_000, ge=1_000)
    guarded_scout_max_output_tokens: int = Field(24_000, ge=1_000)
    synthesis_tokens: int = Field(200_000, ge=1_000)
    synthesis_max_output_tokens: int = Field(32_000, ge=1_000)
    deadline_seconds: float = Field(720, gt=0, description="Wall-clock limit for the whole run")
    research_seconds: float = Field(480, gt=0, description="Scouts still running after this are stopped")
    request_timeout_seconds: float = Field(120, gt=0, description="One model request, or between streamed chunks")

    @model_validator(mode="before")
    @classmethod
    def _depth_defaults(cls, data: Any) -> Any:
        """A depth given as a mapping, from settings or the environment, keeps the limits it does not name."""
        if isinstance(data, dict):
            for name, defaults in (("quick", _QUICK), ("deep", _DEEP)):
                if isinstance(data.get(name), dict):
                    data = data | {name: defaults | data[name]}
        return data

    @model_validator(mode="after")
    def _consistent(self) -> ScoutLimits:
        if self.planner_usd + self.synthesis_usd >= self.cost_usd:
            raise ValueError("planner_usd + synthesis_usd must leave part of cost_usd for scouts")
        if self.research_seconds >= self.deadline_seconds:
            raise ValueError("research_seconds must end before deadline_seconds")
        if (self.planner_usd + self.synthesis_usd + self.gap_usd + self.max_gaps * self.deep_dive_usd
                >= self.followup_cost_usd):
            raise ValueError("followup_cost_usd must leave part of the budget for initial scouts")
        if self.research_seconds + self.gap_seconds + self.deep_dive_seconds + 90 > self.followup_deadline_seconds:
            raise ValueError("followup_deadline_seconds must reserve 90 seconds for synthesis")
        for depth in ("quick", "deep"):
            if getattr(self, depth).model_dump(exclude_none=True, exclude={"follow_up"}):
                try:
                    self.for_depth(depth)
                except ValueError as exc:
                    raise ValueError(f"the {depth} depth's limits are inconsistent: {exc}") from exc
        return self

    def question_caps(self) -> dict[str, int]:
        """The most research questions a plan may have at each depth."""
        return {"quick": self.for_depth("quick").max_questions, "standard": self.max_questions,
                "deep": self.for_depth("deep").max_questions}

    def for_depth(self, depth: Depth) -> ScoutLimits:
        """These limits for a run at `depth`, checked as a whole. The result has no depths of its own."""
        if depth == "standard":
            return self
        update = getattr(self, depth).model_dump(exclude_none=True, exclude={"follow_up"})
        # Empty tiers, as models rather than mappings, so the result takes no depth's defaults.
        return ScoutLimits.model_validate(self.model_dump() | update | {"quick": DepthTier(), "deep": DepthTier()})

    def follows_up(self, depth: Depth) -> bool:
        """Whether a run at `depth` adds the gap follow-up."""
        return depth != "standard" and getattr(self, depth).follow_up

    def followup_scout_usd(self, questions: int) -> float:
        return round((self.followup_cost_usd - self.planner_usd - self.synthesis_usd
                      - self.gap_usd - self.max_gaps * self.deep_dive_usd) / max(questions, 1), 4)

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
    # Provider token rate limits per minute, by provider:model; scouts on a listed model are paced under
    # it (rate_limit.py). Unset, a run starts from this default and switches to the limit OpenAI reports
    # with its first response; set, as JSON such as RESEARCH_TOKENS_PER_MINUTE='{"openai:gpt-6-luna":
    # 2000000}', the limit is fixed, and '{}' turns pacing off. The default is this account's OpenAI tier,
    # read from its response headers on 28 September 2026.
    tokens_per_minute: dict[str, int] = Field(default_factory=lambda: {"openai:gpt-6-luna": 2_000_000})

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
    # Where a study's runs keep their lookups (`for_study`).
    study_cache_root: Path = Path(".cache/studies")
    openalex_api_key: SecretStr | None = Field(None, validation_alias="OPENALEX_API_KEY")
    # The web search engine behind the scouts' `web_search` tool. DuckDuckGo, through the `ddgs` scraping
    # library, stays the default until a paired study shows another is better; it returned nothing for 34%
    # of 2,636 production searches, and 4 of 6 such queries tried again later found results. "hybrid" asks
    # DuckDuckGo and then Exa when DuckDuckGo finds nothing or fails (web.HybridSearch); Exa is paid per
    # search and needs EXA_API_KEY (web.exa_engine).
    search_engine: Literal["duckduckgo", "exa", "hybrid"] = "duckduckgo"
    exa_api_key: SecretStr | None = Field(None, validation_alias="EXA_API_KEY")
    # Readers tried in order when our fetch cannot read a page (reading.py): "oa", "exa", "firecrawl".
    # Unset, it is every reader that can run: the free open-access reader, then Exa and Firecrawl when
    # their keys are set (`readers`). Publishers behind bot protection refused 2 to 9% of fetches, and in
    # the fetch bake-off this chain read 139 of 160 pages our fetch could not. An empty value turns it off.
    read_fallback: Annotated[tuple[str, ...] | None, NoDecode] = None
    firecrawl_api_key: SecretStr | None = Field(None, validation_alias="FIRECRAWL_API_KEY")
    # The bug-finding harness (dryrun.py): with a seed, the research tools answer from a generated
    # offline world instead of the network, failing at `offline_fault_rate`. Only `fake:` models may run.
    offline_world: int | None = None
    offline_fault_rate: float = Field(0.2, ge=0, le=1)
    crossref_mailto: str | None = Field(None, validation_alias="CROSSREF_MAILTO")

    @field_validator("read_fallback", mode="before")
    @classmethod
    def _readers(cls, value: object) -> object:
        from .reading import READERS

        if value is None:
            return None
        if isinstance(value, str):
            value = tuple(part.strip().lower() for part in value.split(",") if part.strip())
        if unknown := sorted(set(value or ()) - set(READERS)):  # type: ignore[arg-type]
            raise ValueError(f"unknown readers: {', '.join(unknown)}; choose from {', '.join(READERS)}")
        return value

    def readers(self) -> tuple[str, ...]:
        """The reading fallback a run uses: `read_fallback` when set, else every reader that can run."""
        if self.read_fallback is not None:
            return self.read_fallback
        return ("oa", *(("exa",) if self.exa_api_key else ()), *(("firecrawl",) if self.firecrawl_api_key else ()))

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

    def for_study(self, study: str) -> Settings:
        """These settings for a run labeled with `study`: its research tools use the study's own cache in
        `reuse` mode, so every arm, on any day, gets the same answer to the same lookup, and a new lookup
        is made once and kept. RESEARCH_CACHE_MODE or RESEARCH_CACHE_DIR, when set, still win."""
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", study):
            raise ValueError(f"study name {study!r} may use only letters, digits, '.', '_', and '-'")
        update: dict[str, Any] = {}
        if "cache_mode" not in self.model_fields_set:
            update["cache_mode"] = "reuse"
        if "cache_dir" not in self.model_fields_set:
            update["cache_dir"] = self.study_cache_root / study
        return self.model_copy(update=update)

    @property
    def database_dsn(self) -> str | None:
        return self.database_url.get_secret_value() if self.database_url else None

    def route_problems(self) -> list[str]:
        """Why the configured models cannot run, before any call is made; empty when they can."""
        problems = []
        roles = {"planner": self.models.planner, "scout": self.models.scout, "synthesizer": self.models.synthesizer,
                 "judge": self.models.judge}
        if self.models.fallback:
            roles["fallback"] = self.models.fallback
        if self.models.scout_alt:
            roles["scout_alt"] = self.models.scout_alt
        if self.search_engine in ("exa", "hybrid") and self.exa_api_key is None and self.offline_world is None:
            problems.append(f"web search: {self.search_engine} needs EXA_API_KEY")
        if self.offline_world is None:
            if "exa" in self.readers() and self.exa_api_key is None:
                problems.append("reading fallback: exa needs EXA_API_KEY")
            if "firecrawl" in self.readers() and self.firecrawl_api_key is None:
                problems.append("reading fallback: firecrawl needs FIRECRAWL_API_KEY")
        fake = {role for role, spec in roles.items() if spec.startswith(f"{FAKE_PROVIDER}:")}
        if fake and self.offline_world is None:
            problems.append(f"{', '.join(sorted(fake))}: fake models run only in the offline world (RESEARCH_OFFLINE_WORLD)")
        if self.offline_world is not None and (real := sorted(set(roles) - fake)):
            problems.append(f"{', '.join(real)}: the offline world runs only fake models")
        for role, spec in roles.items():
            if problem := model_spec_problem(spec):
                problems.append(f"{role}: {problem}")
                continue
            model_id = split_model(spec)[0]
            provider = str(model_provider(model_id))
            if provider == FAKE_PROVIDER:
                continue
            if self.api_key(provider) is None:
                problems.append(f"{role}: {model_id} needs {PROVIDER_KEYS[provider]}")
            elif not self.provider_enabled(provider):
                problems.append(f"{role}: {model_id} needs {provider}, which is not enabled (RESEARCH_ENABLED_PROVIDERS)")
        return problems

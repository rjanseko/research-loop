"""Model construction: one PydanticAI model per role, with its own settings and API key.

Each model carries its settings (reasoning effort, timeout, output cap, prompt caching) instead of
receiving them per call, so a `FallbackModel` sends each model its own. Keys come from `Settings`
and go to the provider directly.

Reasoning effort follows the model, not the role:

- Every Z.ai GLM model thinks at `xhigh`, which PydanticAI sends to GLM-5.3 as `reasoning_effort: max`,
  unless `models.scout_effort` sets the scout's level.
- Opus 5.5 thinks at `medium`, its own default: it thinks more at each level than Opus 5 did.
- Anything else thinks at `high`.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic_ai.exceptions import ContentFilterError, ModelAPIError
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.settings import ModelSettings

from .config import PROVIDER_KEYS, Settings, model_provider
from .rate_limit import ScoutRateLimitModel

Role = Literal["planner", "scout", "synthesizer"]
Effort = Literal["low", "medium", "high", "xhigh"]


def effort_for(model_id: str) -> Effort:
    provider, _, name = model_id.partition(":")
    if provider == "zai" and name.startswith("glm-"):
        return "xhigh"
    if model_id == "anthropic:claude-opus-5-5":
        return "medium"
    return "high"


def role_effort(role: Role, model_id: str, settings: Settings) -> Effort:
    """The effort `role` runs at. `models.scout_effort` replaces the scout's default and no other role's."""
    if role == "scout" and (effort := settings.models.scout_effort) is not None:
        return effort
    return effort_for(model_id)


def model_settings(model_id: str, role: Role, settings: Settings) -> ModelSettings:
    """The settings `model_id` runs with in `role`."""
    limits = settings.limits
    result: dict[str, Any] = {"thinking": role_effort(role, model_id, settings), "timeout": limits.request_timeout_seconds}
    if role == "synthesizer":
        result["max_tokens"] = limits.synthesis_max_output_tokens
    elif role == "planner":
        # Anthropic defaults max_tokens to 4,096, shared by thinking and output.
        result["max_tokens"] = 16_000
    provider = model_provider(model_id)
    # Ask for the growing prompt prefix of a tool loop to be cached. OpenAI caches on its own, and a
    # stable key raises the hit rate; Anthropic caches only when asked; Z.ai caches on its own.
    if provider == "anthropic":
        result["anthropic_cache"] = True
    elif provider == "openai":
        result["openai_prompt_cache_key"] = f"research-loop:{model_id}"
    return ModelSettings(**result)  # type: ignore[typeddict-item]


def sent_settings(model_id: str, role: Role, settings: Settings) -> dict[str, Any]:
    """The settings `model_id` sends in `role` once PydanticAI has prepared a request, such as the
    `reasoning_effort` Z.ai receives for our `thinking` level; a run records them."""
    model = build_model(model_id, role, settings)
    prepared, parameters = model.prepare_request(None, ModelRequestParameters())
    return {"thinking": parameters.thinking, **(prepared or {})}


def build_model(model_id: str, role: Role, settings: Settings, *, sdk_retries: int | None = None) -> Model:
    """A model for `model_id` with its API key from `settings`; raises if the provider is unknown.

    `sdk_retries` replaces the provider client's own retry count. None leaves that default, which is
    two for OpenAI-compatible and Anthropic clients. A scout passes 0: those clients retry a timeout,
    and a second attempt at one slow first reply fills the research window.
    """
    provider, _, name = model_id.partition(":")
    if provider not in PROVIDER_KEYS:
        raise ValueError(f"unknown provider in {model_id!r}")
    key = settings.api_key(provider)
    if key is None:
        # Never let the SDK fall back to a key in the process environment that settings did not validate.
        raise ValueError(f"{model_id} needs {PROVIDER_KEYS[provider]}")
    own = model_settings(model_id, role, settings)
    if provider == "openai":
        from pydantic_ai.models.openai import OpenAIResponsesModel
        from pydantic_ai.providers.openai import OpenAIProvider
        model: Model = OpenAIResponsesModel(name, provider=OpenAIProvider(api_key=key), settings=own)
    elif provider == "anthropic":
        from pydantic_ai.models.anthropic import AnthropicModel
        from pydantic_ai.providers.anthropic import AnthropicProvider
        model = AnthropicModel(name, provider=AnthropicProvider(api_key=key), settings=own)
    elif provider == "zai":
        from pydantic_ai.models.zai import ZaiModel
        from pydantic_ai.providers.zai import ZaiProvider
        model = ZaiModel(name, provider=ZaiProvider(api_key=key), settings=own)
    else:
        from google.genai.types import HttpRetryOptions
        from pydantic_ai.models.google import GoogleModel
        from pydantic_ai.providers.google import GoogleProvider
        # `attempts` counts the first try, so one attempt means no retry.
        retry = None if sdk_retries is None else HttpRetryOptions(attempts=sdk_retries + 1)
        model = GoogleModel(name, provider=GoogleProvider(api_key=key, retry_options=retry), settings=own)
    if sdk_retries is not None and hasattr(client := getattr(model, "client", None), "max_retries"):
        client.max_retries = sdk_retries
    return model


def role_model(role: Role, settings: Settings) -> Model:
    """The model `role` runs on. The planner and synthesizer fall back to `models.fallback` when their
    model refuses a call (ContentFilterError) or its provider fails (ModelAPIError); scouts do not, since
    a failed scout leaves its question unanswered rather than failing the run. A scout's client makes
    one attempt, so a request that reaches the timeout is not sent again. A run-shared wrapper retries
    only timed rate limits from the provider."""
    model_id: str = getattr(settings.models, role)
    primary = build_model(model_id, role, settings, sdk_retries=0 if role == "scout" else None)
    fallback = settings.models.fallback
    if role == "scout":
        return ScoutRateLimitModel(primary)
    if not fallback or fallback == model_id:
        return primary
    return FallbackModel(primary, build_model(fallback, role, settings),
                         fallback_on=(ModelAPIError, ContentFilterError))

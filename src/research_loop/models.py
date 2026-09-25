"""Model construction: one PydanticAI model per role, with its own settings and API key.

Each model carries its settings (reasoning effort, timeout, output cap, prompt caching) instead of
receiving them per call, so a `FallbackModel` sends each model its own. Keys come from `Settings`
and go to the provider directly.

Reasoning effort follows the model, not the role:

- Every Z.ai GLM model thinks at `xhigh`, which PydanticAI sends to GLM-5.3 as `reasoning_effort: max`.
- Opus 5.5 thinks at `medium`, its own default: it thinks more at each level than Opus 5 did.
- Anything else thinks at `high`.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic_ai.exceptions import ContentFilterError, ModelAPIError
from pydantic_ai.models import Model
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.settings import ModelSettings

from .config import PROVIDER_KEYS, Settings, model_provider

Role = Literal["planner", "scout", "synthesizer"]
Effort = Literal["low", "medium", "high", "xhigh"]


def effort_for(model_id: str) -> Effort:
    provider, _, name = model_id.partition(":")
    if provider == "zai" and name.startswith("glm-"):
        return "xhigh"
    if model_id == "anthropic:claude-opus-5-5":
        return "medium"
    return "high"


def model_settings(model_id: str, role: Role, settings: Settings) -> ModelSettings:
    """The settings `model_id` runs with in `role`."""
    limits = settings.limits
    result: dict[str, Any] = {"thinking": effort_for(model_id), "timeout": limits.request_timeout_seconds}
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


def build_model(model_id: str, role: Role, settings: Settings) -> Model:
    """A model for `model_id` with its API key from `settings`; raises if the provider is unknown."""
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
        return OpenAIResponsesModel(name, provider=OpenAIProvider(api_key=key), settings=own)
    if provider == "anthropic":
        from pydantic_ai.models.anthropic import AnthropicModel
        from pydantic_ai.providers.anthropic import AnthropicProvider
        return AnthropicModel(name, provider=AnthropicProvider(api_key=key), settings=own)
    if provider == "zai":
        from pydantic_ai.models.zai import ZaiModel
        from pydantic_ai.providers.zai import ZaiProvider
        return ZaiModel(name, provider=ZaiProvider(api_key=key), settings=own)
    from pydantic_ai.models.google import GoogleModel
    from pydantic_ai.providers.google import GoogleProvider
    return GoogleModel(name, provider=GoogleProvider(api_key=key), settings=own)


def role_model(role: Role, settings: Settings) -> Model:
    """The model `role` runs on. The planner and synthesizer fall back to `models.fallback` when their
    model refuses a call (ContentFilterError) or its provider fails (ModelAPIError); scouts do not, since
    a failed scout leaves its question unanswered rather than failing the run."""
    model_id: str = getattr(settings.models, role)
    primary = build_model(model_id, role, settings)
    fallback = settings.models.fallback
    if role == "scout" or not fallback or fallback == model_id:
        return primary
    return FallbackModel(primary, build_model(fallback, role, settings),
                         fallback_on=(ModelAPIError, ContentFilterError))

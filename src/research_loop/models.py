"""Model construction: one PydanticAI model per role, with its own settings and API key.

Each model carries its settings (reasoning effort, timeout, output cap, prompt caching) instead of
receiving them per call, so a `FallbackModel` sends each model its own. Keys come from `Settings`
and go to the provider directly.

Every model is named with its reasoning effort, as `provider:model@effort`, and runs at exactly that
effort; nothing picks one for it. PydanticAI sends GLM-5.3's `xhigh` as `reasoning_effort: max`.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic_ai.exceptions import ContentFilterError, ModelAPIError
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.settings import ModelSettings

from .config import FAKE_PROVIDER, PROVIDER_KEYS, Settings, model_provider, split_model
from .history import TrimmedHistoryModel
from .rate_limit import ScoutRateLimitModel, TokenPacer, rate_limit_hook

Role = Literal["planner", "scout", "synthesizer"]


def model_settings(spec: str, role: Role, settings: Settings) -> ModelSettings:
    """The settings the `provider:model@effort` in `spec` runs with in `role`."""
    limits = settings.limits
    model_id, effort = split_model(spec)
    if not effort:
        raise ValueError(f"{spec} names no effort; write it as {model_id}@<effort>")
    result: dict[str, Any] = {"thinking": effort, "timeout": limits.request_timeout_seconds}
    if role == "synthesizer":
        result["max_tokens"] = limits.synthesis_max_output_tokens
    elif role == "planner":
        # Anthropic defaults max_tokens to 4,096, shared by thinking and output.
        result["max_tokens"] = settings.model_calls.planner_max_output_tokens
    provider = model_provider(model_id)
    # Ask for the growing prompt prefix of a tool loop to be cached. OpenAI caches on its own, and a
    # stable key raises the hit rate; Anthropic caches only when asked; Z.ai caches on its own.
    if provider == "anthropic":
        result["anthropic_cache"] = True
    elif provider == "openai":
        result["openai_prompt_cache_key"] = f"research-loop:{model_id}"
    elif provider == "deepseek":
        # PydanticAI's profile knows only `deepseek-v4-*` names as thinking models and drops the effort for
        # `deepseek-flash`, which DeepSeek then runs at its default, `high`. So the effort is sent as DeepSeek
        # documents it; it maps medium to high and xhigh to max (api-docs.deepseek.com/guides/thinking_mode).
        del result["thinking"]
        result["extra_body"] = {"thinking": {"type": "enabled"}, "reasoning_effort": effort}
    return ModelSettings(**result)  # type: ignore[typeddict-item]


def sent_settings(spec: str, role: Role, settings: Settings) -> dict[str, Any]:
    """The settings `spec` sends in `role` once PydanticAI has prepared a request, such as the
    `reasoning_effort` Z.ai receives for our `thinking` level; a run records them."""
    model = build_model(spec, role, settings)
    prepared, parameters = model.prepare_request(None, ModelRequestParameters())
    return {"thinking": parameters.thinking, **(prepared or {})}


def build_model(spec: str, role: Role, settings: Settings, *, sdk_retries: int | None = None) -> Model:
    """A model for the `provider:model@effort` in `spec`, with its API key from `settings`; raises if the
    provider is unknown or the effort is missing.

    `sdk_retries` replaces the provider client's own retry count. None leaves that default, which is
    two for OpenAI-compatible and Anthropic clients. A scout passes 0: those clients retry a timeout,
    and a second attempt at one slow first reply fills the research window.
    """
    model_id = split_model(spec)[0]
    provider, _, name = model_id.partition(":")
    if provider == FAKE_PROVIDER:
        from .dryrun import FuzzModel

        return FuzzModel(seed=settings.offline_world or 0, fault_rate=settings.offline_fault_rate, name=name)
    if provider not in PROVIDER_KEYS:
        raise ValueError(f"unknown provider in {model_id!r}")
    key = settings.api_key(provider)
    if key is None:
        # Never let the SDK fall back to a key in the process environment that settings did not validate.
        raise ValueError(f"{model_id} needs {PROVIDER_KEYS[provider]}")
    own = model_settings(spec, role, settings)
    if provider == "openai":
        import httpx2
        from pydantic_ai.models import DEFAULT_HTTP_TIMEOUT
        from pydantic_ai.models.openai import OpenAIResponsesModel
        from pydantic_ai.providers.openai import OpenAIProvider

        # PydanticAI's default client and timeouts, recording the tokens-per-minute limit OpenAI reports with
        # each response, which the pacer uses.
        http_client = httpx2.AsyncClient(
            timeout=httpx2.Timeout(timeout=DEFAULT_HTTP_TIMEOUT,
                                   connect=settings.model_calls.connect_timeout_seconds),
            event_hooks={"response": [rate_limit_hook("openai")]})
        model: Model = OpenAIResponsesModel(name, provider=OpenAIProvider(api_key=key, http_client=http_client),
                                            settings=own)
    elif provider == "anthropic":
        from pydantic_ai.models.anthropic import AnthropicModel
        from pydantic_ai.providers.anthropic import AnthropicProvider
        model = AnthropicModel(name, provider=AnthropicProvider(api_key=key), settings=own)
    elif provider == "zai":
        from pydantic_ai.models.zai import ZaiModel
        from pydantic_ai.providers.zai import ZaiProvider
        model = ZaiModel(name, provider=ZaiProvider(api_key=key), settings=own)
    elif provider == "deepseek":
        from pydantic_ai.models.openai import OpenAIChatModel
        from pydantic_ai.profiles.openai import OpenAIModelProfile
        from pydantic_ai.providers.deepseek import DeepSeekProvider

        # PydanticAI does not yet recognize the deepseek-flash alias as a thinking model. Our settings
        # enable thinking for it, so its profile must also stop forcing tool_choice=required: DeepSeek
        # rejects that combination with HTTP 400. V4 Pro already has the correct provider profile.
        profile = (OpenAIModelProfile(supports_thinking=True, openai_reasoning_enabled_by_default=True,
                                     openai_supports_forced_tool_choice_with_thinking=False)
                   if name == "deepseek-flash" else None)
        model = OpenAIChatModel(name, provider=DeepSeekProvider(api_key=key), profile=profile, settings=own)
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


def token_pacer(model_id: str, settings: Settings) -> TokenPacer | None:
    """A pacer for one run's requests to `model_id`, when its token rate limit is configured. The limit the
    provider reports replaces the configured one, unless RESEARCH_TOKENS_PER_MINUTE was set explicitly."""
    limit = settings.tokens_per_minute.get(model_id)
    fixed = "tokens_per_minute" in settings.model_fields_set
    return TokenPacer(limit, model_id, fixed=fixed) if limit else None


def scout_model(inner: Model, model_id: str, settings: Settings) -> Model:
    """A scout's model: `inner` behind a run-shared wrapper that retries only timed rate limits and paces
    `model_id` under its token rate, and with old page text trimmed from each request (history.py) unless
    `trim_history` is off."""
    model = ScoutRateLimitModel(inner, token_pacer(model_id, settings))
    return TrimmedHistoryModel(model) if settings.trim_history else model


def role_model(role: Role, settings: Settings, spec: str | None = None) -> Model:
    """The model `role` runs on, or `spec` in its place. The planner and synthesizer fall back to
    `models.fallback` when their model refuses a call (ContentFilterError) or its provider fails
    (ModelAPIError); scouts do not, since a failed scout leaves its question unanswered rather than failing
    the run. A scout's client makes one attempt, so a request that reaches the timeout is not sent again."""
    spec = spec or getattr(settings.models, role)
    primary = build_model(spec, role, settings, sdk_retries=0 if role == "scout" else None)
    fallback = settings.models.fallback
    if role == "scout":
        return scout_model(primary, split_model(spec)[0], settings)
    if not fallback or fallback == spec:
        return primary
    return FallbackModel(primary, build_model(fallback, role, settings),
                         fallback_on=(ModelAPIError, ContentFilterError))

from __future__ import annotations

import json

import httpx2
import pytest
from pydantic_ai import Agent, models
from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.models.fallback import FallbackModel

from research_loop.config import Settings
from research_loop.models import (
    build_model,
    model_settings,
    role_model,
    sent_settings,
)


@pytest.fixture
def keyed(monkeypatch: pytest.MonkeyPatch) -> Settings:
    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "ZAI_API_KEY", "GOOGLE_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.setenv(name, "test-key")
    return Settings()


def test_a_model_runs_at_the_effort_named_with_it(keyed: Settings) -> None:
    assert model_settings("zai:glm-5.3-flash@high", "scout", keyed)["thinking"] == "high"
    assert sent_settings("zai:glm-5.3-flash@high", "scout", keyed)["extra_body"]["reasoning_effort"] == "high"
    assert sent_settings("zai:glm-5.3-flash@xhigh", "planner", keyed)["extra_body"]["reasoning_effort"] == "max"
    with pytest.raises(ValueError, match="names no effort"):
        build_model("zai:glm-5.3-flash", "scout", keyed)


def test_deepseek_runs_at_the_effort_named_even_where_pydantic_ai_would_drop_it(keyed: Settings) -> None:
    # PydanticAI knows only deepseek-v4-* names as thinking models, so it dropped the effort of deepseek-flash,
    # which DeepSeek then ran at its default, high.
    from pydantic_ai.models.openai import OpenAIChatModel

    model = build_model("deepseek:deepseek-flash@low", "scout", keyed)
    assert isinstance(model, OpenAIChatModel) and model.base_url.startswith("https://api.deepseek.com")
    for spec, effort in (("deepseek:deepseek-flash@low", "low"), ("deepseek:deepseek-v4-pro@xhigh", "xhigh")):
        assert sent_settings(spec, "scout", keyed)["extra_body"] == {"thinking": {"type": "enabled"},
                                                                     "reasoning_effort": effort}


def test_deepseek_flash_thinking_does_not_force_a_tool_choice(keyed: Settings) -> None:
    from pydantic_ai.models import ModelRequestParameters
    from pydantic_ai.tools import ToolDefinition

    # A structured-output agent requires a tool call. Flash's thinking mode rejects the literal
    # tool_choice=required with HTTP 400; PydanticAI should send auto as it does for V4 Pro.
    params = ModelRequestParameters(function_tools=[ToolDefinition(name="ping")], allow_text_output=False)
    for spec in ("deepseek:deepseek-flash@high", "deepseek:deepseek-v4-pro@high"):
        model = build_model(spec, "scout", keyed)
        assert model._get_tool_choice(model.settings, params)[1] == "auto"


def test_settings_refuse_a_model_without_its_effort(keyed: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RESEARCH_MODELS__SCOUT", "openai:gpt-6-luna")
    with pytest.raises(ValueError, match="must name its effort"):
        Settings()
    monkeypatch.setenv("RESEARCH_MODELS__SCOUT", "openai:gpt-6-luna@max")
    with pytest.raises(ValueError, match="must name its effort"):
        Settings()
    monkeypatch.setenv("RESEARCH_MODELS__SCOUT", "openai:gpt-6-luna@high")
    monkeypatch.setenv("RESEARCH_MODELS__SCOUT_EFFORT", "high")
    with pytest.raises(ValueError, match="SCOUT_EFFORT was removed"):
        Settings()
    monkeypatch.delenv("RESEARCH_MODELS__SCOUT_EFFORT")
    # A command-line model skips field validation, so the route check refuses it too.
    bare = Settings().model_copy(update={"models": Settings().models.model_copy(update={"scout": "zai:glm-5.3"})})
    assert bare.route_problems() == ["scout: 'zai:glm-5.3' must name its effort: zai:glm-5.3@<low|medium|high|xhigh>"]


def test_each_model_carries_its_own_settings(keyed: Settings) -> None:
    synthesizer = model_settings("anthropic:claude-opus-5-5@medium", "synthesizer", keyed)
    assert synthesizer == {"thinking": "medium", "timeout": 120, "max_tokens": 32_000, "anthropic_cache": True}
    planner = model_settings("openai:gpt-6-sol@high", "planner", keyed)
    assert planner["thinking"] == "high" and planner["openai_prompt_cache_key"] == "research-loop:openai:gpt-6-sol"
    assert "max_tokens" not in model_settings("zai:glm-5.3-flash@high", "scout", keyed)
    assert build_model("zai:glm-5.3-flash@xhigh", "scout", keyed).settings["thinking"] == "xhigh"


def test_model_call_limits_come_from_the_environment(keyed: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    from research_loop.scout import run_config

    monkeypatch.setenv("RESEARCH_MODEL_CALLS__PLANNER_MAX_OUTPUT_TOKENS", "12000")
    monkeypatch.setenv("RESEARCH_MODEL_CALLS__RUBRIC_TIMEOUT_SECONDS", "240")
    monkeypatch.setenv("RESEARCH_MODEL_CALLS__QUALITY_MAX_OUTPUT_TOKENS", "4000")
    settings = Settings()
    assert model_settings(settings.models.planner, "planner", settings)["max_tokens"] == 12_000
    assert settings.model_calls.rubric_timeout_seconds == 240
    assert settings.model_calls.quality_max_output_tokens == 4_000
    assert run_config(settings, [], [])["model_calls"]["planner_max_output_tokens"] == 12_000


def test_the_synthesizer_is_claude_with_citations_and_has_no_fallback(keyed: Settings) -> None:
    from research_loop.citations import CitingAnthropicModel

    synthesizer = role_model("synthesizer", keyed)
    assert isinstance(synthesizer, CitingAnthropicModel) and synthesizer.model_name == "claude-opus-5-5"
    assert synthesizer.settings["thinking"] == "medium"
    assert not isinstance(role_model("scout", keyed), FallbackModel)
    # The default planner is the fallback model itself, so it has nothing to fall back to.
    assert not isinstance(role_model("planner", keyed), FallbackModel)
    # Other roles on Claude keep PydanticAI's own model.
    assert type(build_model("anthropic:claude-opus-5-5@medium", "planner", keyed)).__name__ == "AnthropicModel"


def test_a_synthesizer_that_is_not_claude_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RESEARCH_MODELS__SYNTHESIZER", "openai:gpt-6-sol@high")
    with pytest.raises(ValueError, match="must be an anthropic: model"):
        Settings()


def test_another_planner_falls_back_too(monkeypatch: pytest.MonkeyPatch, keyed: Settings) -> None:
    monkeypatch.setenv("RESEARCH_MODELS__PLANNER", "anthropic:claude-opus-5-5@medium")
    planner = role_model("planner", Settings())
    assert isinstance(planner, FallbackModel) and planner.models[1].model_name == "gpt-6-sol"


def test_a_missing_key_fails_instead_of_reaching_for_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    with pytest.raises(ValueError, match="needs ZAI_API_KEY"):
        build_model("zai:glm-5.3-flash@high", "scout", Settings())


def test_a_scout_makes_one_attempt_and_other_roles_keep_the_client_default(keyed: Settings) -> None:
    scout = role_model("scout", keyed)
    planner = role_model("planner", keyed)
    synthesizer = role_model("synthesizer", keyed)
    assert scout.client.max_retries == 0
    assert planner.client.max_retries == 2
    assert synthesizer.client.max_retries == 2


def test_a_google_scout_makes_one_attempt(monkeypatch: pytest.MonkeyPatch, keyed: Settings) -> None:
    monkeypatch.setenv("RESEARCH_MODELS__SCOUT", "google:gemini-2.5-flash@high")
    scout = role_model("scout", Settings())
    retry = scout.client._api_client._http_options.retry_options
    assert retry is not None and retry.attempts == 1


async def test_a_timed_out_scout_request_is_not_sent_again(keyed: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    """The provider client retries a timeout twice unless told not to. One attempt is the whole request."""
    attempts = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal attempts
        attempts += 1
        raise httpx2.ReadTimeout("timed out", request=request)

    monkeypatch.setattr(models, "ALLOW_MODEL_REQUESTS", True)
    scout = role_model("scout", keyed)
    scout.client._client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    with pytest.raises(ModelAPIError, match="timed out"):
        await Agent(scout).run("hello")
    assert attempts == 1


async def test_glm_reaches_zai_with_reasoning_effort_max(keyed: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    """Checked on the wire, offline: the request body a GLM scout sends."""
    from pydantic_ai.models.zai import ZaiModel
    from pydantic_ai.providers.zai import ZaiProvider

    sent: list[dict] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append(json.loads(request.content))
        return httpx2.Response(200, json={
            "id": "x", "object": "chat.completion", "created": 0, "model": "glm-5.3-flash",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })

    monkeypatch.setattr(models, "ALLOW_MODEL_REQUESTS", True)
    provider = ZaiProvider(api_key="test-key", http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)))
    model = ZaiModel("glm-5.3-flash", provider=provider, settings=model_settings("zai:glm-5.3-flash@xhigh", "scout", keyed))
    result = await Agent(model).run("hello")
    assert result.output == "ok"
    assert sent[0]["reasoning_effort"] == "max"


def test_a_scout_request_gets_longer_than_other_roles(keyed: Settings) -> None:
    # At 120 seconds, Luna@xhigh scouts timed out in 5 of 6 rescouts of search-rescout-task8.
    limits = keyed.limits
    assert model_settings("openai:gpt-6-luna@xhigh", "scout", keyed)["timeout"] == limits.scout_request_timeout_seconds
    assert model_settings("openai:gpt-6-sol@high", "planner", keyed)["timeout"] == limits.request_timeout_seconds
    assert limits.scout_request_timeout_seconds > limits.request_timeout_seconds

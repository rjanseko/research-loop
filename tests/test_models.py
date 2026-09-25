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
    effort_for,
    model_settings,
    role_model,
    sent_settings,
)


@pytest.fixture
def keyed(monkeypatch: pytest.MonkeyPatch) -> Settings:
    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "ZAI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.setenv(name, "test-key")
    return Settings()


@pytest.mark.parametrize(("model_id", "effort"), [
    ("zai:glm-5.3", "xhigh"),
    ("zai:glm-5.3-flash", "xhigh"),
    ("zai:glm-5.3-flashx", "xhigh"),
    ("anthropic:claude-opus-5-5", "medium"),
    ("openai:gpt-6-sol", "high"),
])
def test_every_glm_model_thinks_at_max(model_id: str, effort: str) -> None:
    assert effort_for(model_id) == effort


def test_scout_effort_overrides_only_the_scout(keyed: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RESEARCH_MODELS__SCOUT_EFFORT", "high")
    settings = Settings()
    assert model_settings("zai:glm-5.3-flash", "scout", settings)["thinking"] == "high"
    assert model_settings("zai:glm-5.3-flash", "planner", settings)["thinking"] == "xhigh"
    assert sent_settings("zai:glm-5.3-flash", "scout", settings)["extra_body"]["reasoning_effort"] == "high"
    assert sent_settings("zai:glm-5.3-flash", "planner", settings)["extra_body"]["reasoning_effort"] == "max"


def test_each_model_carries_its_own_settings(keyed: Settings) -> None:
    synthesizer = model_settings("anthropic:claude-opus-5-5", "synthesizer", keyed)
    assert synthesizer == {"thinking": "medium", "timeout": 120, "max_tokens": 32_000, "anthropic_cache": True}
    planner = model_settings("openai:gpt-6-sol", "planner", keyed)
    assert planner["thinking"] == "high" and planner["openai_prompt_cache_key"] == "research-loop:openai:gpt-6-sol"
    assert "max_tokens" not in model_settings("zai:glm-5.3-flash", "scout", keyed)
    assert build_model("zai:glm-5.3-flash", "scout", keyed).settings["thinking"] == "xhigh"


def test_the_synthesizer_falls_back_to_another_model_and_scouts_do_not(keyed: Settings) -> None:
    synthesizer = role_model("synthesizer", keyed)
    assert isinstance(synthesizer, FallbackModel)
    assert [m.model_name for m in synthesizer.models] == ["claude-opus-5-5", "gpt-6-sol"]
    # The fallback runs with its own effort, not the primary's.
    assert [m.settings["thinking"] for m in synthesizer.models] == ["medium", "high"]
    assert not isinstance(role_model("scout", keyed), FallbackModel)
    # The default planner is the fallback model itself, so it has nothing to fall back to.
    assert not isinstance(role_model("planner", keyed), FallbackModel)


def test_another_planner_falls_back_too(monkeypatch: pytest.MonkeyPatch, keyed: Settings) -> None:
    monkeypatch.setenv("RESEARCH_MODELS__PLANNER", "anthropic:claude-opus-5-5")
    planner = role_model("planner", Settings())
    assert isinstance(planner, FallbackModel) and planner.models[1].model_name == "gpt-6-sol"


def test_a_missing_key_fails_instead_of_reaching_for_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    with pytest.raises(ValueError, match="needs ZAI_API_KEY"):
        build_model("zai:glm-5.3-flash", "scout", Settings())


def test_a_scout_makes_one_attempt_and_other_roles_keep_the_client_default(keyed: Settings) -> None:
    scout = role_model("scout", keyed)
    planner = role_model("planner", keyed)
    synthesizer = role_model("synthesizer", keyed)
    assert scout.client.max_retries == 0
    assert planner.client.max_retries == 2
    assert synthesizer.models[0].client.max_retries == 2
    assert synthesizer.models[1].client.max_retries == 2


def test_a_google_scout_makes_one_attempt(monkeypatch: pytest.MonkeyPatch, keyed: Settings) -> None:
    monkeypatch.setenv("RESEARCH_MODELS__SCOUT", "google:gemini-2.5-flash")
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
    model = ZaiModel("glm-5.3-flash", provider=provider, settings=model_settings("zai:glm-5.3-flash", "scout", keyed))
    result = await Agent(model).run("hello")
    assert result.output == "ok"
    assert sent[0]["reasoning_effort"] == "max"

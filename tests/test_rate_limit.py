"""Scout rate-limit retry behavior, with a scripted model and no network."""
from __future__ import annotations

import asyncio
import ssl

import httpx
import openai
import pytest
from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.function import AgentInfo, FunctionModel

from research_loop.rate_limit import (
    ScoutRateLimitModel,
    _retry_delay,
    transient_network_error,
)


def _limit_error(wait: str = "0.02s") -> ModelHTTPError:
    return ModelHTTPError(429, "gpt-6-luna", body={
        "message": f"Rate limit reached for gpt-6-luna on tokens per min. Please try again in {wait}.",
        "type": "tokens", "code": "rate_limit_exceeded",
    })


def test_only_explicit_rate_limits_get_a_retry_time() -> None:
    assert _retry_delay(_limit_error("5.749s")) == 5.749
    assert _retry_delay(ModelHTTPError(429, "gpt-6-luna", body={
        "message": "Rate limit reached", "code": "rate_limit_exceeded",
    }, headers={"Retry-After": "3"})) == 3
    assert _retry_delay(ModelHTTPError(429, "gpt-6-luna", body={
        "message": "Rate limit reached", "code": "rate_limit_exceeded",
    }, headers={"retry-after-ms": "250"})) == 0.25
    assert _retry_delay(ModelHTTPError(429, "glm-5.3-flash", body={
        "error": {"message": "Insufficient balance", "code": "insufficient_quota"},
    }, headers={"retry-after": "3"})) is None
    assert _retry_delay(ModelHTTPError(429, "gpt-6-luna", body={
        "message": "Rate limit reached", "code": "rate_limit_exceeded",
    })) is None


async def test_one_429_pauses_other_scout_requests_and_retries_the_same_one() -> None:
    times: list[float] = []
    first_error = asyncio.Event()

    async def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del messages, info
        times.append(asyncio.get_running_loop().time())
        if len(times) == 1:
            first_error.set()
            raise _limit_error()
        return ModelResponse(parts=[TextPart("ok")])

    model = ScoutRateLimitModel(FunctionModel(respond))
    params = ModelRequestParameters()
    first = asyncio.create_task(model.request([], None, params))
    await first_error.wait()
    while model._resume_at <= asyncio.get_running_loop().time():
        await asyncio.sleep(0)
    second = asyncio.create_task(model.request([], None, params))
    first_response, second_response = await asyncio.gather(first, second)

    assert first_response.text == second_response.text == "ok"
    assert len(times) == 3
    assert min(times[1:]) - times[0] >= 0.1


async def test_balance_429_is_not_retried() -> None:
    attempts = 0

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal attempts
        del messages, info
        attempts += 1
        raise ModelHTTPError(429, "glm-5.3-flash", body={
            "error": {"message": "Insufficient balance", "code": "insufficient_quota"},
        })

    model = ScoutRateLimitModel(FunctionModel(respond))
    with pytest.raises(ModelHTTPError, match="Insufficient balance"):
        await model.request([], None, ModelRequestParameters())
    assert attempts == 1


async def test_stream_open_retries_only_a_timed_429() -> None:
    attempts = 0

    async def stream(messages: list[ModelMessage], info: AgentInfo):
        nonlocal attempts
        del messages, info
        attempts += 1
        if attempts == 1:
            raise _limit_error()
        yield "ok"

    model = ScoutRateLimitModel(FunctionModel(stream_function=stream))
    async with model.request_stream([], None, ModelRequestParameters()):
        pass
    assert attempts == 2


async def test_agent_retries_a_rate_limited_scout_request() -> None:
    attempts = 0

    async def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal attempts
        del messages, info
        attempts += 1
        if attempts == 1:
            raise _limit_error()
        return ModelResponse(parts=[TextPart("ok")])

    result = await Agent(ScoutRateLimitModel(FunctionModel(respond))).run("question")
    assert result.output == "ok" and attempts == 2


async def test_repeated_rate_limits_stop_after_two_retries() -> None:
    attempts = 0

    async def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal attempts
        del messages, info
        attempts += 1
        raise _limit_error("0s")

    model = ScoutRateLimitModel(FunctionModel(respond))
    with pytest.raises(ModelHTTPError):
        await model.request([], None, ModelRequestParameters())
    assert attempts == 3


async def test_pacer_holds_a_request_that_would_pass_the_limit_until_the_window_clears(monkeypatch) -> None:
    from research_loop.rate_limit import TokenPacer

    monkeypatch.setattr("research_loop.rate_limit._WINDOW_SECONDS", 0.2)
    pacer = TokenPacer(1_000)
    loop = asyncio.get_running_loop()
    start = loop.time()
    await pacer.acquire(600)
    await pacer.acquire(300)  # 900 fits under 90% of 1,000
    assert loop.time() - start < 0.1
    await pacer.acquire(300)  # 1,200 does not, so it waits for the first two to leave the window
    assert loop.time() - start >= 0.2
    # A request larger than the paced share still goes once the window is empty.
    await asyncio.sleep(0.25)
    await pacer.acquire(5_000)


async def test_pacer_counts_billed_tokens_in_place_of_the_estimate(monkeypatch) -> None:
    from pydantic_ai.usage import RequestUsage

    from research_loop.rate_limit import TokenPacer

    monkeypatch.setattr("research_loop.rate_limit._WINDOW_SECONDS", 0.2)
    pacer = TokenPacer(1_000)
    loop = asyncio.get_running_loop()
    entry = await pacer.acquire(800)
    TokenPacer.settle(entry, RequestUsage(input_tokens=90, output_tokens=10))
    start = loop.time()
    await pacer.acquire(700)  # fits only because the first request billed 100, not 800
    assert loop.time() - start < 0.1


async def test_paced_scouts_wait_their_turn_before_dispatch(monkeypatch) -> None:
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    from research_loop.rate_limit import TokenPacer

    monkeypatch.setattr("research_loop.rate_limit._WINDOW_SECONDS", 0.2)
    times: list[float] = []

    async def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del messages, info
        times.append(asyncio.get_running_loop().time())
        await asyncio.sleep(0.05)  # still in flight when the second asks, so its estimate is counted
        return ModelResponse(parts=[TextPart("ok")])

    model = ScoutRateLimitModel(FunctionModel(respond), TokenPacer(1_000))
    messages = [ModelRequest(parts=[UserPromptPart("x" * 2_400)])]  # about 600 estimated tokens
    await asyncio.gather(*(model.request(messages, None, ModelRequestParameters()) for _ in range(2)))
    assert len(times) == 2 and times[1] - times[0] >= 0.2


def test_the_estimate_starts_from_the_last_billed_reply() -> None:
    from pydantic_ai.messages import (
        ModelRequest,
        ToolCallPart,
        ToolReturnPart,
        UserPromptPart,
    )
    from pydantic_ai.usage import RequestUsage

    from research_loop.rate_limit import estimated_tokens

    history = [ModelRequest(parts=[UserPromptPart("x" * 100_000)]),
               ModelResponse(parts=[ToolCallPart("fetch", {"url": "https://a.test"}, tool_call_id="t1")],
                             usage=RequestUsage(input_tokens=20_000, output_tokens=500)),
               ModelRequest(parts=[ToolReturnPart("fetch", "y" * 8_000, tool_call_id="t1")])]
    assert 20_500 + 2_000 <= estimated_tokens(history, ModelRequestParameters()) < 20_500 + 2_500


def _wrapped(cause: BaseException) -> ModelAPIError:
    """A provider fault as pydantic-ai raises it: a ModelAPIError from the client's own error."""
    error = ModelAPIError("gpt-6-luna", str(cause))
    error.__cause__ = cause
    return error


def test_only_connection_faults_that_are_not_timeouts_are_transient() -> None:
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    # st07 run 7a7fc5b5 lost its decisive question to this error, raised unwrapped by the OpenAI client.
    assert transient_network_error(ssl.SSLError("[SSL: SSLV3_ALERT_BAD_RECORD_MAC] bad record mac"))
    assert transient_network_error(ConnectionResetError())
    assert transient_network_error(httpx.RemoteProtocolError("peer closed connection"))
    assert transient_network_error(_wrapped(openai.APIConnectionError(request=request)))
    assert not transient_network_error(TimeoutError())
    assert not transient_network_error(httpx.ReadTimeout("slow"))
    assert not transient_network_error(_wrapped(openai.APITimeoutError(request=request)))
    assert not transient_network_error(ModelHTTPError(500, "gpt-6-luna"))
    assert not transient_network_error(RuntimeError("study budget refused"))


@pytest.mark.parametrize("fault", [ssl.SSLError("bad record mac"),
                                   _wrapped(openai.APIConnectionError(request=httpx.Request("POST", "https://x.test")))])
async def test_a_transient_network_fault_is_sent_again_once(monkeypatch, fault: BaseException) -> None:
    monkeypatch.setattr("research_loop.rate_limit._NETWORK_RETRY_SECONDS", 0)
    attempts = 0

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal attempts
        del messages, info
        attempts += 1
        if attempts == 1:
            raise fault
        return ModelResponse(parts=[TextPart("ok")])

    response = await ScoutRateLimitModel(FunctionModel(respond)).request([], None, ModelRequestParameters())
    assert response.text == "ok" and attempts == 2


async def test_a_second_network_fault_or_a_timeout_is_not_sent_again(monkeypatch) -> None:
    monkeypatch.setattr("research_loop.rate_limit._NETWORK_RETRY_SECONDS", 0)
    attempts = 0

    def failing(error: BaseException):
        def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            nonlocal attempts
            del messages, info
            attempts += 1
            raise error
        return respond

    with pytest.raises(ssl.SSLError):
        await ScoutRateLimitModel(FunctionModel(failing(ssl.SSLError("bad record mac")))).request(
            [], None, ModelRequestParameters())
    assert attempts == 2
    attempts = 0
    with pytest.raises(TimeoutError):
        await ScoutRateLimitModel(FunctionModel(failing(TimeoutError()))).request([], None, ModelRequestParameters())
    assert attempts == 1


async def test_a_stream_is_sent_again_after_a_network_fault_only_before_it_opens(monkeypatch) -> None:
    monkeypatch.setattr("research_loop.rate_limit._NETWORK_RETRY_SECONDS", 0)
    attempts = 0

    async def stream(messages: list[ModelMessage], info: AgentInfo):
        nonlocal attempts
        del messages, info
        attempts += 1
        if attempts == 1:
            raise ssl.SSLError("bad record mac")
        yield "ok"

    async with ScoutRateLimitModel(FunctionModel(stream_function=stream)).request_stream(
            [], None, ModelRequestParameters()):
        pass
    assert attempts == 2


@pytest.mark.asyncio
async def test_the_pacer_adopts_the_limit_the_provider_reports_unless_one_was_set(monkeypatch) -> None:
    # The configured 200,000 held scouts to a tenth of the account's real 2,000,000 gpt-6-luna tokens a
    # minute, which OpenAI reports in x-ratelimit-limit-tokens (study log, 28 September 2026).
    import json

    import httpx2

    from research_loop import rate_limit
    from research_loop.config import Settings
    from research_loop.models import token_pacer
    from research_loop.rate_limit import (
        TokenPacer,
        rate_limit_hook,
        reported_tokens_per_minute,
    )

    monkeypatch.setattr(rate_limit, "_REPORTED_TOKENS_PER_MINUTE", {})
    hook = rate_limit_hook("openai")

    def response(limit: str, body: object) -> httpx2.Response:
        request = httpx2.Request("POST", "https://api.openai.com/v1/responses", content=json.dumps(body))
        return httpx2.Response(200, headers={"x-ratelimit-limit-tokens": limit}, request=request)

    for ignored in (response("", {"model": "gpt-6-luna"}), response("unknown", {"model": "gpt-6-luna"}),
                    response("2000000", ["not", "a", "mapping"]), response("2000000", {"input": "no model"})):
        await hook(ignored)
    assert reported_tokens_per_minute("openai:gpt-6-luna") is None
    await hook(response("2000000", {"model": "gpt-6-luna", "input": "hi"}))
    assert reported_tokens_per_minute("openai:gpt-6-luna") == 2_000_000

    adopting = TokenPacer(200_000, "openai:gpt-6-luna", fixed=False)
    assert adopting.limit == 2_000_000 and TokenPacer(200_000, "openai:gpt-6-luna").limit == 200_000
    assert TokenPacer(200_000, "openai:gpt-6-sol", fixed=False).limit == 200_000  # nothing reported for Sol

    # The default setting adopts the reported limit; one set explicitly, here by the environment, stands.
    monkeypatch.delenv("RESEARCH_TOKENS_PER_MINUTE", raising=False)
    assert token_pacer("openai:gpt-6-luna", Settings(_env_file=None)).limit == 2_000_000
    monkeypatch.setenv("RESEARCH_TOKENS_PER_MINUTE", '{"openai:gpt-6-luna": 300000}')
    assert token_pacer("openai:gpt-6-luna", Settings(_env_file=None)).limit == 300_000


def test_openai_models_record_the_rate_limit_their_responses_report(monkeypatch) -> None:
    from research_loop.config import Settings
    from research_loop.models import build_model

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    model = build_model("openai:gpt-6-luna@high", "scout", Settings(_env_file=None))
    hooks = model.client._client.event_hooks["response"]
    assert [hook.__qualname__ for hook in hooks] == ["rate_limit_hook.<locals>.record"]

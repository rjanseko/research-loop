"""Scout rate-limit retry behavior, with a scripted model and no network."""
from __future__ import annotations

import asyncio

import pytest
from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.function import AgentInfo, FunctionModel

from research_loop.rate_limit import ScoutRateLimitModel, _retry_delay


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

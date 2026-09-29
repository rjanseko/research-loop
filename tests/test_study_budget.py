"""The study guard refuses requests before a scripted model receives them."""
from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RequestUsage

from research_loop.study_budget import (
    StudyBudget,
    StudyBudgetModel,
    StudyBudgetRefusal,
    upper_input_tokens,
)

pytestmark = pytest.mark.filterwarnings("ignore::pydantic_ai.exceptions.CostNotFoundWarning")


async def test_tiny_cap_refuses_before_dispatch() -> None:
    calls = []

    def respond(messages: list[ModelMessage], _info) -> ModelResponse:
        calls.append(messages)
        return ModelResponse(parts=[TextPart("ok")])

    budget = StudyBudget(Decimal("0.0001"))
    model = StudyBudgetModel(FunctionModel(respond), "openai:gpt-6-sol", budget)
    with pytest.raises(StudyBudgetRefusal, match="would exceed"):
        await Agent(model, output_type=str).run("hello", model_settings={"max_tokens": 100})
    assert not calls and budget.reserved_usd == 0


async def test_returned_request_settles_to_its_actual_charge() -> None:
    from genai_prices import calc_price

    responses = []

    def respond(messages: list[ModelMessage], _info) -> ModelResponse:
        responses.append(ModelResponse(parts=[TextPart("ok")], usage=RequestUsage(input_tokens=1000, output_tokens=10)))
        return responses[-1]

    budget = StudyBudget(Decimal(10))
    model = StudyBudgetModel(FunctionModel(respond), "openai:gpt-6-sol", budget)
    result = await Agent(model, output_type=str).run("hello", model_settings={"max_tokens": 100})
    assert result.output == "ok" and len(responses) == 1
    actual = calc_price(responses[-1].usage, "gpt-6-sol", provider_id="openai").total_price
    assert Decimal(0) < budget.reserved_usd == actual


async def test_failed_request_keeps_its_reservation() -> None:
    def respond(_messages: list[ModelMessage], _info) -> ModelResponse:
        raise RuntimeError("connection reset after send")

    budget = StudyBudget(Decimal(10))
    model = StudyBudgetModel(FunctionModel(respond), "openai:gpt-6-sol", budget)
    with pytest.raises(RuntimeError):
        await Agent(model, output_type=str).run("hello", model_settings={"max_tokens": 100})
    assert budget.reserved_usd > 0


async def test_settled_requests_leave_room_for_a_later_one() -> None:
    # Under byte-reserve-v2, returned scouts kept their reservations, so synthesis was refused
    # with most of the cap held by requests that had cost a few cents.
    def respond(_messages: list[ModelMessage], _info) -> ModelResponse:
        return ModelResponse(parts=[TextPart("ok")], usage=RequestUsage(input_tokens=100, output_tokens=5))

    probe = StudyBudget(Decimal(10))
    model = StudyBudgetModel(FunctionModel(respond), "openai:gpt-6-sol", probe)
    await Agent(model, output_type=str).run("hello", model_settings={"max_tokens": 1000})
    one = await probe.reserve("openai:gpt-6-sol", [], {"max_tokens": 1000}, ModelRequestParameters())
    budget = StudyBudget(one * Decimal("1.5"))
    model = StudyBudgetModel(FunctionModel(respond), "openai:gpt-6-sol", budget)
    for _ in range(3):
        await Agent(model, output_type=str).run("hello", model_settings={"max_tokens": 1000})
    assert budget.reserved_usd < one


async def test_streamed_request_settles_after_the_stream_ends() -> None:
    from pydantic_ai.models.function import AgentInfo

    async def stream(_messages: list[ModelMessage], _info: AgentInfo):
        yield "ok"

    budget = StudyBudget(Decimal(10))
    model = StudyBudgetModel(FunctionModel(stream_function=stream), "openai:gpt-6-sol", budget)
    reserve = await StudyBudget(Decimal(10)).reserve("openai:gpt-6-sol", [], {"max_tokens": 100},
                                                     ModelRequestParameters())
    async with Agent(model, output_type=str).run_stream("hello", model_settings={"max_tokens": 100}) as run:
        assert await run.get_output() == "ok"
    assert Decimal(0) < budget.reserved_usd < reserve


async def test_output_bound_is_required() -> None:
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    budget = StudyBudget(Decimal(10))
    with pytest.raises(StudyBudgetRefusal, match="max_tokens"):
        await budget.reserve("openai:gpt-6-sol", [ModelRequest(parts=[UserPromptPart("hi")])],
                             None, ModelRequestParameters())


async def test_large_scout_context_stays_within_priced_reservation_range() -> None:
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    # A prior factor of four refused this before dispatch as >1M input tokens,
    # although the actual Scout histories were around 100k tokens.
    budget = StudyBudget(Decimal(10))
    charge = await budget.reserve("openai:gpt-6-luna",
                                  [ModelRequest(parts=[UserPromptPart("x" * 300_000)])],
                                  {"max_tokens": 1000}, ModelRequestParameters())
    assert Decimal(0) < charge == budget.reserved_usd < budget.cap_usd


async def test_parallel_reservations_share_one_ceiling() -> None:
    import asyncio

    from pydantic_ai.messages import ModelRequest, UserPromptPart

    messages = [ModelRequest(parts=[UserPromptPart("one question")])]
    settings = {"max_tokens": 1000}
    probe = StudyBudget(Decimal(10))
    one = await probe.reserve("openai:gpt-6-luna", messages, settings, ModelRequestParameters())
    shared = StudyBudget(one * Decimal("1.5"))
    results = await asyncio.gather(
        shared.reserve("openai:gpt-6-luna", messages, settings, ModelRequestParameters()),
        shared.reserve("openai:gpt-6-luna", messages, settings, ModelRequestParameters()),
        return_exceptions=True,
    )
    assert sum(isinstance(item, StudyBudgetRefusal) for item in results) == 1
    assert shared.reserved_usd == one


def test_a_request_after_a_reply_is_bounded_from_its_billed_tokens() -> None:
    from pydantic_ai.messages import (
        ModelRequest,
        ToolCallPart,
        ToolReturnPart,
        UserPromptPart,
    )

    from research_loop.study_budget import upper_input_tokens

    page = "x" * 50_000
    history = [ModelRequest(parts=[UserPromptPart(page)]),
               ModelResponse(parts=[ToolCallPart("fetch", {"url": "https://a.test"}, tool_call_id="t1")],
                             usage=RequestUsage(input_tokens=12_000, output_tokens=300))]
    added = ModelRequest(parts=[ToolReturnPart("fetch", "y" * 20_000, tool_call_id="t1")])
    anchored = upper_input_tokens([*history, added], ModelRequestParameters(), {"max_tokens": 100})
    # The first 50 kB are counted as billed, not re-estimated at two tokens per byte.
    assert 12_300 + 20_000 < anchored < 12_300 + 30_000
    # Without billed usage, the whole request falls back to the byte bound.
    unbilled = [history[0], ModelResponse(parts=history[1].parts), added]
    assert upper_input_tokens(unbilled, ModelRequestParameters(), {"max_tokens": 100}) > 2 * 70_000


async def test_a_rate_limited_request_releases_its_reservation() -> None:
    from pydantic_ai.exceptions import ModelHTTPError

    def respond(_messages: list[ModelMessage], _info) -> ModelResponse:
        raise ModelHTTPError(429, "gpt-6-luna", body={"message": "Rate limit reached", "code": "rate_limit_exceeded"})

    budget = StudyBudget(Decimal(10))
    model = StudyBudgetModel(FunctionModel(respond), "openai:gpt-6-sol", budget)
    with pytest.raises(ModelHTTPError):
        await Agent(model, output_type=str).run("hello", model_settings={"max_tokens": 100})
    assert budget.reserved_usd == 0


@pytest.mark.parametrize("model_id", ["openai:gpt-6-luna", "openai:gpt-6-sol", "google:gemini-2.5-pro"])
@pytest.mark.parametrize("input_tokens", [100_000, 271_999, 272_001, 300_000])
async def test_the_reservation_covers_the_long_context_output_tier(model_id: str, input_tokens: int) -> None:
    # The addendum's case (docs/architectural-audit-addendum-2026-09-27.md, C01): 300,000 input tokens and a
    # 24,000-token output cap reserved $1.0002 on Gemini 2.5 Pro and settled at $1.11, over a $1.05 cap.
    from genai_prices import calc_price

    from research_loop.study_budget import upper_input_tokens

    provider, name = model_id.split(":")
    history = [ModelResponse(parts=[TextPart("prior result")], usage=RequestUsage(input_tokens=input_tokens, output_tokens=1))]
    settings = {"max_tokens": 24_000}
    budget = StudyBudget(Decimal(100))
    charge = await budget.reserve(model_id, history, settings, ModelRequestParameters())
    bound = upper_input_tokens(history, ModelRequestParameters(), settings)
    for used in (input_tokens, bound):
        actual = calc_price(RequestUsage(input_tokens=used, output_tokens=24_000), name, provider_id=provider).total_price
        assert charge >= actual


async def test_a_synthesis_with_a_fallback_reserves_both_attempts() -> None:
    # A declined synthesis continues on its fallback inside the same request (citations.py): the declined
    # attempt may have streamed its whole output, which the fallback then reads as input.
    messages = [ModelRequest(parts=[UserPromptPart("x" * 40_000)])]
    settings, parameters = {"max_tokens": 32_000}, ModelRequestParameters()
    alone = await StudyBudget(Decimal(100)).reserve("anthropic:claude-opus-5-5", messages, settings, parameters)
    both = await StudyBudget(Decimal(100)).reserve("anthropic:claude-opus-5-5", messages, settings, parameters,
                                                   fallback_model_id="anthropic:claude-opus-5")
    upper = upper_input_tokens(messages, parameters, settings)
    # Opus 5 at $5 in and $25 out a million, on the input plus the declined attempt's output.
    assert both - alone == (Decimal(upper + 32_000) * 5 + Decimal(32_000) * 25) / 1_000_000
    with pytest.raises(StudyBudgetRefusal, match="no price"):
        await StudyBudget(Decimal(100)).reserve("anthropic:claude-opus-5-5", messages, settings, parameters,
                                                fallback_model_id="anthropic:no-such-model")


async def test_a_response_that_carries_its_cost_settles_to_it() -> None:
    # A synthesis that fell back carries its cost summed over its attempts at each model's rates; its tokens
    # are only the serving attempt's, so pricing them as the requested model would under-count.
    def respond(_messages: list[ModelMessage], _info) -> ModelResponse:
        return ModelResponse(parts=[TextPart("ok")],
                             usage=RequestUsage(input_tokens=1000, output_tokens=10, cost=Decimal("0.4321")))

    budget = StudyBudget(Decimal(10))
    model = StudyBudgetModel(FunctionModel(respond), "anthropic:claude-opus-5-5", budget,
                             fallback_model_id="anthropic:claude-opus-5")
    await Agent(model, output_type=str).run("hello", model_settings={"max_tokens": 100})
    assert budget.reserved_usd == Decimal("0.4321")

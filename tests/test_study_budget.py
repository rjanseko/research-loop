"""The study guard refuses requests before a scripted model receives them."""
from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.function import FunctionModel

from research_loop.study_budget import StudyBudget, StudyBudgetModel, StudyBudgetRefusal

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


async def test_reservation_is_kept_after_dispatch() -> None:
    calls = []

    def respond(messages: list[ModelMessage], _info) -> ModelResponse:
        calls.append(messages)
        return ModelResponse(parts=[TextPart("ok")])

    budget = StudyBudget(Decimal(10))
    model = StudyBudgetModel(FunctionModel(respond), "openai:gpt-6-sol", budget)
    result = await Agent(model, output_type=str).run("hello", model_settings={"max_tokens": 100})
    assert result.output == "ok" and len(calls) == 1
    assert Decimal(0) < budget.reserved_usd <= budget.cap_usd


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

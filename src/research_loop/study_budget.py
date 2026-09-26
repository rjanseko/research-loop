"""Conservative per-command reservation before a paid study model request.

The new study commands disable SDK retries and fallback. PydanticAI validation may still make a
second request; this wrapper reserves before each one. The input bound starts from the last reply's
billed tokens when there is one, and bounds the rest by bytes, including tool schemas and framing. When a response returns with priced usage, its
reservation is replaced by the actual charge; a request that fails, or whose usage cannot be priced,
keeps its full reservation because the provider may still have charged for it.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Any

from genai_prices import calc_price
from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter, ModelResponse
from pydantic_ai.models import Model, ModelRequestParameters, StreamedResponse
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RequestUsage

from .prices import install_price_overrides

# A serialized UTF-8 byte can account for at most one text token. Doubling the byte count
# allows for provider framing and serialization differences; another 16k covers fixed overhead.
# The former factor of four falsely refused observed ~100k-token Scout requests as >1M tokens.
# v3 settles each returned request to its actual charge; v2 kept every reservation, so parallel
# scouts could hold most of the cap and leave no room for synthesis.
# v4 anchors a request that follows a reply on that reply's billed tokens: the request resends the
# prompt the provider already counted, plus the reply, plus what was added since, and only the
# added part is bounded by its bytes. Scout requests run about 5.3 bytes per billed token, so v3's
# two tokens per byte reserved about eleven times their input and falsely refused four Luna scouts
# at a $0.50 cap (docs/high-level-study-evaluation.md).
BUDGET_POLICY_VERSION = "usage-anchor-v4"
_BYTE_FACTOR = 2
_FIXED_INPUT_TOKENS = 16_000
# Framing for the messages, tool definitions, and settings added since the anchoring reply.
_FIXED_ADDED_TOKENS = 4_000


def upper_input_tokens(messages: list[ModelMessage], parameters: ModelRequestParameters,
                       settings: ModelSettings | None) -> int:
    """An upper bound on a request's input tokens.

    After a reply with billed input, the bound is that reply's input and output tokens plus one token
    per byte of everything since, with the tool definitions and settings counted again in full. Before
    one, it is two tokens per byte of the whole request plus fixed overhead. A guarded model wraps
    one model without fallback, so the reply was counted by the same tokenizer.
    """
    extra = len(repr(parameters).encode()) + len(repr(settings).encode())
    for index in range(len(messages) - 1, -1, -1):
        reply = messages[index]
        if isinstance(reply, ModelResponse) and reply.usage.input_tokens > 0:
            added = len(ModelMessagesTypeAdapter.dump_json(messages[index + 1:])) + extra
            return reply.usage.input_tokens + reply.usage.output_tokens + added + _FIXED_ADDED_TOKENS
    return (len(ModelMessagesTypeAdapter.dump_json(messages)) + extra) * _BYTE_FACTOR + _FIXED_INPUT_TOKENS


class StudyBudgetRefusal(RuntimeError):
    """A study request was not sent because its conservative reservation exceeds the cap."""


class StudyBudget:
    def __init__(self, cap_usd: Decimal) -> None:
        if cap_usd <= 0:
            raise ValueError("study cap must be positive")
        self.cap_usd = cap_usd
        self.reserved_usd = Decimal(0)
        self._lock = asyncio.Lock()

    async def reserve(self, model_id: str, messages: list[ModelMessage], settings: ModelSettings | None,
                      parameters: ModelRequestParameters) -> Decimal:
        max_output = (settings or {}).get("max_tokens")
        if not isinstance(max_output, int) or max_output <= 0:
            raise StudyBudgetRefusal("study requests need an explicit positive max_tokens")
        install_price_overrides()
        provider, _, name = model_id.partition(":")
        try:
            input_rates = [calc_price(RequestUsage(input_tokens=n), name, provider_id=provider).total_price
                           * Decimal(1_000_000) / n for n in (100_000, 1_000_000)]
            output_rate = calc_price(RequestUsage(output_tokens=100_000), name,
                                     provider_id=provider).total_price * 10
        except LookupError as exc:
            raise StudyBudgetRefusal(f"no price for {model_id}") from exc
        upper = upper_input_tokens(messages, parameters, settings)
        if upper > 1_000_000:
            raise StudyBudgetRefusal("input exceeds the priced one-million-token reservation range")
        charge = (max(input_rates) * upper + output_rate * max_output) / 1_000_000
        async with self._lock:
            if self.reserved_usd + charge > self.cap_usd:
                raise StudyBudgetRefusal(
                    f"request reserve ${charge:.4f} would exceed ${self.cap_usd:.4f} cap "
                    f"with ${self.reserved_usd:.4f} already reserved")
            self.reserved_usd += charge
        return charge

    def settle(self, model_id: str, charge: Decimal, usage: RequestUsage) -> None:
        """Replace a returned request's reservation with its actual charge, if it can be priced."""
        provider, _, name = model_id.partition(":")
        try:
            actual = calc_price(usage, name, provider_id=provider).total_price
        except LookupError:
            return
        # No await, so this cannot interleave with a reservation.
        self.reserved_usd += actual - charge


class StudyBudgetModel(WrapperModel):
    """Reserve a conservative maximum before every provider request, including validation retries."""

    def __init__(self, wrapped: Model, model_id: str, budget: StudyBudget):
        super().__init__(wrapped)
        self._priced_model_id, self.budget = model_id, budget

    async def request(self, messages: list[ModelMessage], model_settings: ModelSettings | None,
                      model_request_parameters: ModelRequestParameters) -> ModelResponse:
        effective, _ = self.wrapped.prepare_request(model_settings, model_request_parameters)
        charge = await self.budget.reserve(self._priced_model_id, messages, effective, model_request_parameters)
        response = await self.wrapped.request(messages, model_settings, model_request_parameters)
        self.budget.settle(self._priced_model_id, charge, response.usage)
        return response

    @asynccontextmanager
    async def request_stream(self, messages: list[ModelMessage], model_settings: ModelSettings | None,
                             model_request_parameters: ModelRequestParameters,
                             run_context: Any = None) -> AsyncGenerator[StreamedResponse]:
        effective, _ = self.wrapped.prepare_request(model_settings, model_request_parameters)
        charge = await self.budget.reserve(self._priced_model_id, messages, effective, model_request_parameters)
        async with self.wrapped.request_stream(messages, model_settings, model_request_parameters,
                                               run_context) as stream:
            yield stream
        # Reached only when the stream was consumed without error.
        self.budget.settle(self._priced_model_id, charge, stream.usage)

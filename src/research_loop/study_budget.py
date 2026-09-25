"""Conservative per-command reservation before a paid study model request.

The new study commands disable SDK retries and fallback. PydanticAI validation may still make a
second request; this wrapper reserves before each one, retaining reservations even on failure.
A generous byte-to-token upper bound includes serialized messages, tool schemas, and framing.
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
BUDGET_POLICY_VERSION = "byte-reserve-v2"
_BYTE_FACTOR = 2
_FIXED_INPUT_TOKENS = 16_000


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
        serialized = ModelMessagesTypeAdapter.dump_json(messages)
        request_bytes = len(serialized) + len(repr(parameters).encode()) + len(repr(settings).encode())
        upper_input_tokens = request_bytes * _BYTE_FACTOR + _FIXED_INPUT_TOKENS
        if upper_input_tokens > 1_000_000:
            raise StudyBudgetRefusal("input exceeds the priced one-million-token reservation range")
        charge = (max(input_rates) * upper_input_tokens + output_rate * max_output) / 1_000_000
        async with self._lock:
            if self.reserved_usd + charge > self.cap_usd:
                raise StudyBudgetRefusal(
                    f"request reserve ${charge:.4f} would exceed ${self.cap_usd:.4f} cap "
                    f"with ${self.reserved_usd:.4f} already reserved")
            self.reserved_usd += charge
        return charge


class StudyBudgetModel(WrapperModel):
    """Reserve a conservative maximum before every provider request, including validation retries."""

    def __init__(self, wrapped: Model, model_id: str, budget: StudyBudget):
        super().__init__(wrapped)
        self._priced_model_id, self.budget = model_id, budget

    async def request(self, messages: list[ModelMessage], model_settings: ModelSettings | None,
                      model_request_parameters: ModelRequestParameters) -> ModelResponse:
        effective, _ = self.wrapped.prepare_request(model_settings, model_request_parameters)
        await self.budget.reserve(self._priced_model_id, messages, effective, model_request_parameters)
        return await self.wrapped.request(messages, model_settings, model_request_parameters)

    @asynccontextmanager
    async def request_stream(self, messages: list[ModelMessage], model_settings: ModelSettings | None,
                             model_request_parameters: ModelRequestParameters,
                             run_context: Any = None) -> AsyncGenerator[StreamedResponse]:
        effective, _ = self.wrapped.prepare_request(model_settings, model_request_parameters)
        await self.budget.reserve(self._priced_model_id, messages, effective, model_request_parameters)
        async with self.wrapped.request_stream(messages, model_settings, model_request_parameters,
                                               run_context) as stream:
            yield stream

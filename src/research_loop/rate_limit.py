"""Retry an explicitly timed rate limit across one run's parallel scouts.

The provider SDK retries timeouts along with 429s. Scout keeps SDK retries disabled and handles only
rate limits that say when to retry. One wrapper instance is shared by the run's scouts, so a 429 pauses
new requests from all of them. Provider errors with no retry time, including exhausted balances, fail.
"""
from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import logfire
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import Model, ModelRequestParameters, StreamedResponse
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings

RATE_LIMIT_POLICY_VERSION = "scout-429-v1"

# A repeated 429 may name a later reset. The research deadline still bounds the total wait.
_MAX_RETRIES = 2
_RESET_MARGIN_SECONDS = 0.1
_BODY_WAIT = re.compile(r"try\s+again\s+in\s+(\d+(?:\.\d+)?)\s*(milliseconds?|ms|seconds?|secs?|s)\b", re.IGNORECASE)
_DURATION = re.compile(r"^(\d+(?:\.\d+)?)\s*(milliseconds?|ms|seconds?|secs?|s)?$", re.IGNORECASE)


def _seconds(value: str) -> float | None:
    match = _DURATION.fullmatch(value.strip())
    if not match:
        return None
    amount = float(match.group(1))
    return amount / 1000 if (match.group(2) or "").lower().startswith(("ms", "milli")) else amount


def _retry_delay(error: ModelHTTPError) -> float | None:
    """Only retry a real rate limit with a provider-specified wait."""
    if error.status_code != 429:
        return None
    body = error.body
    if isinstance(body, str):
        with suppress(ValueError):
            body = json.loads(body)
    detail = body.get("error", body) if isinstance(body, dict) else body
    code = str(detail.get("code") or "") if isinstance(detail, dict) else ""
    message = str(detail.get("message") or "") if isinstance(detail, dict) else str(detail or "")
    if code in {"insufficient_balance", "insufficient_quota"}:
        return None
    if code != "rate_limit_exceeded" and "rate limit" not in message.lower():
        return None

    headers = error.headers or {}
    if value := headers.get("retry-after-ms"):
        with suppress(ValueError):
            return max(float(value) / 1000, 0)
    if value := headers.get("retry-after"):
        if (delay := _seconds(value)) is not None:
            return max(delay, 0)
        with suppress(TypeError, ValueError):
            date = parsedate_to_datetime(value)
            if date.tzinfo is not None:
                return max((date - datetime.now(UTC)).total_seconds(), 0)
    if (value := headers.get("x-ratelimit-reset-tokens")) and (delay := _seconds(value)) is not None:
        return max(delay, 0)
    if match := _BODY_WAIT.search(message):
        return _seconds("".join(match.groups()))
    return None


class ScoutRateLimitModel(WrapperModel):
    """Share the provider's pause across parallel scouts while leaving timeout retries disabled."""

    def __init__(self, wrapped: Model):
        super().__init__(wrapped)
        self._resume_at = 0.0
        self._lock = asyncio.Lock()

    async def _wait(self) -> None:
        loop = asyncio.get_running_loop()
        while (remaining := self._resume_at - loop.time()) > 0:
            await asyncio.sleep(remaining)

    async def _pause(self, error: ModelHTTPError) -> bool:
        if (delay := _retry_delay(error)) is None:
            return False
        loop = asyncio.get_running_loop()
        async with self._lock:
            self._resume_at = max(self._resume_at, loop.time() + delay + _RESET_MARGIN_SECONDS)
        logfire.info("Scout rate limit; waiting {delay_seconds}s before retry", delay_seconds=delay)
        return True

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        retries = 0
        while True:
            await self._wait()
            try:
                return await self.wrapped.request(messages, model_settings, model_request_parameters)
            except ModelHTTPError as error:
                if retries >= _MAX_RETRIES or not await self._pause(error):
                    raise
                retries += 1

    @asynccontextmanager
    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context: Any = None,
    ) -> AsyncGenerator[StreamedResponse]:
        retries = 0
        while True:
            await self._wait()
            opened = False
            try:
                async with self.wrapped.request_stream(
                    messages, model_settings, model_request_parameters, run_context
                ) as stream:
                    opened = True
                    yield stream
                return
            except ModelHTTPError as error:
                if opened or retries >= _MAX_RETRIES or not await self._pause(error):
                    raise
                retries += 1

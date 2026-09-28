"""Pace a run's scouts under their model's token rate limit, and retry an explicitly timed rate limit
or a transient network fault.

The provider SDK retries timeouts along with 429s. Scout keeps SDK retries disabled and handles only
rate limits that say when to retry, and connection faults that are not timeouts, which it sends once
more. One wrapper instance is shared by the run's scouts, so a 429 pauses new requests from all of
them. Provider errors with no retry time, including exhausted balances, fail, and so does a timeout.

Retrying alone does not hold under a tight limit: when OpenAI allowed this account 200,000 gpt-6-luna
tokens a minute, four scouts sent about 550,000 in two minutes, and paused scouts resumed together into
the same limit. So when a model's tokens-per-minute limit is known, the wrapper also holds each request
until the tokens sent in the last minute plus the request fit under most of that limit.

The limit is the one the provider reports: OpenAI sends `x-ratelimit-limit-tokens` with every response,
and `rate_limit_hook` records it, so a pacer uses the account's current tier from its first response on.
A configured `RESEARCH_TOKENS_PER_MINUTE` is only the starting point, unless it was set explicitly. The
configured 200,000 had held scouts to a tenth of the real limit after the tier rose to 2,000,000.
"""
from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
import httpx2
import logfire
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError
from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter, ModelResponse
from pydantic_ai.models import Model, ModelRequestParameters, StreamedResponse
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RequestUsage

# v2 adds pacing under a configured tokens-per-minute limit. v3 sends a request once more after a
# transient network fault. v4 paces under the limit the provider reports, unless one was set explicitly.
RATE_LIMIT_POLICY_VERSION = "scout-429-v4"

# Each model's tokens-per-minute limit as its provider last reported it, by `provider:model`.
_REPORTED_TOKENS_PER_MINUTE: dict[str, int] = {}


def reported_tokens_per_minute(model_id: str) -> int | None:
    """The tokens-per-minute limit the provider last reported for `model_id`, if any."""
    return _REPORTED_TOKENS_PER_MINUTE.get(model_id)


def rate_limit_hook(provider: str) -> Callable[[httpx2.Response], Awaitable[None]]:
    """An HTTP response hook that records the tokens-per-minute limit a response reports for its model."""
    async def record(response: httpx2.Response) -> None:
        limit = response.headers.get("x-ratelimit-limit-tokens", "")
        if not limit.isdigit() or int(limit) <= 0:
            return
        with suppress(ValueError, AttributeError, httpx2.RequestNotRead):
            model = json.loads(response.request.content or b"{}").get("model")
            if isinstance(model, str) and model:
                _REPORTED_TOKENS_PER_MINUTE[f"{provider}:{model}"] = int(limit)
    return record

# A repeated 429 may name a later reset. The research deadline still bounds the total wait.
_MAX_RETRIES = 2
_RESET_MARGIN_SECONDS = 0.1
# A connection fault that is not a timeout is sent again once, after this pause. In st07 run 7a7fc5b5 a
# TLS "bad record mac" from the OpenAI client ended a scout that had read 8 pages, losing its question.
_NETWORK_RETRIES = 1
_NETWORK_RETRY_SECONDS = 1.0
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


def transient_network_error(error: BaseException) -> bool:
    """A connection fault worth one more attempt: a TLS or socket error, raw or wrapped by the provider
    client, but never a timeout, since a slow reply sent again twice would fill the research window."""
    if isinstance(error, TimeoutError | httpx.TimeoutException):
        return False
    if isinstance(error, OSError | httpx.TransportError):
        return True
    if type(error) is ModelAPIError and (cause := error.__cause__) is not None:
        # openai and anthropic wrap faults as APIConnectionError, and timeouts as its APITimeoutError.
        name = type(cause).__name__
        return name == "APIConnectionError" or transient_network_error(cause)
    return False


# Pace to this share of the limit: the provider counts a request somewhat differently than the estimate.
_PACE_SHARE = 0.9
_WINDOW_SECONDS = 60.0
# Scout requests run about 5.3 bytes per billed token; four bytes a token estimates new content a little high.
_BYTES_PER_TOKEN = 4


def estimated_tokens(messages: list[ModelMessage], parameters: ModelRequestParameters) -> int:
    """A request's likely input tokens: the last reply's billed tokens plus what was added since.

    This is an estimate for pacing, not the budget guard's upper bound (study_budget.py).
    """
    for index in range(len(messages) - 1, -1, -1):
        reply = messages[index]
        if isinstance(reply, ModelResponse) and reply.usage.input_tokens > 0:
            added = len(ModelMessagesTypeAdapter.dump_json(messages[index + 1:]))
            return reply.usage.input_tokens + reply.usage.output_tokens + added // _BYTES_PER_TOKEN
    size = len(ModelMessagesTypeAdapter.dump_json(messages)) + len(repr(parameters).encode())
    return size // _BYTES_PER_TOKEN


class TokenPacer:
    """A sliding one-minute window of the tokens a run has sent to one model.

    Requests are admitted in arrival order. One that fits under the paced share of the limit, or that
    finds the window empty, goes at once; any other waits for the oldest request to leave the window.
    """

    def __init__(self, tokens_per_minute: int, model_id: str | None = None, *, fixed: bool = True) -> None:
        if tokens_per_minute <= 0:
            raise ValueError("tokens_per_minute must be positive")
        self.tokens_per_minute = tokens_per_minute
        # Unless `fixed`, the limit the provider reports for `model_id` replaces `tokens_per_minute`.
        self.model_id, self.fixed = model_id, fixed
        self._sent: list[list[float]] = []  # [time sent, tokens], oldest first
        self._lock = asyncio.Lock()

    @property
    def limit(self) -> int:
        """The limit paced under: the provider's reported one unless this pacer's was set explicitly."""
        reported = None if self.fixed or not self.model_id else reported_tokens_per_minute(self.model_id)
        return reported or self.tokens_per_minute

    def _in_window(self, now: float) -> float:
        self._sent = [entry for entry in self._sent if entry[0] > now - _WINDOW_SECONDS]
        return sum(tokens for _, tokens in self._sent)

    async def acquire(self, tokens: int) -> list[float]:
        """Wait until `tokens` fit, record them as sent now, and return the entry to settle later."""
        loop = asyncio.get_running_loop()
        async with self._lock:
            waited = 0.0
            while True:
                now = loop.time()
                used = self._in_window(now)
                if not self._sent or used + tokens <= self.limit * _PACE_SHARE:
                    break
                delay = self._sent[0][0] + _WINDOW_SECONDS - now
                waited += delay
                await asyncio.sleep(delay)
            if waited:
                logfire.info("Scout paced {waited_seconds}s under {tokens_per_minute} tokens a minute",
                             waited_seconds=round(waited, 1), tokens_per_minute=self.limit)
            entry = [now, float(tokens)]
            self._sent.append(entry)
            return entry

    @staticmethod
    def settle(entry: list[float], usage: RequestUsage) -> None:
        """Count what the provider billed in place of the estimate, when it reports usage."""
        if usage.input_tokens:
            entry[1] = float(usage.input_tokens + usage.output_tokens)


class ScoutRateLimitModel(WrapperModel):
    """Share the provider's pause across parallel scouts while leaving timeout retries disabled, and pace
    requests under `pacer` when the model has a configured token rate limit."""

    def __init__(self, wrapped: Model, pacer: TokenPacer | None = None):
        super().__init__(wrapped)
        self.pacer = pacer
        self._resume_at = 0.0
        self._lock = asyncio.Lock()

    async def _wait(self, messages: list[ModelMessage], parameters: ModelRequestParameters) -> list[float] | None:
        loop = asyncio.get_running_loop()
        while (remaining := self._resume_at - loop.time()) > 0:
            await asyncio.sleep(remaining)
        return await self.pacer.acquire(estimated_tokens(messages, parameters)) if self.pacer else None

    @staticmethod
    async def _network_retry(error: BaseException, attempts: int) -> bool:
        if attempts >= _NETWORK_RETRIES or not transient_network_error(error):
            return False
        logfire.info("Scout network error {error}; sending the request again", error=type(error).__name__)
        await asyncio.sleep(_NETWORK_RETRY_SECONDS)
        return True

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
        retries = network = 0
        while True:
            entry = await self._wait(messages, model_request_parameters)
            try:
                response = await self.wrapped.request(messages, model_settings, model_request_parameters)
                if entry is not None:
                    TokenPacer.settle(entry, response.usage)
                return response
            except ModelHTTPError as error:
                if retries >= _MAX_RETRIES or not await self._pause(error):
                    raise
                retries += 1
            except (OSError, httpx.TransportError, ModelAPIError) as error:
                if not await self._network_retry(error, network):
                    raise
                network += 1

    @asynccontextmanager
    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context: Any = None,
    ) -> AsyncGenerator[StreamedResponse]:
        retries = network = 0
        while True:
            entry = await self._wait(messages, model_request_parameters)
            opened = False
            try:
                async with self.wrapped.request_stream(
                    messages, model_settings, model_request_parameters, run_context
                ) as stream:
                    opened = True
                    yield stream
                if entry is not None:
                    TokenPacer.settle(entry, stream.usage)
                return
            except ModelHTTPError as error:
                if opened or retries >= _MAX_RETRIES or not await self._pause(error):
                    raise
                retries += 1
            except (OSError, httpx.TransportError, ModelAPIError) as error:
                # Once the stream has been handed on, its reader has the partial reply and cannot restart.
                if opened or not await self._network_retry(error, network):
                    raise
                network += 1

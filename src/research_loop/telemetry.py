"""Logfire tracing for research runs.

Tracing is on by default. Spans go to Logfire only when a token is set (`LOGFIRE_TOKEN`, or a
`logfire auth` project); without one they stay in the process. `RESEARCH_LOGFIRE=false` turns
tracing off. PydanticAI's instrumentation records every model request, tool call, retry, and
usage, with prompts, tool results, and outputs included: retrieved web text is therefore stored in
Logfire under its retention policy. API keys never enter spans, and Logfire's default scrubbing
stays on.

Each run is one trace under a `research run` span. The run ID is set as baggage, so every span in
the trace carries it, and the trace ID is stored on the run's database row: a trace leads to its
run, and a run to its trace. Postgres, not Logfire, is the durable record of runs and evidence.

The research tools' own HTTP clients (page fetches and scholarly lookups) are traced too, so a trace
shows each request's status and latency. A query parameter that carries a secret, such as OpenAlex's
`api_key`, is redacted from the span before it is exported; Logfire's scrubbing does not catch a key
inside a URL.
"""
from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import UUID

import httpx
import logfire

from .config import Settings

_configured = False


def configure_logfire(settings: Settings) -> None:
    """Configure Logfire once per process; later calls do nothing."""
    global _configured
    if _configured:
        return
    token = settings.logfire_token.get_secret_value() if settings.logfire_token else None
    logfire.configure(
        token=token,
        service_name="research-loop",
        environment=settings.env,
        send_to_logfire="if-token-present" if settings.logfire else False,
        console=False,
    )
    if settings.logfire:
        logfire.instrument_pydantic_ai(include_content=True)
    _configured = True


_SECRET_PARAM = re.compile(r"key|token|secret|password", re.IGNORECASE)


def _redact_url(span: Any, request: Any) -> None:
    url = httpx.URL(str(request.url))
    secrets = [name for name in url.params if _SECRET_PARAM.search(name)]
    if not secrets:
        return
    for name in secrets:
        url = url.copy_set_param(name, "[redacted]")
    span.set_attribute("http.url", str(url))
    span.set_attribute("logfire.msg", f"{request.method.decode() if isinstance(request.method, bytes) else request.method} {url}")


async def _redact_url_async(span: Any, request: Any) -> None:
    _redact_url(span, request)


def trace_http(client: httpx.AsyncClient, settings: Settings) -> httpx.AsyncClient:
    """`client`, with its requests traced when tracing is on and any secret query parameter redacted."""
    if settings.logfire:
        logfire.instrument_httpx(client, request_hook=_redact_url, async_request_hook=_redact_url_async)
    return client


@contextmanager
def run_span(run_id: UUID, mode: str, workflow_version: str, **attributes: Any) -> Iterator[Any]:
    """The span a whole run nests under, with `run_id` as baggage on every span inside it."""
    with logfire.set_baggage(run_id=str(run_id)), logfire.span(
        "research run {mode} {run_id}", run_id=str(run_id), mode=mode, workflow_version=workflow_version, **attributes,
    ) as span:
        yield span


def trace_id(span: Any) -> str | None:
    """The span's trace ID as Logfire shows it (32 hex digits); None when the span is not recording."""
    context = span.get_span_context() if span is not None else None
    return format(context.trace_id, "032x") if context and context.trace_id else None

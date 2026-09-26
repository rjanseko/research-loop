from __future__ import annotations

from uuid import uuid4

import httpx
import logfire

from research_loop.config import Settings
from research_loop.telemetry import run_span, trace_http, trace_id


def test_every_span_in_a_run_carries_its_run_id(capfire) -> None:
    run_id = uuid4()
    with run_span(run_id, "scout", "scout-v1", question_count=2) as span:
        with logfire.span("scout {question_id}", question_id="q1"):
            pass
        recorded = trace_id(span)
    spans = {item["name"]: item["attributes"] for item in capfire.exporter.exported_spans_as_dict()}
    run, child = spans["research run {mode} {run_id}"], spans["scout {question_id}"]
    assert (run["run_id"], run["mode"], run["workflow_version"], run["question_count"]) == (
        str(run_id), "scout", "scout-v1", 2)
    assert child["run_id"] == str(run_id)  # baggage reaches spans inside the run
    assert recorded is not None and len(recorded) == 32


def test_no_span_means_no_trace_id() -> None:
    assert trace_id(None) is None


async def test_tool_requests_are_traced_without_their_secrets(capfire) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": []})

    async with trace_http(httpx.AsyncClient(transport=httpx.MockTransport(respond)), Settings()) as client:
        await client.get("https://api.openalex.org/works", params={"search": "swe-bench", "api_key": "SECRET-123"})
        await client.get("https://example.org/page")
    spans = [item for item in capfire.exporter.exported_spans_as_dict() if item["name"] == "GET"]
    assert len(spans) == 2
    values = [str(value) for item in spans for value in item["attributes"].values()]
    assert not any("SECRET-123" in value for value in values)
    urls = sorted(item["attributes"]["http.url"] for item in spans)
    assert urls == ["https://api.openalex.org/works?search=swe-bench&api_key=%5Bredacted%5D", "https://example.org/page"]


async def test_tool_requests_are_not_traced_when_tracing_is_off(capfire) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200)

    async with trace_http(httpx.AsyncClient(transport=httpx.MockTransport(respond)), Settings(logfire=False)) as client:
        await client.get("https://example.org/page")
    assert not [item for item in capfire.exporter.exported_spans_as_dict() if item["name"] == "GET"]

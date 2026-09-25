from __future__ import annotations

import pytest
from pydantic_ai.exceptions import ModelHTTPError

from research_loop.schemas import ToolEvent
from research_loop.telemetry import (
    SourceReach,
    error_snapshot,
    transcript,
    unreached_source,
)


def test_error_snapshot_omits_provider_response_body() -> None:
    exc = ModelHTTPError(400, "test-model", body={"secret": "PRIVATE_TOKEN", "prompt": "PRIVATE_PROMPT"})
    assert error_snapshot(exc) == {"type": "ModelHTTPError", "status_code": 400}
    assert "PRIVATE_TOKEN" not in str(error_snapshot(exc))
    assert error_snapshot(ValueError("PRIVATE_PROMPT")) == {"type": "ValueError"}


def test_benchmark_audit_uses_ephemeral_raw_arguments_before_storage_redaction() -> None:
    from research_loop.acquisition import SourcePolicy
    from research_loop.benchmark import _audit_blocked_sources
    from research_loop.telemetry import safe_tool_args, safe_tool_result

    url = "https://example.org/blocked-source"
    event = {"tool_name": "web_fetch", "args": {"url": url}, "result": {"text": "PRIVATE_PAPER_TEXT"}}
    assert _audit_blocked_sources([event], [], SourcePolicy((url,)))["blocked_fetches_completed"] == [url]
    assert url not in str(safe_tool_args(event["args"]))
    assert "PRIVATE_PAPER_TEXT" not in str(safe_tool_result(event["tool_name"], event["result"]))


def test_fetch_telemetry_keeps_error_codes_but_not_content() -> None:
    from research_loop.telemetry import safe_tool_result

    refused = {"url": "https://blocked.example/a", "error": "BlockedSource", "blocked": "https://blocked.example"}
    stored = safe_tool_result("web_fetch", refused)
    assert stored["error"] == "BlockedSource"
    assert "blocked.example" not in str(stored)
    assert "status" not in safe_tool_result("web_fetch", {"url": "https://a.example", "text": "PRIVATE"})


def test_scholar_telemetry_omits_full_text_and_query() -> None:
    from research_loop.telemetry import safe_tool_args, safe_tool_result

    secret = "PRIVATE_BENCHMARK_INPUT"
    body = {"operation": "fetch", "text": secret * 300, "works": [], "content_sha256": "abc"}
    summary = safe_tool_result("scholar_fetch", body)
    assert secret not in str(summary)
    args = safe_tool_args({"query": secret, "limit": 5})
    assert secret not in str(args)
    assert args["limit"] == 5


@pytest.mark.parametrize(("result", "reason"), [
    ({"url": "https://a.example", "error": "ProxyError"}, "ProxyError"),
    ('{"url": "https://a.example", "error": "ConnectTimeout"}', "ConnectTimeout"),
    ({"error": "SearchUnavailable (TimeoutException)"}, "SearchUnavailable"),
    ({"operation": "search", "provider_errors": ["openalex:ConnectError", "arxiv:HTTPStatusError"]}, "ConnectError"),
    ({"operation": "references", "provider_errors": ["references:ProxyError"]}, "ProxyError"),
    # Reached a source: a status, a refusal of our own, or a search that still returned works.
    ({"url": "https://a.example", "error": "HTTPStatusError", "status": 403}, None),
    ({"url": "https://a.example", "error": "BlockedSource"}, None),
    ({"operation": "search", "works": [{"title": "t"}], "provider_errors": ["crossref:ConnectError"]}, None),
    ({"operation": "search", "provider_errors": ["query is empty"]}, None),
    # ddgs raises DDGSException for a search that found nothing, too.
    ({"error": "SearchUnavailable (DDGSException)"}, None),
    ([{"href": "https://a.example"}], None),
], ids=["proxy", "json-timeout", "search", "scholar", "references", "status", "blocked", "partial", "empty",
        "search-empty-or-failed", "results"])
def test_unreached_source_names_network_failures_only(result, reason) -> None:
    assert unreached_source(result) == reason


def test_source_reach_asks_for_review_when_most_web_and_scholarly_calls_reached_nothing() -> None:
    reach = SourceReach()
    reach.add([
        ToolEvent(tool_name="web_fetch", result={"error": "ProxyError"}),
        ToolEvent(tool_name="scholar_search", result={"provider_errors": ["openalex:ProxyError"]}),
        ToolEvent(tool_name="web_fetch", result={"text": "page"}),
        # Not counted: an empty search is indistinguishable from one whose engines all failed.
        ToolEvent(tool_name="duckduckgo_search", result={"error": "SearchUnavailable (DDGSException)"}),
        # Neither counts: attachments are local, and an unanswered call has no result.
        ToolEvent(tool_name="read_attachment", result={"error": "ProxyError"}),
        ToolEvent(tool_name="web_fetch"),
    ])
    assert (reach.calls, reach.review_reason()) == (4, None)  # two failures are too few to judge
    reach.add([
        ToolEvent(tool_name="duckduckgo_search", result={"error": "SearchUnavailable (TimeoutException)"}),
        ToolEvent(tool_name="scholar_citations", result={"provider_errors": ["citations:ProxyError"]}),
    ])
    assert reach.review_reason() == (
        "4 of 6 web and scholarly tool calls reached no source (ProxyError, SearchUnavailable)"
    )
    reach.add([ToolEvent(tool_name="web_fetch", result={"text": "page"})] * 3)
    assert reach.review_reason() is None  # under half


def test_transcript_keeps_messages_whole_and_cuts_only_oversized_strings() -> None:
    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        TextPart,
        ToolCallPart,
        ToolReturnPart,
        UserPromptPart,
    )

    messages = [
        ModelRequest(parts=[UserPromptPart(content="Which benchmark?")]),
        ModelResponse(parts=[ToolCallPart("web_fetch", {"url": "https://a.example/page"}, tool_call_id="t1")]),
        ModelRequest(parts=[ToolReturnPart("web_fetch", {"text": "x" * 120}, tool_call_id="t1")]),
        ModelResponse(parts=[TextPart(content="done")]),
    ]
    dumped, cut = transcript(messages, max_chars=100)

    assert len(dumped) == 4 and cut == 1
    assert dumped[0]["parts"][0]["content"] == "Which benchmark?"
    assert dumped[1]["parts"][0]["args"] == {"url": "https://a.example/page"}  # real arguments, not hashes
    fetched = dumped[2]["parts"][0]["content"]["text"]
    assert fetched == "x" * 100 + " [truncated 20 chars]"


def test_a_tool_call_starts_when_the_response_carrying_it_arrived() -> None:
    from datetime import UTC, datetime

    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        ToolCallPart,
        ToolReturnPart,
        UserPromptPart,
    )

    from research_loop.telemetry import extract_tool_events

    sent, arrived, returned = (datetime(2026, 9, 25, 12, 0, s, tzinfo=UTC) for s in (0, 7, 9))
    messages = [
        ModelRequest(parts=[UserPromptPart("q")], timestamp=sent),
        ModelResponse(parts=[ToolCallPart("web_fetch", {"url": "https://example.org"}, tool_call_id="c1")],
                      timestamp=arrived),
        ModelRequest(parts=[ToolReturnPart("web_fetch", "text", tool_call_id="c1", timestamp=returned)]),
    ]
    [event] = extract_tool_events(messages)
    # Model time is arrived - sent; tool time is returned - arrived.
    assert (event.called_at, event.returned_at) == (arrived, returned)


def test_cache_hits_come_from_search_metadata_or_the_fetch_result() -> None:
    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        ToolCallPart,
        ToolReturnPart,
    )

    from research_loop.telemetry import extract_tool_events

    calls = [("duckduckgo_search", "s"), ("web_fetch", "f"), ("scholar_search", "o")]
    returns = [
        ToolReturnPart("duckduckgo_search", [{"title": "t"}], tool_call_id="s", metadata={"cache_hit": True}),
        ToolReturnPart("web_fetch", {"url": "https://example.org", "text": "t", "cache_hit": False}, tool_call_id="f"),
        ToolReturnPart("scholar_search", '{"works": [], "cache_hits": 2}', tool_call_id="o"),
    ]
    messages = [ModelResponse(parts=[ToolCallPart(name, {}, tool_call_id=i) for name, i in calls]),
                ModelRequest(parts=returns)]
    assert [event.cache_hit for event in extract_tool_events(messages)] == [True, False, None]

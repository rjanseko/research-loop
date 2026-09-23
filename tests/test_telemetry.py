from __future__ import annotations

from pydantic_ai.exceptions import ModelHTTPError

from research_loop.telemetry import error_snapshot


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

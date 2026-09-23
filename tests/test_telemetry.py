from __future__ import annotations

from pydantic_ai.exceptions import ModelHTTPError

from research_loop.telemetry import error_snapshot


def test_error_snapshot_omits_provider_response_body() -> None:
    exc = ModelHTTPError(400, "test-model", body={"secret": "PRIVATE_TOKEN", "prompt": "PRIVATE_PROMPT"})
    assert error_snapshot(exc) == {"type": "ModelHTTPError", "status_code": 400}
    assert "PRIVATE_TOKEN" not in str(error_snapshot(exc))
    assert error_snapshot(ValueError("PRIVATE_PROMPT")) == {"type": "ValueError"}


def test_benchmark_audit_uses_ephemeral_raw_arguments_before_storage_redaction() -> None:
    from research_loop.benchmark import _blocked_accesses
    from research_loop.telemetry import safe_tool_args, safe_tool_result

    url = "https://example.org/blocked-source"
    event = {"tool_name": "web_fetch", "args": {"url": url}, "result": {"text": "PRIVATE_PAPER_TEXT"}}
    assert _blocked_accesses([event], [url]) == [url]
    assert url not in str(safe_tool_args(event["args"]))
    assert "PRIVATE_PAPER_TEXT" not in str(safe_tool_result(event["tool_name"], event["result"]))

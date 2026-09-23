from __future__ import annotations

import sys
from types import SimpleNamespace

from research_loop import observability
from research_loop.settings import ResearchSettings


def test_logfire_off_by_default(monkeypatch) -> None:
    monkeypatch.setattr(observability, "_configured", False)
    assert not observability.configure_logfire(ResearchSettings.from_env({}))


def test_logfire_is_opt_in_content_free_and_idempotent(monkeypatch) -> None:
    calls: list[tuple[str, dict]] = []
    instance = SimpleNamespace(instrument_pydantic_ai=lambda **kwargs: calls.append(("instrument", kwargs)))
    fake = SimpleNamespace(configure=lambda **kwargs: (calls.append(("configure", kwargs)), instance)[1])
    monkeypatch.setitem(sys.modules, "logfire", fake)
    monkeypatch.setattr(observability, "_configured", False)
    settings = ResearchSettings.from_env({"RESEARCH_LOGFIRE_ENABLED": "true"})
    assert observability.configure_logfire(settings)
    assert observability.configure_logfire(settings)
    assert calls == [
        ("configure", {"local": True, "service_name": "research-loop", "send_to_logfire": "if-token-present", "console": False}),
        ("instrument", {"include_content": False, "include_binary_content": False, "include_model_request_parameters": False}),
    ]

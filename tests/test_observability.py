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
    monkeypatch.setattr(observability, "_instance", None)
    settings = ResearchSettings.from_env({"RESEARCH_LOGFIRE_ENABLED": "true"})
    assert observability.configure_logfire(settings)
    assert observability.configure_logfire(settings)
    assert calls == [
        ("configure", {"local": True, "service_name": "research-loop", "send_to_logfire": "if-token-present", "console": False}),
        ("instrument", {"include_content": False, "include_binary_content": False, "include_model_request_parameters": False}),
    ]
    assert observability._instance is instance


async def test_each_job_runs_in_one_content_free_span(monkeypatch) -> None:
    from contextlib import contextmanager

    from research_loop import ResearchConfig, get_policy
    from research_loop.repository import InMemoryResearchRepository
    from research_loop.synthetic import SyntheticResearchLoop

    spans: list[tuple[str, dict]] = []
    open_spans: list[str] = []

    @contextmanager
    def span(template: str, **attributes):
        spans.append((template, attributes))
        open_spans.append(template)
        try:
            yield
        finally:
            open_spans.remove(template)

    agent_calls_in_span: list[bool] = []
    original = SyntheticResearchLoop._run_agent

    async def traced_run_agent(self, **kwargs):
        agent_calls_in_span.append(bool(open_spans))
        return await original(self, **kwargs)

    monkeypatch.setattr(observability, "_instance", SimpleNamespace(span=span))
    monkeypatch.setattr(SyntheticResearchLoop, "_run_agent", traced_run_agent)
    repo = InMemoryResearchRepository()
    loop = SyntheticResearchLoop(get_policy("synthetic"), ResearchConfig(), repository=repo)
    await loop.run("Private objective text")

    (job_id,) = repo.jobs
    assert spans == [("research job {job_id}", {"job_id": str(job_id), "policy": "synthetic"})]
    assert agent_calls_in_span and all(agent_calls_in_span)
    assert not open_spans


def test_job_span_is_a_no_op_without_logfire(monkeypatch) -> None:
    from uuid import uuid4

    monkeypatch.setattr(observability, "_instance", None)
    with observability.job_span(uuid4(), "quality"):
        pass

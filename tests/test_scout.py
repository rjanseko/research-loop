"""The Scout workflow end to end, offline: scripted models, pages served locally, runs kept in memory."""
from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

from research_loop.agents import planner_agent, scout_agent, synthesizer_agent
from research_loop.config import ScoutLimits, Settings
from research_loop.render import render_markdown
from research_loop.scout import ConfigError, scout
from research_loop.store import MemoryStore

PAGES = {
    "https://example.org/verified": "SWE-bench Verified is a subset of 500 tasks that annotators screened.",
    "https://example.org/leakage": "Some gold patches appear in model training data, a contamination risk.",
}
# Scripted models have no price; every run here therefore says its cost is a lower bound.
UNPRICED = "a model call had no price, so the cost shown is a lower bound"
pytestmark = pytest.mark.filterwarnings("ignore::pydantic_ai.exceptions.CostNotFoundWarning")

QUESTIONS = [{"id": "a", "question": "How was SWE-bench Verified built?"},
             {"id": "b", "question": "Is SWE-bench contaminated?"}]


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch, tmp_path) -> Settings:
    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "ZAI_API_KEY"):
        monkeypatch.setenv(name, "test-key")

    async def no_search(query: str) -> list[dict[str, str]]:
        return []

    monkeypatch.setattr("research_loop.web._duckduckgo", lambda: no_search)
    return Settings(cache_dir=tmp_path, cache_mode="off")


@pytest.fixture
def pages(serve) -> None:
    serve(lambda url: httpx.Response(200, headers={"content-type": "text/html"},
                                     text=f"<html><body><article><p>{PAGES[url]}</p></article></body></html>")
          if url in PAGES else httpx.Response(404))


def _prompt(messages: list[ModelMessage]) -> dict[str, Any]:
    first = messages[0]
    assert isinstance(first, ModelRequest)
    return json.loads(next(p.content for p in first.parts if isinstance(p, UserPromptPart)))


def _output(info: AgentInfo, value: dict[str, Any]) -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, value)])


def planner(questions=QUESTIONS):
    return FunctionModel(lambda messages, info: _output(info, {"questions": questions}))


def researcher(urls: dict[str, str] | None = None):
    """A scout that fetches its question's page, then returns a claim quoting it."""
    urls = urls or {"q1": "https://example.org/verified", "q2": "https://example.org/leakage"}

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        question = _prompt(messages)["question"]
        url = urls[question["id"]]
        if len(messages) == 1:
            return ModelResponse(parts=[ToolCallPart("fetch", {"url": url})])
        quote = PAGES.get(url, "an invented sentence")[:40]
        return _output(info, {
            "question_id": question["id"], "question": question["question"], "conclusion": "found", "confidence": 0.8,
            "claims": [{"id": "c1", "statement": f"Finding for {question['id']}", "confidence": 0.8, "evidence": [
                {"source": {"url": url, "title": "Page"}, "excerpt": "summary", "quote": quote, "confidence": 0.8}]}],
        })

    return FunctionModel(respond)


def streamed(respond) -> FunctionModel:
    """Synthesis streams its reply; stream a scripted output call in one chunk."""
    async def stream(messages: list[ModelMessage], info: AgentInfo):
        part = respond(messages, info).parts[0]
        yield {0: DeltaToolCall(name=part.tool_name, json_args=json.dumps(part.args))}

    return FunctionModel(stream_function=stream)


def reasons(run) -> list[str]:
    return [reason for reason in run.checks.review_reasons if reason != UNPRICED]


def writer(extra_claim: str | None = None, *, calls: list[int] | None = None):
    """A synthesizer that cites every claim and its sources; `extra_claim` adds one that does not exist."""
    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if calls is not None:
            calls.append(len(messages))
        research = _prompt(messages)["research"]
        claims = [claim for result in research["research"] for claim in result.get("claims", [])]
        statements = [{"statement": claim["statement"], "claim_ids": [claim["id"]]} for claim in claims]
        if extra_claim and len(messages) == 1:
            statements.append({"statement": "Unsupported", "claim_ids": [extra_claim]})
        cited = " ".join(f"{c['statement']} [{c['evidence'][0]['source_id']}]." for c in claims)
        return _output(info, {"title": "SWE-bench Verified", "executive_summary": cited,
                              "answer": f"Mostly trustworthy. {cited}", "claims": statements, "caveats": []})

    return streamed(respond)


async def _run(settings: Settings, store: MemoryStore | None = None, *, plan=None, research=None, write=None, **kwargs):
    with (planner_agent.override(model=plan or planner()), scout_agent.override(model=research or researcher()),
          synthesizer_agent.override(model=write or writer())):
        return await scout("Is SWE-bench Verified trustworthy?", settings=settings, store=store or MemoryStore(), **kwargs)


async def test_a_question_becomes_a_cited_answer_traced_to_what_was_read(settings, pages, capfire) -> None:
    store = MemoryStore()
    run = await _run(settings, store)
    assert run.status == "complete" and reasons(run) == []
    assert [q.id for q in run.plan.questions] == ["q1", "q2"]  # renumbered in plan order
    assert sorted(run.ledger.claim_ids()) == ["q1/c1", "q2/c1"]
    evidence = run.ledger.claims_by_id()["q1/c1"].evidence[0]
    assert (evidence.quote_check, evidence.quote_access, evidence.source_access) == ("verified", "full_text", "full_text")
    assert [s.support for s in run.checks.statements] == ["read", "read"]
    assert run.checks.evidence_by_access == {"full_text": 2} and run.checks.quotes_verified == 2

    stored = store.runs[run.run_id]
    assert stored["status"] == "complete" and stored["trace_id"] == run.trace_id
    assert stored["config"]["models"]["scout"] == {"model": "zai:glm-5.3-flash", "thinking": "xhigh"}
    roles = sorted(call["role"] for call in store.calls.values())
    assert roles == ["planner", "scout", "scout", "synthesizer"]
    assert all(call["status"] == "succeeded" and call["messages"] for call in store.calls.values())
    scout_call = next(c for c in store.calls.values() if c["role"] == "scout")
    assert scout_call["output"]["pages_read"] and scout_call["output"]["claims"][0]["evidence"][0]["source_access"]

    spans = capfire.exporter.exported_spans_as_dict()
    assert {s["attributes"].get("run_id") for s in spans if s["name"].startswith("scout")} == {str(run.run_id)}

    markdown = render_markdown(run.to_record())
    assert "## Answer" in markdown and "[s1] Page. https://example.org/verified (read in full)" in markdown


async def test_a_scout_that_runs_out_leaves_its_question_unanswered(settings, pages) -> None:
    def keeps_fetching(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        question = _prompt(messages)["question"]
        if question["id"] == "q1":
            return researcher().function(messages, info)
        # Asks for a page on every request, even after its tools are withdrawn.
        return ModelResponse(parts=[ToolCallPart("fetch", {"url": "https://example.org/leakage"})])

    run = await _run(settings, research=FunctionModel(keeps_fetching))
    assert run.status == "partial"
    (cut,) = [r for r in run.ledger.all() if r.question_id == "q2"]
    assert cut.claims == [] and cut.cut_off.startswith("limit reached") and cut.pages_read
    assert run.checks.not_established[0].startswith("q2: Is SWE-bench contaminated?")
    assert "1 of 2 research questions returned no evidence" in reasons(run)


async def test_scouts_still_running_at_the_research_deadline_are_cut_off(settings, pages) -> None:
    settings.limits = ScoutLimits(research_seconds=0.5, deadline_seconds=30)

    async def slow_on_q2(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if _prompt(messages)["question"]["id"] == "q2":
            await asyncio.sleep(10)
        return researcher().function(messages, info)

    store = MemoryStore()
    run = await _run(settings, store, research=FunctionModel(slow_on_q2))
    assert run.status == "partial" and run.report is not None
    assert "research deadline" in run.ledger.results["q2"][0].cut_off
    assert sorted(c["status"] for c in store.calls.values() if c["role"] == "scout") == ["cancelled", "succeeded"]


async def test_a_cancelled_run_is_recorded_with_what_it_found(settings, pages) -> None:
    started = asyncio.Event()

    async def hangs(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        started.set()
        await asyncio.sleep(60)
        raise AssertionError("not reached")

    store = MemoryStore()
    task = asyncio.create_task(_run(settings, store, research=FunctionModel(hangs)))
    await asyncio.wait_for(started.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    (run,) = store.runs.values()
    assert run["status"] == "cancelled"
    assert {c["status"] for c in store.calls.values() if c["role"] == "scout"} == {"cancelled"}


async def test_when_every_fetch_fails_the_run_fails_and_says_why(settings, pages) -> None:
    urls = {"q1": "https://example.org/gone", "q2": "https://example.org/also-gone"}

    def empty_handed(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        question = _prompt(messages)["question"]
        if len(messages) == 1:
            return ModelResponse(parts=[ToolCallPart("fetch", {"url": urls[question["id"]]})])
        return _output(info, {"question_id": question["id"], "question": question["question"],
                              "conclusion": "nothing found", "confidence": 0.1, "unresolved": ["everything"]})

    run = await _run(settings, research=FunctionModel(empty_handed))
    assert run.status == "failed" and run.report is None
    assert "no research question returned evidence" in reasons(run)
    assert [u.reason for u in run.checks.unreached] == ["HTTPStatusError 404", "HTTPStatusError 404"]
    assert "## Sources that could not be read" in render_markdown(run.to_record())


async def test_a_report_citing_an_unknown_claim_gets_one_retry(settings, pages) -> None:
    calls: list[int] = []
    run = await _run(settings, write=writer(extra_claim="q9/c1", calls=calls))
    assert len(calls) == 2 and run.status == "complete"
    assert run.checks.citation_problems == []


async def test_a_synthesis_that_fails_returns_the_claims_found(settings, pages) -> None:
    def always_wrong(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return _output(info, {"title": "t", "executive_summary": "", "answer": "a",
                              "claims": [{"statement": "s", "claim_ids": ["q9/c9"]}]})

    run = await _run(settings, write=streamed(always_wrong))
    assert run.status == "partial" and run.report is None
    assert run.notes == ["the synthesis did not finish (the model's output failed its checks twice)"]
    markdown = render_markdown(run.to_record())
    assert "## Claims found" in markdown and "- Finding for q1 [s1] (q1/c1)" in markdown


async def test_a_failed_plan_researches_the_question_as_one(settings, pages) -> None:
    def refuses(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return _output(info, {"questions": []})  # fails its check twice

    run = await _run(settings, plan=FunctionModel(refuses), research=researcher({"q1": "https://example.org/verified"}))
    assert [q.question for q in run.plan.questions] == ["Is SWE-bench Verified trustworthy?"]
    assert run.status == "complete" and run.notes[0].startswith("planning failed")


async def test_a_run_that_cannot_be_configured_is_refused_before_any_call(settings, monkeypatch) -> None:
    monkeypatch.delenv("ZAI_API_KEY")
    store = MemoryStore()
    with pytest.raises(ConfigError, match="needs ZAI_API_KEY"):
        await scout("Q?", settings=Settings(cache_mode="off"), store=store)
    assert store.runs == {}

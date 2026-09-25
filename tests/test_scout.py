"""The Scout workflow end to end, offline: scripted models, pages served locally, runs kept in memory."""
from __future__ import annotations

import asyncio
import json
from decimal import Decimal
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
from research_loop.scout import ConfigError, StudyLabels, scout, synthesize_stored
from research_loop.store import MemoryStore
from research_loop.study_budget import StudyBudget

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
    scout_model = stored["config"]["models"]["scout"]
    assert (scout_model["model"], scout_model["thinking"]) == ("openai:gpt-6-luna", "high")
    assert set(stored["config"]["models"]["fallback"]["sent"]) == {"planner", "synthesizer"}
    assert len(stored["input_hash"]) == 64 and stored["study_id"] is None
    assert stored["cache"] == {"mode": "off", "by_provider": {}}
    roles = sorted(call["role"] for call in store.calls.values())
    assert roles == ["planner", "scout", "scout", "synthesizer"]
    assert all(call["status"] == "succeeded" and call["messages"] for call in store.calls.values())
    reasons_by_role = {call["role"]: call["stop_reason"] for call in store.calls.values()}
    assert reasons_by_role == {"planner": "returned a result", "scout": "returned on its own",
                               "synthesizer": "returned a result"}
    assert all(call["tool_seconds"] >= 0 for call in store.calls.values() if call["role"] == "scout")
    assert all(call["tool_seconds"] is None for call in store.calls.values() if call["role"] != "scout")
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
    # The request timeout stays under the time remaining, so this scout is cut off while still working
    # rather than told to return.
    settings.limits = ScoutLimits(research_seconds=0.5, deadline_seconds=30, request_timeout_seconds=0.05)

    async def slow_on_q2(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if _prompt(messages)["question"]["id"] == "q2":
            await asyncio.sleep(10)
        return researcher().function(messages, info)

    store = MemoryStore()
    run = await _run(settings, store, research=FunctionModel(slow_on_q2))
    assert run.status == "partial" and run.report is not None
    assert "research deadline" in run.ledger.results["q2"][0].cut_off
    assert sorted(c["status"] for c in store.calls.values() if c["role"] == "scout") == ["cancelled", "succeeded"]
    (cut_call,) = [c for c in store.calls.values() if c["status"] == "cancelled"]
    assert cut_call["stop_reason"] == "the research deadline passed"


async def test_a_plan_past_its_time_cap_says_so(settings, pages, monkeypatch) -> None:
    monkeypatch.setattr("research_loop.scout._PLAN_SECONDS", 0.2)

    async def slow_plan(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        await asyncio.sleep(10)
        raise AssertionError("not reached")

    store = MemoryStore()
    run = await _run(settings, store, plan=FunctionModel(slow_plan), research=researcher({"q1": "https://example.org/verified"}))
    assert run.notes[0] == "planning failed (planning took longer than 0.2 seconds), so the question was researched as one"
    (plan_call,) = [c for c in store.calls.values() if c["role"] == "planner"]
    assert (plan_call["status"], plan_call["stop_reason"]) == ("cancelled", "planning took longer than 0.2 seconds")


async def test_a_run_counts_its_cache_hits_and_carries_its_study_labels(settings, pages) -> None:
    settings.cache_mode = "reuse"
    await _run(settings)
    store = MemoryStore()
    run = await _run(settings, store, study=StudyLabels("s1", "high", 2))
    stored = store.runs[run.run_id]
    assert (stored["study_id"], stored["arm"], stored["replicate"]) == ("s1", "high", 2)
    assert stored["cache"]["mode"] == "reuse"
    assert stored["cache"]["by_provider"]["web"] == {"hits": 2, "misses": 0, "writes": 0}


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
    monkeypatch.delenv("OPENAI_API_KEY")
    store = MemoryStore()
    with pytest.raises(ConfigError, match="needs OPENAI_API_KEY"):
        await scout("Q?", settings=Settings(cache_mode="off"), store=store)
    assert store.runs == {}


async def test_fixed_ledger_synthesis_reuses_the_production_call(settings, pages) -> None:
    store = MemoryStore()
    original = await _run(settings, store)
    source = store.runs[original.run_id]
    before = len(store.calls)
    with synthesizer_agent.override(model=writer()):
        rerendered = await synthesize_stored(source, settings=settings, store=store,
                                             study=StudyLabels("synthesis-screen", "scripted"))
    assert rerendered.status == "complete"
    assert rerendered.ledger.to_json() == original.ledger.to_json()
    saved = store.runs[rerendered.run_id]
    assert saved["mode"] == "fixed-ledger" and saved["parent_run_id"] == original.run_id
    assert saved["config"]["fixed_ledger"]["source_run_id"] == str(original.run_id)
    assert len(saved["config"]["fixed_ledger"]["ledger_sha256"]) == 64
    assert saved["study_id"] == "synthesis-screen"
    calls = [call for call in store.calls.values() if call["run_id"] == rerendered.run_id]
    assert len(store.calls) == before + 1 and len(calls) == 1
    assert calls[0]["role"] == "synthesizer" and calls[0]["status"] == "succeeded"


async def test_fixed_ledger_synthesis_requires_claims(settings) -> None:
    with pytest.raises(ValueError, match="no claims"):
        await synthesize_stored({"id": "00000000-0000-0000-0000-000000000001",
                                 "workflow_version": "scout-v1", "question": "q",
                                 "plan": {"questions": [{"id": "q1", "question": "q"}]},
                                 "ledger": {"q1": []}}, settings=settings, store=MemoryStore())


async def test_fixed_ledger_budget_refuses_before_streaming(settings, pages, monkeypatch) -> None:
    store = MemoryStore()
    original = await _run(settings, store)
    sent = []
    scripted = writer(calls=sent)
    monkeypatch.setattr("research_loop.scout.build_model", lambda *_args, **_kwargs: scripted)
    budget = StudyBudget(Decimal("0.0001"))
    rerendered = await synthesize_stored(store.runs[original.run_id], settings=settings, store=store,
                                         budget=budget)
    assert rerendered.status == "partial" and rerendered.report is None
    assert not sent and budget.reserved_usd == 0
    call = next(c for c in store.calls.values() if c["run_id"] == rerendered.run_id)
    assert call["error"]["type"] == "StudyBudgetRefusal"
    assert call["stop_reason"].startswith("study budget refused:")


async def test_follow_up_recovers_one_missing_question(settings, pages) -> None:
    from research_loop.agents import gap_agent
    from research_loop.prompts import prompt_fingerprint
    from research_loop.scout import FOLLOWUP_VERSION

    store = MemoryStore()

    def research(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        prompt = _prompt(messages)
        question = prompt["question"]
        if question["id"] == "q2" and "material_gap" not in prompt:
            return _output(info, {"question_id": "q2", "question": question["question"],
                                  "conclusion": "not established", "confidence": 0, "unresolved": ["contamination"]})
        url = "https://example.org/leakage" if question["id"] == "q2" else "https://example.org/verified"
        if len(messages) == 1:
            return ModelResponse(parts=[ToolCallPart("fetch", {"url": url})])
        return _output(info, {"question_id": question["id"], "question": question["question"],
                              "conclusion": "found", "confidence": 0.8,
                              "claims": [{"id": "c1", "statement": f"Finding for {question['id']}",
                                          "confidence": 0.8, "evidence": [{"source": {"url": url, "title": "Page"},
                                                                           "excerpt": "summary", "confidence": 0.8}]}]})

    def gap(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        prompt = _prompt(messages)
        assert prompt["not_established"] == ["q2: Is SWE-bench contaminated? (no evidence found)"]
        assert [item["question_id"] for item in prompt["research"]["research"]] == ["q1", "q2"]
        return _output(info, {"gaps": [{"question_id": "q99" if len(messages) == 1 else "q2",
                                         "follow_up_question": "What direct evidence exists of contamination?",
                                         "reason": "This could change the trust assessment."}]})

    with gap_agent.override(model=FunctionModel(gap)):
        run = await _run(settings, store, research=FunctionModel(research), follow_up=True)
    assert run.workflow_version == FOLLOWUP_VERSION
    assert run.to_record()["workflow_version"] == FOLLOWUP_VERSION
    assert run.status == "complete" and not run.checks.not_established
    assert run.checks.gap_analysis.gaps[0].question_id == "q2"
    assert store.runs[run.run_id]["checks"]["gap_analysis"]["gaps"][0]["question_id"] == "q2"
    assert sorted(run.ledger.claim_ids()) == ["q1/c1", "q2/c1"]
    assert [c["role"] for c in store.calls.values()].count("deep_dive") == 1
    assert run.config["prompt_fingerprint"] == prompt_fingerprint(follow_up=True)
    assert run.config["prompt_fingerprint"] != prompt_fingerprint()
    assert run.config["limits"]["followup_cost_usd"] == 1.25


async def test_follow_up_skips_deep_dive_when_no_material_gap(settings, pages) -> None:
    from research_loop.agents import gap_agent

    store = MemoryStore()
    gap = FunctionModel(lambda messages, info: _output(info, {"gaps": []}))
    with gap_agent.override(model=gap):
        run = await _run(settings, store, follow_up=True)
    assert run.status == "complete"
    assert run.checks.gap_analysis.gaps == []
    assert "deep_dive" not in [c["role"] for c in store.calls.values()]


async def test_unresolved_material_gap_keeps_run_partial(settings, pages) -> None:
    from research_loop.agents import gap_agent

    def research(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        question = _prompt(messages)["question"]
        if "material_gap" in _prompt(messages):
            return _output(info, {"question_id": question["id"], "question": question["question"],
                                  "conclusion": "still uncertain", "confidence": 0,
                                  "unresolved": ["No direct contamination assessment found."]})
        url = "https://example.org/verified"
        if len(messages) == 1:
            return ModelResponse(parts=[ToolCallPart("fetch", {"url": url})])
        return _output(info, {"question_id": question["id"], "question": question["question"],
                              "conclusion": "found", "confidence": 0.8,
                              "claims": [{"id": "c1", "statement": "Verified was screened", "confidence": 0.8,
                                          "evidence": [{"source": {"url": url, "title": "Page"},
                                                        "excerpt": "summary", "confidence": 0.8}]}]})

    gap = FunctionModel(lambda messages, info: _output(info, {"gaps": [
        {"question_id": "q1", "follow_up_question": "Is there direct evidence of contamination?",
         "reason": "Contamination would change the trust assessment."}]}))
    with gap_agent.override(model=gap):
        run = await _run(settings, plan=planner(QUESTIONS[:1]), research=FunctionModel(research), follow_up=True)
    assert not run.checks.not_established
    assert run.report is not None and run.status == "partial"
    assert run.checks.follow_up_unresolved
    assert "The follow-up did not fully resolve this gap." in render_markdown(run.to_record())


async def test_undispatched_budget_refusal_is_not_an_unpriced_call(settings) -> None:
    from pydantic_ai.usage import RunUsage

    from research_loop.scout import _Run

    runner = _Run("Q?", settings, MemoryStore(), [], [], None, None, budget=StudyBudget(Decimal("0.01")))
    # PydanticAI counts the refused request, so the usage has one request and no tokens.
    runner._spend(RunUsage(requests=1), refused=True)
    assert not runner.unpriced and runner.cost == 0
    runner._spend(RunUsage(requests=1))
    assert runner.unpriced


async def test_guarded_scout_call_refuses_before_any_model_dispatch(settings, monkeypatch) -> None:
    from pydantic_ai import UsageLimits

    from research_loop.agents import PlanLimits
    from research_loop.rate_limit import ScoutRateLimitModel
    from research_loop.scout import _Run
    from research_loop.study_budget import StudyBudgetModel, StudyBudgetRefusal

    dispatched = []

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        dispatched.append(messages)
        return _output(info, {"questions": [{"id": "q1", "question": "Q?"}]})

    monkeypatch.setattr("research_loop.scout.build_model", lambda *_args, **_kwargs: FunctionModel(respond))
    budget = StudyBudget(Decimal("0.0001"))
    store = MemoryStore()
    runner = _Run("Q?", settings, store, [], [], None, None, budget=budget)
    model = runner._model("scout")
    assert isinstance(model, ScoutRateLimitModel)
    assert isinstance(model.wrapped, StudyBudgetModel) and model.wrapped.budget is budget
    with pytest.raises(StudyBudgetRefusal):
        await runner._call(role="planner", agent=planner_agent, prompt='{"question":"Q?"}',
                           deps=PlanLimits(1), limits=UsageLimits(request_limit=2, cost_limit=Decimal(1)))
    assert not dispatched and budget.reserved_usd == 0 and not runner.unpriced
    call = next(iter(store.calls.values()))
    assert call["role"] == "planner" and call["error"]["type"] == "StudyBudgetRefusal"

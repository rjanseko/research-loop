"""The Scout workflow end to end, offline: scripted models, pages served locally, runs kept in memory."""
from __future__ import annotations

import asyncio
import hashlib
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
from research_loop.scout import (
    RESCOUT_VERSION,
    SYNTHESIS_VERSION,
    ConfigError,
    SourceRunError,
    StudyLabels,
    rescout_stored,
    scout,
    synthesize_stored,
)
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
    import logfire
    from pydantic_ai import Agent

    store = MemoryStore()
    logfire.instrument_pydantic_ai()
    try:
        run = await _run(settings, store)
    finally:
        Agent.instrument_all(False)
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

    # Each agent run's span is named for its role and carries the run ID and what it worked on.
    spans = [s for s in capfire.exporter.exported_spans_as_dict() if s["name"].startswith("invoke_agent")]
    assert sorted(s["name"] for s in spans) == ["invoke_agent planner", "invoke_agent scout", "invoke_agent scout",
                                                "invoke_agent synthesizer"]
    assert {s["attributes"]["run_id"] for s in spans} == {str(run.run_id)}
    scouted = [json.loads(s["attributes"]["metadata"]) for s in spans if s["name"] == "invoke_agent scout"]
    assert sorted(m["question_id"] for m in scouted) == ["q1", "q2"]
    assert {(m["role"], m["run_id"], m["depth"]) for m in scouted} == {("scout", str(run.run_id), "standard")}

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
    with pytest.raises(SourceRunError, match="no claims"):
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


async def test_fixed_plan_research_reuses_the_plan_without_planning_or_synthesis(settings, pages) -> None:
    store = MemoryStore()
    original = await _run(settings, store)
    source = store.runs[original.run_id]
    case = {"id": "drb2-test", "rubric_version": "1", "sha256": "0" * 64, "dataset_revision": ""}
    source["config"]["case"] = case
    before = set(store.calls)
    with scout_agent.override(model=researcher()):
        again = await rescout_stored(source, settings=settings, store=store, study=StudyLabels("scouts", "flash"))
    assert again.status == "complete" and again.report is None and reasons(again) == []
    assert again.plan == original.plan and sorted(again.ledger.claim_ids()) == ["q1/c1", "q2/c1"]
    saved = store.runs[again.run_id]
    assert saved["mode"] == "fixed-plan" and saved["workflow_version"] == RESCOUT_VERSION
    assert saved["parent_run_id"] == original.run_id and saved["study_id"] == "scouts"
    assert saved["config"]["fixed_plan"]["source_run_id"] == str(original.run_id)
    assert len(saved["config"]["fixed_plan"]["plan_sha256"]) == 64 and saved["config"]["case"] == case
    # A standard plan hashes as plans did before depths existed, so rescouts pair across versions.
    questions = json.dumps({"questions": [{"id": q.id, "question": q.question,
                                           "requires_primary_sources": q.requires_primary_sources}
                                          for q in original.plan.questions]}, ensure_ascii=False, sort_keys=True)
    assert saved["config"]["fixed_plan"]["plan_sha256"] == hashlib.sha256(questions.encode()).hexdigest()
    assert saved["config"]["depth"] == "standard"
    calls = [store.calls[i] for i in set(store.calls) - before]
    assert sorted(call["role"] for call in calls) == ["scout", "scout"]
    # The new ledger can then be synthesized, and the report stays gradable against the frozen case.
    with synthesizer_agent.override(model=writer()):
        written = await synthesize_stored(saved, settings=settings, store=store)
    assert written.status == "complete" and store.runs[written.run_id]["config"]["case"] == case
    # A fixed-ledger synthesis reports its own version, and pairs by the same input digest as before.
    stored = store.runs[written.run_id]
    assert written.workflow_version == written.to_record()["workflow_version"] == SYNTHESIS_VERSION
    assert stored["workflow_version"] == SYNTHESIS_VERSION and stored["mode"] == "fixed-ledger"
    ledger_sha = hashlib.sha256(json.dumps(again.ledger.to_json(), ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    assert stored["config"]["fixed_ledger"]["ledger_sha256"] == ledger_sha
    assert stored["input_hash"] == hashlib.sha256((again.question + "\n" + ledger_sha).encode()).hexdigest()


async def test_fixed_plan_research_lists_an_unanswered_question(settings, pages) -> None:
    store = MemoryStore()
    original = await _run(settings, store)

    def half(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        question = _prompt(messages)["question"]
        if question["id"] == "q1":
            return researcher().function(messages, info)
        return _output(info, {"question_id": "q2", "question": question["question"], "conclusion": "nothing found",
                              "confidence": 0.1, "claims": []})

    with scout_agent.override(model=FunctionModel(half)):
        again = await rescout_stored(store.runs[original.run_id], settings=settings, store=store)
    # The research ran to its end, so the run is complete; the unanswered question is listed for review.
    assert again.status == "complete" and again.checks.answer_support is None
    assert reasons(again) == ["1 of 2 research questions returned no evidence"]


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
    assert run.config["limits"]["followup_cost_usd"] == 2.0


async def test_follow_up_skips_deep_dive_when_no_material_gap(settings, pages) -> None:
    from research_loop.agents import gap_agent

    store = MemoryStore()
    gap = FunctionModel(lambda messages, info: _output(info, {"gaps": []}))
    with gap_agent.override(model=gap):
        run = await _run(settings, store, follow_up=True)
    assert run.status == "complete"
    assert run.checks.gap_analysis.gaps == []
    assert "deep_dive" not in [c["role"] for c in store.calls.values()]


async def test_an_unresolved_material_gap_weakens_the_answer(settings, pages) -> None:
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
    assert run.report is not None and run.status == "complete" and run.checks.answer_support == "weak"
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
    assert model.pacer is not None and model.pacer.tokens_per_minute == 200_000  # gpt-6-luna's configured limit
    with pytest.raises(StudyBudgetRefusal):
        await runner._call(role="planner", agent=planner_agent, prompt='{"question":"Q?"}',
                           deps=PlanLimits(1), limits=UsageLimits(request_limit=2, cost_limit=Decimal(1)))
    assert not dispatched and budget.reserved_usd == 0 and not runner.unpriced
    call = next(iter(store.calls.values()))
    assert call["role"] == "planner" and call["error"]["type"] == "StudyBudgetRefusal"


async def test_a_network_error_in_one_scout_leaves_the_rest_of_the_run(settings, pages) -> None:
    import ssl

    def flaky(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if _prompt(messages)["question"]["id"] == "q2":
            raise ssl.SSLError("[SSL: SSLV3_ALERT_BAD_RECORD_MAC] sslv3 alert bad record mac")
        return researcher().function(messages, info)

    store = MemoryStore()
    run = await _run(settings, store, research=FunctionModel(flaky))
    assert run.status == "partial" and run.report is not None
    assert sorted(run.ledger.claim_ids()) == ["q1/c1"]
    cut = next(result for result in run.ledger.all() if result.question_id == "q2")
    assert cut.cut_off == "network error SSLError"
    assert reasons(run) == ["1 of 2 research questions returned no evidence"]


async def test_an_unexpected_error_in_one_scout_leaves_the_rest_of_the_run(settings, pages) -> None:
    def broken(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if _prompt(messages)["question"]["id"] == "q2":
            "a\ud835".encode()
        return researcher().function(messages, info)

    run = await _run(settings, MemoryStore(), research=FunctionModel(broken))
    assert run.status == "partial" and run.report is not None
    assert sorted(run.ledger.claim_ids()) == ["q1/c1"]
    cut = next(result for result in run.ledger.all() if result.question_id == "q2")
    assert cut.cut_off == "unexpected error UnicodeEncodeError"
    assert "research on q2 stopped on an unexpected error (UnicodeEncodeError); this is a bug" in run.notes


async def test_follow_up_researches_several_gaps_in_parallel(settings, pages) -> None:
    from research_loop.agents import gap_agent

    store = MemoryStore()
    running: set[str] = set()
    overlapped: list[bool] = []

    async def research(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        prompt = _prompt(messages)
        question = prompt["question"]
        topic = question["question"]
        if "material_gap" in prompt and len(messages) == 1:
            # Both deep dives are in flight together before either returns.
            running.add(topic)
            await asyncio.sleep(0.05)
            overlapped.append(len(running) == 2)
        url = "https://example.org/verified"
        if len(messages) == 1:
            return ModelResponse(parts=[ToolCallPart("fetch", {"url": url})])
        running.discard(topic)
        return _output(info, {"question_id": question["id"], "question": topic, "conclusion": "found",
                              "confidence": 0.8,
                              "claims": [{"id": "c1", "statement": f"Finding for {topic}", "confidence": 0.8,
                                          "evidence": [{"source": {"url": url, "title": "Page"},
                                                        "excerpt": "summary", "confidence": 0.8}]}]})

    gaps = [{"question_id": "q2", "follow_up_question": f"Which {kind} databases were used?", "reason": "Missing."}
            for kind in ("experimental", "alloy", "electrolyte", "polymer")]

    def gap(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        prompt = _prompt(messages)
        assert prompt["max_gaps"] == 3
        # Four gaps are refused with a retry; two are accepted.
        return _output(info, {"gaps": gaps if len(messages) == 1 else gaps[:2]})

    with gap_agent.override(model=FunctionModel(gap)):
        run = await _run(settings, store, research=FunctionModel(research), follow_up=True)
    assert [g.follow_up_question for g in run.checks.gap_analysis.gaps] == [
        "Which experimental databases were used?", "Which alloy databases were used?"]
    assert [c["role"] for c in store.calls.values()].count("deep_dive") == 2
    assert overlapped and all(overlapped)
    deep = [claim.statement for result in run.ledger.all() if result.question.startswith("Which")
            for claim in result.claims]
    assert deep == ["Finding for Which experimental databases were used?", "Finding for Which alloy databases were used?"]
    assert sorted(run.ledger.claim_ids()) == ["q1/c1", "q2/c1", "q2/c1~2", "q2/c1~3"]


async def test_the_plan_sets_the_depth_and_its_limits(settings, pages) -> None:
    from research_loop.agents import gap_agent
    from research_loop.scout import FOLLOWUP_VERSION, WORKFLOW_VERSION

    seen: list[dict[str, Any]] = []

    def plan_at(depth: str):
        def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            seen.append(_prompt(messages))
            return _output(info, {"questions": QUESTIONS, "depth": depth})
        return FunctionModel(respond)

    store = MemoryStore()
    quick = await _run(settings, store, plan=plan_at("quick"))
    assert seen[0]["max_questions"] == {"quick": 2, "standard": 4, "deep": 8} and "depth" not in seen[0]
    assert quick.plan.depth == "quick" and quick.workflow_version == WORKFLOW_VERSION
    saved = store.runs[quick.run_id]
    assert saved["config"]["depth"] == "quick" and saved["config"]["limits"]["cost_usd"] == 0.30
    assert saved["config"]["follow_up"] is False and not quick.checks.gap_analysis

    # A deep plan turns the gap follow-up on, and the stored run says so.
    with gap_agent.override(model=FunctionModel(lambda messages, info: _output(info, {"gaps": []}))):
        deep = await _run(settings, store, plan=plan_at("deep"))
    assert deep.workflow_version == FOLLOWUP_VERSION and deep.checks.gap_analysis is not None
    saved = store.runs[deep.run_id]
    assert saved["workflow_version"] == FOLLOWUP_VERSION
    assert saved["config"]["depth"] == "deep" and saved["config"]["follow_up"] is True

    # A depth the user chose wins over the planner's.
    seen.clear()
    with gap_agent.override(model=FunctionModel(lambda messages, info: _output(info, {"gaps": []}))):
        chosen = await _run(settings, store, plan=plan_at("quick"), depth="deep")
    assert seen[0]["depth"] == "deep" and seen[0]["max_questions"] == {"deep": 8}
    assert chosen.plan.depth == "deep" and store.runs[chosen.run_id]["workflow_version"] == FOLLOWUP_VERSION


async def test_a_quick_plan_with_too_many_questions_is_retried(settings, pages) -> None:
    three = [*QUESTIONS, {"id": "c", "question": "Who maintains SWE-bench?"}]

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return _output(info, {"questions": three if len(messages) == 1 else QUESTIONS, "depth": "quick"})

    run = await _run(settings, plan=FunctionModel(respond))
    assert run.plan.depth == "quick" and len(run.plan.questions) == 2


async def test_span_metadata_never_reaches_the_models(settings, pages) -> None:
    sent: list[str] = []

    def recording(inner: FunctionModel) -> FunctionModel:
        def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            sent.append(repr(messages) + repr(info.model_request_parameters))
            return inner.function(messages, info)

        async def stream(messages: list[ModelMessage], info: AgentInfo):
            sent.append(repr(messages) + repr(info.model_request_parameters))
            async for chunk in inner.stream_function(messages, info):
                yield chunk

        return FunctionModel(stream_function=stream) if inner.function is None else FunctionModel(respond)

    run = await _run(settings, plan=recording(planner()), research=recording(researcher()), write=recording(writer()))
    assert run.status == "complete" and sent
    assert not any(str(run.run_id) in text or "question_id': 'q" in text or '"depth"' in text for text in sent)


def test_misattributed_quotes_are_listed_for_review() -> None:
    from research_loop.evidence import EvidenceLedger
    from research_loop.schemas import (
        Claim,
        Evidence,
        ResearchPlan,
        ResearchQuestion,
        ResearchResult,
        SourceRef,
    )
    from research_loop.scout import _checks

    ledger = EvidenceLedger()
    ledger.add(ResearchResult(question_id="q1", question="Q?", conclusion="c", confidence=0.5, claims=[
        Claim(id="c1", statement="s", confidence=0.5, evidence=[
            Evidence(source=SourceRef(url="https://a.example", title="A"), excerpt="e", quote="words", confidence=0.5,
                     quote_check="misattributed", quote_found_in="b.example", source_access="full_text")])]))
    checks = _checks(ResearchPlan(questions=[ResearchQuestion(id="q1", question="Q?")]), ledger, None, False,
                     synthesized=False)
    assert checks.quotes_misattributed == 1
    assert "1 of 1 quotes appear only in another source than the one cited" in checks.review_reasons


async def test_run_status_and_answer_support_are_separate(settings, pages) -> None:
    # Every question ran and the report was written, but one statement cites no evidence.
    def bare(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        research = _prompt(messages)["research"]
        claims = [claim for result in research["research"] for claim in result.get("claims", [])]
        statements = [{"statement": c["statement"], "claim_ids": [c["id"]]} for c in claims]
        return _output(info, {"title": "T", "executive_summary": "S", "answer": "Mostly trustworthy.",
                              "claims": [*statements, {"statement": "An uncited finding", "claim_ids": []}],
                              "caveats": []})

    run = await _run(settings, write=streamed(bare))
    assert run.status == "complete" and run.checks.answer_support == "unsupported"
    good = await _run(settings)
    assert good.status == "complete" and good.checks.answer_support == "supported"


async def test_coverage_items_run_from_the_plan_through_research_to_the_report(settings, pages) -> None:
    from research_loop.agents import gap_agent

    coverage = [{"id": "k1", "requirement": "Computed-data databases", "kind": "category"},
                {"id": "k2", "requirement": "Alloy property databases", "kind": "category"},
                {"id": "k3", "requirement": "Read 'commonly used' as widely cited in reviews", "kind": "assumption"}]
    questions = [{**QUESTIONS[0], "covers": ["k1"]}, {**QUESTIONS[1], "covers": ["k2"]}]
    plan = FunctionModel(lambda messages, info: _output(info, {"questions": questions, "coverage": coverage}))
    seen: dict[str, list[str]] = {}

    def research(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        prompt = _prompt(messages)
        question = prompt["question"]
        deep = "material_gap" in prompt
        seen["deep" if deep else question["id"]] = [item["id"] for item in prompt.get("coverage", [])]
        url = "https://example.org/verified"
        if len(messages) == 1:
            return ModelResponse(parts=[ToolCallPart("fetch", {"url": url})])
        icsd = [item["id"] for item in prompt.get("coverage", []) if item["requirement"] == "ICSD"]
        covers = icsd if deep else ["k1", "zz"] if question["id"] == "q1" else []
        return _output(info, {"question_id": question["id"], "question": question["question"], "conclusion": "c",
                              "confidence": 0.8, "open_items": [] if deep or question["id"] == "q1" else ["ICSD"],
                              "claims": [{"id": "c1", "statement": f"Finding {'deep' if deep else question['id']}",
                                          "confidence": 0.8, "covers": covers,
                                          "evidence": [{"source": {"url": url, "title": "Page"},
                                                        "excerpt": "summary", "confidence": 0.8}]}]})

    gap_prompts: list[dict] = []

    def gap(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        gap_prompts.append(_prompt(messages))
        return _output(info, {"gaps": [{"question_id": "q2", "follow_up_question": "Is ICSD commonly used?",
                                        "reason": "An open member of the set."}]})

    def write(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        research_view = _prompt(messages)["research"]
        claims = [c for result in research_view["research"] for c in result.get("claims", [])]
        return _output(info, {"title": "T", "executive_summary": "S", "answer": "A",
                              "claims": [{"statement": c["statement"], "claim_ids": [c["id"]]} for c in claims],
                              "caveats": [], "not_established": ["k2"]})

    with gap_agent.override(model=FunctionModel(gap)):
        run = await _run(settings, plan=plan, research=FunctionModel(research), write=streamed(write), follow_up=True)
    # Each scout works toward its question's items; the deep dive toward every item still open.
    icsd = next(state.id for state in run.checks.coverage if state.requirement == "ICSD")
    assert seen["q1"] == ["k1"] and seen["q2"] == ["k2"] and seen["deep"] == ["k2", icsd]
    assert [(i["id"], i["status"]) for i in gap_prompts[0]["coverage"]] == [("k1", "covered"), ("k2", "open"),
                                                                             (icsd, "open")]
    states = {state.id: state for state in run.checks.coverage}
    assert set(states) == {"k1", "k2", icsd}  # the assumption is shown to the synthesizer, not tracked
    assert (states["k1"].status, states["k1"].in_report) == ("covered", "cited")
    assert (states["k2"].status, states["k2"].in_report) == ("open", "not_established")
    assert (states[icsd].origin, states[icsd].requirement, states[icsd].in_report) == ("q2", "ICSD", "cited")
    q1_claim = next(claim for claim in run.ledger.claims() if claim.statement == "Finding q1")
    assert q1_claim.covers == ["k1"]  # the unknown ID zz was dropped
    assert run.status == "complete" and run.checks.answer_support == "weak"
    assert any(reason.startswith("1 of 3 coverage items were not established") for reason in run.checks.review_reasons)
    assert "## Coverage" in render_markdown(run.to_record())


def test_the_planner_must_name_existing_coverage_items() -> None:
    from pydantic_ai import ModelRetry

    from research_loop.agents import PlanLimits, _plan_is_workable
    from research_loop.schemas import ResearchPlan

    class Ctx:
        deps = PlanLimits({"quick": 2, "standard": 4, "deep": 8})

    plan = ResearchPlan.model_validate({"questions": [{"id": "a", "question": "Q?", "covers": ["k9"]}],
                                        "coverage": [{"id": "k1", "requirement": "R"}]})
    with pytest.raises(ModelRetry, match="do not exist: k9"):
        _plan_is_workable(Ctx(), plan)


async def test_a_deep_dive_counts_toward_the_item_its_gap_targets(settings, pages) -> None:
    from research_loop.agents import gap_agent

    plan = FunctionModel(lambda messages, info: _output(info, {"questions": QUESTIONS[:1], "coverage": [
        {"id": "k1", "requirement": "Experimental databases", "kind": "category"}]}))

    def research(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        prompt = _prompt(messages)
        question, deep = prompt["question"], "material_gap" in prompt
        url = "https://example.org/verified"
        if len(messages) == 1:
            return ModelResponse(parts=[ToolCallPart("fetch", {"url": url})])
        claims = [] if not deep else [{"id": "c1", "statement": "CSD is an experimental database", "confidence": 0.8,
                                       "evidence": [{"source": {"url": url, "title": "Page"}, "excerpt": "e",
                                                     "confidence": 0.8}]}]  # no `covers`: the model forgot to tag
        return _output(info, {"question_id": question["id"], "question": question["question"], "conclusion": "c",
                              "confidence": 0.5, "claims": claims, "open_items": [] if deep else ["CSD"]})

    def gap(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        open_item = next(i["id"] for i in _prompt(messages)["coverage"] if i["requirement"] == "CSD")
        return _output(info, {"gaps": [
            {"question_id": "q1", "follow_up_question": "Is CSD used?", "reason": "Open.", "coverage_id": open_item},
            {"question_id": "q1", "follow_up_question": "Anything else?", "reason": "r", "coverage_id": "zz"}]})

    with gap_agent.override(model=FunctionModel(gap)):
        run = await _run(settings, plan=plan, research=FunctionModel(research), follow_up=True)
    states = {state.requirement: state for state in run.checks.coverage}
    assert states["CSD"].status == "covered"  # credited through the gap's coverage_id
    assert run.checks.gap_analysis.gaps[1].coverage_id is None  # an unknown ID is dropped, not retried

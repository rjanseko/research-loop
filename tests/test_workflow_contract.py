"""The public run contract, exercised through real agents, tools, and persistence.

Only model responses are scripted; graph and legacy behavior must both hold. Adapted from
research-graph's workflow, failure-boundary, and prompt tests.
"""
from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from contextlib import ExitStack
from dataclasses import replace
from typing import Any

import httpx
import pytest
from pydantic_ai.exceptions import (
    ModelHTTPError,
    UnexpectedModelBehavior,
    UsageLimitExceeded,
)
from pydantic_ai.messages import (
    ModelResponse,
    RetryPromptPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import FunctionModel

from research_loop import agents
from research_loop.async_orchestrator import (
    JobBudgetExceeded,
    PromptExceedsRetryBudget,
    ResearchConfig,
)
from research_loop.ledger import EvidenceLedger
from research_loop.orchestrator import LegacyResearchLoop, ResearchLoop
from research_loop.policy import ModelPolicy, ModelRoute
from research_loop.repository import InMemoryResearchRepository
from research_loop.schemas import FinalReport, ResearchConstraints, ResearchRole
from research_loop.tools import ResearchToolMode


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_parallel_scouts", 0),  # a zero-slot semaphore would wait forever
        ("max_parallel_deep_dives", 0),
        ("max_deep_dives_per_round", -1),
        ("max_verification_rounds", -1),
        ("min_scout_confidence", 1.5),
        ("max_run_seconds", 0),
    ],
)
def test_research_config_rejects_values_that_cannot_run(field: str, value: float) -> None:
    with pytest.raises(ValueError, match=field):
        ResearchConfig(**{field: value})


def test_research_config_allows_disabling_deep_dives_and_verification_rounds() -> None:
    config = ResearchConfig(max_deep_dives_per_round=0, max_verification_rounds=0)
    assert (config.max_deep_dives_per_round, config.max_verification_rounds) == (0, 0)


class YieldingRepository(InMemoryResearchRepository):
    """Writes yield to the event loop, as database I/O does, so cancellation can interrupt them."""

    async def record_tool_events(self, *args: Any, **kwargs: Any) -> None:
        await asyncio.sleep(0)
        await super().record_tool_events(*args, **kwargs)

    async def finish_task(self, *args: Any, **kwargs: Any) -> None:
        await asyncio.sleep(0)
        await super().finish_task(*args, **kwargs)

    async def finish_job(self, *args: Any, **kwargs: Any) -> None:
        await asyncio.sleep(0)
        await super().finish_job(*args, **kwargs)


class FailingCleanupRepository(InMemoryResearchRepository):
    """The database is down by the time a failure is recorded."""

    async def finish_task(self, task_id: Any, **kwargs: Any) -> None:
        if kwargs["status"] == "failed":
            raise RuntimeError("database unavailable")
        await super().finish_task(task_id, **kwargs)

    async def finish_job(self, job_id: Any, **kwargs: Any) -> None:
        if kwargs["status"] == "failed":
            raise RuntimeError("database unavailable")
        await super().finish_job(job_id, **kwargs)


def gap(question_id: str = "q1", severity: int = 4) -> dict[str, Any]:
    return {"question_id": question_id, "reason": "missing_evidence",
            "followup": f"Independently check {question_id}", "severity": severity}


class Script:
    def __init__(self) -> None:
        self.questions = [{"id": "q1", "question": "What was measured?"}]
        self.gaps: list[dict[str, Any]] = []
        self.verification: dict[str, Any] = {"checks": []}
        self.confidence = 0.9
        self.fail_role: str | None = None
        self.fail_question: str | None = None  # with fail_role: fail only this question's call
        self.hang_role: str | None = None
        self.hang_question: str | None = None
        self.reached = asyncio.Event()  # set once a hanging call is in flight
        self.invalid_scout = False
        # Upcoming synthesizer or verifier calls that cite a claim ID missing from the ledger.
        self.invented_refs: dict[str, int] = {}
        self.plans: list[list[dict[str, Any]]] = []  # planner answers to give before `questions`
        self.relabel_results = False  # research results name another question ID and text
        self.cite_attachment: str | None = None  # "listed", or "invented" until a retry asks to fix it
        self.cite_url: str | None = None  # research evidence cites this URL until a retry asks to fix it
        self.fetch_urls: list[str] = []  # the scout calls web_fetch on these before it answers
        self.prompts: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.retries: dict[str, list[str]] = defaultdict(list)  # retry prompts each role received

    def model(self, role: str) -> FunctionModel:
        async def respond(messages, info):
            prompt = next(part.content for message in messages for part in message.parts
                          if isinstance(part, UserPromptPart))
            payload = json.loads(prompt)
            self.prompts[role].append(payload)
            retried = [str(part.content) for part in messages[-1].parts if isinstance(part, RetryPromptPart)]
            self.retries[role] += retried
            invent = self.invented_refs.get(role, 0) > 0
            if invent:
                self.invented_refs[role] -= 1
            question_id = payload.get("question", {}).get("id")
            if role == self.hang_role and self.hang_question in (None, question_id):
                self.reached.set()
                await asyncio.Event().wait()
            if role == self.fail_role and self.fail_question in (None, question_id):
                if self.hang_role:
                    await self.reached.wait()  # fail only once the hanging call is in flight
                raise ModelHTTPError(401, "fixture", {"error": "PRIVATE-PROVIDER-BODY"})
            if role == "planner":
                questions = self.plans.pop(0) if self.plans else self.questions
                output = {"objective": payload["objective"], "questions": questions}
            elif role == "scout" and self.fetch_urls and not any(
                isinstance(part, ToolReturnPart) for message in messages for part in message.parts
            ):
                return ModelResponse(parts=[ToolCallPart("web_fetch", {"url": url}) for url in self.fetch_urls])
            elif role in {"scout", "deep_dive"}:
                question = payload["question"]
                evidence = []
                if self.cite_url:
                    url = "https://example.org/allowed" if retried else self.cite_url
                    evidence.append({"source": {"url": url, "title": "Source"},
                                     "excerpt": "The measurement is approximate.", "confidence": 0.9})
                if self.cite_attachment:
                    listed = payload["constraints"]["attachments"][0]["attachment_id"]
                    attachment_id = "att-9-invented" if self.cite_attachment == "invented" and not retried else listed
                    evidence.append({"source": {"attachment_id": attachment_id, "locator": "document",
                                                "title": "notes.txt", "source_type": "attachment"},
                                     "excerpt": "The measurement is approximate.", "confidence": 0.9})
                output = {
                    "question_id": question["id"] + ("-relabelled" if self.relabel_results else ""),
                    "question": question["question"] + (" (paraphrased)" if self.relabel_results else ""),
                    "conclusion": f"Evidence from {role}",
                    "confidence": 2 if self.invalid_scout and role == "scout" else self.confidence,
                    "claims": [{"id": "c1", "statement": "The measurement is approximate.",
                                "confidence": 0.9, "evidence": evidence}],
                }
            elif role == "gap_analyst":
                gaps = self.gaps
                if retried:  # drop what the retry named: gaps for questions outside the plan
                    planned = {question["id"] for question in payload["plan"]["questions"]}
                    gaps = [item for item in gaps if item["question_id"] in planned]
                output = {"gaps": gaps}
            elif role == "synthesizer":
                refs = [claim["id"] for result in payload["evidence"] for claim in result.get("claims", [])]
                refs += ["q9/c9"] if invent else []
                output = {"answer": "The measurement is approximate.",
                          "claims": [{"statement": "The measurement is approximate.", "claim_ids": refs}]}
            elif role == "verifier":
                output = self.verification
                if retried and output.get("followups"):  # drop follow-ups for questions outside the ledger
                    known = {result["question_id"] for result in payload["evidence"]}
                    output = {**output, "followups": [item for item in output["followups"]
                                                      if item["question_id"] in known]}
                if invent:
                    output = {**output, "checks": [*output.get("checks", []), {
                        "statement": "Invented", "claim_ids": ["q9/c9"], "supported": False,
                        "severity": "major", "explanation": "cites a claim the ledger does not have"}]}
            else:
                raise AssertionError(role)
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, output)])

        return FunctionModel(respond)


@pytest.fixture(params=[ResearchLoop, LegacyResearchLoop], ids=["graph", "legacy"])
def workflow(request):
    route = ModelRoute("test", 5, 5, 50_000)
    loop = request.param(
        ModelPolicy("workflow-contract", {role: route for role in ResearchRole}),
        ResearchConfig(tool_mode=ResearchToolMode.NORMALIZED, scholarly_tools=False,
                       scholarly_cache_mode="off"),
        repository=InMemoryResearchRepository(),
    )
    script = Script()
    with ExitStack() as stack:
        for role, agent in (
            ("planner", agents.planner_agent), ("scout", agents.scout_agent),
            ("gap_analyst", agents.gap_agent), ("deep_dive", agents.deep_dive_agent),
            ("synthesizer", agents.synthesizer_agent), ("verifier", agents.verifier_agent),
        ):
            stack.enter_context(agent.override(model=script.model(role)))
        yield loop, script


async def run(loop, **kwargs):
    # Empty fan-outs and round-limit regressions should fail instead of hanging.
    return await asyncio.wait_for(loop.run("Assess the measurement", **kwargs), timeout=15)


@pytest.mark.asyncio
async def test_finishing_prompt_stops_before_the_call_when_a_retry_cannot_fit(workflow):
    loop, script = workflow
    loop.policy.routes[ResearchRole.SYNTHESIZER] = ModelRoute("test", 5, 5, 1_000)
    with pytest.raises(PromptExceedsRetryBudget, match="synthesizer"):
        await run(loop)

    assert "synthesizer" not in script.prompts
    assert "verifier" not in script.prompts
    assert script.prompts["scout"]
    (failed,) = [task for task in loop.repository.tasks.values() if task["status"] == "failed"]
    assert failed["role"] is ResearchRole.SYNTHESIZER
    assert failed["error"] == {"type": "PromptExceedsRetryBudget"}
    assert failed["usage"] is None
    job = next(iter(loop.repository.jobs.values()))
    assert job["status"] == "failed"
    assert job["error"] == {"type": "PromptExceedsRetryBudget"}
    assert job["evidence_ledger"]


@pytest.mark.asyncio
async def test_happy_path_persists_report_and_releases_job_resources(workflow):
    loop, script = workflow
    outcome = await run(loop)

    assert list(script.prompts) == ["planner", "scout", "gap_analyst", "synthesizer", "verifier"]
    job = loop.repository.jobs[outcome.job_id]
    assert job["status"] == "succeeded"
    assert job["final_report"] == outcome.report.model_dump(mode="json")
    assert job["verification"] == outcome.verification.model_dump(mode="json")
    assert outcome.report.claim_ids_used == ["q1/c1"]
    assert all(task["status"] == "succeeded" for task in loop.repository.tasks.values())
    assert loop._job_spend == loop._fetch_memos == loop._source_policies == {}
    # The run succeeded, but the scripted verifier checked nothing, so the report is unassessed.
    assert outcome.review_reasons == ["the verifier checked none of the report's statements"]
    assert job["review_reasons"] == outcome.review_reasons
    # The stored ledger alone resolves every claim the stored report and verification cite.
    stored = EvidenceLedger.from_json(job["evidence_ledger"])
    cited = set(FinalReport.model_validate(job["final_report"]).claim_ids_used)
    cited |= {claim_id for check in outcome.verification.checks for claim_id in check.claim_ids}
    assert cited and cited <= stored.claim_ids() == outcome.ledger.claim_ids()


@pytest.mark.asyncio
@pytest.mark.parametrize("refused", [3, 2])
async def test_run_whose_fetches_mostly_reached_no_source_needs_review(workflow, monkeypatch, refused):
    from research_loop import web

    loop, script = workflow
    script.fetch_urls = [f"https://site{index}.example/page" for index in range(4)]

    async def download(self, url: str) -> dict[str, Any]:
        if int(url[len("https://site")]) < refused:
            raise httpx.ProxyError("CONNECT refused")
        return {"text": "The measurement is approximate.", "extraction": "trafilatura", "content_sha256": "0"}

    async def public(url: str) -> bool:
        return True

    monkeypatch.setattr(web, "public_url", public)
    monkeypatch.setattr(web.WebAcquisition, "_extract", download)
    outcome = await run(loop)

    unreached = "3 of 4 web and scholarly tool calls reached no source (ProxyError)"
    assert (unreached in outcome.review_reasons) is (refused == 3)
    assert loop.repository.jobs[outcome.job_id]["review_reasons"] == outcome.review_reasons
    assert loop._source_reach == {}


@pytest.mark.asyncio
@pytest.mark.filterwarnings("ignore:A `cost_limit` is set but cannot be enforced")  # scripted models are unpriced
async def test_planner_sees_the_budget_only_when_the_job_has_a_cost_cap(workflow):
    loop, script = workflow
    outcome = await run(loop)
    assert "budget" not in script.prompts["planner"][0]  # uncapped policies send the prompt as before
    assert [row["id"] for row in outcome.sources] == []  # the scripted evidence cites no sources

    for role in (ResearchRole.SCOUT, ResearchRole.DEEP_DIVE):
        loop.policy.routes[role] = replace(loop.policy.routes[role], cost_limit=0.8 if role is ResearchRole.SCOUT else 1.25)
    loop.policy.job_cost_limit, loop.policy.job_reserve_usd = 4.0, 1.5
    # Scripted models have no prices, so a capped job refuses the call after the planner's.
    with pytest.raises(JobBudgetExceeded, match="no pricing data"):
        await run(loop)
    assert script.prompts["planner"][1]["budget"] == {
        "total_usd": 4.0, "reserved_for_synthesis_and_verification_usd": 1.5, "research_usd": 2.5,
        "cost_cap_per_call_usd": {"scout": 0.8, "deep_dive": 1.25},
    }


@pytest.mark.asyncio
async def test_capture_stores_every_task_transcript_only_when_the_repository_opts_in(workflow, monkeypatch):
    from research_loop import web

    loop, script = workflow
    script.fetch_urls = ["https://site0.example/page"]

    async def download(self, url: str) -> dict[str, Any]:
        return {"text": "The measurement is approximate.", "extraction": "trafilatura", "content_sha256": "0"}

    async def public(url: str) -> bool:
        return True

    monkeypatch.setattr(web, "public_url", public)
    monkeypatch.setattr(web.WebAcquisition, "_extract", download)
    await run(loop)
    assert loop.repository.task_messages == {}  # off by default

    loop.repository = InMemoryResearchRepository(capture_transcripts=True)
    await run(loop)
    transcripts = loop.repository.task_messages
    assert set(transcripts) == set(loop.repository.tasks)
    (scout_id,) = [task_id for task_id, task in loop.repository.tasks.items() if task["role"] is ResearchRole.SCOUT]
    parts = [part for message in transcripts[scout_id]["messages"] for part in message["parts"]]
    # The real fetch argument and fetched text, which tool telemetry keeps only as hashes.
    assert any(part.get("args") == {"url": "https://site0.example/page"} for part in parts)
    assert any("The measurement is approximate." in json.dumps(part.get("content")) for part in parts
               if part["part_kind"] == "tool-return")
    assert all(entry["truncated_values"] == 0 for entry in transcripts.values())


@pytest.mark.asyncio
async def test_capture_keeps_the_transcript_of_a_failed_task(workflow):
    loop, script = workflow
    loop.repository = InMemoryResearchRepository(capture_transcripts=True)
    script.fail_role = "scout"
    with pytest.raises(ModelHTTPError):
        await run(loop)

    (failed_id,) = [task_id for task_id, task in loop.repository.tasks.items() if task["status"] == "failed"]
    messages = loop.repository.task_messages[failed_id]["messages"]
    assert messages[0]["parts"][-1]["part_kind"] == "user-prompt"  # the request the provider refused


@pytest.mark.asyncio
@pytest.mark.parametrize("rounds", [0, 1, 2])
async def test_repeated_verifier_requests_stop_at_round_limit_and_keep_findings(workflow, rounds):
    loop, script = workflow
    loop.config = replace(loop.config, max_verification_rounds=rounds)
    script.verification = {
        "checks": [{"statement": "The measurement is approximate.", "claim_ids": ["q1/c1"],
                    "supported": False, "severity": "major", "explanation": "Independent check missing"}],
        "needs_research": True, "followups": [gap()],
    }
    outcome = await run(loop)

    assert len(script.prompts["verifier"]) == len(script.prompts["synthesizer"]) == rounds + 1
    tasks = [task for task in loop.repository.tasks.values() if task["role"] is ResearchRole.DEEP_DIVE]
    assert [task["attempt"] for task in tasks] == list(range(1, rounds + 1))
    assert len(outcome.ledger.for_question("q1")) == rounds + 1
    assert len(outcome.ledger.claim_ids()) == rounds + 1
    assert outcome.report.claim_ids_used == sorted(outcome.ledger.claim_ids())
    assert outcome.verification.model_dump(mode="json") == script.verification
    assert loop.repository.jobs[outcome.job_id]["verification"] == script.verification


@pytest.mark.asyncio
@pytest.mark.parametrize("needs_research,followups", [(False, [gap()]), (True, [])])
async def test_verifier_requires_both_request_and_followups_to_research(workflow, needs_research, followups):
    loop, script = workflow
    script.verification = {"needs_research": needs_research, "followups": followups}
    outcome = await run(loop)
    assert len(script.prompts["verifier"]) == 1
    assert script.prompts["deep_dive"] == []
    assert len(outcome.ledger.all()) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["initial", "verification"])
@pytest.mark.parametrize("limit", [1, 3])
async def test_gap_selection_filters_unknowns_deduplicates_and_caps_by_severity(workflow, stage, limit):
    loop, script = workflow
    loop.config = replace(loop.config, max_deep_dives_per_round=limit, max_verification_rounds=1)
    script.questions.append({"id": "q2", "question": "How reliable is it?"})
    gaps = [gap("absent", 5), gap("q1", 2), gap("q2", 3), gap("q1", 4)]
    if stage == "initial":
        script.gaps = gaps
    else:
        script.verification = {"needs_research": True, "followups": gaps}
    outcome = await run(loop)

    # Each call naming a question outside the plan gets a retry, and the model drops that gap.
    retries = script.retries["gap_analyst" if stage == "initial" else "verifier"]
    assert retries and all("absent" in retry for retry in retries)
    expected = [gap("q1", 4), gap("q2", 3)][:limit]
    assert [prompt["gap"] for prompt in script.prompts["deep_dive"]] == expected
    assert len(outcome.ledger.for_question("q1")) == 2
    assert len(outcome.ledger.for_question("q2")) == (1 if limit == 1 else 2)
    assert outcome.ledger.for_question("absent") == []


@pytest.mark.asyncio
@pytest.mark.parametrize("disable_dives", [False, True])
async def test_unactionable_verifier_followups_terminate_without_dispatch(workflow, disable_dives):
    loop, script = workflow
    loop.config = replace(loop.config, max_verification_rounds=1,
                          max_deep_dives_per_round=0 if disable_dives else 4)
    script.verification = {"needs_research": True, "followups": [gap() if disable_dives else gap("absent")]}
    outcome = await run(loop)
    assert script.prompts["deep_dive"] == []
    assert len(outcome.ledger.all()) == 1
    assert outcome.verification.needs_research
    if not disable_dives:
        # The follow-up for a question outside the plan is retried away, so no empty round follows.
        assert len(script.retries["verifier"]) == 1
        assert len(script.prompts["synthesizer"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("confidence,expected_dives", [(0.69, 1), (0.70, 0)])
async def test_low_confidence_triggers_research_even_when_gap_analyst_finds_none(workflow, confidence, expected_dives):
    loop, script = workflow
    script.confidence = confidence
    outcome = await run(loop)
    assert len(script.prompts["deep_dive"]) == expected_dives
    assert len(outcome.ledger.for_question("q1")) == 1 + expected_dives
    if expected_dives:
        assert script.prompts["deep_dive"][0]["gap"]["reason"] == "low_confidence"


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["planner", "scout", "gap_analyst", "deep_dive", "synthesizer", "verifier"])
async def test_provider_failure_records_failed_task_and_job_without_response_body(workflow, role):
    loop, script = workflow
    script.fail_role = role
    script.gaps = [gap()]  # ensure the deep-dive failure case is reachable
    with pytest.raises(ModelHTTPError):
        await run(loop)

    (job,) = loop.repository.jobs.values()
    failed = [task for task in loop.repository.tasks.values() if task["status"] == "failed"]
    assert len(failed) == 1
    assert failed[0]["role"].value == role
    assert job["status"] == "failed"
    assert job["final_report"] is job["verification"] is None
    assert job["error"] == failed[0]["error"] == {"type": "ModelHTTPError", "status_code": 401}
    # Evidence gathered before the failure is kept; a run that failed before any is stored without one.
    stored = job["evidence_ledger"]
    assert (stored is None) == (role in {"planner", "scout"})
    if stored:
        assert EvidenceLedger.from_json(stored).for_question("q1")
    assert "PRIVATE-PROVIDER-BODY" not in str(loop.repository.tasks)
    assert all(task["status"] in {"succeeded", "failed"} for task in loop.repository.tasks.values())
    assert loop._job_spend == loop._fetch_memos == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("salvage", [False, True])
async def test_scout_request_exhaustion_obeys_salvage_policy_through_whole_run(workflow, salvage):
    loop, script = workflow
    loop.config = replace(loop.config, salvage_exhausted_research=salvage, max_deep_dives_per_round=0)
    loop.policy.routes[ResearchRole.SCOUT] = ModelRoute("test", 1, 5, 50_000)
    script.invalid_scout = True  # real validation retry spends the scout's request allocation
    if salvage:
        outcome = await run(loop)
        (result,) = outcome.ledger.all()
        assert result.claims == [] and result.confidence == 0
        assert result.unresolved_questions == [script.questions[0]["question"]]
        assert len(script.prompts["synthesizer"]) == len(script.prompts["verifier"]) == 1
        assert loop.repository.jobs[outcome.job_id]["status"] == "succeeded"
    else:
        with pytest.raises(UsageLimitExceeded):
            await run(loop)
        assert script.prompts["synthesizer"] == script.prompts["verifier"] == []
        assert next(iter(loop.repository.jobs.values()))["status"] == "failed"
    (scout,) = [task for task in loop.repository.tasks.values() if task["role"] is ResearchRole.SCOUT]
    assert scout["status"] == "failed"
    assert scout["usage"]["requests"] == 1
    assert scout["error"] == {"type": "UsageLimitExceeded"}
    assert len(script.prompts["scout"]) == 1
    assert loop._job_spend == loop._fetch_memos == {}


@pytest.mark.asyncio
async def test_followup_prompts_keep_constraints_and_evidence_without_prior_task_transcripts(workflow):
    loop, script = workflow
    loop.config = replace(loop.config, max_verification_rounds=2)
    script.verification = {"needs_research": True, "followups": [gap()]}
    script.cite_url = "https://example.org/allowed"
    constraints = ResearchConstraints(blocked_urls=["https://example.org/blocked"], notes=["Use primary evidence"])
    outcome = await run(loop, constraints=constraints)

    def keys(value):
        if isinstance(value, dict):
            return set(value).union(*(keys(item) for item in value.values()))
        if isinstance(value, list):
            return set().union(*(keys(item) for item in value))
        return set()

    for task in loop.repository.tasks.values():
        prompt = json.loads(task["prompt"])
        assert prompt["constraints"]["blocked_urls"] == constraints.blocked_urls
        assert prompt["constraints"]["notes"] == constraints.notes
        assert not ({"messages", "tasks", "usage", "effective_config", "tool_call_id", "part_kind"} & keys(prompt))
        for task_id in loop.repository.tasks:
            assert str(task_id) not in task["prompt"]
    # Only the growing evidence ledger crosses rounds, with stable, distinct claim IDs.
    assert [len(p["evidence"]) for p in script.prompts["synthesizer"]] == [1, 2, 3]
    for synthesis, verification in zip(script.prompts["synthesizer"], script.prompts["verifier"], strict=True):
        assert synthesis["evidence"] == verification["evidence"]
        assert synthesis["sources"] == verification["sources"]
        assert [row["id"] for row in synthesis["sources"]] == ["s1"]
        assert "doi" not in synthesis["sources"][0]
        ids = [c["id"] for result in verification["evidence"] for c in result["claims"]]
        assert verification["report"]["claims"][0]["claim_ids"] == ids
        for result in verification["evidence"]:
            assert "search_queries_used" not in result
            assert "suggested_followups" not in result
            for claim in result["claims"]:
                for item in claim["evidence"]:
                    assert item["source_id"] == "s1"
                    assert "source" not in item
    gap_prompt = script.prompts["gap_analyst"][0]
    assert gap_prompt["sources"] == script.prompts["synthesizer"][0]["sources"]
    assert "source" not in gap_prompt["results"][0]["claims"][0]["evidence"][0]
    assert len(outcome.ledger.claim_ids()) == 3


def _one_followup_round(loop, script) -> None:
    """Low-confidence scouting forces an initial deep dive; one verifier follow-up adds another."""
    loop.config = replace(loop.config, max_verification_rounds=1)
    script.confidence = 0.3
    script.verification = {"needs_research": True, "followups": [gap()]}


@pytest.mark.asyncio
async def test_deep_dives_record_the_task_that_asked_for_them(workflow):
    loop, script = workflow
    _one_followup_round(loop, script)
    await run(loop)
    tasks = list(loop.repository.tasks.values())

    def only(role: ResearchRole, attempt: int = 0) -> dict[str, Any]:
        (task,) = [t for t in tasks if t["role"] is role and t["attempt"] == attempt]
        return task

    first_verifier = min((t for t in tasks if t["role"] is ResearchRole.VERIFIER), key=lambda t: t["started_at"])
    assert only(ResearchRole.DEEP_DIVE, attempt=0)["parent_task_id"] == only(ResearchRole.GAP_ANALYST)["id"]
    assert only(ResearchRole.DEEP_DIVE, attempt=1)["parent_task_id"] == first_verifier["id"]
    assert only(ResearchRole.SCOUT)["parent_task_id"] is None


@pytest.mark.asyncio
async def test_research_agents_in_one_job_share_one_fetch_memo_and_http_clients(workflow, monkeypatch):
    import research_loop.async_orchestrator as orchestrator

    loop, script = workflow
    _one_followup_round(loop, script)
    loop.config = replace(loop.config, scholarly_tools=True)
    memos: list[Any] = []
    fetch_clients: list[Any] = []
    metadata_clients: list[Any] = []

    def recording(cls):
        class Recording(cls):
            def __init__(self, **kwargs: Any) -> None:
                super().__init__(**kwargs)
                memos.append(self.memo)
                fetch_clients.append(getattr(self, "fetch_client", self.client))
                if hasattr(self, "fetch_client"):
                    metadata_clients.append(self.client)
        return Recording

    monkeypatch.setattr(orchestrator, "WebAcquisition", recording(orchestrator.WebAcquisition))
    monkeypatch.setattr(orchestrator, "ScholarClient", recording(orchestrator.ScholarClient))
    await run(loop)
    first_job = list(memos)
    first_fetch, first_metadata = fetch_clients[0], metadata_clients[0]
    memos.clear()
    fetch_clients.clear()
    metadata_clients.clear()
    await run(loop)

    # The scout and both deep dives each build a web fetcher and a scholar client.
    assert len(first_job) == 6
    assert all(memo is first_job[0] for memo in first_job)
    assert all(memo is memos[0] for memo in memos) and memos[0] is not first_job[0]
    assert loop._fetch_memos == {}
    # One download client and one metadata client per job, closed when the job ends.
    assert all(client is fetch_clients[0] for client in fetch_clients) and fetch_clients[0] is not first_fetch
    assert all(client is metadata_clients[0] for client in metadata_clients) and len(metadata_clients) == 3
    assert fetch_clients[0] is not metadata_clients[0]
    assert all(client.is_closed for client in (first_fetch, first_metadata, fetch_clients[0], metadata_clients[0]))
    assert loop._http_clients == {}


def _no_running_records(loop) -> None:
    assert [job["status"] for job in loop.repository.jobs.values()] == ["failed"]
    assert all(task["status"] != "running" for task in loop.repository.tasks.values())
    assert loop._job_spend == loop._fetch_memos == loop._http_clients == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["scout", "synthesizer"])
async def test_cancelled_run_leaves_no_running_records(workflow, role):
    loop, script = workflow
    loop.repository = YieldingRepository()
    script.hang_role = role
    running = asyncio.create_task(run(loop))
    await asyncio.wait_for(script.reached.wait(), timeout=15)
    running.cancel()  # what Ctrl-C does to asyncio.run's main task
    with pytest.raises(asyncio.CancelledError):
        await running

    _no_running_records(loop)
    (job,) = loop.repository.jobs.values()
    (hung,) = [task for task in loop.repository.tasks.values() if task["role"].value == role]
    assert job["error"] == hung["error"] == {"type": "CancelledError"}


@pytest.mark.asyncio
async def test_failed_branch_records_the_siblings_it_cancels(workflow):
    loop, script = workflow
    loop.repository = YieldingRepository()
    script.questions.append({"id": "q2", "question": "How reliable is it?"})
    script.hang_role, script.hang_question = "scout", "q2"
    script.fail_role, script.fail_question = "scout", "q1"
    with pytest.raises(ModelHTTPError):
        await run(loop)

    _no_running_records(loop)
    errors = {task["question_id"]: task["error"] for task in loop.repository.tasks.values()
              if task["role"] is ResearchRole.SCOUT}
    assert errors == {"q1": {"type": "ModelHTTPError", "status_code": 401}, "q2": {"type": "CancelledError"}}


@pytest.mark.asyncio
async def test_failure_survives_a_failed_cleanup_write(workflow):
    loop, script = workflow
    loop.repository = FailingCleanupRepository()
    script.fail_role = "synthesizer"
    with pytest.raises(ModelHTTPError) as raised:
        await run(loop)
    # Both the task and the job write failed; the provider error still surfaces, with notes.
    assert raised.value.__notes__ == ["Recording this failure also failed (RuntimeError)."] * 2
    assert loop._job_spend == loop._fetch_memos == {}


def _cited_claim_ids(outcome) -> set[str]:
    return {*outcome.report.claim_ids_used,
            *(claim_id for check in outcome.verification.checks for claim_id in check.claim_ids)}


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["synthesizer", "verifier"])
async def test_invented_claim_citation_gets_a_retry_that_fixes_it(workflow, role):
    loop, script = workflow
    script.invented_refs[role] = 1
    outcome = await run(loop)

    (retry,) = script.retries[role]
    assert "q9/c9" in retry
    assert _cited_claim_ids(outcome) <= outcome.ledger.claim_ids()
    (task,) = [task for task in loop.repository.tasks.values() if task["role"].value == role]
    assert task["status"] == "succeeded"
    assert task["usage"]["requests"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["synthesizer", "verifier"])
async def test_run_fails_when_citations_stay_invented(workflow, role):
    loop, script = workflow
    script.invented_refs[role] = 99
    with pytest.raises(UnexpectedModelBehavior):
        await run(loop)

    assert len(script.retries[role]) == 1  # one retry, then the run stops
    (job,) = loop.repository.jobs.values()
    (task,) = [task for task in loop.repository.tasks.values() if task["role"].value == role]
    assert job["status"] == task["status"] == "failed"
    assert task["error"] == {"type": "UnexpectedModelBehavior"}


@pytest.mark.asyncio
@pytest.mark.parametrize(("bad_plan", "problem"), [
    ([], "no research questions"),
    ([{"id": "q1", "question": "A"}, {"id": "q1", "question": "B"}], "repeated: q1"),
    ([{"id": f"q{i}", "question": f"Question {i}"} for i in range(11)], "at most 10"),
], ids=["empty", "duplicate-ids", "too-many"])
async def test_unworkable_plan_gets_a_retry(workflow, bad_plan, problem):
    loop, script = workflow
    script.plans = [bad_plan]
    outcome = await run(loop)

    (retry,) = script.retries["planner"]
    assert problem in retry
    assert [question.id for question in outcome.plan.questions] == ["q1"]


@pytest.mark.asyncio
async def test_run_fails_before_research_when_the_plan_stays_unworkable(workflow):
    loop, script = workflow
    script.plans = [[], []]
    with pytest.raises(UnexpectedModelBehavior):
        await run(loop)
    assert script.prompts["scout"] == []


@pytest.mark.asyncio
async def test_research_results_are_filed_under_the_question_asked(workflow):
    loop, script = workflow
    script.relabel_results = True
    script.confidence = 0.3  # a deep dive follows, and is filed the same way
    outcome = await run(loop)

    assert list(outcome.ledger.results) == ["q1"]
    assert [result.question for result in outcome.ledger.for_question("q1")] == ["What was measured?"] * 2
    research = [task for task in loop.repository.tasks.values()
                if task["role"] in (ResearchRole.SCOUT, ResearchRole.DEEP_DIVE)]
    assert {task["output"]["question_id"] for task in research} == {"q1"}  # stored as filed


@pytest.mark.asyncio
@pytest.mark.parametrize("cite", ["listed", "invented"])
async def test_attachment_evidence_must_cite_a_run_attachment(workflow, tmp_path, cite):
    loop, script = workflow
    notes = tmp_path / "notes.txt"
    notes.write_text("The measurement is approximate.", encoding="utf-8")
    script.cite_attachment = cite
    outcome = await run(loop, constraints=ResearchConstraints(attachment_paths=[str(notes)]))

    cited = {item.source.attachment_id for claim in outcome.ledger.claims() for item in claim.evidence}
    assert cited == {outcome.attachments.records[0].attachment_id}
    if cite == "invented":
        (retry,) = script.retries["scout"]
        assert "att-9-invented" in retry
    else:
        assert script.retries["scout"] == []


@pytest.mark.asyncio
async def test_evidence_citing_a_blocked_source_gets_a_retry(workflow):
    loop, script = workflow
    script.cite_url = "https://blocked.example/leaked-report"
    outcome = await run(loop, constraints=ResearchConstraints(blocked_urls=["https://blocked.example"]))

    (retry,) = script.retries["scout"]
    assert "https://blocked.example" in retry
    cited = {str(item.source.url) for claim in outcome.ledger.claims() for item in claim.evidence}
    assert cited == {"https://example.org/allowed"}


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["scout", "synthesizer"])
async def test_run_past_its_deadline_ends_failed(workflow, role):
    loop, script = workflow
    loop.repository = YieldingRepository()
    loop.config = replace(loop.config, max_run_seconds=0.2)
    script.hang_role = role  # the model never answers
    with pytest.raises(TimeoutError):
        await run(loop)

    _no_running_records(loop)
    (job,) = loop.repository.jobs.values()
    (hung,) = [task for task in loop.repository.tasks.values() if task["role"].value == role]
    assert job["error"] == {"type": "TimeoutError"}
    assert hung["error"] == {"type": "CancelledError"}

"""Adapted from research-graph's workflow, failure-boundary and prompt tests.

Exercise this repo's public run contract through real agents and persistence.
Only model responses are scripted; graph and legacy behavior must both hold.
"""
from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from contextlib import ExitStack
from dataclasses import replace
from typing import Any

import pytest
from pydantic_ai import models
from pydantic_ai.exceptions import ModelHTTPError, UsageLimitExceeded
from pydantic_ai.messages import ModelResponse, ToolCallPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel

from research_loop import agents
from research_loop.async_orchestrator import ResearchConfig
from research_loop.orchestrator import LegacyResearchLoop, ResearchLoop
from research_loop.policy import ModelPolicy, ModelRoute
from research_loop.repository import InMemoryResearchRepository
from research_loop.schemas import ResearchConstraints, ResearchRole
from research_loop.tools import ResearchToolMode


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
        self.invalid_scout = False
        self.prompts: dict[str, list[dict[str, Any]]] = defaultdict(list)

    def model(self, role: str) -> FunctionModel:
        async def respond(messages, info):
            prompt = next(part.content for message in messages for part in message.parts
                          if isinstance(part, UserPromptPart))
            payload = json.loads(prompt)
            self.prompts[role].append(payload)
            if role == self.fail_role:
                raise ModelHTTPError(401, "fixture", {"error": "PRIVATE-PROVIDER-BODY"})
            if role == "planner":
                output = {"objective": payload["objective"], "questions": self.questions}
            elif role in {"scout", "deep_dive"}:
                question = payload["question"]
                output = {
                    "question_id": question["id"], "question": question["question"],
                    "conclusion": f"Evidence from {role}",
                    "confidence": 2 if self.invalid_scout and role == "scout" else self.confidence,
                    "claims": [{"id": "c1", "statement": "The measurement is approximate.",
                                "confidence": 0.9, "evidence": []}],
                }
            elif role == "gap_analyst":
                output = {"gaps": self.gaps}
            elif role == "synthesizer":
                refs = [claim["id"] for result in payload["evidence"] for claim in result["claims"]]
                output = {"answer": "The measurement is approximate.",
                          "claims": [{"statement": "The measurement is approximate.", "claim_ids": refs}]}
            elif role == "verifier":
                output = self.verification
            else:
                raise AssertionError(role)
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, output)])

        return FunctionModel(respond)


@pytest.fixture(params=[ResearchLoop, LegacyResearchLoop], ids=["graph", "legacy"])
def workflow(request, monkeypatch):
    # FunctionModel remains usable while accidental provider requests are refused.
    monkeypatch.setattr(models, "ALLOW_MODEL_REQUESTS", False)
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
    assert loop._job_spend == loop._fetch_memos == {}


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
    assert len(script.prompts["verifier"]) <= 2
    assert len(outcome.ledger.all()) == 1
    assert outcome.verification.needs_research


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
        ids = [c["id"] for result in verification["evidence"] for c in result["claims"]]
        assert verification["report"]["claims"][0]["claim_ids"] == ids
    assert len(outcome.ledger.claim_ids()) == 3

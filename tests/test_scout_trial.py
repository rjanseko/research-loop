"""scripts/scout_trial.py: arm policies, the baseline's scout results, and the measures computed without a model."""
from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path

from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

from research_loop.agents import scout_agent
from research_loop.async_orchestrator import AsyncResearchLoop, ResearchConfig
from research_loop.ledger import EvidenceLedger
from research_loop.policy import ModelPolicy, ModelRoute, get_policy
from research_loop.repository import InMemoryResearchRepository
from research_loop.schemas import (
    Claim,
    Evidence,
    ResearchConstraints,
    ResearchPlan,
    ResearchQuestion,
    ResearchResult,
    ResearchRole,
    SourceRef,
)
from research_loop.settings import ResearchSettings

_SPEC = importlib.util.spec_from_file_location("scout_trial", Path(__file__).parents[1] / "scripts" / "scout_trial.py")
scout_trial = importlib.util.module_from_spec(_SPEC)
sys.modules["scout_trial"] = scout_trial
_SPEC.loader.exec_module(scout_trial)


def _result(qid: str, *, url: str = "https://example.org/a", quote_check: str | None = None) -> ResearchResult:
    return ResearchResult(question_id=qid, question="q", conclusion="c", confidence=0.8, claims=[
        Claim(id="c1", statement="s", confidence=0.8, evidence=[Evidence(
            source=SourceRef(url=url, title="t"), excerpt="e", quote="q" if quote_check else None,
            quote_check=quote_check, confidence=0.8)])])


def test_an_arm_changes_only_the_scout_routes() -> None:
    policy = get_policy("value")
    arm = scout_trial._arm_policy(policy, scout_trial.Arm("glm-low", "zai:glm-5.3", "low"))
    assert (arm.routes[ResearchRole.SCOUT].model, arm.routes[ResearchRole.SCOUT].thinking) == ("zai:glm-5.3", "low")
    assert arm.cheap_scout == arm.routes[ResearchRole.SCOUT]
    assert arm.routes[ResearchRole.SCOUT].max_requests == policy.routes[ResearchRole.SCOUT].max_requests
    assert policy.routes[ResearchRole.SCOUT].thinking == "high"  # the study policy is left as it was
    flash = scout_trial._arm_policy(policy, scout_trial.Arm("flash-1", "zai:glm-5.3-flash"))
    assert flash.routes[ResearchRole.SCOUT].thinking == policy.routes[ResearchRole.SCOUT].thinking
    assert scout_trial._synthesis_policy(policy).routes[ResearchRole.SYNTHESIZER].model == "openai:gpt-6-luna"


def test_the_baseline_is_each_questions_first_result_and_merged_runs_keep_their_claims_apart() -> None:
    ledger = EvidenceLedger()
    for result in (_result("q1"), _result("q2"), _result("q1", url="https://example.org/deep")):
        ledger.add(result)
    baseline = scout_trial.baseline_results(ledger, ["q1", "q2", "q3"])
    assert set(baseline) == {"q1", "q2"} and str(baseline["q1"].claims[0].evidence[0].source.url) == "https://example.org/a"
    merged = scout_trial.ledger_of([_result("q1"), _result("q1")])
    assert merged.claim_ids() == {"q1/c1", "q1/c1~2"}


def test_evidence_metrics_count_sources_and_unfound_quotes() -> None:
    results = [_result("q1", quote_check="verified"), _result("q2", url="https://example.org/b", quote_check="not_found")]
    metrics = scout_trial.evidence_metrics(results, 3)
    assert metrics == {"questions_answered": 2, "questions": 3, "claims": 2, "cited_sources": 2, "quotes": 2,
                       "quotes_not_found": 1, "sources_not_found": 0}


def test_an_arm_is_one_stored_job_of_its_scouts_and_the_fixed_synthesizers_report() -> None:
    from research_loop.agents import synthesizer_agent
    from research_loop.repository import CapturingResearchRepository

    prompt_trial = scout_trial._load("prompt_trial", Path(__file__).parents[1] / "scripts" / "prompt_trial.py")

    def scout(messages, info: AgentInfo) -> ModelResponse:
        result = _result("q1").model_dump(mode="json") | {"question_id": "q1"}
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, result)])

    async def synthesize(messages, info: AgentInfo):  # synthesis streams
        report = {"title": "T", "answer": "Findings [s1].", "claims": [{"statement": "s", "claim_ids": ["q1/c1"]}]}
        yield {0: DeltaToolCall(info.output_tools[0].name, json.dumps(report))}

    route = ModelRoute("test", 3, 4, 100_000)
    repo = CapturingResearchRepository(InMemoryResearchRepository())
    loop = AsyncResearchLoop(ModelPolicy("p", {role: route for role in ResearchRole}),
                             ResearchConfig(scholarly_tools=False), repo, settings=ResearchSettings.from_env({}))
    plan = ResearchPlan(objective="o", questions=[ResearchQuestion(id="q1", question="q", priority=3)])
    trial = {"name": "scout", "arm": "flash-1", "model": "zai:glm-5.3-flash"}
    with scout_agent.override(model=FunctionModel(scout)), synthesizer_agent.override(model=FunctionModel(stream_function=synthesize)):
        unit, metrics = asyncio.run(scout_trial.scout_arm(
            loop, prompt_trial.TrialBudget(1.0), prompt_trial, plan, "o", ResearchConstraints(), trial))
    assert unit.error is None and [r.question_id for r in unit.result[0]] == ["q1"]
    job = repo.jobs[unit.job_id]
    assert job["status"] == "succeeded" and job["config"]["trial"] == trial
    assert job["final_report"]["title"] == "T" and job["evidence_ledger"]["q1"]
    assert metrics["requests"] == 1 and metrics["salvage_calls"] == 0

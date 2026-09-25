from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path

from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

from research_loop.agents import synthesizer_agent, verifier_agent
from research_loop.async_orchestrator import AsyncResearchLoop, ResearchConfig
from research_loop.ledger import EvidenceLedger
from research_loop.policy import ModelPolicy, ModelRoute
from research_loop.repository import (
    CapturingResearchRepository,
    InMemoryResearchRepository,
)
from research_loop.schemas import (
    Claim,
    Evidence,
    ResearchConstraints,
    ResearchResult,
    ResearchRole,
    SourceRef,
)
from research_loop.settings import ResearchSettings

_SCRIPTS = Path(__file__).parents[1] / "scripts"


def _load(name: str) -> object:
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


resynthesize = _load("resynthesize")
prompt_trial = _load("prompt_trial")


def test_a_synthesis_that_fails_at_the_provider_is_rerun_on_the_fallback_verified_and_recorded() -> None:
    calls: list[int] = []

    async def synthesize(messages, info: AgentInfo):  # synthesis streams
        calls.append(1)
        if len(calls) == 1:
            raise ModelAPIError("test:primary", "Request timed out.")
        report = {"title": "T", "answer": "Findings [s1].", "claims": [{"statement": "s", "claim_ids": ["q1/c1"]}]}
        yield {0: DeltaToolCall(info.output_tools[0].name, json.dumps(report))}

    def verify(messages, info: AgentInfo) -> ModelResponse:
        checks = [{"statement": "s", "claim_ids": ["q1/c1"], "supported": True, "severity": "none", "explanation": "ok"}]
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"checks": checks})])

    route = ModelRoute("test:route", 3, 4, 100_000)
    routes = {role: route for role in ResearchRole} | {
        ResearchRole.SYNTHESIZER: ModelRoute("test:primary", 3, 4, 100_000, refusal_fallback="test:fallback")}
    repo = CapturingResearchRepository(InMemoryResearchRepository())
    loop = AsyncResearchLoop(ModelPolicy("p", routes), ResearchConfig(scholarly_tools=False), repo,
                             settings=ResearchSettings.from_env({}))
    ledger = EvidenceLedger()
    ledger.add(ResearchResult(question_id="q1", question="q", conclusion="c", confidence=0.8, claims=[
        Claim(id="c1", statement="s", confidence=0.8, evidence=[Evidence(
            source=SourceRef(url="https://example.org/a", title="t"), excerpt="e", confidence=0.8)])]))
    trial = {"name": "resynthesis", "source_job": "b796008c"}
    with synthesizer_agent.override(model=FunctionModel(stream_function=synthesize)), \
            verifier_agent.override(model=FunctionModel(verify)):
        entry = asyncio.run(resynthesize.resynthesize(loop, prompt_trial.TrialBudget(1.0, concurrency=1), prompt_trial,
                                                      "o", ledger, ResearchConstraints(), trial))
    assert entry["error"] is None and (entry["checked"], entry["unsupported"]) == (1, 0)
    assert [(c["role"], c["model"], c["status"], c["error"]) for c in entry["calls"]] == [
        ("synthesizer", "test:primary", "failed", "ModelAPIError"),
        ("synthesizer", "test:fallback", "succeeded", None),
        ("verifier", "test:route", "succeeded", None)]
    job = repo.jobs[entry["trial_job_id"]]
    assert job["status"] == "succeeded" and job["final_report"]["title"] == "T"
    lines = resynthesize.render({**entry, "max_usd": 1.0}).splitlines()
    assert "ModelAPIError" in lines[1] and "test:fallback" in lines[2] and lines[4].startswith("statements 1, checked 1")

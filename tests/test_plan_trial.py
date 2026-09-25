"""scripts/plan_trial.py: plan agreement and coverage, and each arm as one stored unit."""
from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from research_loop.agents import planner_agent, scout_agent
from research_loop.async_orchestrator import AsyncResearchLoop, ResearchConfig
from research_loop.policy import ModelPolicy, ModelRoute
from research_loop.repository import (
    CapturingResearchRepository,
    InMemoryResearchRepository,
)
from research_loop.schemas import ResearchConstraints, ResearchRole
from research_loop.settings import ResearchSettings

_SPEC = importlib.util.spec_from_file_location("plan_trial", Path(__file__).parents[1] / "scripts" / "plan_trial.py")
plan_trial = importlib.util.module_from_spec(_SPEC)
sys.modules["plan_trial"] = plan_trial
_SPEC.loader.exec_module(plan_trial)

_BY_COUNTRY = [f"What pension schemes does {c} run as of 2025?" for c in ("Indonesia", "Malaysia", "Vietnam")]
_BY_TOPIC = ["What are the contribution rates across Indonesia, Malaysia and Vietnam?",
             "What retirement ages apply across Indonesia, Malaysia and Vietnam?"]


def test_plans_split_the_same_way_agree_and_different_splits_do_not() -> None:
    reworded = [q.replace("run as of 2025", "operate, as of early 2025") for q in _BY_COUNTRY]
    assert plan_trial.plan_agreement(_BY_COUNTRY, reworded) == 1.0
    assert plan_trial.plan_agreement(_BY_COUNTRY, _BY_TOPIC) < 0.5
    assert plan_trial.plan_agreement(_BY_COUNTRY, []) == 0.0


def test_coverage_counts_the_entities_the_objective_names() -> None:
    objective = "I need a report on pensions in several countries, including Indonesia, Malaysia and Vietnam."
    assert {"indonesia", "malaysia", "vietnam"} <= plan_trial.named_entities(objective)
    assert plan_trial.coverage(_BY_COUNTRY, objective) == 1.0
    assert plan_trial.coverage(_BY_COUNTRY[:1], objective) < 0.5


def _plan_reply(info: AgentInfo, count: int) -> ModelResponse:
    plan = {"objective": "o", "questions": [{"id": f"Q{i}", "question": f"q{i}", "priority": 3} for i in range(count)]}
    return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, plan)])


def test_best_of_three_keeps_the_plan_the_selector_chose() -> None:
    calls: list[int] = []

    def planner(messages, info: AgentInfo) -> ModelResponse:
        calls.append(1)
        return _plan_reply(info, len(calls))  # plans of one, two, and three questions

    def selector(messages, info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"choice": 2, "reason": "one subject each"})])

    route = ModelRoute("test", 3, 0, 100_000)
    repo = CapturingResearchRepository(InMemoryResearchRepository())
    loop = AsyncResearchLoop(ModelPolicy("p", {role: route for role in ResearchRole}), ResearchConfig(), repo,
                             settings=ResearchSettings.from_env({}))

    async def run():
        job_id = await loop._create_job("o", ResearchConstraints(), session_id=None, root_run_id=None, kind="trial")
        async with loop._job_scope(job_id):
            return await plan_trial.plan_arm("best-of-3", loop, route, "o", ResearchConstraints(), job_id), job_id

    with planner_agent.override(model=FunctionModel(planner)), \
            plan_trial.selector_agent.override(model=FunctionModel(selector)):
        result, job_id = asyncio.run(run())
    assert result["choice"] == 2 and sorted(result["candidates"]) == [1, 2, 3]
    assert len(result["plan"].questions) == result["candidates"][1]
    assert len(repo.jobs[job_id]["plan"]["questions"]) == len(result["plan"].questions)


def test_the_landscape_arm_gives_the_planner_the_scouts_findings() -> None:
    seen: list[str] = []

    def scout(messages, info: AgentInfo) -> ModelResponse:
        result = {"question_id": "landscape", "question": "q", "conclusion": "Seven national schemes", "confidence": 0.5,
                  "claims": [{"id": "c1", "statement": "Indonesia runs JHT and JP", "confidence": 0.5, "evidence": [
                      {"source": {"url": "https://example.org", "title": "t"}, "excerpt": "e", "confidence": 0.5}]}]}
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, result)])

    def planner(messages, info: AgentInfo) -> ModelResponse:
        seen.append(str(messages[0].parts[-1].content))
        return _plan_reply(info, 2)

    route = ModelRoute("test", 3, 4, 100_000)
    loop = AsyncResearchLoop(ModelPolicy("p", {role: route for role in ResearchRole}, cheap_scout=route),
                             ResearchConfig(scholarly_tools=False), CapturingResearchRepository(InMemoryResearchRepository()),
                             settings=ResearchSettings.from_env({}))

    async def run():
        job_id = await loop._create_job("o", ResearchConstraints(), session_id=None, root_run_id=None, kind="trial")
        async with loop._job_scope(job_id):
            return await plan_trial.plan_arm("landscape", loop, route, "o", ResearchConstraints(), job_id)

    with scout_agent.override(model=FunctionModel(scout)), planner_agent.override(model=FunctionModel(planner)):
        result = asyncio.run(run())
    assert result["landscape_findings"] == 1
    assert "Indonesia runs JHT and JP" in seen[0] and "landscape_guidance" in seen[0]


def test_without_a_landscape_the_planner_prompt_is_unchanged() -> None:
    seen: list[str] = []

    def planner(messages, info: AgentInfo) -> ModelResponse:
        seen.append(str(messages[0].parts[-1].content))
        return _plan_reply(info, 1)

    route = ModelRoute("test", 3, 0, 100_000)
    loop = AsyncResearchLoop(ModelPolicy("p", {role: route for role in ResearchRole}), ResearchConfig(),
                             InMemoryResearchRepository(), settings=ResearchSettings.from_env({}))

    async def run():
        job_id = await loop._create_job("o", ResearchConstraints(), session_id=None, root_run_id=None, kind="trial")
        async with loop._job_scope(job_id):
            await loop._plan(job_id, "o", ResearchConstraints(), None)

    with planner_agent.override(model=FunctionModel(planner)):
        asyncio.run(run())
    assert "landscape" not in seen[0]

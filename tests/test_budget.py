from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic_ai import Agent
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.usage import RunUsage

from research_loop.async_orchestrator import AsyncResearchLoop, JobBudgetExceeded
from research_loop.policy import ModelPolicy, ModelRoute
from research_loop.repository import InMemoryResearchRepository
from research_loop.schemas import ResearchRole


def _loop(route: ModelRoute, job_cost_limit: float | None) -> AsyncResearchLoop:
    policy = ModelPolicy("budget-test", {role: route for role in ResearchRole}, job_cost_limit=job_cost_limit)
    return AsyncResearchLoop(policy, repository=InMemoryResearchRepository())


@pytest.mark.asyncio
async def test_job_cap_clamps_call_limit_and_blocks_once_spent() -> None:
    route = ModelRoute("test", 5, 5, 10_000, cost_limit=3.0)
    loop = _loop(route, job_cost_limit=5.0)
    job_id = uuid4()
    loop._job_spend[job_id] = Decimal(0)

    assert loop._limits(route, loop._remaining_budget(job_id)).cost_limit == 3.0
    loop._record_spend(job_id, RunUsage(requests=1, cost=Decimal("4")))
    assert loop._limits(route, loop._remaining_budget(job_id)).cost_limit == pytest.approx(1.0)
    loop._record_spend(job_id, RunUsage(requests=1, cost=Decimal("1.5")))
    assert loop._job_spend[job_id] == Decimal("5.5")

    with pytest.raises(JobBudgetExceeded):
        await loop._run_agent(job_id=job_id, agent=Agent(output_type=str), role=ResearchRole.PLANNER,
                              route=route, prompt="plan")
    assert loop.repository.tasks == {}


@pytest.mark.asyncio
@pytest.mark.filterwarnings("ignore:A `cost_limit` is set but cannot be enforced")
async def test_job_cap_fails_closed_after_unpriced_call() -> None:
    route = ModelRoute("test", 5, 5, 10_000)
    loop = _loop(route, job_cost_limit=5.0)
    job_id = uuid4()
    loop._job_spend[job_id] = Decimal(0)
    agent = Agent(output_type=str)

    # TestModel has no pricing data, so the job can no longer prove it is under the cap.
    await loop._run_agent(job_id=job_id, agent=agent, role=ResearchRole.PLANNER, route=route, prompt="plan")
    assert loop._job_spend[job_id] is None
    with pytest.raises(JobBudgetExceeded, match="pricing"):
        await loop._run_agent(job_id=job_id, agent=agent, role=ResearchRole.PLANNER, route=route, prompt="plan")


@pytest.mark.asyncio
async def test_uncapped_job_runs_and_failed_task_keeps_usage() -> None:
    route = ModelRoute("test", 1, 5, 10_000)
    loop = _loop(route, job_cost_limit=None)
    job_id = uuid4()
    loop._job_spend[job_id] = Decimal(0)
    agent = Agent(output_type=str)

    @agent.tool_plain
    def lookup() -> str:
        return "value"

    # TestModel calls the tool first, so the second request trips request_limit=1.
    with pytest.raises(UsageLimitExceeded):
        await loop._run_agent(job_id=job_id, agent=agent, role=ResearchRole.SCOUT, route=route, prompt="scout")
    (task,) = loop.repository.tasks.values()
    assert task["status"] == "failed"
    assert task["usage"]["requests"] == 1

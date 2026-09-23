from __future__ import annotations

import json

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


@pytest.mark.asyncio
async def test_single_agent_job_persists_lifecycle_and_clears_spend() -> None:
    route = ModelRoute("test", 5, 5, 10_000)
    loop = _loop(route, job_cost_limit=None)
    outcome = await loop.run_agent_job(
        "long-horizon synthesis", agent=Agent(output_type=str), role=ResearchRole.SYNTHESIZER,
        route=route, prompt="synthesize", config={"long_horizon": {"id": "c1"}},
    )
    job = loop.repository.jobs[outcome.job_id]
    assert job["status"] == "succeeded"
    assert job["config"]["orchestrator"]["kind"] == "single-agent"
    assert job["config"]["long_horizon"] == {"id": "c1"}
    assert outcome.cost_usd is None  # TestModel has no pricing data
    assert loop._job_spend == {}

    failing = _loop(ModelRoute("test", 0, 5, 10_000), job_cost_limit=None)
    with pytest.raises(UsageLimitExceeded):
        await failing.run_agent_job("x", agent=Agent(output_type=str), role=ResearchRole.SYNTHESIZER,
                                    route=ModelRoute("test", 0, 5, 10_000), prompt="x")
    (failed_job,) = failing.repository.jobs.values()
    assert failed_job["status"] == "failed"
    assert failed_job["error"] == {"type": "UsageLimitExceeded"}
    assert failing._job_spend == {}


def test_reserve_holds_budget_for_finishing_steps_and_salvage() -> None:
    route = ModelRoute("test", 5, 5, 10_000)
    policy = ModelPolicy("reserve-test", {role: route for role in ResearchRole},
                         job_cost_limit=5.0, job_reserve_usd=2.0)
    loop = AsyncResearchLoop(policy, repository=InMemoryResearchRepository())
    job_id = uuid4()
    loop._job_spend[job_id] = Decimal("3.5")

    with pytest.raises(JobBudgetExceeded, match="reserve"):
        loop._remaining_budget(job_id, policy.job_reserve_for(ResearchRole.SCOUT))
    for role in (ResearchRole.GAP_ANALYST, ResearchRole.SYNTHESIZER, ResearchRole.VERIFIER):
        assert loop._remaining_budget(job_id, policy.job_reserve_for(role)) == pytest.approx(1.5)
    assert loop._remaining_budget(job_id, policy.job_reserve_for(ResearchRole.DEEP_DIVE, salvage=True)) == pytest.approx(1.5)


def _research_setup(*, salvage: bool, job_cost_limit: float | None = None):
    from research_loop.async_orchestrator import ResearchConfig
    from research_loop.schemas import ResearchQuestion, ResearchResult

    route = ModelRoute("test", 1, 5, 10_000, cost_limit=0.8)
    policy = ModelPolicy("salvage-test", {role: route for role in ResearchRole}, job_cost_limit=job_cost_limit)
    loop = AsyncResearchLoop(policy, ResearchConfig(salvage_exhausted_research=salvage),
                             repository=InMemoryResearchRepository())
    agent = Agent(output_type=ResearchResult)

    @agent.tool_plain
    def scholar_search(query: str) -> dict:
        return {"works": [{"title": "SWE-bench", "provider_id": "W42", "abstract": "PRIVATE-FULL-TEXT"}]}

    question = ResearchQuestion(id="p01", question="What is SWE-bench?")
    return loop, agent, route, question


def _scripted_scout(prompts: list[str]):
    from pydantic_ai.messages import ModelResponse, ToolCallPart
    from pydantic_ai.models.function import AgentInfo, FunctionModel

    def respond(messages, info: AgentInfo) -> ModelResponse:
        content = str(getattr(messages[-1].parts[-1], "content", ""))
        prompts.append(content)
        if "budget_exhausted" in content:
            source = {"url": "https://example.org/w42", "title": "SWE-bench"}
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {
                "question_id": "p01", "question": "What is SWE-bench?", "conclusion": "Salvaged from W42",
                "confidence": 0.4,
                "claims": [{"id": "c1", "statement": "W42 is SWE-bench", "confidence": 0.4, "evidence": [
                    {"source": source, "excerpt": "abstract", "quote": "PRIVATE-FULL-TEXT", "confidence": 0.4},
                    {"source": source, "excerpt": "abstract", "quote": "never returned by a tool", "confidence": 0.4},
                ]}],
            })])
        return ModelResponse(parts=[ToolCallPart("scholar_search", {"query": "SWE-bench"})])

    return FunctionModel(respond)


@pytest.mark.asyncio
@pytest.mark.filterwarnings("ignore:A `cost_limit` is set but cannot be enforced")
async def test_exhausted_research_is_salvaged_without_persisting_tool_output() -> None:
    loop, agent, route, question = _research_setup(salvage=True)
    job_id = uuid4()
    prompts: list[str] = []
    with agent.override(model=_scripted_scout(prompts)):
        result = await loop._run_research(
            question, job_id=job_id, agent=agent, role=ResearchRole.SCOUT, route=route,
            prompt='{"question": {"id": "p01"}}', question_id="p01",
        )
    assert result.conclusion == "Salvaged from W42"
    assert "W42" in prompts[-1] and "PRIVATE-FULL-TEXT" in prompts[-1]  # the model sees what it gathered
    exhausted, salvaged = loop.repository.tasks.values()
    assert exhausted["status"] == "failed"
    # The interrupted run's tool history is kept, not only successful runs'.
    assert [event["tool_name"] for event in loop.repository.tool_events if event["task_id"] == exhausted["id"]] == ["scholar_search"]
    assert salvaged["status"] == "succeeded"
    assert salvaged["parent_task_id"] == exhausted["id"]  # the salvage call points at the run it wraps up
    # The salvage call has no tools; its quotes are checked against the exhausted run's tool output.
    assert [item.quote_check for item in result.claims[0].evidence] == ["verified", "not_found"]
    assert salvaged["effective_config"]["salvage"] is True
    assert salvaged["effective_config"]["max_requests"] == 2
    assert salvaged["effective_config"]["cost_limit"] == pytest.approx(0.4)  # half the route cap
    assert "result_sha256" in salvaged["prompt"]
    assert "PRIVATE-FULL-TEXT" not in salvaged["prompt"]


@pytest.mark.asyncio
async def test_exhausted_research_fails_when_salvage_is_off() -> None:
    loop, agent, route, question = _research_setup(salvage=False)
    with agent.override(model=_scripted_scout([])), pytest.raises(UsageLimitExceeded):
        await loop._run_research(
            question, job_id=uuid4(), agent=agent, role=ResearchRole.SCOUT, route=route,
            prompt='{"question": {"id": "p01"}}', question_id="p01",
        )


@pytest.mark.asyncio
async def test_research_without_budget_returns_empty_result_without_calls() -> None:
    loop, agent, route, question = _research_setup(salvage=True, job_cost_limit=1.0)
    job_id = uuid4()
    loop._job_spend[job_id] = Decimal("1.0")
    result = await loop._run_research(
        question, job_id=job_id, agent=agent, role=ResearchRole.SCOUT, route=route,
        prompt='{"question": {"id": "p01"}}', question_id="p01",
    )
    assert result.confidence == 0.0
    assert result.claims == []
    assert result.unresolved_questions == ["What is SWE-bench?"]
    assert loop.repository.tasks == {}


def _tool_messages(calls: list[tuple[str, dict, object]]) -> list:
    """A run's messages: each (tool, args, result) as a call and its return; result None is unanswered."""
    from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart

    messages: list = []
    for index, (tool, args, result) in enumerate(calls):
        messages.append(ModelResponse(parts=[ToolCallPart(tool_name=tool, args=args, tool_call_id=f"c{index}")]))
        if result is not None:
            messages.append(ModelRequest(parts=[ToolReturnPart(tool_name=tool, content=result, tool_call_id=f"c{index}")]))
    return messages


def test_salvage_skips_errors_unanswered_and_repeated_calls() -> None:
    from research_loop.async_orchestrator import _gathered_evidence

    gathered, counts = _gathered_evidence(_tool_messages([
        ("web_fetch", {"url": "https://a.example"}, {"url": "https://a.example", "error": "HTTPStatusError", "status": 404}),
        ("scholar_search", {"query": "x"}, json.dumps({"works": [], "provider_errors": ["openalex:HTTPError"]})),
        ("scholar_search", {"query": "y"}, json.dumps({"works": [{"title": "Found"}], "provider_errors": []})),
        ("scholar_search", {"query": "y"}, json.dumps({"works": [{"title": "Found"}], "provider_errors": []})),
        ("web_fetch", {"url": "https://b.example"}, None),
        ("web_fetch", {"url": "https://c.example"}, {"url": "https://c.example", "text": "page text"}),
    ]))
    assert [item["args"] for item in gathered] == [{"query": "y"}, {"url": "https://c.example"}]
    assert counts == {"unanswered": 1, "errors": 2, "repeated": 1, "kept": 2, "cut": 0, "left_out": 0}


def test_salvage_shares_its_budget_so_late_results_are_not_crowded_out() -> None:
    from research_loop.async_orchestrator import _SALVAGE_EVIDENCE_CHARS, _gathered_evidence

    calls = [("scholar_search", {"query": f"q{i}"}, {"text": "s" * 16_000}) for i in range(4)]
    calls += [("scholar_get", {"id": f"g{i}"}, {"text": "g" * 500}) for i in range(4)]
    calls += [("web_fetch", {"url": "https://late.example"}, {"text": "LATE " + "f" * 20_000})]
    gathered, counts = _gathered_evidence(_tool_messages(calls))
    sizes = [len(item["result"]) for item in gathered]
    assert len(gathered) == 9 and gathered[-1]["args"] == {"url": "https://late.example"}
    assert "LATE" in gathered[-1]["result"]
    assert all(size > 500 for size in sizes[4:8])                      # short results are kept whole
    assert len(set(sizes[:4] + sizes[8:])) == 1                        # long ones share one allowance
    assert sum(sizes) <= _SALVAGE_EVIDENCE_CHARS
    assert counts["cut"] == 5 and counts["left_out"] == 0


def test_salvage_leaves_out_the_oldest_results_only_when_there_are_too_many() -> None:
    from research_loop.async_orchestrator import _SALVAGE_EVIDENCE_CHARS, _SALVAGE_MIN_RESULT_CHARS, _gathered_evidence

    fit = _SALVAGE_EVIDENCE_CHARS // _SALVAGE_MIN_RESULT_CHARS
    calls = [("web_fetch", {"url": f"https://{i}.example"}, {"text": "t" * 2_000}) for i in range(fit + 10)]
    gathered, counts = _gathered_evidence(_tool_messages(calls))
    assert counts["left_out"] == 10 and len(gathered) == fit
    assert gathered[0]["args"] == {"url": "https://10.example"}
    assert all(len(item["result"]) == _SALVAGE_MIN_RESULT_CHARS for item in gathered)


def test_salvage_evidence_bound_leaves_room_for_one_retry() -> None:
    from research_loop.async_orchestrator import _SALVAGE_EVIDENCE_CHARS
    from research_loop.policy import retry_token_budget

    route = ModelRoute("anthropic:any", 20, 40, 180_000, 5.0).salvage()  # the densest tokenizer on record
    prompt = "x" * (_SALVAGE_EVIDENCE_CHARS + 4_000)                      # plus the question and instruction
    assert retry_token_budget(prompt, route, ResearchRole.DEEP_DIVE, output_allowance=6_000) <= route.total_tokens_limit

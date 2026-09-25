from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from research_loop.async_orchestrator import AsyncResearchLoop, ResearchConfig
from research_loop.benchmark import run_benchmark
from research_loop.budget_notes import LAST_REQUEST_NOTE, NOTE_PREFIX, budget_note
from research_loop.policy import ModelPolicy, ModelRoute
from research_loop.repository import InMemoryResearchRepository
from research_loop.schemas import ResearchRole
from research_loop.settings import ResearchSettings
from research_loop.tools import ResearchToolMode


def _notes(messages) -> list[str]:
    return [part.content for message in messages if isinstance(message, ModelRequest)
            for part in message.parts if isinstance(part, UserPromptPart) and part.content.startswith(NOTE_PREFIX)]


def _script(seen: list[list[str]]):
    """Two turns of two parallel fetches, then an answer, recording the notes each request carries."""
    def respond(messages, info: AgentInfo) -> ModelResponse:
        seen.append(_notes(messages))
        turn = len(seen)
        if turn <= 2:
            return ModelResponse(parts=[ToolCallPart("fetch_page", {"n": 2 * turn - 1}),
                                        ToolCallPart("fetch_page", {"n": 2 * turn})])
        return ModelResponse(parts=[TextPart("done")])
    return FunctionModel(respond)


async def _run(budget_notes: tuple[str, ...], role: ResearchRole = ResearchRole.DEEP_DIVE):
    route = ModelRoute("test", 6, 10, 1_000_000)
    config = ResearchConfig(budget_notes=budget_notes, scholarly_tools=False, tool_mode=ResearchToolMode.NORMALIZED)
    loop = AsyncResearchLoop(ModelPolicy("p", {r: route for r in ResearchRole}), config,
                             repository=InMemoryResearchRepository())
    seen: list[list[str]] = []
    agent = Agent(output_type=str)

    @agent.tool_plain
    def fetch_page(n: int) -> str:
        return f"page {n}"

    with agent.override(model=_script(seen)):
        await loop._run_agent(job_id=uuid4(), agent=agent, role=role, route=route,
                              prompt="research", research_tools=True)
    return seen, loop.repository


@pytest.mark.asyncio
async def test_default_runs_send_no_budget_note() -> None:
    seen, repository = await _run(())
    assert seen == [[], [], []]
    (task,) = repository.tasks.values()
    assert "budget_notes" not in task["effective_config"]


@pytest.mark.asyncio
async def test_each_request_ends_with_what_is_left_and_earlier_notes_stay_as_sent() -> None:
    seen, repository = await _run(("deep_dive",))
    first = budget_note(0, 0, 6, 10)
    second = budget_note(1, 2, 6, 10)
    assert seen[0] == [first]
    # The earlier note is resent unchanged, so the cached prompt prefix survives.
    assert seen[1] == [first, second]
    assert seen[2] == [first, second, budget_note(2, 4, 6, 10)]
    assert "6 of 6 model requests and 10 of 10 tool calls" in first
    assert "5 of 6 model requests and 8 of 10 tool calls" in second

    # Tool telemetry still sees the four fetches.
    assert [event["result"] for event in repository.tool_events] == [f"page {n}" for n in range(1, 5)]
    (task,) = repository.tasks.values()
    assert task["effective_config"]["budget_notes"] is True


@pytest.mark.asyncio
async def test_notes_apply_only_to_the_roles_named() -> None:
    seen, _ = await _run(("deep_dive",), role=ResearchRole.SCOUT)
    assert seen == [[], [], []]


def test_note_asks_for_the_result_when_a_limit_is_reached() -> None:
    assert budget_note(5, 0, 6, 10) == LAST_REQUEST_NOTE
    assert "No tool calls are left, and 3 of 6" in budget_note(3, 10, 6, 10)


def test_config_takes_tool_loop_roles_only_and_records_them_only_when_set() -> None:
    assert "budget_notes" not in ResearchConfig().snapshot()
    config = ResearchConfig(budget_notes=("deep_dive", "deep_dive"))
    assert config.budget_notes == (ResearchRole.DEEP_DIVE,)
    assert config.snapshot()["budget_notes"] == ["deep_dive"]
    with pytest.raises(ValueError, match="scout and deep_dive only"):
        ResearchConfig(budget_notes=("synthesizer",))


@pytest.mark.asyncio
async def test_benchmark_manifest_records_budget_notes(tmp_path: Path) -> None:
    suite = tmp_path / "cases.json"
    suite.write_text(json.dumps([{"name": "synthetic-case", "objective": "Private fixture prompt"}]))
    output = tmp_path / "manifest.json"
    await run_benchmark(suite, policies=["synthetic"], max_concurrency=1, manifest_path=output,
                        settings=ResearchSettings.from_env({"RESEARCH_BENCHMARK_OUTPUT": str(tmp_path)}),
                        budget_notes=(ResearchRole.DEEP_DIVE,))
    manifest = json.loads(output.read_text())
    assert manifest["run_config"]["budget_notes"] == ["deep_dive"]


async def _run_spending(max_requests: int, max_tool_calls: int, calls_per_turn: int):
    """A loop that fetches `calls_per_turn` pages whenever it is offered tools, recording the tools offered."""
    offered: list[list[str]] = []

    def respond(messages, info: AgentInfo) -> ModelResponse:
        offered.append(sorted(tool.name for tool in info.function_tools))
        if info.function_tools:
            return ModelResponse(parts=[ToolCallPart("fetch_page", {"n": i}) for i in range(calls_per_turn)])
        return ModelResponse(parts=[TextPart("result")])

    route = ModelRoute("test", max_requests, max_tool_calls, 1_000_000)
    config = ResearchConfig(budget_notes=("deep_dive",), scholarly_tools=False, tool_mode=ResearchToolMode.NORMALIZED)
    loop = AsyncResearchLoop(ModelPolicy("p", {r: route for r in ResearchRole}), config,
                             repository=InMemoryResearchRepository())
    agent = Agent(output_type=str)

    @agent.tool_plain
    def fetch_page(n: int) -> str:
        return f"page {n}"

    with agent.override(model=FunctionModel(respond)):
        output = await loop._run_agent(job_id=uuid4(), agent=agent, role=ResearchRole.DEEP_DIVE, route=route,
                                       prompt="research", research_tools=True)
    return output, offered


@pytest.mark.asyncio
async def test_the_last_request_offers_no_tools_so_the_loop_returns_its_result() -> None:
    output, offered = await _run_spending(max_requests=3, max_tool_calls=100, calls_per_turn=1)
    assert output == "result"
    assert ["fetch_page" in tools for tools in offered] == [True, True, False]
    assert len(offered[0]) > 1 and offered[-1] == []  # the capability's web search is withdrawn too


@pytest.mark.asyncio
async def test_a_batch_past_the_tool_call_limit_finishes_and_then_tools_are_withdrawn() -> None:
    # Four calls a turn against a limit of six: the second batch takes the loop to eight, within the slack.
    output, offered = await _run_spending(max_requests=10, max_tool_calls=6, calls_per_turn=4)
    assert output == "result"
    assert ["fetch_page" in tools for tools in offered] == [True, True, False]


@pytest.mark.asyncio
async def test_without_budget_notes_a_loop_keeps_its_tools_to_the_limit() -> None:
    from pydantic_ai.exceptions import UsageLimitExceeded

    route = ModelRoute("test", 2, 100, 1_000_000)
    loop = AsyncResearchLoop(ModelPolicy("p", {r: route for r in ResearchRole}),
                             ResearchConfig(scholarly_tools=False, tool_mode=ResearchToolMode.NORMALIZED),
                             repository=InMemoryResearchRepository())
    agent = Agent(output_type=str)

    @agent.tool_plain
    def fetch_page(n: int) -> str:
        return f"page {n}"

    always = FunctionModel(lambda messages, info: ModelResponse(parts=[ToolCallPart("fetch_page", {"n": 1})]))
    with agent.override(model=always), pytest.raises(UsageLimitExceeded):
        await loop._run_agent(job_id=uuid4(), agent=agent, role=ResearchRole.DEEP_DIVE, route=route,
                              prompt="research", research_tools=True)

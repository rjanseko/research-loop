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

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from research_loop.async_orchestrator import AsyncResearchLoop, ResearchConfig
from research_loop.policy import ModelPolicy, ModelRoute
from research_loop.repository import InMemoryResearchRepository
from research_loop.schemas import ResearchRole
from research_loop.tool_history import MIN_TRIM_CHARS, ToolResultTrimmer
from research_loop.tools import ResearchToolMode


def _page(n: int) -> str:
    return f"page {n} " + "evidence " * 600  # well above MIN_TRIM_CHARS


def _script(seen: list[list[str]]):
    """Fetch pages 1-3 one at a time, recording the tool results each request carries."""
    def respond(messages, info: AgentInfo) -> ModelResponse:
        seen.append([
            part.model_response_str()
            for message in messages if isinstance(message, ModelRequest)
            for part in message.parts if isinstance(part, ToolReturnPart)
        ])
        fetched = len(seen) - 1
        if fetched < 3:
            return ModelResponse(parts=[ToolCallPart("fetch_page", {"n": fetched + 1})])
        return ModelResponse(parts=[TextPart("done")])
    return FunctionModel(respond)


async def _run(keep_recent: int | None) -> tuple[list[list[str]], InMemoryResearchRepository]:
    route = ModelRoute("test", 10, 10, 1_000_000)
    config = ResearchConfig(keep_recent_tool_results=keep_recent, scholarly_tools=False,
                            tool_mode=ResearchToolMode.NORMALIZED)
    loop = AsyncResearchLoop(ModelPolicy("p", {role: route for role in ResearchRole}), config,
                             repository=InMemoryResearchRepository())
    seen: list[list[str]] = []
    agent = Agent(output_type=str)

    @agent.tool_plain
    def fetch_page(n: int) -> str:
        return _page(n)

    with agent.override(model=_script(seen)):
        await loop._run_agent(job_id=uuid4(), agent=agent, role=ResearchRole.SCOUT, route=route,
                              prompt="research", research_tools=True)
    return seen, loop.repository


@pytest.mark.asyncio
async def test_default_runs_resend_every_tool_result_whole() -> None:
    seen, _ = await _run(None)
    assert seen[-1] == [_page(1), _page(2), _page(3)]


@pytest.mark.asyncio
async def test_older_results_the_model_has_read_are_sent_as_stubs() -> None:
    seen, repository = await _run(1)
    assert seen[1] == [_page(1)]  # a result is always sent whole the first time
    assert seen[2][0].startswith("[Trimmed to save context: you already read this fetch_page result")
    assert seen[2][1] == _page(2)
    final = seen[3]
    assert [text.startswith("[Trimmed") for text in final] == [True, True, False]
    assert final[0].endswith(_page(1)[:500]) and final[2] == _page(3)

    # Telemetry, and the quote and source checks that read it, see what the tools returned.
    assert [event["result"] for event in repository.tool_events] == [_page(1), _page(2), _page(3)]
    (task,) = repository.tasks.values()
    assert task["effective_config"]["keep_recent_tool_results"] == 1


def test_trimmer_keeps_short_results_and_restores_originals() -> None:
    short = ToolReturnPart("search", "a few hits", tool_call_id="s")
    long = ToolReturnPart("fetch", "x" * MIN_TRIM_CHARS, tool_call_id="f")
    messages = [ModelRequest(parts=[short, long]), ModelResponse(parts=[TextPart("next")]),
                ModelRequest(parts=[ToolReturnPart("fetch", "y" * MIN_TRIM_CHARS, tool_call_id="g")])]
    trimmer = ToolResultTrimmer(0)
    trimmed = trimmer(messages)
    assert trimmed[0].parts[0] is short and trimmed[0].parts[1].content.startswith("[Trimmed")
    assert trimmed[2] is messages[2]  # unread results are never trimmed
    assert trimmer(trimmed)[0].parts[1] is trimmed[0].parts[1]  # trimming again changes nothing
    assert trimmer.restore(trimmed)[0].parts[1] is long
    with pytest.raises(ValueError):
        ResearchConfig(keep_recent_tool_results=-1)

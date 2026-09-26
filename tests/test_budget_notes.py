from __future__ import annotations

from dataclasses import replace

from pydantic_ai import Agent, UsageLimits
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from research_loop.budget_notes import (
    DEADLINE_NOTE,
    LAST_REQUEST_NOTE,
    NOTE_PREFIX,
    LoopBudget,
    tool_yield,
)


def _notes(messages) -> list[str]:
    return [part.content for message in messages if isinstance(message, ModelRequest)
            for part in message.parts if isinstance(part, UserPromptPart) and part.content.startswith(NOTE_PREFIX)]


def _agent() -> Agent[None, str]:
    agent = Agent(output_type=str)

    @agent.tool_plain
    def fetch(url: str) -> dict:
        """Stands in for the research fetch: a URL with 'missing' in it fails."""
        return {"url": url, "error": "HTTPStatusError", "status": 404} if "missing" in url else {"url": url, "text": "page"}

    return agent


async def _run(budget: LoopBudget, urls_per_turn: list[str]) -> tuple[str, list[list[str]], list[list[str]]]:
    """Fetch `urls_per_turn` whenever tools are offered; record the notes and tools each request carries."""
    notes: list[list[str]] = []
    offered: list[list[str]] = []

    def respond(messages, info: AgentInfo) -> ModelResponse:
        notes.append(_notes(messages))
        offered.append(sorted(tool.name for tool in info.function_tools))
        if info.function_tools:
            return ModelResponse(parts=[ToolCallPart("fetch", {"url": url}) for url in urls_per_turn])
        return ModelResponse(parts=[TextPart("result")])

    result = await _agent().run("research", model=FunctionModel(respond), capabilities=budget.capabilities(),
                                usage_limits=UsageLimits(request_limit=budget.max_requests, tool_calls_limit=100))
    return result.output, notes, offered


async def test_each_request_ends_with_what_is_left_and_earlier_notes_stay_as_sent() -> None:
    budget = LoopBudget(max_requests=3, max_productive=16, max_misses=12)
    output, notes, offered = await _run(budget, ["https://a.test/1", "https://a.test/missing"])
    assert output == "result"
    first, second = notes[1]
    assert notes[0] == [first]
    assert "3 of 3 model requests left" in first and "16 of 16 productive tool calls and 12 of 12 misses" in first
    # The earlier note is resent unchanged, so the cached prompt prefix survives.
    assert "15 of 16 productive tool calls and 11 of 12 misses" in second
    assert "Last batch: 1 of 2 returned something" in second
    # The third request is the last: its note says so and it is offered no tools.
    assert notes[2][-1] == LAST_REQUEST_NOTE
    assert offered == [["fetch"], ["fetch"], []]


async def test_misses_withdraw_tools_while_productive_calls_remain() -> None:
    budget = LoopBudget(max_requests=10, max_productive=16, max_misses=3)
    output, notes, offered = await _run(budget, ["https://a.test/missing-1", "https://a.test/missing-2"])
    assert output == "result"
    # Two misses, then four: the miss budget of three is spent after the second batch.
    assert offered == [["fetch"], ["fetch"], []]
    assert "The miss limit is spent" in notes[2][-1]


async def test_productive_calls_withdraw_tools_once_spent() -> None:
    budget = LoopBudget(max_requests=10, max_productive=2, max_misses=12)
    output, notes, offered = await _run(budget, ["https://a.test/1", "https://a.test/2"])
    assert output == "result"
    assert offered == [["fetch"], []]
    assert "No productive tool calls are left" in notes[1][-1]


def test_a_miss_is_an_empty_search_or_a_failed_call_and_other_tools_are_not_counted() -> None:
    spent = tool_yield([
        ModelRequest(parts=[
            ToolReturnPart(tool_name="web_search", content={"access": "snippet", "results": []}, tool_call_id="a"),
            ToolReturnPart(tool_name="fetch", content={"url": "https://a.test", "error": "HTTPStatusError"}, tool_call_id="b"),
            ToolReturnPart(tool_name="fetch", content={"access": "full_text", "text": "article"}, tool_call_id="c"),
            ToolReturnPart(tool_name="scholar_search", content={"works": [{"title": "T"}]}, tool_call_id="d"),
            ToolReturnPart(tool_name="scholar_get", content={"works": [], "provider_errors": ["get:HTTPStatusError"]}, tool_call_id="e"),
            ToolReturnPart(tool_name="final_result", content="ok", tool_call_id="f"),
        ])
    ])
    assert (spent.productive, spent.misses, spent.last_productive, spent.last_total) == (2, 3, 2, 5)
    budget = LoopBudget(12, 16, 12)
    assert "one or two broader searches" in budget.note(1, spent)
    assert "Last batch" not in budget.note(1, tool_yield([]))
    assert "No productive tool calls are left" in budget.note(1, replace(spent, productive=16))


async def test_a_close_deadline_withdraws_tools_while_budget_remains() -> None:
    remaining = 1_000.0

    def time_left() -> float:
        return remaining

    budget = LoopBudget(max_requests=10, max_productive=16, max_misses=12, time_left=time_left, return_within=120)
    notes: list[list[str]] = []
    offered: list[list[str]] = []

    def respond(messages, info: AgentInfo) -> ModelResponse:
        nonlocal remaining
        notes.append(_notes(messages))
        offered.append(sorted(tool.name for tool in info.function_tools))
        if info.function_tools:
            remaining = 30
            return ModelResponse(parts=[ToolCallPart("fetch", {"url": "https://a.test/1"})])
        return ModelResponse(parts=[TextPart("result")])

    result = await _agent().run("research", model=FunctionModel(respond), capabilities=budget.capabilities(),
                                usage_limits=UsageLimits(request_limit=budget.max_requests, tool_calls_limit=100))
    assert result.output == "result"
    assert "10 of 10 model requests left" in notes[0][-1]
    assert notes[1][-1] == DEADLINE_NOTE
    assert offered == [["fetch"], []]
    assert budget.finish_reason(result.usage.requests, result.all_messages()) == \
        "returned because the research deadline was close"


async def test_a_returned_loop_says_which_budget_it_spent() -> None:
    async def finish(budget: LoopBudget, urls: list[str]) -> str:
        def respond(messages, info: AgentInfo) -> ModelResponse:
            if info.function_tools and urls:
                return ModelResponse(parts=[ToolCallPart("fetch", {"url": url}) for url in urls])
            return ModelResponse(parts=[TextPart("result")])

        result = await _agent().run("research", model=FunctionModel(respond), capabilities=budget.capabilities(),
                                    usage_limits=UsageLimits(request_limit=budget.max_requests, tool_calls_limit=100))
        return budget.finish_reason(result.usage.requests, result.all_messages())

    assert await finish(LoopBudget(10, 2, 12), ["https://a.test/1", "https://a.test/2"]) == \
        "returned after its productive calls were spent"
    assert await finish(LoopBudget(10, 16, 3), ["https://a.test/missing-1", "https://a.test/missing-2"]) == \
        "returned after its misses were spent"
    assert await finish(LoopBudget(3, 16, 12), ["https://a.test/1"]) == "returned on its last request"
    assert await finish(LoopBudget(10, 16, 12), []) == "returned on its own"


async def test_a_turn_past_the_tool_call_limit_loses_only_its_excess_calls() -> None:
    budget = LoopBudget(max_requests=10, max_productive=2, max_misses=1)
    notes: list[list[str]] = []

    def respond(messages, info: AgentInfo) -> ModelResponse:
        notes.append(_notes(messages))
        if len(messages) == 1:
            return ModelResponse(parts=[ToolCallPart("fetch", {"url": f"https://a.test/{n}"}) for n in range(50)])
        return ModelResponse(parts=[TextPart("result")])

    # Without trimming, PydanticAI refuses all 50 calls against the limit of 15 and the loop ends with nothing.
    result = await _agent().run("research", model=FunctionModel(respond), capabilities=budget.capabilities(),
                                usage_limits=UsageLimits(request_limit=10, tool_calls_limit=budget.tool_call_limit))
    assert result.output == "result" and result.usage.tool_calls == budget.tool_call_limit == 15
    asked = [part for message in result.all_messages() if isinstance(message, ModelResponse)
             for part in message.parts if isinstance(part, ToolCallPart)]
    assert len(asked) == 15
    # The second request carries the first request's note, then the dropped calls, then its own note.
    assert len(notes[1]) == 3 and notes[1][0] == notes[0][0]
    assert notes[1][1].startswith(NOTE_PREFIX + "Only the first 15 of the 50 tool calls in your last turn ran")

"""Optional budget notes for research tool loops.

A scout or deep dive is stopped by its route's request and tool-call limits, which it cannot
see. With `ResearchConfig.budget_notes` naming its role, each model request ends with a short
note giving what is left of both, and inviting parallel tool calls, after the budget tracker of
BATS (Liu et al. 2025, arXiv:2511.17006). The note is appended to the newest request only, and
PydanticAI keeps the processed history, so earlier notes stay as they were sent and the cached
prompt prefix survives from one request to the next.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.capabilities import PrepareTools
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.tools import ToolDefinition

NOTE_PREFIX = "[Research budget] "
NOTE = (
    NOTE_PREFIX + "{requests} of {max_requests} model requests and {tool_calls} of {max_tool_calls} tool calls "
    "left, this request included. Put independent searches and fetches in one turn as parallel tool calls. "
    "Return your result while a request is left: a run that reaches either limit is cut off, and its result "
    "is written from truncated tool output."
)
LAST_REQUEST_NOTE = NOTE_PREFIX + "This is your last model request. Do not call tools; return your result now."
NO_TOOLS_NOTE = (
    NOTE_PREFIX + "No tool calls are left, and {requests} of {max_requests} model requests are. Do not call "
    "tools; return your result now."
)
PRODUCTIVE_SPENT_NOTE = (
    NOTE_PREFIX + "No productive tool calls are left, and {requests} of {max_requests} model requests are. "
    "Do not call tools; return your result now."
)
MISS_SPENT_NOTE = (
    NOTE_PREFIX + "The miss limit is spent, and {requests} of {max_requests} model requests are. "
    "Do not call tools; return your result now."
)
YIELD_NOTE = (
    NOTE_PREFIX + "{requests} of {max_requests} model requests left, this request included. "
    "{productive} of {max_productive} productive tool calls and {misses} of {max_misses} misses left. "
    "{batch}"
    "Return your result while a request is left: a run that reaches a limit is cut off, and its result "
    "is written from truncated tool output."
)
PARALLEL = "Put independent searches and fetches in one turn as parallel tool calls. "
NARROW = (
    "The last batch mostly missed. Use one or two broader searches, without quotes or site: operators, "
    "and fetch only addresses a search returned. "
)


def budget_note(requests_used: int, tool_calls_used: int, max_requests: int, max_tool_calls: int) -> str:
    """The note for the next request, given what the run has used so far."""
    requests = max(max_requests - requests_used, 0)
    tool_calls = max(max_tool_calls - tool_calls_used, 0)
    if requests <= 1:
        return LAST_REQUEST_NOTE
    if tool_calls == 0:
        return NO_TOOLS_NOTE.format(requests=requests, max_requests=max_requests)
    return NOTE.format(requests=requests, max_requests=max_requests, tool_calls=tool_calls,
                       max_tool_calls=max_tool_calls)


def _data(content: Any) -> Any:
    if isinstance(content, str) and content[:1] in "{[":
        try:
            return json.loads(content)
        except ValueError:
            return content
    return content


def call_yield(tool_name: str, content: Any) -> str:
    """Whether one tool return spent a productive call, a miss, or neither.

    A search with results, a fetch with text, and a further window of a page already fetched are
    productive. An empty search, an HTTP error, a timeout, and a blocked or unsafe URL are misses.
    The output tool is neither: it is the result, not research.
    """
    name = tool_name.lower()
    if name == "final_result" or not (name == "duckduckgo_search" or name == "web_fetch" or name.startswith("scholar_")):
        return "ignore"
    data = _data(content)
    if name == "duckduckgo_search":
        if isinstance(data, list):
            return "productive" if data else "miss"
        if isinstance(data, dict) and not data.get("error"):
            results = data.get("results")
            if isinstance(results, list):
                return "productive" if results else "miss"
        return "miss"
    if isinstance(data, dict) and data.get("error"):
        return "miss"
    if isinstance(data, dict) and (data.get("text") or data.get("works")):
        return "productive"
    return "miss"


@dataclass(frozen=True)
class ToolYield:
    productive: int = 0
    misses: int = 0
    last_productive: int = 0
    last_total: int = 0


def tool_yield(messages: list[ModelMessage]) -> ToolYield:
    """Productive calls and misses so far, and how the latest batch of returns did."""
    productive = misses = 0
    last_productive = last_total = 0
    for message in messages:
        if not isinstance(message, ModelRequest):
            continue
        batch = [call_yield(part.tool_name, part.content) for part in message.parts if isinstance(part, ToolReturnPart)]
        batch = [kind for kind in batch if kind != "ignore"]
        if not batch:
            continue
        last_total = len(batch)
        last_productive = sum(kind == "productive" for kind in batch)
        productive += last_productive
        misses += last_total - last_productive
    return ToolYield(productive, misses, last_productive, last_total)


def yield_note(requests_used: int, spent: ToolYield, max_requests: int, max_productive: int, max_misses: int) -> str:
    """The note when a route counts productive calls and misses separately."""
    requests = max(max_requests - requests_used, 0)
    if requests <= 1:
        return LAST_REQUEST_NOTE
    if spent.productive >= max_productive:
        return PRODUCTIVE_SPENT_NOTE.format(requests=requests, max_requests=max_requests)
    if spent.misses >= max_misses:
        return MISS_SPENT_NOTE.format(requests=requests, max_requests=max_requests)
    if spent.last_total and spent.last_productive * 2 < spent.last_total:
        batch = f"Last batch: {spent.last_productive} of {spent.last_total} returned something. " + NARROW
    elif spent.last_total:
        batch = f"Last batch: {spent.last_productive} of {spent.last_total} returned something. " + PARALLEL
    else:
        batch = PARALLEL
    return YIELD_NOTE.format(
        requests=requests, max_requests=max_requests,
        productive=max(max_productive - spent.productive, 0), max_productive=max_productive,
        misses=max(max_misses - spent.misses, 0), max_misses=max_misses, batch=batch,
    )


class BudgetNotes:
    """History processor for one agent run: end the newest request with a budget note."""

    def __init__(self, max_requests: int, max_tool_calls: int, max_misses: int | None = None) -> None:
        self.max_requests = max_requests
        self.max_tool_calls = max_tool_calls
        self.max_misses = max_misses

    def __call__(self, ctx: RunContext[Any], messages: list[ModelMessage]) -> list[ModelMessage]:
        if not messages or not isinstance(last := messages[-1], ModelRequest):
            return messages
        if any(isinstance(part, UserPromptPart) and isinstance(part.content, str)
               and part.content.startswith(NOTE_PREFIX) for part in last.parts):
            return messages  # already noted
        if self.max_misses is None:
            note = budget_note(ctx.usage.requests, ctx.usage.tool_calls, self.max_requests, self.max_tool_calls)
        else:
            note = yield_note(ctx.usage.requests, tool_yield(messages), self.max_requests, self.max_tool_calls,
                              self.max_misses)
        return [*messages[:-1], replace(last, parts=[*last.parts, UserPromptPart(note)])]


# How far past a route's max_tool_calls a loop with budget notes may go: its tools are withdrawn once it
# has used max_tool_calls, but a parallel batch asked for just before then still runs, instead of the
# whole loop failing on the limit and a salvage call writing its result. The sixth settings-study pilot's
# broad scout failed that way, on a batch that took it past 48 tool calls at request 17.
TOOL_BATCH_SLACK = 12


def withdraw_tools_when_spent(max_requests: int, max_tool_calls: int,
                              max_misses: int | None = None) -> PrepareTools[Any]:
    """Offer no tools on a loop's last request, or once a budget is spent, so it returns its result.

    The model then writes its result with its whole history in view, where a salvage call after a limit
    sees tool output cut to fit, and costs a call of its own. The budget note says the same thing in words.
    With `max_misses`, tools go once productive calls or misses are spent; otherwise once every tool call
    counts up to `max_tool_calls`.
    """
    async def prepare(ctx: RunContext[Any], tool_defs: list[ToolDefinition]) -> list[ToolDefinition]:
        if max_requests - ctx.usage.requests <= 1:
            return []
        if max_misses is None:
            spent = ctx.usage.tool_calls >= max_tool_calls
        else:
            counted = tool_yield(ctx.messages)
            spent = counted.productive >= max_tool_calls or counted.misses >= max_misses
        return [] if spent else tool_defs

    return PrepareTools(prepare)

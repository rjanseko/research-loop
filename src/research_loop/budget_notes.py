"""Optional budget notes for research tool loops.

A scout or deep dive is stopped by its route's request and tool-call limits, which it cannot
see. With `ResearchConfig.budget_notes` naming its role, each model request ends with a short
note giving what is left of both, and inviting parallel tool calls, after the budget tracker of
BATS (Liu et al. 2025, arXiv:2511.17006). The note is appended to the newest request only, and
PydanticAI keeps the processed history, so earlier notes stay as they were sent and the cached
prompt prefix survives from one request to the next.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.messages import ModelMessage, ModelRequest, UserPromptPart

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


class BudgetNotes:
    """History processor for one agent run: end the newest request with a budget note."""

    def __init__(self, max_requests: int, max_tool_calls: int) -> None:
        self.max_requests = max_requests
        self.max_tool_calls = max_tool_calls

    def __call__(self, ctx: RunContext[Any], messages: list[ModelMessage]) -> list[ModelMessage]:
        if not messages or not isinstance(last := messages[-1], ModelRequest):
            return messages
        if any(isinstance(part, UserPromptPart) and isinstance(part.content, str)
               and part.content.startswith(NOTE_PREFIX) for part in last.parts):
            return messages  # already noted
        note = budget_note(ctx.usage.requests, ctx.usage.tool_calls, self.max_requests, self.max_tool_calls)
        return [*messages[:-1], replace(last, parts=[*last.parts, UserPromptPart(note)])]

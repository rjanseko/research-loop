"""Budget notes for a scout's tool loop, and withdrawing its tools once a budget is spent.

A loop cannot see its limits, so it runs into them: in five settings-study pilots every research loop
did, and a separate salvage call had to write its result. With a note ending each request (after the
budget tracker of BATS, Liu et al. 2025, arXiv:2511.17006), 9 of 10 loops returned their result on
their own. The budget counts productive calls (a search with results, a fetch with text, a scholarly
call with works) apart from misses (an empty search, an HTTP error, a blocked URL), so a loop that is
only failing stops without starving one that is working.

The note is appended to the newest request only, and PydanticAI keeps the processed history, so
earlier notes stay as they were sent and the cached prompt prefix survives between requests. When the
research deadline is closer than one request timeout, the note says so and the tools are withdrawn:
a request still running at the deadline is cut off and keeps no claims.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.capabilities import Hooks, PrepareTools, ProcessHistory
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.tools import ToolDefinition

from .tools import productive

NOTE_PREFIX = "[Research budget] "
LAST_REQUEST_NOTE = NOTE_PREFIX + "This is your last model request. Do not call tools; return your result now."
DEADLINE_NOTE = NOTE_PREFIX + "The research deadline is close. Do not call tools; return your result now."
PRODUCTIVE_SPENT_NOTE = (
    NOTE_PREFIX + "No productive tool calls are left, and {requests} of {max_requests} model requests are. "
    "Do not call tools; return your result now."
)
MISS_SPENT_NOTE = (
    NOTE_PREFIX + "The miss limit is spent, and {requests} of {max_requests} model requests are. "
    "Do not call tools; return your result now."
)
NOTE = (
    NOTE_PREFIX + "{requests} of {max_requests} model requests left, this request included. "
    "{productive} of {max_productive} productive tool calls and {misses} of {max_misses} misses left. "
    "{batch}"
    "Return your result while a request is left: a run that reaches a limit is cut off."
)
PARALLEL = "Put independent searches and fetches in one turn as parallel tool calls. "
NARROW = (
    "The last batch mostly missed. Use one or two broader searches, without quotes or site: operators, "
    "and fetch only addresses a search returned. "
)


# How far past its productive and miss budgets a loop's framework limit sits: tools are withdrawn once a
# budget is spent, but a parallel batch asked for just before then still runs, instead of the whole loop
# failing on the limit. The sixth settings-study pilot's broad scout failed that way at request 17.
TOOL_BATCH_SLACK = 12
DROPPED_NOTE = (
    NOTE_PREFIX + "Only the first {kept} of the {asked} tool calls in your last turn ran; the rest were dropped "
    "because they exceeded your tool-call limit."
)


@dataclass(frozen=True)
class ToolYield:
    productive: int = 0
    misses: int = 0
    last_productive: int = 0
    last_total: int = 0


def tool_yield(messages: list[ModelMessage]) -> ToolYield:
    """Productive calls and misses so far, and how the latest batch of returns did."""
    spent = ToolYield()
    for message in messages:
        if not isinstance(message, ModelRequest):
            continue
        batch = [productive(part.tool_name, part.content) for part in message.parts if isinstance(part, ToolReturnPart)]
        batch = [kind for kind in batch if kind is not None]
        if batch:
            found = sum(batch)
            spent = ToolYield(spent.productive + found, spent.misses + len(batch) - found, found, len(batch))
    return spent


@dataclass(frozen=True)
class LoopBudget:
    max_requests: int
    max_productive: int
    max_misses: int
    # Seconds left until the research deadline, and how many must remain for another tool-using request.
    # The scout sets `return_within` to its request timeout: a request started with less than that left
    # would be cut off, and a cutoff keeps no claims.
    time_left: Callable[[], float] | None = None
    return_within: float = 0

    @property
    def tool_call_limit(self) -> int:
        """The framework's limit on the loop's tool calls: its budgets plus `TOOL_BATCH_SLACK`."""
        return self.max_productive + self.max_misses + TOOL_BATCH_SLACK

    def closing(self) -> bool:
        """Whether the next request must be the result so it can finish before the research deadline."""
        return self.time_left is not None and self.time_left() <= self.return_within

    def note(self, requests_used: int, spent: ToolYield) -> str:
        """The note for the next request, given what the loop has used so far."""
        if self.closing():
            return DEADLINE_NOTE
        requests = max(self.max_requests - requests_used, 0)
        if requests <= 1:
            return LAST_REQUEST_NOTE
        if spent.productive >= self.max_productive:
            return PRODUCTIVE_SPENT_NOTE.format(requests=requests, max_requests=self.max_requests)
        if spent.misses >= self.max_misses:
            return MISS_SPENT_NOTE.format(requests=requests, max_requests=self.max_requests)
        if spent.last_total:
            batch = (f"Last batch: {spent.last_productive} of {spent.last_total} returned something. "
                     + (NARROW if spent.last_productive * 2 < spent.last_total else PARALLEL))
        else:
            batch = PARALLEL
        return NOTE.format(requests=requests, max_requests=self.max_requests,
                           productive=max(self.max_productive - spent.productive, 0), max_productive=self.max_productive,
                           misses=max(self.max_misses - spent.misses, 0), max_misses=self.max_misses, batch=batch)

    def spent(self, requests_used: int, messages: list[ModelMessage]) -> bool:
        """Whether the loop is on its last request, out of time, or has spent its productive calls or misses."""
        if self.closing():
            return True
        counted = tool_yield(messages)
        return (self.max_requests - requests_used <= 1
                or counted.productive >= self.max_productive or counted.misses >= self.max_misses)

    def finish_reason(self, requests: int, messages: list[ModelMessage]) -> str:
        """Why a loop that returned its result stopped: a spent budget, the deadline, or on its own."""
        if any(isinstance(part, UserPromptPart) and part.content == DEADLINE_NOTE
               for message in messages if isinstance(message, ModelRequest) for part in message.parts):
            return "returned because the research deadline was close"
        counted = tool_yield(messages)
        if counted.productive >= self.max_productive:
            return "returned after its productive calls were spent"
        if counted.misses >= self.max_misses:
            return "returned after its misses were spent"
        if requests >= self.max_requests:
            return "returned on its last request"
        return "returned on its own"

    def capabilities(self) -> list[Any]:
        """A note on each request, no tools once a budget is spent, and no more tool calls in a turn than
        the limit leaves, so the loop returns its result with its whole history in view instead of being
        cut off. PydanticAI refuses a whole turn that would pass the limit: one Luna turn asked for 165
        searches against 40 and lost its question."""
        dropped: list[tuple[int, int]] = []  # (kept, asked) for the latest trimmed turn, until it is noted

        def add_note(ctx: RunContext[Any], messages: list[ModelMessage]) -> list[ModelMessage]:
            if not messages or not isinstance(last := messages[-1], ModelRequest):
                return messages
            if any(isinstance(part, UserPromptPart) and isinstance(part.content, str)
                   and part.content.startswith(NOTE_PREFIX) for part in last.parts):
                return messages  # already noted
            notes = [UserPromptPart(DROPPED_NOTE.format(kept=kept, asked=asked)) for kept, asked in dropped]
            dropped.clear()
            notes.append(UserPromptPart(self.note(ctx.usage.requests, tool_yield(messages))))
            return [*messages[:-1], replace(last, parts=[*last.parts, *notes])]

        async def withdraw(ctx: RunContext[Any], tool_defs: list[ToolDefinition]) -> list[ToolDefinition]:
            return [] if self.spent(ctx.usage.requests, ctx.messages) else tool_defs

        def trim(ctx: RunContext[Any], /, *, request_context: ModelRequestContext,
                 response: ModelResponse) -> ModelResponse:
            names = {tool.name for tool in request_context.model_request_parameters.function_tools}
            calls = [part for part in response.parts if isinstance(part, ToolCallPart) and part.tool_name in names]
            left = self.tool_call_limit - ctx.usage.tool_calls
            if len(calls) <= left or left <= 0:
                return response
            excess = {id(part) for part in calls[left:]}
            dropped.append((left, len(calls)))
            return replace(response, parts=[part for part in response.parts if id(part) not in excess])

        return [ProcessHistory(add_note), PrepareTools(withdraw), Hooks(after_model_request=trim)]


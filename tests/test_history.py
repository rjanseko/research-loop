from __future__ import annotations

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from research_loop.budget_notes import tool_yield
from research_loop.history import (
    KEEP_CHARS,
    TRIM_AFTER,
    TRIMMED_PAGE,
    TrimmedHistoryModel,
    trimmed,
)
from research_loop.tools import labeled_texts

# Most of a full 12,000-character fetch window: three fit under KEEP_CHARS, and a fourth does not.
BIG = 15_000


def _page(n: int, start: int = 0, size: int = BIG) -> dict:
    text = f"Page {n} says the effect was 12%. ".ljust(size, "x")
    return {"access": "full_text", "url": f"https://a.test/{n}", "text": text, "start": start,
            "total_chars": size, "truncated": False, "next_start": None, "extraction": "trafilatura",
            "content_sha256": "abc"}


def _turn(n: int, size: int = BIG) -> list:
    return [ModelResponse(parts=[ToolCallPart("fetch", {"url": f"https://a.test/{n}"}, tool_call_id=f"f{n}"),
                                 ToolCallPart("web_search", {"query": "q"}, tool_call_id=f"s{n}")]),
            ModelRequest(parts=[ToolReturnPart("fetch", _page(n, size=size), tool_call_id=f"f{n}"),
                                ToolReturnPart("web_search", {"access": "snippet", "results": [{"url": "u", "snippet": "s"}]},
                                               tool_call_id=f"s{n}")])]


def _history(turns: int, size: int = BIG) -> list:
    return [ModelRequest(parts=[UserPromptPart("research")]), *[m for n in range(turns) for m in _turn(n, size)]]


def _texts(messages: list) -> list[str | None]:
    """Each fetched page's name, "Page 2", or None where its text was trimmed."""
    return [part.content["text"][:6] if "text" in part.content else None
            for message in messages if isinstance(message, ModelRequest)
            for part in message.parts if isinstance(part, ToolReturnPart) and part.tool_name == "fetch"]


def test_the_oldest_pages_leave_view_once_the_newest_fill_the_limit_and_stay_out() -> None:
    history = _history(5)
    sent = trimmed(history)
    # The newest three pages fit in KEEP_CHARS; the fourth newest would not, so it and all older are stubs.
    assert 3 * BIG <= KEEP_CHARS < 4 * BIG
    assert _texts(sent) == [None, None, "Page 2", "Page 3", "Page 4"]
    stub = next(p for m in sent if isinstance(m, ModelRequest) for p in m.parts
                if isinstance(p, ToolReturnPart) and p.tool_name == "fetch")
    assert stub.content == {"access": "full_text", "url": "https://a.test/0", "start": 0, "total_chars": BIG,
                            "truncated": False, "next_start": None, "extraction": "trafilatura",
                            "text_removed": TRIMMED_PAGE}
    # Search results, the prompt, and the caller's history are untouched.
    assert sum(bool(isinstance(p, ToolReturnPart) and p.tool_name == "web_search" and p.content.get("results"))
               for m in sent if isinstance(m, ModelRequest) for p in m.parts) == 5
    assert _texts(history) == [f"Page {n}" for n in range(5)]
    # A page trimmed from one request is trimmed from every later one, so no request is larger than the last
    # plus what was added since (the budget guard's bound), and the cached prefix holds.
    later = trimmed([*history, *_turn(5)])
    assert _texts(later) == [None, None, None, "Page 3", "Page 4", "Page 5"] and later[:5] == sent[:5]


def test_a_short_loop_is_never_trimmed_and_the_latest_pages_always_stay() -> None:
    assert _texts(trimmed(_history(8, size=5_000))) == [f"Page {n}" for n in range(8)]
    # The pages of the latest TRIM_AFTER responses stay even when they alone pass the limit.
    huge = _history(3, size=KEEP_CHARS)
    assert TRIM_AFTER == 2 and _texts(trimmed(huge)) == [None, "Page 1", "Page 2"]


@pytest.mark.asyncio
async def test_the_model_sees_stubs_while_the_kept_history_keeps_every_page() -> None:
    seen: list[list[str | None]] = []

    def respond(messages, info: AgentInfo) -> ModelResponse:
        seen.append(_texts(messages))
        turn = len(seen)
        if turn <= 4:
            return ModelResponse(parts=[ToolCallPart("fetch", {"url": f"https://a.test/{turn}"})])
        return ModelResponse(parts=[TextPart("result")])

    agent: Agent[None, str] = Agent(output_type=str)

    @agent.tool_plain
    def fetch(url: str, start: int = 0) -> dict:
        return _page(int(url.rsplit("/", 1)[1]), start)

    result = await agent.run("research", model=TrimmedHistoryModel(FunctionModel(respond)))
    assert seen[-1] == [None, "Page 2", "Page 3", "Page 4"]
    # What the quote check reads still has all four pages.
    assert [t.text[:6] for t in labeled_texts(result.all_messages()) if t.access == "full_text"] == [
        f"Page {n}" for n in (1, 2, 3, 4)]


def test_reading_a_page_again_uses_no_budget() -> None:
    history = _history(2)
    again = [ModelResponse(parts=[ToolCallPart("fetch", {"url": "https://a.test/0"}, tool_call_id="r0")]),
             ModelRequest(parts=[ToolReturnPart("fetch", _page(0), tool_call_id="r0"),
                                 ToolReturnPart("fetch", _page(0, start=12000), tool_call_id="r1")])]
    before, after = tool_yield(history), tool_yield([*history, *again])
    # The same window is free; the next window of the same page is a new read.
    assert (after.productive, after.misses) == (before.productive + 1, before.misses)

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
from research_loop.history import TRIM_AFTER, TRIMMED_PAGE, TrimmedHistoryModel, trimmed
from research_loop.tools import labeled_texts


def _page(n: int, start: int = 0) -> dict:
    return {"access": "full_text", "url": f"https://a.test/{n}", "text": f"Page {n} says the effect was 12%.",
            "start": start, "total_chars": 40, "truncated": False, "next_start": None, "extraction": "trafilatura",
            "content_sha256": "abc"}


def _turn(n: int) -> list:
    return [ModelResponse(parts=[ToolCallPart("fetch", {"url": f"https://a.test/{n}"}, tool_call_id=f"f{n}"),
                                 ToolCallPart("web_search", {"query": "q"}, tool_call_id=f"s{n}")]),
            ModelRequest(parts=[ToolReturnPart("fetch", _page(n), tool_call_id=f"f{n}"),
                                ToolReturnPart("web_search", {"access": "snippet", "results": [{"url": "u", "snippet": "s"}]},
                                               tool_call_id=f"s{n}")])]


def _history(turns: int) -> list:
    return [ModelRequest(parts=[UserPromptPart("research")]), *[m for n in range(turns) for m in _turn(n)]]


def _texts(messages: list) -> list[str | None]:
    return [part.content.get("text") for message in messages if isinstance(message, ModelRequest)
            for part in message.parts if isinstance(part, ToolReturnPart) and part.tool_name == "fetch"]


def test_page_text_leaves_view_after_two_responses_and_stays_out() -> None:
    history = _history(4)
    sent = trimmed(history)
    # Pages 0 and 1 have had two or more responses since; pages 2 and 3 are still in view.
    assert _texts(sent) == [None, None, "Page 2 says the effect was 12%.", "Page 3 says the effect was 12%."]
    stub = next(p for m in sent if isinstance(m, ModelRequest) for p in m.parts
                if isinstance(p, ToolReturnPart) and p.tool_name == "fetch")
    assert stub.content == {"access": "full_text", "url": "https://a.test/0", "start": 0, "total_chars": 40,
                            "truncated": False, "next_start": None, "extraction": "trafilatura",
                            "text_removed": TRIMMED_PAGE}
    # Search results, the prompt, and the caller's history are untouched.
    assert sum(bool(isinstance(p, ToolReturnPart) and p.tool_name == "web_search" and p.content.get("results"))
               for m in sent if isinstance(m, ModelRequest) for p in m.parts) == 4
    assert _texts(history) == [f"Page {n} says the effect was 12%." for n in range(4)]
    # A page trimmed from one request is trimmed from every later one, so the cached prefix holds.
    assert trimmed([*history, *_turn(4)])[:5] == sent[:5]
    assert TRIM_AFTER == 2


@pytest.mark.asyncio
async def test_the_model_sees_stubs_while_the_kept_history_keeps_every_page() -> None:
    seen: list[list[str | None]] = []

    def respond(messages, info: AgentInfo) -> ModelResponse:
        seen.append(_texts(messages))
        turn = len(seen)
        if turn <= 3:
            return ModelResponse(parts=[ToolCallPart("fetch", {"url": f"https://a.test/{turn}"})])
        return ModelResponse(parts=[TextPart("result")])

    agent: Agent[None, str] = Agent(output_type=str)

    @agent.tool_plain
    def fetch(url: str, start: int = 0) -> dict:
        return _page(int(url.rsplit("/", 1)[1]), start)

    result = await agent.run("research", model=TrimmedHistoryModel(FunctionModel(respond)))
    # Page 1 was sent with the requests that answered it and the next; page 2 is on its second.
    assert seen[-1] == [None, "Page 2 says the effect was 12%.", "Page 3 says the effect was 12%."]
    # What the quote check reads still has all three pages.
    assert [t.text for t in labeled_texts(result.all_messages()) if t.access == "full_text"] == [
        f"Page {n} says the effect was 12%." for n in (1, 2, 3)]


def test_reading_a_page_again_uses_no_budget() -> None:
    history = _history(2)
    again = [ModelResponse(parts=[ToolCallPart("fetch", {"url": "https://a.test/0"}, tool_call_id="r0")]),
             ModelRequest(parts=[ToolReturnPart("fetch", _page(0), tool_call_id="r0"),
                                 ToolReturnPart("fetch", _page(0, start=12000), tool_call_id="r1")])]
    before, after = tool_yield(history), tool_yield([*history, *again])
    # The same window is free; the next window of the same page is a new read.
    assert (after.productive, after.misses) == (before.productive + 1, before.misses)

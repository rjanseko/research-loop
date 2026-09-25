"""The research tools a scout calls, and what code reads back from their results.

Every result says how much of a source it holds in `access`: `snippet` for search results,
`metadata` or `abstract` for scholarly records, `full_text` for a fetched page or PDF window.
After the call, `labeled_texts` rebuilds those labeled texts from the call's messages for the
evidence checks (evidence.py), and `tool_outcomes` lists the searches, pages read, and sources
that could not be reached. The tools only read public sources; none has a side effect.
"""
from __future__ import annotations

import json
import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any

from pydantic_ai import FunctionToolset, RunContext
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.toolsets import ToolsetTool, WrapperToolset

from .evidence import ToolText, identity_keys
from .schemas import UnreachedSource
from .scholar import ScholarClient, ScholarWork
from .web import WebAcquisition, WebSearch

WEB_SEARCH, FETCH, SCHOLAR_SEARCH, SCHOLAR_GET = "web_search", "fetch", "scholar_search", "scholar_get"
RESEARCH_TOOLS = frozenset({WEB_SEARCH, FETCH, SCHOLAR_SEARCH, SCHOLAR_GET})
# Authors listed per scholarly record; the rest are counted.
_AUTHORS_SHOWN = 5


def _work(work: ScholarWork) -> dict[str, Any]:
    record = work.model_dump(exclude_none=True, exclude={"authors", "record_updates"})
    if work.authors:
        record["authors"] = work.authors[:_AUTHORS_SHOWN] + (
            [f"and {len(work.authors) - _AUTHORS_SHOWN} more"] if len(work.authors) > _AUTHORS_SHOWN else [])
    if work.record_updates:
        record["record_updates"] = work.record_updates
    return {"access": "abstract" if work.abstract else "metadata", **record}


def research_toolset(search: WebSearch, pages: WebAcquisition, scholar: ScholarClient) -> FunctionToolset:
    async def web_search(query: str) -> dict[str, Any]:
        """Search the web. Returns titles, URLs, and short snippets (access: snippet); fetch a result to read it."""
        result = await search.search(query)
        return {"access": "snippet", **result} if "results" in result else result

    async def fetch(url: str, start: int = 0) -> dict[str, Any]:
        """Read a public HTTPS page or PDF: up to 12,000 characters of its text from `start` (access: full_text).

        When the result has `next_start`, call again with start=next_start to read further.
        """
        result = await pages.fetch(url, start=start)
        return {"access": "full_text", **result} if "text" in result else result

    async def scholar_search(query: str, year_from: int | None = None, year_to: int | None = None,
                             limit: int = 5) -> dict[str, Any]:
        """Search scholarly records (OpenAlex and arXiv). Each work says its access: `abstract` when it
        includes one, else `metadata`. Preprint and published records are separate works."""
        response = await scholar.search(query, year_from, year_to, limit)
        return {"works": [_work(w) for w in response.works], "provider_errors": response.provider_errors,
                "truncated": response.truncated}

    async def scholar_get(identifier: str) -> dict[str, Any]:
        """Look up one work by DOI (10.xxx), OpenAlex ID (W123), or arXiv ID (2310.06770)."""
        response = await scholar.get(identifier)
        return {"works": [_work(w) for w in response.works], "provider_errors": response.provider_errors}

    return FunctionToolset(tools=[web_search, fetch, scholar_search, scholar_get])


@dataclass
class TimedToolset(WrapperToolset[Any]):
    """The research tools, timing each call by the question its scout researches.

    A scout's tool time is the union of its calls' intervals, so a parallel batch counts once; the rest of
    its call's time is model time. Every copy PydanticAI makes for a run shares one set of intervals.
    """

    intervals: dict[str, list[tuple[float, float]]] = field(default_factory=dict)

    async def call_tool(self, name: str, tool_args: dict[str, Any], ctx: RunContext[Any],
                        tool: ToolsetTool[Any]) -> Any:
        started = time.monotonic()
        try:
            return await self.wrapped.call_tool(name, tool_args, ctx, tool)
        finally:
            question = getattr(ctx.deps, "question", None)
            self.intervals.setdefault(getattr(question, "id", ""), []).append((started, time.monotonic()))

    def seconds(self, question_id: str) -> float:
        """Wall-clock seconds the tools of `question_id`'s scout ran, overlaps counted once."""
        total, end = 0.0, float("-inf")
        for start, stop in sorted(self.intervals.get(question_id, [])):
            if stop > end:
                total += stop - max(start, end)
                end = stop
        return total


def _data(content: Any) -> Any:
    if isinstance(content, str) and content[:1] in "{[":
        try:
            return json.loads(content)
        except ValueError:
            return content
    return content


def _returns(messages: Iterable[ModelMessage]) -> Iterator[ToolReturnPart]:
    for message in messages:
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, ToolReturnPart) and part.tool_name in RESEARCH_TOOLS:
                    yield part


def labeled_texts(messages: Iterable[ModelMessage]) -> list[ToolText]:
    """Every source the research tools returned in `messages`, with its access level, identity, and text."""
    texts: list[ToolText] = []
    for part in _returns(messages):
        data = _data(part.content)
        if not isinstance(data, dict):
            continue
        if part.tool_name == WEB_SEARCH:
            for item in data.get("results") or []:
                texts.append(ToolText("snippet", identity_keys(url=item.get("url")),
                                      f"{item.get('title', '')}\n{item.get('snippet', '')}"))
        elif part.tool_name == FETCH and data.get("text"):
            texts.append(ToolText("full_text", identity_keys(url=data.get("url")), data["text"]))
        else:
            for work in data.get("works") or []:
                keys = identity_keys(url=work.get("url"), doi=work.get("doi"), arxiv_id=work.get("arxiv_id"))
                if work.get("openalex_id"):
                    keys |= identity_keys(url=work["openalex_id"])
                texts.append(ToolText("abstract" if work.get("abstract") else "metadata", keys,
                                      f"{work.get('title', '')}\n{work.get('abstract') or ''}"))
    return texts


def productive(tool_name: str, content: Any) -> bool | None:
    """Whether a research tool's return found something: a search with results, a fetch with text, a
    scholarly call with works. None for other tools, such as the output tool."""
    if tool_name not in RESEARCH_TOOLS:
        return None
    data = _data(content)
    if not isinstance(data, dict) or data.get("error"):
        return False
    if tool_name == WEB_SEARCH:
        return bool(data.get("results"))
    if tool_name == FETCH:
        return bool(data.get("text"))
    return bool(data.get("works"))


@dataclass
class ToolOutcomes:
    searches: list[str] = field(default_factory=list)
    pages_read: list[str] = field(default_factory=list)
    unreached: list[UnreachedSource] = field(default_factory=list)
    calls: int = 0
    misses: int = 0


def tool_outcomes(messages: Iterable[ModelMessage]) -> ToolOutcomes:
    """What a research call searched for, which pages it read, and what it could not reach."""
    messages = list(messages)
    args: dict[str, dict[str, Any]] = {}
    for message in messages:
        if isinstance(message, ModelResponse):
            for part in message.parts:
                if isinstance(part, ToolCallPart) and part.tool_name in RESEARCH_TOOLS:
                    try:
                        args[part.tool_call_id] = part.args_as_dict()
                    except ValueError:
                        args[part.tool_call_id] = {}
    outcomes = ToolOutcomes()
    for part in _returns(messages):
        call = args.get(part.tool_call_id, {})
        data = _data(part.content)
        outcomes.calls += 1
        if not productive(part.tool_name, part.content):
            outcomes.misses += 1
        if part.tool_name in (WEB_SEARCH, SCHOLAR_SEARCH) and call.get("query"):
            outcomes.searches.append(str(call["query"]))
        if not isinstance(data, dict):
            continue
        if part.tool_name == FETCH:
            url = str(data.get("url") or call.get("url") or "")
            if data.get("text"):
                if url not in outcomes.pages_read:
                    outcomes.pages_read.append(url)
            elif data.get("error"):
                reason = data["error"] + (f" {data['status']}" if data.get("status") else "")
                outcomes.unreached.append(UnreachedSource(target=url, reason=reason))
        elif data.get("error"):
            outcomes.unreached.append(UnreachedSource(target=f"{part.tool_name}: {call.get('query', '')}",
                                                      reason=str(data["error"])))
        elif part.tool_name == SCHOLAR_GET and not data.get("works"):
            outcomes.unreached.append(UnreachedSource(target=str(call.get("identifier", "")),
                                                      reason=", ".join(data.get("provider_errors") or ["not found"])))
    return outcomes

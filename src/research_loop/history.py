"""What a scout's model is sent of its own history: once a loop has read a lot, its oldest pages and search
snippets leave view.

Each request of a tool loop resends the loop's whole history. In the first two deep example runs
(d8c8198e and 2c66e8bd), fetched page text was 63% of the scouts' tool results, and the scouts
together sent only about 115,000 tokens a minute under Luna's token rate limit, so each managed six to
nine requests. Now a request keeps the newest `KEEP_CHARS` of page text, and always the pages of the
latest `TRIM_AFTER` responses; older pages are replaced by `TRIMMED_PAGE`, which tells the scout to
fetch the page again before quoting it. The run's fetch memo serves the re-read without a download, and
the loop budget does not count it (budget_notes.py).

Search results are trimmed the same way past `SEARCH_KEEP_CHARS` of snippets, keeping each result's title
and address: Exa's highlights, even capped, are several times DuckDuckGo's snippets.

A short loop is never trimmed. Trimming every page two responses after it was read cost quotes: in the
cheap check 6d29289d, 46 of 66 quotes verified against 54 of 55 without trimming, and the scouts that
lost the most never re-read a page, among them a four-request loop that trimming saved little.

Only the request changes. The history PydanticAI keeps, and so the text the quote check reads
(tools.labeled_texts), still holds every page. Pages are trimmed oldest first, and a page once trimmed
stays trimmed in later requests, so each request is at most the last one plus what was added since:
the budget guard's bound (study_budget.py) and the token pacer's estimate still hold, and the cached
prompt prefix survives up to the oldest page trimmed by the latest request.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import Any

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolReturnPart,
)
from pydantic_ai.models import ModelRequestParameters, StreamedResponse
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings

from .tools import FETCH, WEB_SEARCH, fetched_text, searched_snippets

# Page text a request keeps, newest first: three full fetch windows of 40,000 characters (acquisition.py),
# about 30,000 tokens. Luna allows this account 2,000,000 tokens a minute, so the limit is set by what a
# scout can use, not by the rate limit.
KEEP_CHARS = 120_000
# Search-result snippets a request keeps, newest first: about seven DuckDuckGo searches, or three Exa
# searches with their highlights capped (web.EXA_HIGHLIGHT_CHARS). Older results keep their titles and
# addresses, so a scout can still fetch them.
SEARCH_KEEP_CHARS = 16_000
# Responses a page's text is always sent with: the one that reads it and the next, so a scout can compare
# pages from one batch before deciding what to quote.
TRIM_AFTER = 2
TRIMMED_PAGE = (
    "This page's text has left your view to save space. To quote it, fetch this url again with the same "
    "start first: the re-read is instant and does not use your budget. Quote only text you can see."
)
TRIMMED_SEARCH = "These results' snippets have left your view to save space; fetch a result to read it."


def trimmed(messages: list[ModelMessage]) -> list[ModelMessage]:
    """`messages` with old tool text replaced, newest kept first: the text of every fetched page past the
    newest `KEEP_CHARS`, and the snippets of every search past the newest `SEARCH_KEEP_CHARS`, except what
    the latest `TRIM_AFTER` responses returned. Once one of a kind is trimmed, every older one is too."""
    after = 0
    kept = {FETCH: 0, WEB_SEARCH: 0}
    cut = {FETCH: False, WEB_SEARCH: False}
    out: list[ModelMessage] = []
    for message in reversed(messages):
        if isinstance(message, ModelResponse):
            after += 1
        elif isinstance(message, ModelRequest):
            parts = []
            for part in reversed(message.parts):
                found = _trimmable(part)
                if found is None:
                    parts.append(part)
                    continue
                tool, data, size = found
                if after < TRIM_AFTER or (not cut[tool] and kept[tool] + size <= _LIMITS[tool]):
                    kept[tool] += size
                    parts.append(part)
                else:
                    cut[tool] = True
                    parts.append(replace(part, content=_STUBS[tool](data)))
            parts.reverse()
            if any(new is not old for new, old in zip(parts, message.parts, strict=True)):
                message = replace(message, parts=parts)
        out.append(message)
    out.reverse()
    return out


def _trimmable(part: Any) -> tuple[str, dict[str, Any], int] | None:
    """A fetched page or a search with snippets, its data, and how much of its text trimming would remove."""
    if not isinstance(part, ToolReturnPart):
        return None
    if part.tool_name == FETCH and (page := fetched_text(part.content)) is not None:
        return FETCH, page, len(page["text"])
    if part.tool_name == WEB_SEARCH and (found := searched_snippets(part.content)) is not None:
        return WEB_SEARCH, found, sum(len(r.get("snippet") or "") for r in found["results"] if isinstance(r, dict))
    return None


def _stub_page(page: dict[str, Any]) -> dict[str, Any]:
    kept = {key: value for key, value in page.items() if key not in ("text", "content_sha256")}
    return {**kept, "text_removed": TRIMMED_PAGE}


def _stub_search(found: dict[str, Any]) -> dict[str, Any]:
    results = [{key: r[key] for key in ("title", "url") if key in r} for r in found["results"] if isinstance(r, dict)]
    return {**found, "results": results, "snippets_removed": TRIMMED_SEARCH}


_LIMITS = {FETCH: KEEP_CHARS, WEB_SEARCH: SEARCH_KEEP_CHARS}
_STUBS = {FETCH: _stub_page, WEB_SEARCH: _stub_search}


class TrimmedHistoryModel(WrapperModel):
    """Sends the wrapped model each request with old page text trimmed (`trimmed`)."""

    async def request(self, messages: list[ModelMessage], model_settings: ModelSettings | None,
                      model_request_parameters: ModelRequestParameters) -> Any:
        return await self.wrapped.request(trimmed(messages), model_settings, model_request_parameters)

    @asynccontextmanager
    async def request_stream(self, messages: list[ModelMessage], model_settings: ModelSettings | None,
                             model_request_parameters: ModelRequestParameters,
                             run_context: Any = None) -> AsyncIterator[StreamedResponse]:
        async with self.wrapped.request_stream(trimmed(messages), model_settings, model_request_parameters,
                                               run_context) as stream:
            yield stream

"""What a scout's model is sent of its own history: once a loop has read a lot, its oldest pages leave view.

Each request of a tool loop resends the loop's whole history. In the first two deep example runs
(d8c8198e and 2c66e8bd), fetched page text was 63% of the scouts' tool results, and the scouts
together sent only about 115,000 tokens a minute under Luna's token rate limit, so each managed six to
nine requests. Now a request keeps the newest `KEEP_CHARS` of page text, and always the pages of the
latest `TRIM_AFTER` responses; older pages are replaced by `TRIMMED_PAGE`, which tells the scout to
fetch the page again before quoting it. The run's fetch memo serves the re-read without a download, and
the loop budget does not count it (budget_notes.py).

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

from .tools import FETCH, fetched_text

# Page text a request keeps, newest first: four full fetch windows.
KEEP_CHARS = 48_000
# Responses a page's text is always sent with: the one that reads it and the next, so a scout can compare
# pages from one batch before deciding what to quote.
TRIM_AFTER = 2
TRIMMED_PAGE = (
    "This page's text has left your view to save space. To quote it, fetch this url again with the same "
    "start first: the re-read is instant and does not use your budget. Quote only text you can see."
)


def trimmed(messages: list[ModelMessage]) -> list[ModelMessage]:
    """`messages` with the text of every fetched page past the newest `KEEP_CHARS` replaced, except pages
    from the latest `TRIM_AFTER` responses. Once one page is trimmed, every older page is too."""
    after = kept = 0
    cut = False
    out: list[ModelMessage] = []
    for message in reversed(messages):
        if isinstance(message, ModelResponse):
            after += 1
        elif isinstance(message, ModelRequest):
            parts = []
            for part in reversed(message.parts):
                if not isinstance(part, ToolReturnPart) or part.tool_name != FETCH or (
                        page := fetched_text(part.content)) is None:
                    parts.append(part)
                    continue
                size = len(page["text"])
                if after < TRIM_AFTER or (not cut and kept + size <= KEEP_CHARS):
                    kept += size
                    parts.append(part)
                else:
                    cut = True
                    parts.append(_stub(part, page))
            parts.reverse()
            if any(new is not old for new, old in zip(parts, message.parts, strict=True)):
                message = replace(message, parts=parts)
        out.append(message)
    out.reverse()
    return out


def _stub(part: ToolReturnPart, page: dict[str, Any]) -> ToolReturnPart:
    kept = {key: value for key, value in page.items() if key not in ("text", "content_sha256")}
    return replace(part, content={**kept, "text_removed": TRIMMED_PAGE})


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

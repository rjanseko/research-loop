"""What a scout's model is sent of its own history: a page's text leaves its view once it has been read.

Each request of a tool loop resends the loop's whole history. In the first two deep example runs
(d8c8198e and 2c66e8bd), fetched page text was 63% of the scouts' tool results, and the scouts
together sent only about 115,000 tokens a minute under Luna's token rate limit, so each managed six to
nine requests. Now a fetched page's text is sent with `TRIM_AFTER` responses and then replaced by
`TRIMMED_PAGE`, which tells the scout to fetch the page again before quoting it. The run's fetch memo
serves the re-read without a download, and the loop budget does not count it (budget_notes.py).

Only the request changes. The history PydanticAI keeps, and so the text the quote check reads
(tools.labeled_texts), still holds every page. A page once trimmed stays trimmed in later requests, so
the cached prompt prefix survives up to the oldest page trimmed by the latest request.
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

# Responses a page's text is sent with: the one that reads it and the next, so a scout can compare
# pages from one batch before deciding what to quote.
TRIM_AFTER = 2
TRIMMED_PAGE = (
    "This page's text has left your view to save space. To quote it, fetch this url again with the same "
    "start first: the re-read is instant and does not use your budget. Quote only text you can see."
)


def trimmed(messages: list[ModelMessage]) -> list[ModelMessage]:
    """`messages` with the text of each fetched page older than `TRIM_AFTER` responses replaced."""
    after = 0
    out: list[ModelMessage] = []
    for message in reversed(messages):
        if isinstance(message, ModelResponse):
            after += 1
        elif isinstance(message, ModelRequest) and after >= TRIM_AFTER:
            parts = [_stub(part) for part in message.parts]
            if any(new is not old for new, old in zip(parts, message.parts, strict=True)):
                message = replace(message, parts=parts)
        out.append(message)
    out.reverse()
    return out


def _stub(part: Any) -> Any:
    if not isinstance(part, ToolReturnPart) or part.tool_name != FETCH or (data := fetched_text(part.content)) is None:
        return part
    kept = {key: value for key, value in data.items() if key not in ("text", "content_sha256")}
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

"""Anthropic citations for the synthesizer, which on this design is always a Claude model.

The synthesizer receives each source's supporting evidence as one search result: its `source` is the
source's ID (s4), and each passage is one text block that opens with the claim it supports
(`EvidenceLedger.passages`). Claude cites whole blocks, and each citation names the search result and its
block range, so code, not the model, maps every cited span of the report to the sources and claims behind
it, and writes the report's inline [sN] citations and its claim list.

PydanticAI (2.51 and before) neither sends search_result blocks nor keeps the citations Claude returns
(pydantic/pydantic-ai#2128). `CitingAnthropicModel` does both: a `TextContent` whose metadata is a search
result goes to Claude as a search_result block, and each text block's citations are kept in its
`TextPart.provider_details`. Citations cannot be combined with structured output, so the report comes back
as tagged text that `cited_report` parses into a `FinalReport`.
"""
from __future__ import annotations

import re
from collections.abc import AsyncGenerator, AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from typing import Any, cast

from pydantic_ai.exceptions import UserError
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    TextContent,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.models import (
    ModelRequestParameters,
    StreamedResponse,
    check_allow_model_requests,
)
from pydantic_ai.models.anthropic import AnthropicModel, AnthropicModelSettings
from pydantic_ai.settings import ModelSettings

from .evidence import SourcePassages, strip_inline_citations
from .prompts import MISSING_SECTION
from .schemas import FinalReport, ReportClaim

SEARCH_RESULT = "search_result"
SECTIONS = ("title", "summary", "answer", "caveats", "not_established")
_REQUIRED = ("title", "summary", "answer")
_SECTION = re.compile(r"<(" + "|".join(SECTIONS) + r")>(.*?)</\1>", re.DOTALL)
# Whitespace and section tags at the end of a cited span, which its citation goes before.
_TRAILING = re.compile(r"(?:\s|</?[a-z_]+>)*\Z")
_TAG = re.compile(r"</?[a-z_]+>")
_ITEM_ID = re.compile(r"[A-Za-z0-9_~/-]+")


# The API caps a cited document's title at 500 characters; source titles are written by the scouts, so a longer
# one is cut rather than risk a refused request.
TITLE_CHARS = 500


def search_results(passages: Sequence[SourcePassages]) -> list[TextContent]:
    """The synthesizer's citable passages as prompt content, which Claude receives as search_result blocks
    (`CitingAnthropicModel`). The passages are kept once, in the metadata, and the text is a short stand-in
    that no request sends: a copy of them in the text doubled the stored messages and the budget guard's
    byte-based reservation, which refused the first live check (docs/study-log.md)."""
    contents = []
    for source in passages:
        title = source.title if len(source.title) <= TITLE_CHARS else source.title[: TITLE_CHARS - 3].rstrip() + "..."
        contents.append(TextContent(
            content=f"[search result {source.source_id}: {len(source.passages)} passages]",
            metadata={"kind": SEARCH_RESULT, "source": source.source_id, "title": title,
                      "blocks": [passage.text for passage in source.passages]}))
    return contents


def _search_result_block(metadata: Any) -> dict[str, Any] | None:
    if not isinstance(metadata, dict) or metadata.get("kind") != SEARCH_RESULT:
        return None
    return {"type": "search_result", "source": metadata["source"], "title": metadata["title"],
            "content": [{"type": "text", "text": text} for text in metadata["blocks"]],
            "citations": {"enabled": True}}


@dataclass
class _Block:
    text: str = ""
    citations: list[dict[str, Any]] = field(default_factory=list)


class _CitationRecorder:
    """Passes a streamed response's events through unchanged, and keeps each text block's text and citations."""

    def __init__(self, stream: AsyncIterator[Any]) -> None:
        self._stream = stream
        self.blocks: dict[int, _Block] = {}

    def __aiter__(self) -> AsyncIterator[Any]:
        return self._events()

    async def close(self) -> None:
        """Close the underlying stream, as PydanticAI's `close_stream` does for the SDK's own."""
        await self._stream.close()  # type: ignore[attr-defined]

    async def _events(self) -> AsyncIterator[Any]:
        async for event in self._stream:
            kind = getattr(event, "type", None)
            if kind == "content_block_start" and event.content_block.type == "text":
                self.blocks[event.index] = _Block(
                    event.content_block.text or "",
                    [_dump(citation) for citation in event.content_block.citations or []])
            elif kind == "content_block_delta" and event.index in self.blocks:
                if event.delta.type == "text_delta":
                    self.blocks[event.index].text += event.delta.text
                elif event.delta.type == "citations_delta":
                    self.blocks[event.index].citations.append(_dump(event.delta.citation))
            yield event

    def attach(self, response: ModelResponse, provider_name: str) -> ModelResponse:
        """`response` with each text block's citations in its TextPart's `provider_details`. Parts and blocks
        are paired in order by their text, so a part PydanticAI left out or split leaves the rest paired."""
        blocks = [self.blocks[index] for index in sorted(self.blocks)]
        parts = []
        for part in response.parts:
            if isinstance(part, TextPart):
                match = next((i for i, block in enumerate(blocks) if block.text == part.content), None)
                if match is not None:
                    block = blocks[match]
                    blocks = blocks[match + 1:]
                    if block.citations:
                        part = replace(part, provider_name=provider_name,
                                       provider_details={**(part.provider_details or {}), "citations": block.citations})
            parts.append(part)
        return replace(response, parts=parts)


def _dump(citation: Any) -> dict[str, Any]:
    return citation.model_dump(mode="json") if hasattr(citation, "model_dump") else dict(citation)


class CitingAnthropicModel(AnthropicModel):
    """An Anthropic model that sends search results with citations on and keeps the citations it gets back.

    `request` streams, as PydanticAI's own AnthropicModel does when a long reply requires it, so the request
    timeout bounds the wait between chunks rather than the whole reply. Streaming a run through
    `request_stream` would drop the citations, so it is refused.
    """

    async def _map_user_prompt(self, part: UserPromptPart) -> AsyncGenerator[Any]:  # type: ignore[override]
        if isinstance(part.content, str):
            async for block in super()._map_user_prompt(part):
                yield block
            return
        for item in part.content:
            if isinstance(item, TextContent) and (block := _search_result_block(item.metadata)):
                yield block
            else:
                async for mapped in super()._map_user_prompt(replace(part, content=[item])):
                    yield mapped

    async def request(self, messages: list[ModelMessage], model_settings: ModelSettings | None,
                      model_request_parameters: ModelRequestParameters) -> ModelResponse:
        check_allow_model_requests()
        model_settings, model_request_parameters = self.prepare_request(model_settings, model_request_parameters)
        settings = cast(AnthropicModelSettings, model_settings or {})
        stream = await self._messages_create(messages, True, settings, model_request_parameters)
        recorder = _CitationRecorder(stream)
        async with stream:
            streamed = await self._process_streamed_response(recorder, model_request_parameters, settings)  # type: ignore[arg-type]
            async for _ in streamed:
                pass
            response = streamed.get()
        return recorder.attach(response, self.system)

    @asynccontextmanager
    async def request_stream(self, *args: Any, **kwargs: Any) -> AsyncIterator[StreamedResponse]:
        raise UserError("CitingAnthropicModel keeps citations only through request(); call the synthesizer "
                        "without streaming")
        yield  # pragma: no cover


def missing_sections(text: str) -> list[str]:
    """What the synthesizer's reply lacks: each required section that is absent or empty."""
    found = {name: body.strip() for name, body in _SECTION.findall(text)}
    missing = [name for name in _REQUIRED if not found.get(name)]
    return [MISSING_SECTION.format(name=name) for name in missing[:1]]


def _references(part: TextPart, by_source: dict[str, SourcePassages]) -> list[tuple[str, str]]:
    """The (source ID, claim ID) pairs a text part cites, in order, each once."""
    pairs: list[tuple[str, str]] = []
    for citation in (part.provider_details or {}).get("citations", []):
        if citation.get("type") != "search_result_location" or not (source := by_source.get(citation.get("source", ""))):
            continue
        start, end = int(citation.get("start_block_index", 0)), int(citation.get("end_block_index", 0))
        for passage in source.passages[max(start, 0):max(end, start + 1)]:
            if (source.source_id, passage.claim_id) not in pairs:
                pairs.append((source.source_id, passage.claim_id))
    return pairs


def _marked(text: str, source_ids: list[str]) -> str:
    """`text` with an inline citation of `source_ids` after its last word, before trailing space and tags."""
    end = _TRAILING.search(text)
    cut = end.start() if end else len(text)
    if not text[:cut].strip():
        return text
    return f"{text[:cut]} [{', '.join(source_ids)}]{text[cut:]}"


def cited_report(response: ModelResponse, passages: Sequence[SourcePassages]) -> FinalReport:
    """The report in the synthesizer's tagged reply, with inline citations and a claim list written by code
    from the passages Claude cited. Citations the model typed itself are removed first, so every [sN] in the
    report stands for a passage the API says the text drew on."""
    by_source = {source.source_id: source for source in passages}
    text = ""
    claims: list[ReportClaim] = []
    for part in response.parts:
        if not isinstance(part, TextPart):
            continue
        content = strip_inline_citations(part.content)
        if pairs := _references(part, by_source):
            source_ids = list(dict.fromkeys(source_id for source_id, _ in pairs))
            content = _marked(content, source_ids)
            statement = " ".join(_TAG.sub(" ", strip_inline_citations(part.content)).split())
            claim = ReportClaim(statement=statement, claim_ids=list(dict.fromkeys(claim_id for _, claim_id in pairs)))
            # A sentence the summary and the answer both cite is one statement.
            if statement and claim not in claims:
                claims.append(claim)
        text += content
    sections = {name: body.strip() for name, body in _SECTION.findall(text)}
    caveats = [line.strip().lstrip("-*•").strip() for line in sections.get("caveats", "").splitlines()]
    return FinalReport(
        title=" ".join(strip_inline_citations(sections.get("title", "")).split()),
        executive_summary=sections.get("summary", ""),
        answer=sections.get("answer", ""),
        claims=claims,
        caveats=[caveat for caveat in caveats if caveat],
        not_established=[item for item in _ITEM_ID.findall(strip_inline_citations(sections.get("not_established", "")))
                         if item.casefold() != "none"],
    )

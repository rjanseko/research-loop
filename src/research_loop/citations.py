"""Anthropic citations for the synthesizer, which on this design is always a Claude model.

The synthesizer receives each source's evidence as one search result: its `source` is the
source's ID (s4), and each passage is one text block that opens with the claim it supports
(`EvidenceLedger.passages`). Claude cites whole blocks, and each citation names the search result and its
block range, so code, not the model, maps every cited span of the report to the sources and claims behind
it, and writes the report's inline [sN] citations and its claim list.

PydanticAI (2.51 and before) neither sends search_result blocks nor keeps the citations Claude returns
(pydantic/pydantic-ai#2128). `CitingAnthropicModel` does both: a `TextContent` whose metadata is a search
result goes to Claude as a search_result block, and each text block's citations are kept in its
`TextPart.provider_details`. Citations cannot be combined with structured output, so the report comes back
as tagged text that `cited_report` parses into a `FinalReport`. A citation is used only when its source,
its search result's position, its block range, and its cited text all agree with the passages sent
(`mismatched_citations` names the ones that do not).

When the synthesizer's safety classifiers decline the request, Anthropic continues it on the configured
fallback model inside the same stream (server-side fallback, `ScoutModels.synthesizer_fallbacks`): the text
already streamed stays, and the fallback model writes the rest. When no model finishes it, the reply ends
with a refusal, and whatever text it has is incomplete: the run discards it (`scout._Run._cited_report`). PydanticAI 2.48 prices only the top-level
usage, which covers only the attempt that served the reply, so `CitingAnthropicModel` prices every attempt
in `usage.iterations` at its own model's rates and records the handoff in the response's `provider_details`.
"""
from __future__ import annotations

import re
from collections.abc import AsyncGenerator, AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Any, cast

from genai_prices import calc_price
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
from pydantic_ai.usage import RequestUsage

from .evidence import (
    SENTENCE_END,
    Passage,
    SourcePassages,
    inline_source_ids,
    strip_inline_citations,
)
from .prices import install_price_overrides
from .schemas import FinalReport, ReportAssertion, ReportClaim

SEARCH_RESULT = "search_result"
SECTIONS = ("title", "summary", "answer", "caveats", "not_established")
_REQUIRED = ("title", "summary", "answer")
# A section ends at its closing tag, or, when a reply leaves it open, at the next section or the reply's end.
_SECTION = re.compile(r"<(" + "|".join(SECTIONS) + r")>(.*?)(?:</\1>|(?=<(?:" + "|".join(SECTIONS) + r")>)|\Z)",
                      re.DOTALL)
# Whitespace and section tags at the end of a cited span, which its citation goes before.
_TRAILING = re.compile(r"(?:\s|</?[a-z_]+>)*\Z")
_TAG = re.compile(r"</?[a-z_]+>")
# A sentence's closing punctuation, which its citation goes before; after a closing quote or bracket, the
# citation follows it rather than go inside.
_SENTENCE_CLOSE = re.compile(r"[.!?]+\Z")
_ITEM_ID = re.compile(r"[A-Za-z0-9_~/-]+")
# Assertion boundaries fail closed when the next sentence begins with lowercase text, too.
_ASSERTION_END = re.compile(r"(?<=[.!?])\s+(?=\S)")


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
    """Passes a streamed response's events through unchanged, and keeps each text block's text and citations,
    each server-side fallback's handoff, and the usage of every attempt."""

    def __init__(self, stream: AsyncIterator[Any]) -> None:
        self._stream = stream
        self.blocks: dict[int, _Block] = {}
        self.fallbacks: list[dict[str, Any]] = []
        self.iterations: list[Any] = []
        # The model Anthropic names to retry a refusal on when it skipped the fallback, such as for a rate limit.
        self.recommended_model: str | None = None

    def __aiter__(self) -> AsyncIterator[Any]:
        return self._events()

    async def close(self) -> None:
        """Close the underlying stream, as PydanticAI's `close_stream` does for the SDK's own."""
        await self._stream.close()  # type: ignore[attr-defined]

    async def _events(self) -> AsyncIterator[Any]:
        async for event in self._stream:
            kind = getattr(event, "type", None)
            # The usage of each attempt; the final message_delta's replaces the opening one's.
            usage = (getattr(event.message, "usage", None) if kind == "message_start"
                     else event.usage if kind == "message_delta" else None)
            if usage is not None and getattr(usage, "iterations", None):
                self.iterations = list(usage.iterations)
            if kind == "message_delta" and (details := getattr(event.delta, "stop_details", None)) is not None:
                self.recommended_model = getattr(details, "recommended_model", None) or self.recommended_model
            if kind == "content_block_start" and event.content_block.type == "fallback":
                self.fallbacks.append(_dump(event.content_block))
            elif kind == "content_block_start" and event.content_block.type == "text":
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
        response = replace(response, parts=parts)
        if self.recommended_model:
            response = replace(response, provider_details={**(response.provider_details or {}),
                                                           "recommended_model": self.recommended_model})
        if self.fallbacks or any(item.type == "fallback_message" for item in self.iterations):
            response = _fallen_back(response, self.fallbacks, self.iterations)
        return response


def _dump(value: Any) -> dict[str, Any]:
    return value.model_dump(mode="json", by_alias=True) if hasattr(value, "model_dump") else dict(value)


def _fallen_back(response: ModelResponse, fallbacks: list[dict[str, Any]], iterations: list[Any]) -> ModelResponse:
    """`response` after a server-side fallback: named by the model that served it, with its cost summed over
    every attempt, each priced at its own model's rates, and the handoffs and attempts in `provider_details`.

    The token counts stay the serving attempt's, as Anthropic reports them, since one usage cannot hold two
    models' tokens; `provider_details["fallback"]["attempts"]` has each attempt's. PydanticAI keeps a cost that
    is already set. A declined attempt is priced even when it was refused before any output in a category
    Anthropic does not bill, so the cost may be over. When an attempt cannot be priced, the cost is left for
    PydanticAI to price from the serving attempt alone, which is under; the route check refuses a synthesizer
    or fallback without a price, so only a model Anthropic chose itself could cause that."""
    install_price_overrides()
    attempts: list[dict[str, Any]] = []
    cost: Decimal | None = Decimal(0)
    for item in iterations:
        if item.type not in ("message", "fallback_message"):
            continue
        model = (item.model or response.model_name or "").removeprefix("anthropic:")
        usage = RequestUsage(input_tokens=item.input_tokens + item.cache_creation_input_tokens
                             + item.cache_read_input_tokens,
                             cache_write_tokens=item.cache_creation_input_tokens,
                             cache_read_tokens=item.cache_read_input_tokens, output_tokens=item.output_tokens)
        try:
            price = calc_price(usage, model, provider_id="anthropic").total_price
        except LookupError:
            price = None
        cost = cost + price if cost is not None and price is not None else None
        attempts.append({"model": model, "served": item.type == "fallback_message", "input_tokens": usage.input_tokens,
                         "output_tokens": usage.output_tokens,
                         "cost_usd": None if price is None else str(price)})
    served = next((attempt["model"] for attempt in attempts if attempt["served"]), response.model_name)
    usage = replace(response.usage, cost=cost if attempts else None)
    details = {**(response.provider_details or {}), "fallback": {"handoffs": fallbacks, "attempts": attempts}}
    return replace(response, model_name=served, usage=usage, provider_details=details)


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
    """The required sections the synthesizer's reply lacks or leaves empty. It is not retried, so the report
    keeps what the reply has (`cited_report`) and the run notes what was missing."""
    found = {name: body.strip() for name, body in _SECTION.findall(text)}
    return [name for name in _REQUIRED if not found.get(name)]


def _citation_problem(citation: dict[str, Any], passages: Sequence[SourcePassages]) -> str | None:
    """Why `citation` does not match the search results sent, which are `passages` in order, or None when
    its source, its search result's position, its block range (end exclusive), and its cited text, which
    Anthropic documents as the cited blocks joined, all agree."""
    if citation.get("type") != "search_result_location":
        return f"a {citation.get('type')} citation"
    index, source_id = citation.get("search_result_index"), citation.get("source")
    if not isinstance(index, int) or not 0 <= index < len(passages) or passages[index].source_id != source_id:
        return f"search result {index} is not {source_id}"
    blocks = passages[index].passages
    start, end = citation.get("start_block_index"), citation.get("end_block_index")
    if not isinstance(start, int) or not isinstance(end, int) or not 0 <= start < end <= len(blocks):
        return f"{source_id} has no blocks {start} to {end}"
    if citation.get("cited_text") != "".join(passage.text for passage in blocks[start:end]):
        return f"the cited text of {source_id} blocks {start} to {end} is not theirs"
    return None


def mismatched_citations(response: ModelResponse, passages: Sequence[SourcePassages]) -> list[str]:
    """Why each citation in `response` that `cited_report` leaves out does not match the passages sent."""
    return [problem for part in response.parts if isinstance(part, TextPart)
            for citation in (part.provider_details or {}).get("citations", [])
            if (problem := _citation_problem(citation, passages))]


def _references(part: TextPart, passages: Sequence[SourcePassages]) -> list[tuple[str, Passage]]:
    """The exact source blocks referenced by valid provider citations, in first-cited order."""
    pairs: list[tuple[str, Passage]] = []
    seen: set[tuple[str, str | None]] = set()
    for citation in (part.provider_details or {}).get("citations", []):
        if _citation_problem(citation, passages):
            continue
        source = passages[citation["search_result_index"]]
        for passage in source.passages[citation["start_block_index"]:citation["end_block_index"]]:
            key = (source.source_id, passage.passage_id or passage.text)
            if key not in seen:
                seen.add(key)
                pairs.append((source.source_id, passage))
    return pairs


def _marked(text: str, source_ids: list[str]) -> str:
    """`text` with an inline citation of `source_ids` closing each of its sentences: before the sentence's
    full stop, or after its last word when it has none, and before trailing space and tags. A cited block
    can hold several sentences, and one marked only at its end left the others uncited (run 6e811f5f); a
    citation after a full stop would read as the next sentence's."""
    citation = f" [{', '.join(source_ids)}]"
    # The separators are kept (odd indices), so the text is rebuilt unchanged apart from the citations.
    pieces = re.split(f"({SENTENCE_END.pattern})", text)
    return "".join(piece if index % 2 else _mark_sentence(piece, citation) for index, piece in enumerate(pieces))


def _mark_sentence(sentence: str, citation: str) -> str:
    end = _TRAILING.search(sentence)
    body, rest = sentence[:end.start()], sentence[end.start():] if end else ""
    close = _SENTENCE_CLOSE.search(body)
    head = body[:close.start()] if close else body
    words = head.rstrip()
    if not words.strip():
        return sentence
    # After the last word, so any space before the full stop stays where it was.
    return f"{words}{citation}{body[len(words):]}{rest}"


def _assertion_spans(body: str) -> list[tuple[int, int]]:
    """Non-heading prose sentences and table rows in a rendered report section."""
    spans: list[tuple[int, int]] = []
    lines = list(re.finditer(r"[^\n]+", body))
    for index, line in enumerate(lines):
        raw = line.group()
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or set(stripped) <= set("|-: "):
            continue
        if stripped.startswith("|") and index + 1 < len(lines) and set(lines[index + 1].group().strip()) <= set("|-: "):
            continue  # a table header, whose following line is the rule
        boundaries = [] if stripped.startswith("|") else list(_ASSERTION_END.finditer(raw))
        position = 0
        for boundary in [*boundaries, None]:
            stop = boundary.start() if boundary else len(raw)
            piece = raw[position:stop]
            if strip_inline_citations(piece).strip():
                left = len(piece) - len(piece.lstrip())
                right = len(piece.rstrip())
                spans.append((line.start() + position + left, line.start() + position + right))
            position = boundary.end() if boundary else stop
    return spans


def _report_assertions(text: str, parts: list[tuple[int, int, list[tuple[str, Passage]], str]]) -> list[ReportAssertion]:
    assertions: list[ReportAssertion] = []
    for section in _SECTION.finditer(text):
        kind = section.group(1)
        if kind not in ("summary", "answer", "caveats"):
            continue
        raw = section.group(2)
        body = raw.strip()
        base = section.start(2) + len(raw) - len(raw.lstrip())
        for start, end in _assertion_spans(body):
            visible = body[start:end]
            sources = list(dict.fromkeys(inline_source_ids(visible)))
            absolute_start, absolute_end = base + start, base + end
            overlap = [(refs, scope) for first, last, refs, scope in parts
                       if first < absolute_end and last > absolute_start and refs]
            linked = [(source_id, passage) for refs, _ in overlap for source_id, passage in refs
                      if source_id in sources]
            claim_ids = list(dict.fromkeys(p.claim_id for _, p in linked))
            passage_ids = list(dict.fromkeys(p.passage_id for _, p in linked if p.passage_id))
            if not sources:
                scope = "uncited"
            elif not linked or any(item_scope == "ambiguous" for _, item_scope in overlap):
                scope = "ambiguous"
            else:
                scope = "exact"
            assertions.append(ReportAssertion(
                section="caveat" if kind == "caveats" else kind,
                statement=strip_inline_citations(visible).strip(), start=start, end=end,
                source_ids=sources, passage_ids=passage_ids, claim_ids=claim_ids, citation_scope=scope))
    return assertions


def cited_report(response: ModelResponse, passages: Sequence[SourcePassages]) -> FinalReport:
    """The report in the synthesizer's tagged reply, with inline citations and a claim list written by code
    from the passages Claude cited. Citations the model typed itself are removed first, so every [sN] in the
    report stands for a passage the API says the text drew on; a citation that does not match the passages
    sent is left out (`mismatched_citations`). `passages` are the search results sent, in their order. The
    reply is never retried, so a missing section is left empty, and a reply with no <answer> section has the
    text outside its sections as its answer."""
    text = ""
    claims: list[ReportClaim] = []
    part_spans: list[tuple[int, int, list[tuple[str, Passage]], str]] = []
    for part in response.parts:
        if not isinstance(part, TextPart):
            continue
        content = strip_inline_citations(part.content)
        pairs = _references(part, passages)
        scope = "uncited"
        if pairs:
            source_ids = list(dict.fromkeys(source_id for source_id, _ in pairs))
            passage_ids = list(dict.fromkeys(passage.passage_id for _, passage in pairs if passage.passage_id))
            plain = _TAG.sub(" ", content).strip()
            multiple_sentences = len(_ASSERTION_END.split(plain)) > 1
            # One provider text block can contain several unrelated facts. Its citations do not identify
            # which sentence used which passage; put the visible marker at the block's end and flag it.
            content = _mark_sentence(content, f" [{', '.join(source_ids)}]") if multiple_sentences else _marked(content, source_ids)
            statement = " ".join(_TAG.sub(" ", strip_inline_citations(part.content)).split())
            scope = "ambiguous" if multiple_sentences else "exact"
            claim = ReportClaim(statement=statement,
                                claim_ids=list(dict.fromkeys(p.claim_id for _, p in pairs)),
                                source_ids=source_ids, passage_ids=passage_ids, citation_scope=scope)
            # A sentence the summary and the answer both cite is one statement.
            if statement and claim not in claims:
                claims.append(claim)
        part_spans.append((len(text), len(text) + len(content), pairs, scope))
        text += content
    sections = {name: body.strip() for name, body in _SECTION.findall(text)}
    if not sections.get("answer"):
        sections["answer"] = _TAG.sub("", _SECTION.sub("", text)).strip()
    caveats = [line.strip().lstrip("-*•").strip() for line in sections.get("caveats", "").splitlines()]
    return FinalReport(
        title=" ".join(strip_inline_citations(sections.get("title", "")).split()),
        executive_summary=sections.get("summary", ""),
        answer=sections.get("answer", ""),
        claims=claims,
        assertions=_report_assertions(text, part_spans),
        caveats=[caveat for caveat in caveats if caveat],
        not_established=[item for item in _ITEM_ID.findall(strip_inline_citations(sections.get("not_established", "")))
                         if item.casefold() != "none"],
    )

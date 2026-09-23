"""Deterministic checks of evidence against the text a research run's tools returned.

A quote is `verified` when its segments, split at '...' and bracketed insertions, appear in
order within one piece of tool output, after normalizing case, whitespace, typographic quotes
and dashes, and word-internal hyphens (which PDF line breaks introduce). Otherwise it is
`not_found`: no tool output in that research run contained it, so the wording may have been
reconstructed or invented. Paraphrases in `excerpt` are not checked.

A cited source is `observed` when its URL appears in the tool output, ignoring scheme, `www.`,
query, fragment, and trailing slash, or when its DOI or arXiv ID does. Otherwise it is
`not_found`: the model cited a source none of its tools returned. This shows the source was
seen, not that it supports the claim.
"""
from __future__ import annotations

import json
import re
import unicodedata
from typing import Any, Iterable, Literal
from urllib.parse import unquote, urlparse

from .schemas import ResearchResult, SourceRef, ToolEvent

_TYPOGRAPHY = str.maketrans({
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"',
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "−": "-",
    "­": None,  # soft hyphen
})
_WORD_HYPHEN = re.compile(r"(?<=\w)-\s*(?=\w)")
_GAP = re.compile(r"\.\.\.|\[[^\]]*\]")
_SEGMENT_EDGES = " \"'.,;:!?"


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(_TYPOGRAPHY).casefold()
    return " ".join(_WORD_HYPHEN.sub("", text).split())


def _segments(quote: str) -> list[str]:
    return [segment for segment in (part.strip(_SEGMENT_EDGES) for part in _GAP.split(_normalize(quote))) if segment]


def _contains(haystack: str, segments: list[str]) -> bool:
    position = 0
    for segment in segments:
        position = haystack.find(segment, position)
        if position < 0:
            return False
        position += len(segment)
    return True


def quote_found(quote: str, texts: Iterable[str]) -> bool:
    segments = _segments(quote)
    return bool(segments) and any(_contains(_normalize(text), segments) for text in texts)


def _strings(value: Any, out: list[str]) -> None:
    if isinstance(value, str):
        if value[:1] in ("{", "["):  # a tool that returned serialized JSON
            try:
                return _strings(json.loads(value), out)
            except ValueError:
                pass
        out.append(value)
    elif isinstance(value, dict):
        for item in value.values():
            _strings(item, out)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _strings(item, out)


def tool_texts(events: Iterable[ToolEvent]) -> list[str]:
    """Every string the tools returned to the model, in call order."""
    texts: list[str] = []
    for event in events:
        _strings(event.result, texts)
    return texts


def check_quotes(result: ResearchResult, texts: Iterable[str]) -> ResearchResult:
    """Set each evidence item's quote_check from `texts`, the tool output its research run saw."""
    haystacks = list(dict.fromkeys(_normalize(text) for text in texts))

    def check(quote: str | None) -> Literal["verified", "not_found"] | None:
        segments = _segments(quote) if quote else []
        if not segments:
            return None
        return "verified" if any(_contains(haystack, segments) for haystack in haystacks) else "not_found"

    claims = [
        claim.model_copy(update={"evidence": [
            item.model_copy(update={"quote_check": check(item.quote)}) for item in claim.evidence
        ]})
        for claim in result.claims
    ]
    return result.model_copy(update={"claims": claims})


_URL = re.compile(r"https?://[^\s\"'<>\\]+", re.IGNORECASE)
_ARXIV_ID = re.compile(r"\d{4}\.\d{4,5}")


def _url_key(url: str) -> str:
    parsed = urlparse(url.strip().rstrip(".,;:)]"))
    return (parsed.hostname or "").removeprefix("www.") + unquote(parsed.path).rstrip("/")


def _source_ids(source: SourceRef) -> list[str]:
    """Lowercased identifiers besides the URL that show a source was seen: its DOI and arXiv ID."""
    key = _url_key(str(source.url))
    ids = [source.doi or "", key.removeprefix("doi.org/") if key.startswith("doi.org/") else ""]
    for text in (source.arxiv_id or "", key if key.startswith("arxiv.org/") else ""):
        if match := _ARXIV_ID.search(text):
            ids.append(match.group(0))
    return [identifier.lower() for identifier in ids if identifier]


def check_sources(result: ResearchResult, texts: Iterable[str]) -> ResearchResult:
    """Set each URL-cited evidence item's source_check from `texts`, the tool output its run saw."""
    texts = list(texts)
    seen = {_url_key(url) for text in texts for url in _URL.findall(text)}
    haystack = "\n".join(texts).lower()

    def check(source: SourceRef) -> Literal["observed", "not_found"] | None:
        if source.url is None:
            return None
        if _url_key(str(source.url)) in seen or any(identifier in haystack for identifier in _source_ids(source)):
            return "observed"
        return "not_found"

    claims = [
        claim.model_copy(update={"evidence": [
            item.model_copy(update={"source_check": check(item.source)}) for item in claim.evidence
        ]})
        for claim in result.claims
    ]
    return result.model_copy(update={"claims": claims})

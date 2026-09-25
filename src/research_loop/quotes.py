"""Deterministic checks of evidence against the text a research run's tools returned.

A quote is `verified` when each of its segments, split at '...' and bracketed insertions,
appears in one piece of tool output, in any order, comparing only letters and digits after
Unicode (NFKC) normalization and case folding. Spacing, punctuation, list bullets, and
hyphenated line breaks therefore never decide the check. Otherwise it is
`not_found`: no tool output in that research run contained it, so the wording may have been
reconstructed or invented. Paraphrases in `excerpt` are not checked.

A cited source is `observed` when its URL appears in the tool output, ignoring scheme, `www.`,
query, fragment, and trailing slash, or when its DOI or arXiv ID does. Otherwise it is
`not_found`: the model cited a source none of its tools returned. This shows the source was
seen, not that it supports the claim.

`source_keys` reuses that normalization to tell whether two citations name the same work.
"""
from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Iterable
from typing import Any, Literal
from urllib.parse import unquote, urlparse

from .schemas import ResearchResult, SourceRef, ToolEvent

# Only letters and digits are compared. PDF extraction puts spaces inside words ("s olutions")
# and breaks words with hyphens, and quotes drop list bullets and code comment signs; none of
# that changes the wording. NFKC folds ligatures and the ellipsis character.
_NON_WORD = re.compile(r"[\W_]+")
_GAP = re.compile(r"\.\.\.|\[[^\]]*\]")


def _key(text: str) -> str:
    return _NON_WORD.sub("", unicodedata.normalize("NFKC", text).casefold())


def _segments(quote: str) -> list[str]:
    return [segment for segment in map(_key, _GAP.split(unicodedata.normalize("NFKC", quote))) if segment]


def _contains(haystack: str, segments: list[str]) -> bool:
    return all(segment in haystack for segment in segments)


def quote_found(quote: str, texts: Iterable[str]) -> bool:
    segments = _segments(quote)
    return bool(segments) and any(_contains(_key(text), segments) for text in texts)


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
    haystacks = list(dict.fromkeys(_key(text) for text in texts))

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


def find_urls(text: str) -> list[str]:
    return _URL.findall(text)


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


def source_keys(source: SourceRef) -> frozenset[str]:
    """Identifiers of the work a source cites; two citations of one work share at least one.

    The URL (normalized as above, but keeping its query, which can name the work), DOI, arXiv
    ID, catalog IDs, and attachment. Locator, title, provider, and fetch time are not
    identity, so one paper cited at two pages or fetched through two providers matches. A
    source with none of these falls back to its title.
    """
    keys = {f"id:{identifier}" for identifier in _source_ids(source)}
    if source.url is not None:
        query = urlparse(str(source.url)).query
        keys.add(f"url:{_url_key(str(source.url))}" + (f"?{query}" if query else ""))
    for name in ("openalex_id", "acl_id", "attachment_id"):
        if value := getattr(source, name):
            keys.add(f"{name}:{value.lower()}")
    return frozenset(keys or {f"title:{' '.join(source.title.lower().split())}"})


class ToolOutputIndex:
    """What `texts`, a run's tool output, show of sources: their URLs, and the text for DOIs and arXiv IDs."""

    def __init__(self, texts: Iterable[str]) -> None:
        texts = list(texts)
        self.urls = {_url_key(url) for text in texts for url in find_urls(text)}
        self.haystack = "\n".join(texts).lower()

    def observed(self, source: SourceRef) -> bool:
        """Whether a tool returned `source`: its URL, ignoring scheme, `www.`, query, and fragment, or its DOI or arXiv ID."""
        return _url_key(str(source.url)) in self.urls or any(i in self.haystack for i in _source_ids(source))


def check_sources(result: ResearchResult, texts: Iterable[str]) -> ResearchResult:
    """Set each URL-cited evidence item's source_check from `texts`, the tool output its run saw."""
    index = ToolOutputIndex(texts)

    def check(source: SourceRef) -> Literal["observed", "not_found"] | None:
        if source.url is None:
            return None
        return "observed" if index.observed(source) else "not_found"

    claims = [
        claim.model_copy(update={"evidence": [
            item.model_copy(update={"source_check": check(item.source)}) for item in claim.evidence
        ]})
        for claim in result.claims
    ]
    return result.model_copy(update={"claims": claims})

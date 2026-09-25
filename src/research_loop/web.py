"""Web search and page fetching for research tools: bounded, cached, and public-HTTPS only."""
from __future__ import annotations

import asyncio
import hashlib
import io
import unicodedata
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from .acquisition import (
    MAX_FETCH_CHARS,
    UNREACHABLE_AFTER,
    AcquisitionCache,
    BlockedSource,
    CacheMode,
    FetchMemo,
    SourcePolicy,
    bounded_public_get,
    fetch_cache_key,
    fetch_window,
    is_pdf,
    public_fetch_client,
    public_url,
    wait_rate_slot,
)

# Decoded bytes read per page; some leaderboard pages embed a few MB of data.
_MAX_PAGE_BYTES = 5_000_000
# PDF pages extracted; a longer document is marked `pypdf-first-pages`.
_PDF_PAGE_LIMIT = 30


def search_cache_key(query: str) -> str:
    """A query as search engines read it: Unicode-normalized, case-folded, whitespace collapsed.

    Quotes, operators such as `site:`, punctuation, and word order change results and are kept.
    """
    return " ".join(unicodedata.normalize("NFKC", query).casefold().split())


# Waits before a search's second and third attempts. In the settings-study pilots 279 of 697 searches
# failed: about a third found nothing, which the search client raises as an error, and most of the rest
# answered when retried later, so throttling, not an outage.
SEARCH_RETRY_DELAYS = (2.0, 6.0)
NO_RESULTS_HINT = "No results for this query. Try fewer or broader terms, without quotes or site: operators."


def _no_results(exc: Exception) -> bool:
    """Whether the search client's error says the query found nothing, rather than that it could not search."""
    return "no results" in str(exc).lower()


def _duckduckgo() -> Callable[[str], Awaitable[list[dict[str, str]]]]:
    from pydantic_ai.common_tools.duckduckgo import duckduckgo_search_tool

    return duckduckgo_search_tool().function


class WebSearch:
    """DuckDuckGo search with a shared rate slot, retries, and an optional cache.

    A query that finds nothing returns no results and a hint to broaden it, without a retry: the search
    client raises that as an error, and reporting it as a failure sent scouts looking for other search
    engines. Other failures are tried again after each of `retry_delays`, then returned as an error
    result instead of failing the run. Failures are never cached.
    """

    def __init__(self, *, cache: AcquisitionCache | None = None,
                 retry_delays: tuple[float, ...] = SEARCH_RETRY_DELAYS,
                 engine: Callable[[str], Awaitable[list[dict[str, str]]]] | None = None) -> None:
        self.cache = cache
        self.retry_delays = retry_delays
        self._engine = engine

    async def search(self, query: str) -> dict[str, Any]:
        key = search_cache_key(query)
        if self.cache is not None:
            cached = self.cache.get("duckduckgo", key)
            if cached is not None:
                return {"results": cached["results"]}
            if self.cache.mode == "replay":
                return {"error": "CacheMiss"}
        engine = self._engine or _duckduckgo()
        error = "unknown"
        for delay in (*self.retry_delays, None):
            await wait_rate_slot("duckduckgo")
            try:
                raw = await engine(query)
            except Exception as exc:  # noqa: BLE001 - the search client raises its own types on rate limits and drops
                if _no_results(exc):
                    return {"results": [], "hint": NO_RESULTS_HINT}
                error = type(exc).__name__
                if delay is not None:
                    await asyncio.sleep(delay)
                continue
            results = [{"title": item.get("title", ""), "url": item.get("href", ""), "snippet": item.get("body", "")}
                       for item in raw if item.get("href")]
            if self.cache is not None:
                self.cache.put("duckduckgo", key, {"query": query, "results": results})
            return {"results": results} if results else {"results": [], "hint": NO_RESULTS_HINT}
        return {"error": f"SearchUnavailable ({error})",
                "hint": f"Web search failed {len(self.retry_delays) + 1} times; continue with scholar_search "
                        "or try again later with a different query."}


class WebAcquisition:
    """Fetch a public HTTPS page or PDF and return a window of its extracted text."""

    def __init__(self, *, cache_root: Path, cache_mode: CacheMode = "live",
                 client: httpx.AsyncClient | None = None, memo: FetchMemo | None = None,
                 policy: SourcePolicy | None = None) -> None:
        self.cache = AcquisitionCache(cache_root, cache_mode, ttl_seconds=86400)
        self.client = client
        self.memo = memo or FetchMemo()
        self.policy = policy or SourcePolicy()

    async def fetch(self, url: str, max_chars: int = MAX_FETCH_CHARS, start: int = 0) -> dict[str, Any]:
        max_chars = max(1000, min(max_chars, MAX_FETCH_CHARS))
        start = max(0, start)
        # Blocked sources are refused before the cache, the memo, DNS, or any request.
        if entry := self.policy.blocks(url):
            return {"url": url, "error": "BlockedSource", "blocked": entry}
        # Cache next so replay works offline; entries exist only for URLs that passed the check.
        cache_key = fetch_cache_key(url, max_chars, start)
        cached = self.cache.get("web", cache_key)
        if cached is not None:
            return cached
        if self.cache.mode == "replay":
            return {"url": url, "error": "CacheMiss"}
        document = self.memo.get("web", url)
        if document is None:
            host = (urlparse(url).hostname or "").lower()
            if self.memo.connection_failures.get(host, 0) >= UNREACHABLE_AFTER:
                return {"url": url, "error": "HostUnreachable",
                        "hint": "This site has not answered in this run; use another source for it."}
            if not await public_url(url):
                return {"url": url, "error": "UnsafeURL"}
            try:
                document = await self._extract(url)
            except BlockedSource as exc:  # redirected to a blocked source
                return {"url": url, "error": "BlockedSource", "blocked": exc.entry}
            except (httpx.HTTPError, ValueError, ImportError, TypeError) as exc:
                failure: dict[str, Any] = {"url": url, "error": type(exc).__name__}
                if isinstance(exc, httpx.HTTPStatusError):
                    failure["status"] = exc.response.status_code
                    if exc.response.status_code in (404, 410):
                        # Most were addresses a model guessed: 108 of the pilots' 725 fetches.
                        failure["hint"] = "No page at this address. Find the page with a search rather than guessing its URL."
                elif isinstance(exc, httpx.ConnectError | httpx.ConnectTimeout):
                    self.memo.connection_failures[host] = self.memo.connection_failures.get(host, 0) + 1
                elif type(exc) is ValueError:
                    failure["detail"] = str(exc)  # this module's own messages, e.g. "unsupported content type"
                return failure
            self.memo.put("web", url, document)
        window = fetch_window(document["text"], start, max_chars)
        if window is None:
            return {"url": url, "error": "StartBeyondEnd", "total_chars": len(document["text"])}
        result = {"url": url, **window, "extraction": document["extraction"],
                  "content_sha256": document["content_sha256"]}
        self.cache.put("web", cache_key, result)
        return result

    async def _extract(self, url: str) -> dict[str, Any]:
        """Download a public page or PDF and extract its full text.

        A 403 to the fetcher's own User-Agent is tried once more as a browser (BROWSER_USER_AGENT):
        5 of 12 sites that refused the fetcher in the pilots served a browser.
        """
        async def get(client: httpx.AsyncClient) -> httpx.Response:
            response = await bounded_public_get(client, url, _MAX_PAGE_BYTES, self.policy)
            if response.status_code == 403:
                response = await bounded_public_get(client, url, _MAX_PAGE_BYTES, self.policy, as_browser=True)
            return response

        if self.client:
            response = await get(self.client)
        else:
            async with public_fetch_client(timeout=15) as client:
                response = await get(client)
        response.raise_for_status()
        media = response.headers.get("content-type", "").split(";")[0].lower()
        if is_pdf(media, response.content):
            # Parsing is CPU-bound; a worker thread keeps a long PDF from stalling the other agents.
            extracted, more_pages = await asyncio.to_thread(_pdf_text, response.content)
            method = "pypdf-first-pages" if more_pages else "pypdf"
        elif media in ("text/html", "application/xhtml+xml"):
            extracted, method = await asyncio.to_thread(_html_text, response.text)
        else:
            raise ValueError("unsupported content type")
        if not extracted.strip():
            raise ValueError("empty extraction")
        return {"text": extracted, "extraction": method, "content_sha256": hashlib.sha256(response.content).hexdigest()}


def _pdf_text(content: bytes) -> tuple[str, bool]:
    """Text of a PDF's first pages, and whether it has more."""
    from pypdf import PdfReader

    pages = PdfReader(io.BytesIO(content)).pages
    return "\n\n".join(page.extract_text() or "" for page in pages[:_PDF_PAGE_LIMIT]), len(pages) > _PDF_PAGE_LIMIT


def _html_text(html: str) -> tuple[str, str]:
    """Main text of an HTML page and the extractor that produced it."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    for item in soup(["script", "style", "nav", "footer", "header"]):
        item.decompose()
    try:
        import trafilatura
        extracted = trafilatura.extract(str(soup), include_comments=False, include_tables=True) or ""
    except Exception:  # noqa: BLE001 - arbitrary HTML can break the extractor; fall back below
        extracted = ""
    if extracted.strip():
        return extracted, "trafilatura"
    return soup.get_text(" ", strip=True), "beautifulsoup-fallback"

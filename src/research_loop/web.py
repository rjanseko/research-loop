"""Deterministic web-page extraction for the normalized research lane."""
from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import Any

import httpx
from pydantic_ai import FunctionToolset, Tool

from .acquisition import (
    MAX_FETCH_CHARS,
    AcquisitionCache,
    BlockedSource,
    CacheMode,
    FetchMemo,
    SourcePolicy,
    bounded_public_get,
    fetch_cache_key,
    fetch_window,
    public_fetch_client,
    public_url,
    wait_rate_slot,
)

# Decoded HTML bytes read per page; some leaderboard pages embed a few MB of data.
_MAX_PAGE_BYTES = 5_000_000


def resilient_duckduckgo_tool(*, retry_delay: float = 2.0) -> Tool:
    """PydanticAI's DuckDuckGo search under the same name, with a shared rate slot and one retry.

    A search failure is returned to the model as an error result instead of failing the run.
    """
    from pydantic_ai.common_tools.duckduckgo import duckduckgo_search_tool

    inner = duckduckgo_search_tool()

    async def duckduckgo_search(query: str) -> Any:
        error = "unknown"
        for attempt in range(2):
            await wait_rate_slot("duckduckgo")
            try:
                return await inner.function(query=query)
            except Exception as exc:  # noqa: BLE001 - the search client raises its own types on rate limits and drops
                error = type(exc).__name__
                if attempt == 0:
                    await asyncio.sleep(retry_delay)
        return {"error": f"SearchUnavailable ({error})",
                "hint": "Web search failed twice; continue with scholar tools or a different query."}

    return Tool(duckduckgo_search, name=inner.name, description=inner.description)


class WebAcquisition:
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
            return {**cached, "cache_hit": True}
        if self.cache.mode == "replay":
            return {"url": url, "error": "CacheMiss", "cache_hit": False}
        document = self.memo.get("web", url)
        if document is None:
            if not await public_url(url):
                return {"url": url, "error": "UnsafeURL"}
            try:
                document = await self._extract(url)
            except BlockedSource as exc:  # redirected to a blocked source
                return {"url": url, "error": "BlockedSource", "blocked": exc.entry}
            except (httpx.HTTPError, ValueError, ImportError, TypeError) as exc:
                failure: dict[str, Any] = {"url": url, "error": type(exc).__name__, "cache_hit": False}
                if isinstance(exc, httpx.HTTPStatusError):
                    failure["status"] = exc.response.status_code
                elif type(exc) is ValueError:
                    failure["detail"] = str(exc)  # this module's own messages, e.g. "unsupported content type"
                return failure
            self.memo.put("web", url, document)
        window = fetch_window(document["text"], start, max_chars)
        if window is None:
            return {"url": url, "error": "StartBeyondEnd", "total_chars": len(document["text"]), "cache_hit": False}
        result = {"url": url, **window, "extraction": document["extraction"],
                  "content_sha256": document["content_sha256"], "cache_hit": False}
        self.cache.put("web", cache_key, result)
        return result

    async def _extract(self, url: str) -> dict[str, Any]:
        """Download a public HTML page and extract its full main text."""
        if self.client:
            response = await bounded_public_get(self.client, url, _MAX_PAGE_BYTES, self.policy)
        else:
            async with public_fetch_client(timeout=15) as client:
                response = await bounded_public_get(client, url, _MAX_PAGE_BYTES, self.policy)
        response.raise_for_status()
        media = response.headers.get("content-type", "").split(";")[0].lower()
        if media not in ("text/html", "application/xhtml+xml"):
            raise ValueError("unsupported content type")
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(response.text, "html.parser")
        for item in soup(["script", "style", "nav", "footer", "header"]):
            item.decompose()
        clean_html = str(soup)
        try:
            import trafilatura
            extracted = trafilatura.extract(clean_html, include_comments=False, include_tables=True) or ""
        except Exception:  # noqa: BLE001 - arbitrary HTML can break the extractor; fall back below
            extracted = ""
        method = "trafilatura"
        if not extracted.strip():
            extracted = soup.get_text(" ", strip=True)
            method = "beautifulsoup-fallback"
        if not extracted.strip():
            raise ValueError("empty extraction")
        return {"text": extracted, "extraction": method,
                "content_sha256": hashlib.sha256(response.content).hexdigest()}


def build_web_toolset(acquisition: WebAcquisition) -> FunctionToolset:
    async def web_fetch(url: str, max_chars: int = MAX_FETCH_CHARS, start: int = 0) -> dict[str, Any]:
        """Fetch a public HTTPS HTML page and return up to max_chars characters of main text from `start`.

        When the result has `next_start`, call again with start=next_start to read further.
        """
        return await acquisition.fetch(url, max_chars, start)

    return FunctionToolset(tools=[web_fetch])

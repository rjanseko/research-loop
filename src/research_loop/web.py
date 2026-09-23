"""Deterministic web-page extraction for the normalized research lane."""
from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import Any

import httpx
from pydantic_ai import FunctionToolset, Tool

from .scholar import AcquisitionCache, CacheMode, _public_url, _bounded_public_get, _wait_rate_slot

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
            await _wait_rate_slot("duckduckgo")
            try:
                return await inner.function(query=query)
            except Exception as exc:  # the search client raises its own types on rate limits and drops
                error = type(exc).__name__
                if attempt == 0:
                    await asyncio.sleep(retry_delay)
        return {"error": f"SearchUnavailable ({error})",
                "hint": "Web search failed twice; continue with scholar tools or a different query."}

    return Tool(duckduckgo_search, name=inner.name, description=inner.description)


class WebAcquisition:
    def __init__(self, *, cache_root: Path, cache_mode: CacheMode = "live",
                 client: httpx.AsyncClient | None = None) -> None:
        self.cache = AcquisitionCache(cache_root, cache_mode, ttl_seconds=86400)
        self.client = client

    async def fetch(self, url: str, max_chars: int = 12000) -> dict[str, Any]:
        max_chars = max(1000, min(max_chars, 12000))
        # Cache first so replay works offline; entries exist only for URLs that passed the check.
        cache_key = f"{url}|max_chars={max_chars}"
        cached = self.cache.get("web", cache_key)
        if cached is not None:
            return {**cached, "cache_hit": True}
        if self.cache.mode == "replay":
            return {"url": url, "error": "CacheMiss", "cache_hit": False}
        if not await _public_url(url):
            return {"url": url, "error": "UnsafeURL"}
        try:
            if self.client:
                response = await _bounded_public_get(self.client, url, _MAX_PAGE_BYTES)
            else:
                async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
                    response = await _bounded_public_get(client, url, _MAX_PAGE_BYTES)
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
            except Exception:
                extracted = ""
            method = "trafilatura"
            if not extracted.strip():
                extracted = soup.get_text(" ", strip=True)
                method = "beautifulsoup-fallback"
            if not extracted.strip():
                raise ValueError("empty extraction")
            document = {
                "url": url, "text": extracted[:max_chars], "extraction": method,
                "content_sha256": hashlib.sha256(response.content).hexdigest(),
                "truncated": len(extracted) > max_chars, "cache_hit": False,
            }
            self.cache.put("web", cache_key, document)
            return document
        except (httpx.HTTPError, ValueError, ImportError, TypeError) as exc:
            failure: dict[str, Any] = {"url": url, "error": type(exc).__name__, "cache_hit": False}
            if isinstance(exc, httpx.HTTPStatusError):
                failure["status"] = exc.response.status_code
            elif type(exc) is ValueError:
                failure["detail"] = str(exc)  # this module's own messages, e.g. "unsupported content type"
            return failure


def build_web_toolset(acquisition: WebAcquisition) -> FunctionToolset:
    async def web_fetch(url: str, max_chars: int = 12000) -> dict[str, Any]:
        """Fetch a public HTTPS HTML page and return bounded main-content text."""
        return await acquisition.fetch(url, max_chars)

    return FunctionToolset(tools=[web_fetch])

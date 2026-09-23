"""Deterministic web-page extraction for the normalized research lane."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import httpx
from pydantic_ai import FunctionToolset

from .scholar import AcquisitionCache, CacheMode, _public_url, _bounded_public_get


class WebAcquisition:
    def __init__(self, *, cache_root: Path, cache_mode: CacheMode = "live",
                 client: httpx.AsyncClient | None = None) -> None:
        self.cache = AcquisitionCache(cache_root, cache_mode, ttl_seconds=86400)
        self.client = client

    async def fetch(self, url: str, max_chars: int = 12000) -> dict[str, Any]:
        if not await _public_url(url):
            return {"url": url, "error": "UnsafeURL"}
        max_chars = max(1000, min(max_chars, 12000))
        cache_key = f"{url}|max_chars={max_chars}"
        cached = self.cache.get("web", cache_key)
        if cached is not None:
            return {**cached, "cache_hit": True}
        if self.cache.mode == "replay":
            return {"url": url, "error": "CacheMiss", "cache_hit": False}
        try:
            if self.client:
                response = await _bounded_public_get(self.client, url, 2_000_000)
            else:
                async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
                    response = await _bounded_public_get(client, url, 2_000_000)
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
            return {"url": url, "error": type(exc).__name__, "cache_hit": False}


def build_web_toolset(acquisition: WebAcquisition) -> FunctionToolset:
    async def web_fetch(url: str, max_chars: int = 12000) -> dict[str, Any]:
        """Fetch a public HTTPS HTML page and return bounded main-content text."""
        return await acquisition.fetch(url, max_chars)

    return FunctionToolset(tools=[web_fetch])

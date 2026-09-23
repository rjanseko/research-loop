"""Shared acquisition infrastructure for the web and scholarly research tools.

Disk cache, per-job document memo, text windows, per-provider rate slots, the task's blocked
sources, and the guarded public-HTTPS download that every fetch goes through. Provider adapters
live in scholar.py and web.py.
"""
from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import re
import socket
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import unquote, urljoin, urlparse
from uuid import uuid4

import httpx

# Part of every cache key; bump it to invalidate recorded entries.
CACHE_VERSION = 1
# Recorded in manifests. 1: fetch returned only the first 12,000 characters.
# 2: fetch pages through a document with `start`, backed by a per-job memo.
# 3: fetches refuse the task's blocked sources, including redirects to them.
FETCH_VERSION = 3
# Longest text window one fetch returns; `start` pages through the rest.
MAX_FETCH_CHARS = 12_000
CacheMode = Literal["off", "live", "record", "replay"]


class AcquisitionCache:
    """Small per-request cache; benchmark mode defaults to off."""

    def __init__(self, root: Path, mode: CacheMode = "live", ttl_seconds: int = 86400) -> None:
        self.root, self.mode, self.ttl_seconds = root, mode, ttl_seconds

    def _path(self, provider: str, key: str) -> Path:
        digest = hashlib.sha256(f"v{CACHE_VERSION}:{provider}:{key}".encode()).hexdigest()
        return self.root / provider / f"{digest}.json"

    def get(self, provider: str, key: str) -> Any | None:
        if self.mode not in ("live", "replay"):
            return None
        path = self._path(provider, key)
        try:
            payload = json.loads(path.read_text())
            if self.mode == "replay" or time.time() - payload["created_at"] <= self.ttl_seconds:
                return payload["value"]
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return None

    def put(self, provider: str, key: str, value: Any) -> None:
        if self.mode not in ("live", "record"):
            return
        encoded = json.dumps({"created_at": time.time(), "value": value}, ensure_ascii=False)
        if len(encoded) > 128_000:
            return
        path = self._path(provider, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + f".{uuid4().hex}.tmp")
        tmp.write_text(encoded)
        tmp.replace(path)


class FetchMemo:
    """Full extracted documents fetched during one research job, shared by all of its agents.

    Agents page through a memoized document instead of downloading it again, and a second
    agent reuses the first one's fetch, whatever the disk cache mode. Each job gets its own
    memo, so benchmark cases stay independent.
    """

    def __init__(self, max_documents: int = 64) -> None:
        self.max_documents = max_documents
        self._documents: OrderedDict[str, dict[str, Any]] = OrderedDict()

    def get(self, kind: str, url: str) -> dict[str, Any] | None:
        document = self._documents.get(f"{kind}|{url}")
        if document is not None:
            self._documents.move_to_end(f"{kind}|{url}")
        return document

    def put(self, kind: str, url: str, document: dict[str, Any]) -> None:
        self._documents[f"{kind}|{url}"] = document
        self._documents.move_to_end(f"{kind}|{url}")
        while len(self._documents) > self.max_documents:
            self._documents.popitem(last=False)


def fetch_window(text: str, start: int, max_chars: int) -> dict[str, Any] | None:
    """The `max_chars` characters of `text` from `start`, or None when `start` is past the end."""
    if start and start >= len(text):
        return None
    end = start + max_chars
    more = end < len(text)
    return {"text": text[start:end], "start": start, "total_chars": len(text), "truncated": more,
            "next_start": end if more else None}


def fetch_cache_key(url: str, max_chars: int, start: int) -> str:
    # Windows from the start keep the version-1 key, so earlier recordings still replay.
    return f"{url}|max_chars={max_chars}" + (f"|start={start}" if start else "")


_rate_lock = threading.Lock()
_next_request_at: dict[str, float] = {}
_RATE_INTERVAL = {"openalex": 0.2, "crossref": 0.2, "arxiv": 3.0, "opencitations": 0.3, "acl": 0.3,
                  "duckduckgo": 1.0}


async def wait_rate_slot(provider: str) -> None:
    with _rate_lock:
        now = time.monotonic()
        reserved = max(now, _next_request_at.get(provider, now))
        _next_request_at[provider] = reserved + _RATE_INTERVAL[provider]
    if reserved > now:
        await asyncio.sleep(reserved - now)


_ARXIV_ID = re.compile(r"\d{4}\.\d{4,5}")


def _location(url: str) -> tuple[str, str, str, str | None] | None:
    """(host, path, query, arXiv ID) of a URL, normalized for blocked-source matching."""
    url = url.strip()
    parsed = urlparse(url if "://" in url else "https://" + url)
    host = (parsed.hostname or "").removeprefix("www.")
    if "." not in host:
        return None
    host = "doi.org" if host == "dx.doi.org" else host
    path = unquote(parsed.path).rstrip("/").lower()
    arxiv = _ARXIV_ID.search(path) if host.endswith("arxiv.org") else None
    return host, path, parsed.query.lower(), arxiv.group(0) if arxiv else None


class BlockedSource(ValueError):
    """A fetch named a source the task blocks, or was redirected to one."""

    def __init__(self, url: str, entry: str) -> None:
        super().__init__("source is blocked for this task")
        self.url, self.entry = url, entry


@dataclass(frozen=True)
class SourcePolicy:
    """Sources a task may not fetch or cite, such as a benchmark's blocked URLs.

    A URL matches a blocked entry when, ignoring scheme, `www.`, letter case, fragment, and a
    trailing slash, it has the entry's host and the entry's path or a path beneath it. An entry
    with a query also needs that query, and an entry without a path blocks its whole host. An
    arXiv entry matches every form of the paper (abs, pdf, html, any version), and doi.org
    matches dx.doi.org. Matching errs toward blocking.
    """

    blocked: tuple[str, ...] = ()

    def blocks(self, url: str) -> str | None:
        """The blocked entry that `url` matches, if any."""
        target = _location(url)
        if target is None:
            return None
        for entry in self.blocked:
            rule = _location(entry)
            if rule and _covers(rule, target):
                return entry
        return None

    def check(self, url: str) -> None:
        if entry := self.blocks(url):
            raise BlockedSource(url, entry)


def _covers(rule: tuple[str, str, str, str | None], target: tuple[str, str, str, str | None]) -> bool:
    host, path, query, arxiv = rule
    if arxiv and target[3]:
        return arxiv == target[3]
    return (host == target[0]
            and (not path or target[1] == path or target[1].startswith(path + "/"))
            and (not query or query == target[2]))


# Sites such as Wikimedia reject the default library User-Agent; identify the fetcher instead.
FETCH_USER_AGENT = "research-loop/0.5 (research agent page fetcher)"


async def read_capped(response: httpx.Response, max_bytes: int) -> httpx.Response:
    """A streamed response read into a complete one, stopping once it passes `max_bytes`."""
    chunks = []
    total = 0
    async for chunk in response.aiter_bytes():  # decoded bytes, so the cap bounds decompression
        total += len(chunk)
        if total > max_bytes:
            raise ValueError("response exceeded size limit")
        chunks.append(chunk)
    # The body is already decoded: drop encoding headers or httpx would decode it again.
    headers = [(key, value) for key, value in response.headers.multi_items()
               if key.lower() not in ("content-encoding", "content-length")]
    return httpx.Response(response.status_code, headers=headers, content=b"".join(chunks), request=response.request)


async def bounded_public_get(client: httpx.AsyncClient, url: str, max_bytes: int,
                             policy: SourcePolicy | None = None) -> httpx.Response:
    """Download a public HTTPS URL, following at most three redirects, each checked like the first."""
    for _ in range(4):
        if policy:
            policy.check(url)  # before the DNS check, so a blocked host is never resolved
        if not await public_url(url):
            raise ValueError("unsafe URL")
        async with client.stream("GET", url, headers={"User-Agent": FETCH_USER_AGENT},
                                 follow_redirects=False, timeout=15) as response:
            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    raise ValueError("redirect missing location")
                url = urljoin(url, location)
                continue
            return await read_capped(response, max_bytes)
    raise ValueError("too many redirects")


async def public_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        return False
    try:
        addresses = await asyncio.to_thread(socket.getaddrinfo, parsed.hostname, parsed.port or 443)
        return bool(addresses) and all(ipaddress.ip_address(item[4][0]).is_global for item in addresses)
    except (OSError, ValueError):
        return False

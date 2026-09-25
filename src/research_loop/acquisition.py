"""Shared acquisition infrastructure for the web and scholarly research tools.

Disk cache, per-job document memo, text windows, per-provider rate slots, the task's blocked
sources, and the guarded public-HTTPS download that every fetch goes through. Provider adapters
live in scholar.py and web.py.
"""
from __future__ import annotations

import asyncio
import functools
import hashlib
import ipaddress
import json
import re
import socket
import ssl
import threading
import time
import urllib.request
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import unquote, urljoin, urlparse
from uuid import uuid4

import httpcore
import httpx

# Part of every cache key; bump it to invalidate recorded entries.
CACHE_VERSION = 1
# Recorded on every run. 1: fetch returned only the first 12,000 characters.
# 2: fetch pages through a document with `start`, backed by a per-job memo.
# 3: fetches refuse the task's blocked sources, including redirects to them.
# 4: scholar_search year bounds also filter arXiv and Crossref, not only OpenAlex.
# 5: web_fetch reads PDFs, and both fetches know a PDF by its signature as well as its content type.
# 6: web_fetch retries a 403 once as a browser, hints after a 404, and stops trying a host that did not
#    answer twice; web search tells no results from an outage and tries three times.
# 7: one fetch tool for pages and PDFs; every result says its access level (snippet, metadata,
#    abstract, full text); scholarly records carry OpenAlex abstracts.
FETCH_VERSION = 7


def is_pdf(media: str, content: bytes) -> bool:
    """A PDF by its declared type or, as servers often send one as octet-stream, by its first bytes."""
    return media == "application/pdf" or content[:5] == b"%PDF-"
# Longest text window one fetch returns; `start` pages through the rest.
MAX_FETCH_CHARS = 12_000
CacheMode = Literal["off", "live", "record", "replay", "reuse"]


class AcquisitionCache:
    """Small per-request cache; benchmark mode defaults to off.

    `live` reads entries up to `ttl_seconds` old and writes; `record` only writes; `replay` reads
    entries of any age and never writes, so its callers treat a miss as an error; `reuse` reads
    entries of any age and writes, so a miss goes live and is recorded. `off` does neither.
    """

    def __init__(self, root: Path, mode: CacheMode = "live", ttl_seconds: int = 86400) -> None:
        self.root, self.mode, self.ttl_seconds = root, mode, ttl_seconds

    def _path(self, provider: str, key: str) -> Path:
        digest = hashlib.sha256(f"v{CACHE_VERSION}:{provider}:{key}".encode()).hexdigest()
        return self.root / provider / f"{digest}.json"

    def get(self, provider: str, key: str) -> Any | None:
        if self.mode not in ("live", "replay", "reuse"):
            return None
        path = self._path(provider, key)
        try:
            payload = json.loads(path.read_text())
            if self.mode != "live" or time.time() - payload["created_at"] <= self.ttl_seconds:
                return payload["value"]
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return None

    def put(self, provider: str, key: str, value: Any) -> None:
        if self.mode not in ("live", "record", "reuse"):
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
        # Hosts whose connections failed in this job, and how often; see UNREACHABLE_AFTER.
        self.connection_failures: dict[str, int] = {}

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
_RATE_INTERVAL = {"openalex": 0.2, "crossref": 0.2, "arxiv": 3.0, "duckduckgo": 1.0}


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
# Sent once, after a site refuses FETCH_USER_AGENT with a 403. In the settings-study pilots 108 of 725
# fetches were 403s, and 5 of 12 of those sites, ssa.gov among them, served the page to a browser's
# User-Agent, though not to the fetcher's own with browser Accept headers; decided 25 September 2026.
BROWSER_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36"
BROWSER_HEADERS = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/pdf;q=0.8,*/*;q=0.7",
                   "Accept-Language": "en-US,en;q=0.9"}
# Connection failures (refused, timed out) to one host after which a job's fetches stop trying it.
UNREACHABLE_AFTER = 2


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
                             policy: SourcePolicy | None = None, *, as_browser: bool = False) -> httpx.Response:
    """Download a public HTTPS URL, following at most three redirects, each checked like the first.

    `as_browser` sends BROWSER_USER_AGENT and browser Accept headers in place of the fetcher's own.
    """
    headers = {"User-Agent": BROWSER_USER_AGENT, **BROWSER_HEADERS} if as_browser else {"User-Agent": FETCH_USER_AGENT}
    for _ in range(4):
        if policy:
            policy.check(url)  # before the DNS check, so a blocked host is never resolved
        if not await public_url(url):
            raise ValueError("unsafe URL")
        async with client.stream("GET", url, headers=headers,
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
    return bool(await _global_addresses(parsed.hostname, parsed.port or 443))


async def _global_addresses(host: str, port: int) -> list[str]:
    """The addresses `host` resolves to, or none unless every one of them is public."""
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, host, port, type=socket.SOCK_STREAM)
        addresses = list(dict.fromkeys(str(info[4][0]) for info in infos))
        return addresses if all(ipaddress.ip_address(address).is_global for address in addresses) else []
    except (OSError, ValueError):
        return []


class _PublicOnlyBackend(httpcore.AsyncNetworkBackend):
    """Connects to the public address it checked, so a second DNS answer cannot redirect it.

    The URL check before a download resolves the host separately; a rebinding DNS server could
    answer that lookup with a public address and the connection's with a private one. TLS still
    verifies the certificate against the URL's hostname, which httpcore passes to start_tls.
    """

    def __init__(self) -> None:
        self._inner = httpcore.AnyIOBackend()

    async def connect_tcp(self, host: str, port: int, timeout: float | None = None,
                          local_address: str | None = None, socket_options: Any = None) -> httpcore.AsyncNetworkStream:
        addresses = await _global_addresses(host, port)
        if not addresses:
            raise ValueError("unsafe URL")
        error: Exception | None = None
        for address in addresses:
            try:
                return await self._inner.connect_tcp(address, port, timeout, local_address, socket_options)
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                error = exc
        assert error is not None
        raise error

    async def connect_unix_socket(self, path: str, timeout: float | None = None,
                                  socket_options: Any = None) -> httpcore.AsyncNetworkStream:
        raise ValueError("unsafe URL")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


@functools.cache
def shared_ssl_context() -> ssl.SSLContext:
    """One verifying TLS context for every client; building one loads the certificate bundle."""
    return httpx.create_ssl_context()


class _PublicOnlyTransport(httpx.AsyncHTTPTransport):
    def __init__(self) -> None:
        # httpx takes no network backend, so this builds the one attribute its transport uses,
        # the pool, with one; the parent constructor would load TLS certificates for a discarded pool.
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=shared_ssl_context(), max_connections=10, keepalive_expiry=5.0,
            network_backend=_PublicOnlyBackend(),
        )


def public_fetch_client(timeout: float) -> httpx.AsyncClient:
    """A client for bounded_public_get that connects only to public addresses.

    With an HTTPS proxy in the environment the proxy makes the connection and is the egress
    boundary, so the client keeps using it and the URL check in bounded_public_get is the only one.
    """
    proxies = urllib.request.getproxies()
    if "https" in proxies or "all" in proxies:
        return httpx.AsyncClient(timeout=timeout, follow_redirects=False, verify=shared_ssl_context())
    return httpx.AsyncClient(transport=_PublicOnlyTransport(), timeout=timeout, follow_redirects=False)

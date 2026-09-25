"""Blocked sources: one matching rule, enforced in the fetch tool before the cache, DNS, or any request."""
from __future__ import annotations

import httpx
import pytest

from research_loop.acquisition import AcquisitionCache, SourcePolicy
from research_loop.web import WebAcquisition

REPORT = "https://www.example.org/reports/2024/"
SITE = "https://blocked.example"
PAPER = "https://arxiv.org/abs/2310.06770"
DOI = "https://doi.org/10.1234/x"
QUERY = "https://example.org/page?id=7"
POLICY = SourcePolicy((REPORT, SITE, PAPER, DOI, QUERY))


@pytest.mark.parametrize(("url", "entry"), [
    ("http://example.org/reports/2024", REPORT),                     # scheme, www, trailing slash
    ("https://EXAMPLE.org/Reports/2024/appendix#table-2", REPORT),   # case, a path beneath, fragment
    ("https://blocked.example/anything/at/all", SITE),               # an entry without a path blocks the host
    ("https://arxiv.org/pdf/2310.06770v2", PAPER),                   # any form and version of the paper
    ("https://export.arxiv.org/abs/2310.06770", PAPER),
    ("https://dx.doi.org/10.1234/x", DOI),
    ("https://example.org/page?id=7", QUERY),
])
def test_blocked_entries_match_every_form_of_the_source(url: str, entry: str) -> None:
    assert POLICY.blocks(url) == entry


@pytest.mark.parametrize("url", [
    "https://example.org/reports/2024-summary",   # a sibling path, not one beneath the entry
    "https://other.org/reports/2024",
    "https://notblocked.example/",
    "https://example.org/page?id=8",
    "https://arxiv.org/abs/2310.06771",
    "not a url",
])
def test_other_sources_are_not_blocked(url: str) -> None:
    assert POLICY.blocks(url) is None


@pytest.mark.asyncio
async def test_web_fetch_refuses_a_blocked_source_before_the_cache_dns_or_network(tmp_path) -> None:
    AcquisitionCache(tmp_path, "record").put("web", "https://blocked.example/doc|max_chars=12000",
                                             {"url": "https://blocked.example/doc", "text": "cached copy"})
    requested: list[str] = []
    transport = httpx.MockTransport(lambda request: requested.append(str(request.url)) or httpx.Response(200))
    async with httpx.AsyncClient(transport=transport) as http:
        fetcher = WebAcquisition(cache_root=tmp_path, cache_mode="live", client=http, policy=POLICY)
        result = await fetcher.fetch("https://blocked.example/doc")
    # The conftest guard also fails the test if the fetch resolved blocked.example.
    assert result == {"url": "https://blocked.example/doc", "error": "BlockedSource", "blocked": SITE}
    assert requested == []


@pytest.mark.asyncio
async def test_web_fetch_refuses_a_redirect_to_a_blocked_source(public_urls, tmp_path) -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://arxiv.org/pdf/2310.06770v1"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        fetcher = WebAcquisition(cache_root=tmp_path, cache_mode="off", client=http, policy=POLICY)
        result = await fetcher.fetch("https://mirror.example/paper")
    assert requested == ["https://mirror.example/paper"]  # the blocked destination is never requested
    assert (result["error"], result["blocked"]) == ("BlockedSource", PAPER)


@pytest.mark.asyncio
async def test_every_form_of_a_blocked_paper_is_refused_directly_and_after_redirects(public_urls, tmp_path) -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://dx.doi.org/10.1234/x"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        fetcher = WebAcquisition(cache_root=tmp_path, cache_mode="off", client=http, policy=POLICY)
        direct = await fetcher.fetch("https://arxiv.org/pdf/2310.06770")
        redirected = await fetcher.fetch("https://mirror.example/paper.pdf")
    assert (direct["error"], direct["blocked"]) == ("BlockedSource", PAPER)
    assert (redirected["error"], redirected["blocked"]) == ("BlockedSource", DOI)
    assert requested == ["https://mirror.example/paper.pdf"]

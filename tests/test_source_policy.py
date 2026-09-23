"""Blocked sources: one matching rule, enforced in the fetch tools and audited by kind of contact."""
from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from research_loop.acquisition import AcquisitionCache, SourcePolicy
from research_loop.schemas import SourceRef
from research_loop.scholar import ScholarClient
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
async def test_scholar_fetch_refuses_blocked_sources_directly_and_after_redirects(public_urls, tmp_path) -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://dx.doi.org/10.1234/x"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = ScholarClient(cache=AcquisitionCache(tmp_path, "off"), client=http, policy=POLICY)
        direct = await client.fetch("https://arxiv.org/pdf/2310.06770")
        redirected = await client.fetch("https://mirror.example/paper.pdf")
    assert (direct.provider_errors, direct.blocked_source) == (["fetch:BlockedSource"], PAPER)
    assert (redirected.provider_errors, redirected.blocked_source) == (["fetch:BlockedSource"], DOI)
    assert requested == ["https://mirror.example/paper.pdf"]


def test_audit_separates_refusals_completed_fetches_search_sightings_and_citations() -> None:
    from research_loop.benchmark import _audit_blocked_sources

    events = [
        {"tool_name": "duckduckgo_search", "args": {"query": "q"},
         "result": [{"href": "https://blocked.example/a", "title": "t"}, {"href": "https://fine.example/"}]},
        {"tool_name": "web_fetch", "args": {"url": "https://blocked.example/a"},
         "result": {"url": "https://blocked.example/a", "error": "BlockedSource", "blocked": SITE}},
        {"tool_name": "scholar_fetch", "args": {"url": "https://mirror.example/p"},
         "result": {"operation": "fetch", "provider_errors": ["fetch:BlockedSource"], "blocked_source": PAPER}},
        # A provider-native fetch the application could not refuse.
        {"tool_name": "web_fetch", "args": {"url": "https://example.org/reports/2024/x"}, "result": {"text": "page"}},
    ]
    sources = [SourceRef(url="http://example.org/reports/2024", title="Report"), SourceRef(url="https://fine.example", title="Fine")]
    assert _audit_blocked_sources(events, sources, POLICY) == {
        "blocked_fetches_refused": [PAPER, SITE],
        "blocked_fetches_completed": [REPORT],
        "blocked_sources_in_search": [SITE],
        "blocked_sources_cited": [REPORT],
    }


@pytest.mark.parametrize(("completed", "cited", "score"), [([], [], 1.0), ([REPORT], [], 0.0), ([], [REPORT], 0.0)])
def test_compliance_fails_only_on_blocked_content_that_was_fetched_or_cited(completed, cited, score) -> None:
    from research_loop.evals import BlockedSourceCompliance

    ctx = SimpleNamespace(inputs=SimpleNamespace(blocked_urls=[REPORT]),
                          output=SimpleNamespace(blocked_fetches_completed=completed, blocked_sources_cited=cited,
                                                 blocked_fetches_refused=[SITE], blocked_sources_in_search=[SITE]))
    assert BlockedSourceCompliance().evaluate(ctx) == score

"""The reading fallback, offline: our fetch fails, and other readers are tried in order (reading.py)."""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from research_loop.acquisition import SourcePolicy
from research_loop.reading import (
    ExaContentsReader,
    ExternalSpend,
    FirecrawlReader,
    OpenAccessReader,
    identifiers,
    usable,
)
from research_loop.study_budget import StudyBudget
from research_loop.web import WebAcquisition

ARTICLE = "Inverse design of materials. " * 60  # long enough to count as the page
PAGE = "https://www.mdpi.com/1996-1944/15/5/1811"


def test_identifiers_come_from_the_address_alone() -> None:
    assert identifiers("https://doi.org/10.1038/s41524-019-0221-0") == ("10.1038/s41524-019-0221-0", None)
    # RSC article IDs are DOI suffixes under 10.1039; the bake-off found 26 RSC pages our fetch failed on.
    assert identifiers("https://pubs.rsc.org/en/content/articlehtml/2023/mh/d3mh00039g") == ("10.1039/d3mh00039g", None)
    assert identifiers("https://arxiv.org/abs/2307.09288") == (None, "2307.09288")
    assert identifiers(PAGE) == (None, None)


def test_a_challenge_page_or_a_stub_is_not_the_page() -> None:
    assert usable(ARTICLE)
    assert not usable("Just a moment... checking your browser. " * 20)
    assert not usable("Too short.")


def _blocked_fetch(status: int = 403) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(status)))


def _services(sent: list[str], *, exa_text: str = "", firecrawl_text: str = ARTICLE) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(f"{request.url.host}{request.url.path}")
        if request.url.host == "api.exa.ai":
            body = json.loads(request.content)
            return httpx.Response(200, json={"results": [{"url": body["urls"][0], "text": exa_text}],
                                             "costDollars": {"total": 0.001}})
        if request.url.host == "api.firecrawl.dev":
            return httpx.Response(200, json={"success": True, "data": {"markdown": firecrawl_text,
                                                                      "metadata": {"statusCode": 200}}})
        return httpx.Response(404)
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_a_refused_page_is_read_by_the_first_reader_that_can(public_urls, tmp_path: Path) -> None:
    sent: list[str] = []
    spend, budget = ExternalSpend(), StudyBudget(Decimal("1.00"))
    async with _blocked_fetch() as own, _services(sent) as services:
        pages = WebAcquisition(cache_root=tmp_path, cache_mode="off", client=own)
        pages.fallbacks = [OpenAccessReader(services, pages._extract),
                           ExaContentsReader(services, "k", spend, budget),
                           FirecrawlReader(services, "k", spend, budget)]
        result = await pages.fetch(PAGE)
    # OpenAlex knows no DOI for this MDPI address, Exa has no text, so Firecrawl reads it.
    assert result["via"] == "firecrawl" and result["text"].startswith("Inverse design")
    assert sent == ["api.openalex.org/works", "api.exa.ai/contents", "api.firecrawl.dev/v2/scrape"]
    assert spend.pages == 2 and spend.usd == Decimal("0.001") + Decimal("0.0054")
    assert budget.reserved_usd == spend.usd  # reservations settled to what each read cost


@pytest.mark.asyncio
async def test_no_reader_is_tried_for_a_missing_page_or_a_blocked_one(public_urls, tmp_path: Path) -> None:
    sent: list[str] = []
    async with _blocked_fetch(404) as own, _services(sent) as services:
        pages = WebAcquisition(cache_root=tmp_path, cache_mode="off", client=own,
                               policy=SourcePolicy(("https://blocked.example/report",)))
        pages.fallbacks = [ExaContentsReader(services, "k", ExternalSpend()), FirecrawlReader(services, "k", ExternalSpend())]
        missing = await pages.fetch(PAGE)  # a 404 is usually a guessed address
        blocked = await pages.fetch("https://blocked.example/report")
    assert missing["status"] == 404 and "also_tried" not in missing
    assert blocked["error"] == "BlockedSource" and sent == []


@pytest.mark.asyncio
async def test_when_every_reader_fails_the_scout_is_told_what_was_tried(public_urls, tmp_path: Path) -> None:
    async with _blocked_fetch() as own, _services([], firecrawl_text="") as services:
        pages = WebAcquisition(cache_root=tmp_path, cache_mode="off", client=own)
        pages.fallbacks = [ExaContentsReader(services, "k", ExternalSpend()), FirecrawlReader(services, "k", ExternalSpend())]
        result = await pages.fetch(PAGE)
    assert result["status"] == 403 and result["also_tried"] == "exa: not found; firecrawl: not found"


@pytest.mark.asyncio
async def test_pages_read_by_the_fallback_are_cached_apart_from_our_own(public_urls, tmp_path: Path) -> None:
    async with _blocked_fetch() as own, _services([]) as services:
        with_fallback = WebAcquisition(cache_root=tmp_path, cache_mode="reuse", client=own)
        with_fallback.fallbacks = [FirecrawlReader(services, "k", ExternalSpend())]
        assert (await with_fallback.fetch(PAGE))["via"] == "firecrawl"
        # An arm without the fallback shares the study cache but never gets a page only the fallback read.
        without = WebAcquisition(cache_root=tmp_path, cache_mode="reuse", client=own)
        assert (await without.fetch(PAGE))["status"] == 403


@pytest.mark.asyncio
async def test_the_open_access_copy_comes_from_europe_pmc(public_urls, tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/search"):
            return httpx.Response(200, json={"resultList": {"result": [{"pmcid": "PMC123"}]}})
        if request.url.path.endswith("/fullTextXML"):
            return httpx.Response(200, text=f"<article><body><p>{ARTICLE}</p></body></article>")
        return httpx.Response(404)

    async with _blocked_fetch() as own, httpx.AsyncClient(transport=httpx.MockTransport(handler)) as services:
        pages = WebAcquisition(cache_root=tmp_path, cache_mode="off", client=own)
        pages.fallbacks = [OpenAccessReader(services, pages._extract)]
        result = await pages.fetch("https://pubs.acs.org/doi/10.1021/acs.chemmater.0c01234")
    assert result["via"] == "oa:europepmc" and "<p>" not in result["text"]


@pytest.mark.asyncio
async def test_an_mdpi_address_finds_its_doi_and_its_europe_pmc_copy(public_urls, tmp_path: Path) -> None:
    # MDPI answers our fetcher with a bot challenge, and 10 of 11 MDPI articles scouts failed to read had DOIs
    # in OpenAlex; without one, every MDPI page went to a paid reader.
    queries: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/works":
            queries.append(request.url.params["filter"])
            return httpx.Response(200, json={"results": [{"doi": "https://doi.org/10.3390/ma15051811"}]})
        if request.url.path.endswith("/search"):
            assert request.url.params["query"] == 'DOI:"10.3390/ma15051811"'
            return httpx.Response(200, json={"resultList": {"result": [{"pmcid": "PMC8911677"}]}})
        if request.url.path.endswith("/fullTextXML"):
            return httpx.Response(200, text=f"<article><body><p>{ARTICLE}</p></body></article>")
        return httpx.Response(404)

    async with _blocked_fetch() as own, httpx.AsyncClient(transport=httpx.MockTransport(handler)) as services:
        pages = WebAcquisition(cache_root=tmp_path, cache_mode="off", client=own)
        pages.fallbacks = [OpenAccessReader(services, pages._extract)]
        result = await pages.fetch(PAGE + "/htm")
    assert result["via"] == "oa:europepmc"
    assert queries == ["primary_location.source.issn:1996-1944,biblio.volume:15,biblio.issue:5,biblio.first_page:1811"]


@pytest.mark.asyncio
async def test_a_repository_copy_is_read_when_the_best_location_refuses(serve, tmp_path: Path) -> None:
    base = "https://papers.example"
    serve(lambda url: httpx.Response(200, headers={"content-type": "text/html"},
                                     text=f"<html><body><p>{ARTICLE}</p></body></html>")
          if url.endswith("/eprints/paper.html") else httpx.Response(403))

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.openalex.org":
            return httpx.Response(200, json={
                "best_oa_location": {"pdf_url": f"{base}/publisher.pdf"},
                "locations": [{"pdf_url": f"{base}/publisher.pdf"}, {"landing_page_url": "https://doaj.org/article/x"},
                              {"pdf_url": f"{base}/eprints/paper.html"}]})
        return httpx.Response(200, json={"resultList": {"result": []}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as services:
        pages = WebAcquisition(cache_root=tmp_path, cache_mode="off")
        document = await OpenAccessReader(services, pages._extract).read("https://doi.org/10.3390/app9071417")
    assert document is not None and document["via"] == "oa:openalex" and "Inverse design" in document["text"]


def test_the_fallback_needs_the_keys_of_its_paid_readers(monkeypatch) -> None:
    from research_loop.config import Settings

    for name in ("EXA_API_KEY", "FIRECRAWL_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    problems = Settings(read_fallback="oa,exa,firecrawl", _env_file=None).route_problems()
    assert "reading fallback: exa needs EXA_API_KEY" in problems
    assert "reading fallback: firecrawl needs FIRECRAWL_API_KEY" in problems
    with pytest.raises(ValueError, match="unknown readers"):
        Settings(read_fallback="oa,jina", _env_file=None)


def test_unset_the_fallback_is_every_reader_that_can_run(monkeypatch) -> None:
    from research_loop.config import Settings

    for name in ("EXA_API_KEY", "FIRECRAWL_API_KEY", "RESEARCH_READ_FALLBACK"):
        monkeypatch.delenv(name, raising=False)
    # The free open-access reader always, and a paid reader only once its key is set.
    assert Settings(_env_file=None).readers() == ("oa",)
    monkeypatch.setenv("EXA_API_KEY", "k")
    assert Settings(_env_file=None).readers() == ("oa", "exa")
    monkeypatch.setenv("FIRECRAWL_API_KEY", "k")
    assert Settings(_env_file=None).readers() == ("oa", "exa", "firecrawl")
    assert not [p for p in Settings(_env_file=None).route_problems() if p.startswith("reading fallback")]
    # An empty value turns it off, and a named list is used as given.
    monkeypatch.setenv("RESEARCH_READ_FALLBACK", "")
    assert Settings(_env_file=None).readers() == ()
    monkeypatch.setenv("RESEARCH_READ_FALLBACK", "firecrawl,oa")
    assert Settings(_env_file=None).readers() == ("firecrawl", "oa")


def test_a_scouts_outcomes_say_which_reader_read_a_page() -> None:
    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        ToolCallPart,
        ToolReturnPart,
    )

    from research_loop.tools import tool_outcomes

    content = {"url": PAGE, "text": ARTICLE, "start": 0, "extraction": "firecrawl-markdown", "via": "firecrawl"}
    messages = [ModelResponse(parts=[ToolCallPart("fetch", {"url": PAGE}, tool_call_id="x")]),
                ModelRequest(parts=[ToolReturnPart("fetch", content, tool_call_id="x")])]
    outcomes = tool_outcomes(messages)
    assert outcomes.pages_read == [PAGE] and outcomes.read_via == {PAGE: "firecrawl"}


@pytest.fixture
def core_unpaced(monkeypatch):
    from research_loop import acquisition, reading

    monkeypatch.setitem(acquisition._RATE_INTERVAL, "core", 0)
    monkeypatch.setattr(reading, "_core_resume_at", 0.0)
    return reading


@pytest.mark.asyncio
async def test_core_full_text_is_read_last_and_only_for_the_same_doi(public_urls, core_unpaced) -> None:
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.core.ac.uk":
            asked.append(request.headers.get("authorization", ""))
            doi = request.url.params["q"].removeprefix('doi:"').removesuffix('"')
            found = "10.3390/other" if doi == "10.3390/mismatch" else doi
            return httpx.Response(200, json={"results": [{"doi": found, "fullText": ARTICLE}]})
        return httpx.Response(404)  # no Europe PMC copy, no OpenAlex record

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as services:
        reader = OpenAccessReader(services, _never_extract, core_key="k")
        document = await reader.read("https://doi.org/10.3390/app9071417")
        assert document is not None and document["via"] == "oa:core"
        assert await reader.read("https://doi.org/10.3390/mismatch") is None
    assert asked == ["Bearer k", "Bearer k"]


@pytest.mark.asyncio
async def test_core_is_not_asked_again_until_its_limit_resets(public_urls, core_unpaced) -> None:
    asked = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal asked
        if request.url.host != "api.core.ac.uk":
            return httpx.Response(404)
        asked += 1
        return httpx.Response(429, headers={"x-ratelimit-remaining": "0",
                                            "x-ratelimit-retry-after": "2999-01-01T00:00:00+0000"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as services:
        reader = OpenAccessReader(services, _never_extract)
        assert await reader.read("https://doi.org/10.3390/app9071417") is None
        assert await reader.read("https://doi.org/10.3390/app11093835") is None
    assert asked == 1 and core_unpaced._core_resume_at > 1e10


async def _never_extract(url: str) -> dict:
    raise ValueError("no page")

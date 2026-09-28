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
    # No identifier in an MDPI address, Exa has no text, so Firecrawl reads it.
    assert result["via"] == "firecrawl" and result["text"].startswith("Inverse design")
    assert sent == ["api.exa.ai/contents", "api.firecrawl.dev/v2/scrape"]
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

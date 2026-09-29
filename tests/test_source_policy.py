"""Blocked sources: one matching rule, enforced in every research tool and on a scout's evidence; the fetch
tool refuses before the cache, DNS, or any request."""
from __future__ import annotations

import json

import httpx
import pytest
from pydantic_ai.messages import ModelRequest, ToolReturnPart

from research_loop.acquisition import MAX_FETCH_CHARS, AcquisitionCache, SourcePolicy
from research_loop.schemas import SourceRef
from research_loop.scholar import ScholarResponse, ScholarWork
from research_loop.tools import blocked_shown, research_toolset
from research_loop.web import WebAcquisition, WebSearch

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
    ("https://mirror.example/article/10.1234/x", DOI),               # a copy elsewhere, named by the blocked DOI
    ("https://publisher.example/doi/pdf/10.1234/X/", DOI),
])
def test_blocked_entries_match_every_form_of_the_source(url: str, entry: str) -> None:
    assert POLICY.blocks(url) == entry


@pytest.mark.parametrize("url", [
    "https://example.org/reports/2024-summary",   # a sibling path, not one beneath the entry
    "https://other.org/reports/2024",
    "https://notblocked.example/",
    "https://example.org/page?id=8",
    "https://arxiv.org/abs/2310.06771",
    "https://mirror.example/article/10.1234/xy",  # another DOI that begins with the blocked one
    "not a url",
])
def test_other_sources_are_not_blocked(url: str) -> None:
    assert POLICY.blocks(url) is None


@pytest.mark.asyncio
async def test_web_fetch_refuses_a_blocked_source_before_the_cache_dns_or_network(tmp_path) -> None:
    AcquisitionCache(tmp_path, "record").put("web", f"https://blocked.example/doc|max_chars={MAX_FETCH_CHARS}",
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


@pytest.mark.parametrize(("work", "entry"), [
    ({"doi": "10.1234/X"}, DOI),                                              # a DOI alone, as a citation may give it
    ({"doi": "https://doi.org/10.1234/x"}, DOI),
    ({"arxiv_id": "2310.06770v3"}, PAPER),
    ({"urls": ("https://doi.org/10.9999/other", "https://example.org/reports/2024/full.pdf")}, REPORT),
    ({"doi": "10.1234/x/full"}, DOI),                                         # a DOI taken from a path with the rest of it
])
def test_a_work_is_blocked_by_any_of_its_identifiers(work: dict, entry: str) -> None:
    assert POLICY.blocks_work(**work) == entry


def test_a_blocked_doi_taken_from_a_path_still_matches_the_bare_doi() -> None:
    # drb2-task59 blocks frontiersin.org/journals/physiology/articles/10.3389/fphys.2021.667000/full, whose path
    # gives the DOI with "/full" on the end; a scholarly record gives the DOI alone.
    policy = SourcePolicy(("https://www.frontiersin.org/journals/physiology/articles/10.3389/fphys.2021.667000/full",))
    assert policy.blocks_work(doi="10.3389/fphys.2021.667000")
    assert policy.blocks_work(doi="10.3389/fphys.2021.66700") is None
    assert SourcePolicy().blocks_work(doi="10.1234/x") is None


class _Scholar:
    """A scholarly client that returns a blocked work beside an allowed one, and records what it was asked."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    async def _works(self) -> ScholarResponse:
        return ScholarResponse(works=[
            ScholarWork(provider="stub", title="The blocked review", doi="10.1234/x", abstract="Blocked text"),
            ScholarWork(provider="stub", title="Its copy", url="https://example.org/reports/2024/copy.pdf"),
            ScholarWork(provider="stub", title="Another paper", doi="10.5555/ok", abstract="Allowed text")])

    async def search(self, query, year_from=None, year_to=None, limit=5) -> ScholarResponse:
        self.asked.append(query)
        return await self._works()

    async def get(self, identifier: str) -> ScholarResponse:
        self.asked.append(identifier)
        return await self._works()


async def test_scholarly_tools_leave_out_and_refuse_blocked_works(tmp_path) -> None:
    # The audit's case (docs/architectural-audit-2026-09-27.md, F06): scholar_get returned a blocked DOI's abstract.
    scholar = _Scholar()
    tools = research_toolset(WebSearch(engine=None, retry_delays=()),
                             WebAcquisition(cache_root=tmp_path, cache_mode="off", policy=POLICY), scholar).tools
    found = await tools["scholar_search"].function("materials inverse design")
    assert [work["title"] for work in found["works"]] == ["Another paper"]
    refused = await tools["scholar_get"].function("10.1234/X")
    assert refused["error"] == "BlockedSource" and refused["blocked"] == DOI and scholar.asked == ["materials inverse design"]
    looked_up = await tools["scholar_get"].function("W123")
    assert [work["title"] for work in looked_up["works"]] == ["Another paper"]


def test_a_scouts_evidence_citing_a_blocked_work_by_doi_alone_is_refused() -> None:
    from types import SimpleNamespace

    from pydantic_ai import ModelRetry

    from research_loop.agents import Assignment, _result_fits_assignment
    from research_loop.schemas import (
        Claim,
        Evidence,
        ResearchQuestion,
        ResearchResult,
        SourceRef,
    )

    def result(source: SourceRef) -> ResearchResult:
        evidence = Evidence(source=source, excerpt="e", quote="Blocked text", confidence=1)
        return ResearchResult(question_id="q1", question="Q?", conclusion="c", confidence=1,
                              claims=[Claim(id="c1", statement="s", evidence=[evidence], confidence=1)])

    ctx = SimpleNamespace(deps=Assignment(ResearchQuestion(id="q1", question="Q?"), POLICY))
    for source in (SourceRef(doi="10.1234/x", title="Review"), SourceRef(arxiv_id="2310.06770", title="Paper"),
                   SourceRef(url="https://mirror.example/10.1234/x", title="Copy")):
        with pytest.raises(ModelRetry, match="blocked for this task"):
            _result_fits_assignment(ctx, result(source))
    assert _result_fits_assignment(ctx, result(SourceRef(doi="10.5555/ok", title="Other"))).claims


@pytest.mark.asyncio
async def test_a_copy_that_prints_a_blocked_doi_is_refused_even_from_the_cache(public_urls, tmp_path) -> None:
    # drb2-task8's blocked review was read in full five times from cdn.techscience.cn, an address that names
    # neither a blocked entry nor the DOI; its first page prints the DOI (docs/study-log.md, 27 September 2026).
    body = "<html><body><article><p>Review. doi:10.1234/x</p><p>" + "Inverse design text. " * 200 + "</p></article></body></html>"
    copy = "https://cdn.example/files/review.pdf"

    async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, headers={"content-type": "text/html"}, text=body))) as http:
        # Recorded in a study cache before this rule existed.
        unblocked = WebAcquisition(cache_root=tmp_path, cache_mode="reuse", client=http)
        assert "text" in await unblocked.fetch(copy, max_chars=1000)
        assert "text" in await unblocked.fetch(copy, max_chars=1000, start=1000)
        fresh = await WebAcquisition(cache_root=tmp_path / "fresh", cache_mode="off", client=http,
                                     policy=POLICY).fetch(copy)
        blocked = WebAcquisition(cache_root=tmp_path, cache_mode="reuse", client=http, policy=POLICY)
        first, later = await blocked.fetch(copy, max_chars=1000), await blocked.fetch(copy, max_chars=1000, start=1000)
    for result in (fresh, first, later):
        assert (result["error"], result["blocked"]) == ("BlockedSource", DOI)
    assert "text" in await WebAcquisition(cache_root=tmp_path, cache_mode="reuse", policy=SourcePolicy(
        ("https://doi.org/10.9999/other",))).fetch(copy, max_chars=1000)


# drb2-task8's blocked review, which Exa search returned twice in its top three results, and whose CDN copy
# carries neither a blocked address nor the DOI (docs/study-log.md, 28 September 2026).
TITLE = "Machine Learning-Based Methods for Materials Inverse Design: A Review"
TITLED = SourcePolicy((DOI,), titles=(TITLE,))


def test_a_blocked_title_matches_its_copies_whatever_the_case_punctuation_or_spacing() -> None:
    blocked = f"title: {TITLE}"
    assert TITLED.blocks_title("CMC | Machine Learning-Based Methods for Materials Inverse Design: A Review") == blocked
    assert TITLED.blocks_title("machine learning based methods for materials inverse design  a review (2025)") == blocked
    assert TITLED.blocks_title("Machine learning-based inverse design methods considering data characteristics") is None
    # A title is matched whole, on word boundaries, and a short one is never matched.
    assert TITLED.blocks_title("Machine Learning-Based Methods for Materials Inverse Designers: A guide") is None
    assert SourcePolicy(titles=("A Review",)).blocks_title("A review of everything") is None
    assert SourcePolicy().blocks_title(TITLE) is None
    assert TITLED.blocks_work(urls=("https://cdn.example/x.pdf",), title=TITLE) == blocked


def test_a_search_result_under_a_shortened_title_or_printing_the_doi_is_blocked() -> None:
    """Search engines cut long titles and add their site's name, so the whole title never appears. These are the
    results that showed drb2-task8's blocked review to scouts in 25 of 42 fetch-v18 runs (study log,
    29 September 2026)."""
    blocked = f"title: {TITLE}"
    for title in ("Machine Learning-Based Methods for Materials Inverse Design ...",
                  "Machine Learning-Based Methods for Materials Inverse Design …",
                  "Machine Learning-Based Methods for Materials Inverse Design",
                  "Machine Learning-Based Methods for Materials - ProQuest",
                  "Machine Learning-Based Methods for Materials Inverse Design | PDF"):
        assert TITLED.blocks_result(title, "three main inverse design methods") == blocked, title
    review = "https://doi.org/10.32604/cmc.2025.060109"
    assert SourcePolicy((review,)).blocks_result(
        "Machine Learning-Based Methods for Materials - ProQuest",
        "Computers, Materials, & Continua; Vol. 82, Iss. 2, (2025): 1463-1492. DOI:10.32604/cmc.2025.060109 Download") == review
    # Other works that share only the opening words, or a short prefix, still pass.
    for title in ("Machine learning-based inverse design methods considering data characteristics ...",
                  "Machine Learning-Based Methods for Materials Discovery and Design",
                  "Machine Learning-Based Methods ...",
                  "[2210.11931] Deep Reinforcement Learning for Inverse ..."):
        assert TITLED.blocks_result(title, "An unrelated abstract") is None, title
    assert SourcePolicy().blocks_result(TITLE, "") is None
    # A scout cited both of these for the review's three strategies in the v14 search study; its evidence is refused.
    for cited in ("Machine Learning-Based Methods for Materials - ProQuest",
                  "Scholar - SciOpen record for Machine Learning-Based Methods for Materials Inverse Design"):
        assert TITLED.blocks_work(urls=("https://www.proquest.com/docview/3199833354",), title=cited) == blocked, cited


@pytest.mark.asyncio
async def test_a_copy_known_only_by_its_title_is_left_out_of_search_scholarship_and_reading(public_urls,
                                                                                           tmp_path) -> None:
    async def engine(query: str) -> list[dict[str, str]]:
        return [{"title": TITLE, "href": "https://cdn.example/review.pdf", "body": "Exploration-based methods..."},
                {"title": "A mirror", "href": "https://mirror.example/r", "body": f"{TITLE}. Abstract: ..."},
                {"title": "Another review", "href": "https://ok.example/r", "body": "Inverse design survey"}]

    found = await WebSearch(engine=engine, retry_delays=(), policy=TITLED).search("materials inverse design review")
    assert [item["url"] for item in found["results"]] == ["https://ok.example/r"]

    class Scholar(_Scholar):
        async def _works(self) -> ScholarResponse:
            return ScholarResponse(works=[
                ScholarWork(provider="stub", title=TITLE, url="https://cdn.example/review.pdf", abstract="Blocked"),
                ScholarWork(provider="stub", title="Another paper", doi="10.5555/ok", abstract="Allowed text")])

    tools = research_toolset(WebSearch(engine=None, retry_delays=()),
                             WebAcquisition(cache_root=tmp_path, cache_mode="off", policy=TITLED), Scholar()).tools
    assert [w["title"] for w in (await tools["scholar_search"].function("inverse design"))["works"]] == ["Another paper"]

    # A page whose opening prints the title is refused; one that only cites it further down is read.
    pages = {"https://cdn.example/review.pdf": f"<p>Computers, Materials &amp; Continua. {TITLE}.</p><p>{'Text. ' * 400}</p>",
             "https://ok.example/citing": f"<p>{'Our own study of inverse design. ' * 200}</p><p>[12] {TITLE}.</p>"}
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(
            200, headers={"content-type": "text/html"},
            text=f"<html><body><article>{pages[str(request.url)]}</article></body></html>"))) as http:
        fetcher = WebAcquisition(cache_root=tmp_path, cache_mode="off", client=http, policy=TITLED)
        copy = await fetcher.fetch("https://cdn.example/review.pdf")
        citing = await fetcher.fetch("https://ok.example/citing")
    assert (copy["error"], copy["blocked"]) == ("BlockedSource", f"title: {TITLE}")
    assert "text" in citing


def test_a_scouts_evidence_citing_a_blocked_work_by_its_title_is_refused() -> None:
    from types import SimpleNamespace

    from pydantic_ai import ModelRetry

    from research_loop.agents import Assignment, _result_fits_assignment
    from research_loop.schemas import Claim, Evidence, ResearchQuestion, ResearchResult

    evidence = Evidence(source=SourceRef(url="https://cdn.example/review.pdf", title=TITLE), excerpt="e",
                        quote="q", confidence=1)
    result = ResearchResult(question_id="q1", question="Q?", conclusion="c", confidence=1,
                            claims=[Claim(id="c1", statement="s", evidence=[evidence], confidence=1)])
    with pytest.raises(ModelRetry, match="blocked for this task"):
        _result_fits_assignment(SimpleNamespace(deps=Assignment(ResearchQuestion(id="q1", question="Q?"), TITLED)),
                                result)


def test_a_runs_stored_messages_show_what_an_older_policy_let_through() -> None:
    def returned(tool: str, content: dict) -> ModelRequest:
        return ModelRequest(parts=[ToolReturnPart(tool_name=tool, content=json.dumps(content), tool_call_id=tool)])

    messages = [
        returned("web_search", {"results": [
            {"title": "Machine Learning-Based Methods for Materials Inverse Design ...", "url": "https://cdn.example/a.pdf",
             "snippet": "Then, three main inverse design methods"},
            {"title": "Another review", "url": "https://ok.example/r", "snippet": "Inverse design survey"}]}),
        returned("fetch", {"url": "https://cdn.example/b.pdf", "start": 0, "text": f"CMC. {TITLE}. Abstract ..."}),
        returned("fetch", {"url": "https://ok.example/r", "start": 0, "text": "Our own study of inverse design."}),
        returned("scholar_search", {"works": [{"title": TITLE, "url": "https://cdn.example/c"},
                                              {"title": "Another paper", "doi": "10.5555/ok"}]}),
    ]
    assert blocked_shown(messages, TITLED) == [
        "web_search: Machine Learning-Based Methods for Materials Inverse Design ...",
        "fetch: https://cdn.example/b.pdf", f"scholar_search: {TITLE}"]
    assert blocked_shown(messages, SourcePolicy()) == []

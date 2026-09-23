from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from research_loop.acquisition import AcquisitionCache
from research_loop.citations import (
    SemanticScholar,
    discover_basis_papers,
    render_basis_papers,
    seeds_from_bibliography,
)


def test_seeds_come_from_arxiv_ids_and_dois_in_fields_or_urls() -> None:
    seeds, without = seeds_from_bibliography([
        {"title": "SWE-bench", "url": "https://arxiv.org/abs/2310.06770v3"},
        {"title": "SWE-bench, accepted", "arxiv_id": "2310.06770", "doi": None},    # same lookup, kept once
        {"title": "SWE-Bench+", "url": "https://doi.org/10.48550/arXiv.2410.06992"},  # arXiv DOI -> arXiv ID
        {"title": "UTBoost", "doi": "10.18653/V1/2025.ACL-LONG.189"},
        {"title": "A DOI link", "url": "https://doi.org/10.1145/3597503.3639187"},
        {"title": "Leaderboard", "url": "https://www.swebench.com/"},                 # web page: no identifier
    ])
    assert seeds == {
        "arXiv:2310.06770": "SWE-bench",
        "arXiv:2410.06992": "SWE-Bench+",
        "DOI:10.18653/v1/2025.acl-long.189": "UTBoost",
        "DOI:10.1145/3597503.3639187": "A DOI link",
    }
    assert without == 1


def _ref(paper_id: str | None, title: str = "", *, arxiv: str | None = None, citations: int = 0, year: int = 2020):
    ids = {"ArXiv": arxiv} if arxiv else {}
    return {"paperId": paper_id, "title": title or str(paper_id), "year": year, "externalIds": ids,
            "citationCount": citations, "venue": ""}


PAPERS = {
    "arXiv:2310.06770": {**_ref("swe", "SWE-bench", arxiv="2310.06770"),
                         "references": [_ref("humaneval", "HumanEval", arxiv="2107.03374", citations=5000, year=2021),
                                        _ref("react", "ReAct", citations=3000), _ref(None, "unmatched")]},
    "arXiv:2410.06992": {**_ref("plus", "SWE-Bench+"),
                         "references": [_ref("humaneval", "HumanEval", arxiv="2107.03374", citations=5000, year=2021),
                                        _ref("swe", "SWE-bench", arxiv="2310.06770", citations=3800),
                                        _ref("react", "ReAct", citations=3000)]},
    "DOI:10.1/utboost": {**_ref("utboost", "UTBoost"),
                         "references": [_ref("swe", "SWE-bench", arxiv="2310.06770", citations=3800),
                                        _ref("humaneval", "HumanEval", arxiv="2107.03374", citations=5000, year=2021)]},
    # The same paper under a second lookup counts as one seed.
    "DOI:10.1/utboost-publication": None,
}
BIBLIOGRAPHY = [
    {"title": "SWE-bench", "arxiv_id": "2310.06770"},
    {"title": "SWE-Bench+", "arxiv_id": "2410.06992"},
    {"title": "UTBoost", "doi": "10.1/utboost"},
    {"title": "Unknown", "doi": "10.1/unknown"},
    {"title": "Leaderboard", "url": "https://www.swebench.com/"},
]


def _api(papers: dict, calls: list[httpx.Request], *, throttle_first: bool = False, titles: dict | None = None,
         citing: dict | None = None):
    """A fake Semantic Scholar: batch lookups from `papers`, title matches from `titles`, and
    citations from `citing` (paper ID -> citing works, newest first), paged by offset and limit."""
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if throttle_first and len(calls) == 1:
            return httpx.Response(429, headers={"retry-after": "0"})
        if request.url.path.endswith("/citations"):
            paper_id = request.url.path.split("/")[-2]
            works = (citing or {}).get(paper_id, [])
            offset, size = int(request.url.params["offset"]), int(request.url.params["limit"])
            page = works[offset:offset + size]
            body = {"offset": offset, "data": [{"citingPaper": work} for work in page]}
            if offset + size < len(works):
                body["next"] = offset + size
            return httpx.Response(200, json=body)
        if request.url.path.endswith("/search/match"):
            match = (titles or {}).get(request.url.params["query"])
            return httpx.Response(200, json={"data": [match]}) if match else httpx.Response(404, json={"error": "Title match not found"})
        ids = json.loads(request.content)["ids"]
        records = [papers.get(lookup) for lookup in ids]
        if not any(records):
            return httpx.Response(400, json={"error": "No valid paper ids given"})
        return httpx.Response(200, json=records)
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_basis_papers_rank_works_by_how_many_seeds_cite_them(tmp_path: Path) -> None:
    calls: list[httpx.Request] = []
    async with _api(PAPERS, calls) as http:
        scholar = SemanticScholar(AcquisitionCache(tmp_path, mode="record"), api_key="k", client=http)
        report = await discover_basis_papers(BIBLIOGRAPHY, scholar, min_seed_citations=2, forward_limit=0)

    assert [(p.title, p.cited_by_seeds, p.in_study) for p in report.papers] == [
        ("HumanEval", 3, False),   # cited by all three seeds; the study never cites it
        ("SWE-bench", 2, True),    # a seed itself
        ("ReAct", 2, False),       # same seed count, fewer total citations
    ]
    assert report.papers[0].arxiv_id == "2107.03374"
    assert report.papers[0].citing_seeds == ["SWE-Bench+", "SWE-bench", "UTBoost"]
    assert (report.sources, report.sources_without_identifier, report.seeds, report.resolved_seeds) == (5, 1, 4, 3)
    assert report.unresolved == ["DOI:10.1/unknown"]
    assert (report.references, report.unmatched_references) == (8, 1)
    assert len(calls) == 2 and calls[0].headers["x-api-key"] == "k"
    assert calls[1].url.params["query"] == "Unknown"  # the unresolved seed was tried by title
    assert json.loads(calls[0].content)["ids"] == ["arXiv:2310.06770", "arXiv:2410.06992", "DOI:10.1/utboost", "DOI:10.1/unknown"]


async def test_one_paper_under_two_lookups_is_one_seed(tmp_path: Path) -> None:
    papers = {**PAPERS, "DOI:10.1/utboost-publication": PAPERS["DOI:10.1/utboost"]}
    bibliography = [*BIBLIOGRAPHY, {"title": "UTBoost (ACL)", "doi": "10.1/utboost-publication"}]
    async with _api(papers, []) as http:
        report = await discover_basis_papers(bibliography, SemanticScholar(AcquisitionCache(tmp_path, mode="off"), client=http),
                                             forward_limit=0)
    assert report.resolved_seeds == 3
    assert report.papers[0].cited_by_seeds == 3


async def test_lookups_are_cached_per_paper_and_replay_offline(tmp_path: Path) -> None:
    calls: list[httpx.Request] = []
    async with _api(PAPERS, calls) as http:
        first = await discover_basis_papers(BIBLIOGRAPHY, SemanticScholar(AcquisitionCache(tmp_path, mode="record"), client=http),
                                            forward_limit=0)
    replay = SemanticScholar(AcquisitionCache(tmp_path, mode="replay"))
    again = await discover_basis_papers(BIBLIOGRAPHY, replay, forward_limit=0)
    assert again == first and replay.cache_hits == 5 and len(calls) == 2  # 4 lookups and 1 title match

    with pytest.raises(LookupError):
        await discover_basis_papers([{"title": "new", "doi": "10.1/new"}], SemanticScholar(AcquisitionCache(tmp_path, mode="replay")),
                                    forward_limit=0)


async def test_throttled_batches_are_retried(tmp_path: Path) -> None:
    calls: list[httpx.Request] = []
    async with _api(PAPERS, calls, throttle_first=True) as http:
        report = await discover_basis_papers(BIBLIOGRAPHY, SemanticScholar(AcquisitionCache(tmp_path, mode="off"), client=http),
                                            forward_limit=0)
    assert len(calls) == 3 and report.resolved_seeds == 3  # throttled batch, batch, title match


def test_report_renders_new_works_in_bold(tmp_path: Path) -> None:
    from research_loop.citations import BasisPaper, BasisPaperReport
    report = BasisPaperReport(sources=5, sources_without_identifier=1, seeds=4, resolved_seeds=3, references=8,
                              unmatched_references=1, min_seed_citations=2,
                              papers=[BasisPaper(paper_id="h", title="HumanEval | Codex", year=2021, arxiv_id="2107.03374",
                                                 citation_count=5000, cited_by_seeds=3, in_study=False)])
    text = render_basis_papers(report, "Study")
    assert "| 1 | HumanEval / Codex | 2021 | 3 | 5000 | **no** | arXiv:2107.03374 |" in text


async def test_long_horizon_writes_basis_papers_from_aggregated_evidence(tmp_path: Path) -> None:
    from research_loop.long_horizon import find_basis_papers, write_basis_papers
    from research_loop.settings import ResearchSettings

    spec = {"title": "Study", "execution": {"scholarly_cache_mode": "record"}}
    settings = ResearchSettings.from_env({"RESEARCH_BENCHMARK_CACHE": str(tmp_path / "cache")})
    async with _api(PAPERS, []) as http:
        report = await find_basis_papers(spec, SimpleNamespace(bibliography=BIBLIOGRAPHY), settings, client=http)
    write_basis_papers(tmp_path / "synthesis", spec, report)
    assert json.loads((tmp_path / "synthesis" / "basis_papers.json").read_text())["papers"][0]["title"] == "HumanEval"
    assert (tmp_path / "synthesis" / "basis_papers.md").read_text().startswith("# Basis papers: Study")
    assert list((tmp_path / "cache" / "scholarly" / "semanticscholar").glob("*.json"))


async def test_a_seed_whose_id_is_not_indexed_is_found_by_its_exact_title(tmp_path: Path) -> None:
    papers = {**PAPERS, "utb-s2": {**_ref("utb-s2", "UTBoost: Rigorous Evaluation of Coding Agents on SWE-Bench"),
                                   "references": [_ref("humaneval", "HumanEval", arxiv="2107.03374")]}}
    bibliography = [
        {"title": "UTBoost: Rigorous Evaluation of Coding Agents on SWE-Bench", "doi": "10.18653/v1/2025.acl-long.189"},
        {"title": "SWE-Bench+", "doi": "10.1/near-miss"},
    ]
    titles = {
        "UTBoost: Rigorous Evaluation of Coding Agents on SWE-Bench":
            {"paperId": "utb-s2", "title": "UTBoost: rigorous evaluation of coding agents on SWE-bench"},
        "SWE-Bench+": {"paperId": "other", "title": "SWE-Bench++"},  # close is not the same paper
    }
    calls: list[httpx.Request] = []
    async with _api(papers, calls, titles=titles) as http:
        report = await discover_basis_papers(bibliography, SemanticScholar(AcquisitionCache(tmp_path, mode="off"), client=http),
                                             min_seed_citations=1, forward_limit=0)
    assert (report.resolved_seeds, report.resolved_by_title) == (1, 1)
    assert report.unresolved == ["DOI:10.1/near-miss"]
    assert [p.title for p in report.papers] == ["HumanEval"]


async def test_a_batch_of_only_unknown_ids_counts_as_not_found(tmp_path: Path) -> None:
    async with _api({}, []) as http:
        report = await discover_basis_papers([{"title": "x", "doi": "10.1/x"}],
                                             SemanticScholar(AcquisitionCache(tmp_path, mode="off"), client=http),
                                             forward_limit=0)
    assert report.unresolved == ["DOI:10.1/x"] and report.papers == []


CITING = {
    "swe": [_ref("agentless", "Agentless", citations=400, year=2024), _ref("survey", "A survey", citations=90, year=2025),
            _ref("utboost", "UTBoost"), _ref("lone", "Cites one seed", year=2025)],
    "plus": [_ref("survey", "A survey", citations=90, year=2025), _ref("utboost", "UTBoost"),
             _ref("agentless", "Agentless", citations=400, year=2024)],
    "utboost": [_ref("survey", "A survey", citations=90, year=2025)],
}


async def test_forward_snowballing_ranks_later_work_by_how_many_seeds_it_cites(tmp_path: Path) -> None:
    async with _api(PAPERS, [], citing=CITING) as http:
        report = await discover_basis_papers(BIBLIOGRAPHY, SemanticScholar(AcquisitionCache(tmp_path, mode="record"), client=http))
    assert [(w.title, w.cites_seeds, w.in_study) for w in report.citing_works] == [
        ("A survey", 3, False),
        ("Agentless", 2, False),
        ("UTBoost", 2, True),      # a seed that cites other seeds
    ]
    assert report.citing_works[0].cited_seeds == ["SWE-Bench+", "SWE-bench", "UTBoost"]
    assert (report.forward_limit, report.citations, report.seeds_with_more_citations) == (1000, 8, 0)
    assert "## Later work citing the seeds" in render_basis_papers(report, "Study")

    replayed = await discover_basis_papers(BIBLIOGRAPHY, SemanticScholar(AcquisitionCache(tmp_path, mode="replay")))
    assert replayed == report


async def test_forward_snowballing_pages_and_stops_at_its_limit(tmp_path: Path) -> None:
    from research_loop.citations import SemanticScholar as Client

    many = {"swe": [_ref(f"w{i}", f"Work {i}") for i in range(1_200)]}
    calls: list[httpx.Request] = []
    async with _api(PAPERS, calls, citing=many) as http:
        works, more = await Client(AcquisitionCache(tmp_path, mode="off"), client=http).citations("swe", 1_000)
        everything, rest = await Client(AcquisitionCache(tmp_path, mode="off"), client=http).citations("swe", 5_000)
    assert (len(works), more) == (1_000, True)
    assert (len(everything), rest) == (1_200, False)
    assert [int(c.url.params["limit"]) for c in calls] == [500, 500, 500, 500, 500]


async def test_forward_snowballing_skips_seeds_still_throttled(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/citations"):
            return httpx.Response(429, headers={"retry-after": "0"})
        ids = json.loads(request.content)["ids"]
        return httpx.Response(200, json=[PAPERS.get(lookup) for lookup in ids])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        report = await discover_basis_papers(BIBLIOGRAPHY[:3], SemanticScholar(AcquisitionCache(tmp_path, mode="off"), client=http))
    assert report.seeds_citations_unread == 3 and report.citing_works == []
    assert report.papers  # the backward pass still stands
    assert "could not be read" in render_basis_papers(report, "Study")

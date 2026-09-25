"""Basis-paper discovery: citation snowballing over a study's bibliography.

The scholarly works a study cited are the seeds. Each seed's reference list comes from
Semantic Scholar, whose records carry references for arXiv preprints; OpenAlex records for
them list none, and most of this literature is on arXiv. A work that many seeds cite is a
basis paper: something the literature builds on (backward snowballing). A later work that
cites many seeds is closely tied to the study's topic (forward snowballing). Works the study
did not cite itself are flagged, since those are what snowballing adds.

This is code, not an agent: no model is called, and research workers never see these
results. Responses are cached per seed, so a `record` run can be replayed offline.
"""
from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Iterable, Mapping
from typing import Any

import httpx
from pydantic import BaseModel, Field

from .acquisition import AcquisitionCache, read_capped, wait_rate_slot

PROVIDER = "semanticscholar"
SEMANTIC_SCHOLAR_API = "https://api.semanticscholar.org"
_API = SEMANTIC_SCHOLAR_API + "/graph/v1"
_FIELDS = ",".join(
    ["paperId", "title", "year", "externalIds", "citationCount", "venue"]
    + [f"references.{name}" for name in ("paperId", "title", "year", "externalIds", "citationCount", "venue")]
)
_CITING_FIELDS = "paperId,title,year,externalIds,citationCount,venue"
# Citations per page: small enough that each page fits one cache entry (128 KB).
_CITATION_PAGE = 500
# Seeds per batch request; the API takes up to 500, and fewer keep each response well under the cap.
_BATCH = 100
_MAX_RESPONSE_BYTES = 8_000_000
# The shared, unauthenticated pool throttles in bursts; this tool calls no model, so waiting is cheap.
_ATTEMPTS = 6
_MAX_BACKOFF_SECONDS = 30.0
_ARXIV_ID = re.compile(r"(?<![\d.])(\d{4}\.\d{4,5})(?:v\d+)?")
_NOT_ALNUM = re.compile(r"[\W_]+")


class BasisPaper(BaseModel):
    """A work the study's seeds cite, with how many of them cite it."""

    paper_id: str = Field(description="Semantic Scholar paper ID")
    title: str
    year: int | None = None
    venue: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    citation_count: int | None = Field(default=None, description="Citations across all of Semantic Scholar")
    cited_by_seeds: int = Field(description="How many of the study's resolved seeds cite this work")
    citing_seeds: list[str] = Field(default_factory=list, description="Titles of the seeds that cite it")
    in_study: bool = Field(description="Whether the study already cites this work")


class CitingWork(BaseModel):
    """A work that cites the study's seeds, with how many of them it cites."""

    paper_id: str = Field(description="Semantic Scholar paper ID")
    title: str
    year: int | None = None
    venue: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    citation_count: int | None = Field(default=None, description="Citations across all of Semantic Scholar")
    cites_seeds: int = Field(description="How many of the study's resolved seeds this work cites")
    cited_seeds: list[str] = Field(default_factory=list, description="Titles of the seeds it cites")
    in_study: bool = Field(description="Whether the study already cites this work")


class BasisPaperReport(BaseModel):
    backend: str = "semantic_scholar"
    sources: int = Field(description="Bibliography entries examined")
    sources_without_identifier: int = Field(description="Entries with no DOI or arXiv ID, such as web pages")
    seeds: int = Field(description="Distinct DOI and arXiv lookups")
    resolved_seeds: int = Field(description="Seeds Semantic Scholar knew, counted once per paper")
    unresolved: list[str] = Field(default_factory=list, description="Lookups Semantic Scholar did not know")
    resolved_by_title: int = Field(default=0, description="Seeds found by an exact title match after their ID failed")
    seeds_without_references: int = 0
    references: int = Field(description="References seen across resolved seeds")
    unmatched_references: int = Field(description="References Semantic Scholar could not match to a paper")
    min_seed_citations: int
    papers: list[BasisPaper] = Field(default_factory=list)
    forward_limit: int = Field(default=0, description="Citations read per seed; 0 skips forward snowballing")
    citations: int = Field(default=0, description="Citing works seen across resolved seeds")
    seeds_with_more_citations: int = Field(default=0, description="Seeds cited more often than forward_limit")
    seeds_citations_unread: int = Field(default=0, description="Seeds whose citations could not be read (throttled or failing)")
    citing_works: list[CitingWork] = Field(default_factory=list)


def _arxiv_id(value: str | None) -> str | None:
    match = _ARXIV_ID.search(value or "")
    return match.group(1) if match else None


def _lookup(source: Mapping[str, Any]) -> str | None:
    """The Semantic Scholar ID for a bibliography entry: arXiv first, then DOI, from fields or URL."""
    url = str(source.get("url") or "")
    doi = (source.get("doi") or "").strip().removeprefix("https://doi.org/") or None
    if doi is None and "doi.org/" in url:
        doi = url.split("doi.org/", 1)[1].strip("/") or None
    arxiv = _arxiv_id(source.get("arxiv_id")) or (_arxiv_id(url) if "arxiv.org/" in url else None)
    if arxiv is None and doi and doi.lower().startswith("10.48550/arxiv."):
        arxiv = _arxiv_id(doi.lower().removeprefix("10.48550/arxiv."))
    if arxiv:
        return f"arXiv:{arxiv}"
    return f"DOI:{doi.lower()}" if doi else None


def seeds_from_bibliography(bibliography: Iterable[Mapping[str, Any]]) -> tuple[dict[str, str], int]:
    """Lookups for the bibliography's scholarly works, keyed to a title, and the count without one.

    A preprint and its publication stay separate bibliography entries; if both resolve to one
    Semantic Scholar paper they become one seed after resolution.
    """
    seeds: dict[str, str] = {}
    without = 0
    for source in bibliography:
        lookup = _lookup(source)
        if lookup is None:
            without += 1
        else:
            seeds.setdefault(lookup, str(source.get("title") or lookup))
    return seeds, without


class SemanticScholar:
    """Batch paper lookups with their references, cached per paper."""

    def __init__(self, cache: AcquisitionCache, *, api_key: str | None = None,
                 client: httpx.AsyncClient | None = None) -> None:
        self.cache, self.api_key, self.client = cache, api_key, client
        self.cache_hits = 0

    @staticmethod
    def _key(lookup: str) -> str:
        return json.dumps({"paper": lookup, "fields": _FIELDS}, sort_keys=True)

    async def match_title(self, title: str) -> str | None:
        """Paper ID of the work with exactly this title (ignoring case and punctuation), if any."""
        key = json.dumps({"match": title}, sort_keys=True)
        cached = self.cache.get(PROVIDER, key)
        if cached is not None:
            self.cache_hits += 1
            return cached.get("paper_id")
        if self.cache.mode == "replay":
            raise LookupError(f"{PROVIDER} cache miss in replay mode")
        response = await self._send("GET", "/paper/search/match", {"query": title[:300], "fields": "title"}, None)
        paper_id = None
        if response.status_code == 200:
            best = (response.json().get("data") or [{}])[0]
            if _normalized(best.get("title")) == _normalized(title):
                paper_id = best.get("paperId")
        elif response.status_code != 404:  # 404: no match
            response.raise_for_status()
        self.cache.put(PROVIDER, key, {"paper_id": paper_id})
        return paper_id

    async def citations(self, paper_id: str, limit: int) -> tuple[list[dict[str, Any]], bool]:
        """Up to `limit` works citing a paper, newest first, and whether more exist."""
        citing: list[dict[str, Any]] = []
        offset = 0
        while offset < limit:
            size = min(_CITATION_PAGE, limit - offset)
            key = json.dumps({"citations": paper_id, "offset": offset, "limit": size, "fields": _CITING_FIELDS},
                             sort_keys=True)
            page = self.cache.get(PROVIDER, key)
            if page is not None:
                self.cache_hits += 1
            elif self.cache.mode == "replay":
                raise LookupError(f"{PROVIDER} cache miss in replay mode")
            else:
                response = await self._send("GET", f"/paper/{paper_id}/citations",
                                            {"fields": _CITING_FIELDS, "limit": size, "offset": offset}, None)
                response.raise_for_status()
                raw = response.json()
                page = {"data": [item.get("citingPaper") for item in raw.get("data") or []], "next": raw.get("next")}
                self.cache.put(PROVIDER, key, page)
            citing.extend(item for item in page["data"] if item)
            if page.get("next") is None:
                return citing, False
            offset = int(page["next"])
        return citing, True

    async def papers(self, lookups: Iterable[str]) -> dict[str, dict[str, Any] | None]:
        """Each lookup's paper record, or None when Semantic Scholar does not know it."""
        found: dict[str, dict[str, Any] | None] = {}
        missing: list[str] = []
        for lookup in dict.fromkeys(lookups):
            cached = self.cache.get(PROVIDER, self._key(lookup))
            if cached is not None:
                self.cache_hits += 1
                found[lookup] = cached.get("paper")
            elif self.cache.mode == "replay":
                raise LookupError(f"{PROVIDER} cache miss in replay mode")
            else:
                missing.append(lookup)
        for start in range(0, len(missing), _BATCH):
            chunk = missing[start:start + _BATCH]
            records = await self._batch(chunk)
            if not isinstance(records, list) or len(records) != len(chunk):
                raise ValueError("Semantic Scholar batch response does not match the request")
            for lookup, paper in zip(chunk, records):
                self.cache.put(PROVIDER, self._key(lookup), {"paper": paper})
                found[lookup] = paper
        return found

    async def _batch(self, ids: list[str]) -> Any:
        response = await self._send("POST", "/paper/batch", {"fields": _FIELDS}, {"ids": ids})
        # A batch in which no ID is known is refused outright instead of answered with nulls.
        if response.status_code == 400 and "no valid paper ids" in response.text.lower():
            return [None] * len(ids)
        response.raise_for_status()
        return response.json()

    async def _send(self, method: str, path: str, params: dict[str, Any], body: Any) -> httpx.Response:
        headers = {"User-Agent": "research-loop/0.5 (citation snowballing)"}
        if self.api_key:
            headers["x-api-key"] = self.api_key
        for attempt in range(_ATTEMPTS):
            if self.client is None:
                await wait_rate_slot(PROVIDER)
                async with httpx.AsyncClient(follow_redirects=False, timeout=60) as client:
                    response = await self._stream(client, method, path, params, body, headers)
            else:
                response = await self._stream(self.client, method, path, params, body, headers)
            if response.status_code not in (429, 500, 502, 503, 504) or attempt == _ATTEMPTS - 1:
                return response
            try:
                delay = float(response.headers.get("retry-after", ""))
            except ValueError:
                delay = 2.0 ** (attempt + 1)
            await asyncio.sleep(min(max(delay, 0.0), _MAX_BACKOFF_SECONDS))
        return response

    @staticmethod
    async def _stream(client: httpx.AsyncClient, method: str, path: str, params: dict[str, Any], body: Any,
                      headers: dict[str, str]) -> httpx.Response:
        async with client.stream(method, f"{_API}{path}", params=params, json=body, headers=headers) as response:
            return await read_capped(response, _MAX_RESPONSE_BYTES)


def _normalized(title: Any) -> str:
    return _NOT_ALNUM.sub("", str(title or "")).casefold()


def _identifiers(paper: Mapping[str, Any]) -> tuple[str | None, str | None]:
    ids = paper.get("externalIds") or {}
    doi = ids.get("DOI")
    return (doi.lower() if doi else None), _arxiv_id(ids.get("ArXiv"))


async def discover_basis_papers(
    bibliography: list[Mapping[str, Any]],
    scholar: SemanticScholar,
    *,
    min_seed_citations: int = 2,
    limit: int = 30,
    forward_limit: int = 1000,
) -> BasisPaperReport:
    """Rank the works a study's seeds cite (backward) and the works citing its seeds (forward)."""
    if min_seed_citations < 1 or limit < 1 or forward_limit < 0:
        raise ValueError("min_seed_citations and limit must be at least 1, forward_limit at least 0")
    seeds, without = seeds_from_bibliography(bibliography)
    records = await scholar.papers(seeds)

    resolved: dict[str, tuple[str, dict[str, Any]]] = {}  # paper ID -> (seed title, record)
    unresolved = []
    for lookup, title in seeds.items():
        paper = records.get(lookup)
        if not paper or not paper.get("paperId"):
            unresolved.append(lookup)
        else:
            resolved.setdefault(paper["paperId"], (str(paper.get("title") or title), paper))

    # An ID Semantic Scholar does not index (some ACL DOIs, for one) may still name a paper it has.
    matched = {lookup: await scholar.match_title(seeds[lookup]) for lookup in unresolved if seeds[lookup] != lookup}
    by_title = await scholar.papers(paper_id for paper_id in matched.values() if paper_id)
    for lookup, paper_id in matched.items():
        paper = by_title.get(paper_id) if paper_id else None
        if paper and paper.get("paperId"):
            unresolved.remove(lookup)
            resolved.setdefault(paper["paperId"], (str(paper.get("title") or seeds[lookup]), paper))

    study_ids = {lookup.split(":", 1)[1].lower() for lookup in seeds}
    citing: dict[str, set[str]] = {}  # reference paper ID -> citing seed paper IDs
    details: dict[str, dict[str, Any]] = {}
    total = unmatched = empty = 0
    for paper_id, (_, paper) in resolved.items():
        references = paper.get("references") or []
        empty += not references
        for reference in references:
            total += 1
            reference_id = (reference or {}).get("paperId")
            if not reference_id:
                unmatched += 1
                continue
            citing.setdefault(reference_id, set()).add(paper_id)
            details.setdefault(reference_id, reference)

    papers = []
    for reference_id, seed_ids in citing.items():
        if len(seed_ids) < min_seed_citations:
            continue
        reference = details[reference_id]
        doi, arxiv = _identifiers(reference)
        papers.append(BasisPaper(
            paper_id=reference_id,
            title=str(reference.get("title") or reference_id),
            year=reference.get("year"),
            venue=reference.get("venue") or None,
            doi=doi,
            arxiv_id=arxiv,
            citation_count=reference.get("citationCount"),
            cited_by_seeds=len(seed_ids),
            citing_seeds=sorted(resolved[seed_id][0] for seed_id in seed_ids),
            in_study=reference_id in resolved or bool({doi, arxiv} & study_ids),
        ))
    papers.sort(key=lambda p: (-p.cited_by_seeds, -(p.citation_count or 0), p.year or 9999, p.title.lower()))

    # Forward: later works that cite several seeds. Sequential, so the rate slot paces requests.
    cites: dict[str, set[str]] = {}
    citing_details: dict[str, dict[str, Any]] = {}
    seen_citations = more = unread = 0
    for paper_id in resolved if forward_limit else ():
        try:
            works, truncated = await scholar.citations(paper_id, forward_limit)
        except httpx.HTTPStatusError as exc:
            # Forward snowballing is best effort: a seed still throttled after retries is counted, not fatal.
            if exc.response.status_code not in (429, 500, 502, 503, 504):
                raise
            unread += 1
            continue
        more += truncated
        for work in works:
            seen_citations += 1
            work_id = work.get("paperId")
            if work_id:
                cites.setdefault(work_id, set()).add(paper_id)
                citing_details.setdefault(work_id, work)
    citing_works = []
    for work_id, seed_ids in cites.items():
        if len(seed_ids) < min_seed_citations:
            continue
        work = citing_details[work_id]
        doi, arxiv = _identifiers(work)
        citing_works.append(CitingWork(
            paper_id=work_id,
            title=str(work.get("title") or work_id),
            year=work.get("year"),
            venue=work.get("venue") or None,
            doi=doi,
            arxiv_id=arxiv,
            citation_count=work.get("citationCount"),
            cites_seeds=len(seed_ids),
            cited_seeds=sorted(resolved[seed_id][0] for seed_id in seed_ids),
            in_study=work_id in resolved or bool({doi, arxiv} & study_ids),
        ))
    citing_works.sort(key=lambda w: (-w.cites_seeds, -(w.citation_count or 0), -(w.year or 0), w.title.lower()))
    return BasisPaperReport(
        sources=len(bibliography),
        sources_without_identifier=without,
        seeds=len(seeds),
        resolved_seeds=len(resolved),
        unresolved=unresolved,
        resolved_by_title=sum(1 for lookup, paper_id in matched.items() if lookup not in unresolved),
        seeds_without_references=empty,
        references=total,
        unmatched_references=unmatched,
        min_seed_citations=min_seed_citations,
        papers=papers[:limit],
        forward_limit=forward_limit,
        citations=seen_citations,
        seeds_with_more_citations=more,
        seeds_citations_unread=unread,
        citing_works=citing_works[:limit],
    )


def render_basis_papers(report: BasisPaperReport, title: str) -> str:
    lines = [
        f"# Basis papers: {title}",
        "",
        (f"Works cited by at least {report.min_seed_citations} of the study's {report.resolved_seeds} resolved "
         f"seeds, ranked by how many seeds cite them, then by total citations (Semantic Scholar). "
         f"{report.sources} bibliography entries gave {report.seeds} DOI or arXiv lookups; "
         f"{report.sources_without_identifier} entries had neither, {report.resolved_by_title} were found by title, "
         f"and {len(report.unresolved)} lookups were not found. {report.seeds_without_references} resolved seeds "
         f"listed no references, and {report.unmatched_references} of {report.references} references matched no "
         "paper."),
        "",
        "| # | Work | Year | Cited by seeds | Citations | In study | ID |",
        "|---:|---|---:|---:|---:|:---:|---|",
    ]
    for rank, paper in enumerate(report.papers, 1):
        identifier = f"arXiv:{paper.arxiv_id}" if paper.arxiv_id else (f"DOI:{paper.doi}" if paper.doi else paper.paper_id)
        lines.append(
            f"| {rank} | {paper.title.replace('|', '/')} | {paper.year or ''} | {paper.cited_by_seeds} | "
            f"{paper.citation_count if paper.citation_count is not None else ''} | "
            f"{'yes' if paper.in_study else '**no**'} | {identifier} |"
        )
    if not report.papers:
        lines.append("| | No work reached the threshold. | | | | | |")
    if report.forward_limit:
        lines += [
            "",
            "## Later work citing the seeds",
            "",
            f"Works that cite at least {report.min_seed_citations} of the resolved seeds, ranked by how many they "
            f"cite, then by total citations. Up to {report.forward_limit} citing works were read per seed "
            f"({report.citations} in all); {report.seeds_with_more_citations} seeds are cited more often than that, "
            "and their oldest citing works were not read."
            + (f" The citations of {report.seeds_citations_unread} seeds could not be read (throttled); rerun to "
               "fill them in." if report.seeds_citations_unread else ""),
            "",
            "| # | Work | Year | Seeds cited | Citations | In study | ID |",
            "|---:|---|---:|---:|---:|:---:|---|",
        ]
        for rank, work in enumerate(report.citing_works, 1):
            identifier = f"arXiv:{work.arxiv_id}" if work.arxiv_id else (f"DOI:{work.doi}" if work.doi else work.paper_id)
            lines.append(
                f"| {rank} | {work.title.replace('|', '/')} | {work.year or ''} | {work.cites_seeds} | "
                f"{work.citation_count if work.citation_count is not None else ''} | "
                f"{'yes' if work.in_study else '**no**'} | {identifier} |"
            )
        if not report.citing_works:
            lines.append("| | No work reached the threshold. | | | | | |")
    return "\n".join(lines) + "\n"

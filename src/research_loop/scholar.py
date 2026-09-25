"""Scholarly metadata search and lookup: OpenAlex, arXiv, and Crossref.

Provider records stay separate: a preprint and its later publication are two records, and neither
status is inferred from the other. Nothing here calls a model. Every endpoint is a fixed provider host.
"""
from __future__ import annotations

import asyncio
import json
import re
import xml.etree.ElementTree as ET
from typing import Any, Literal
from urllib.parse import quote

import httpx
from pydantic import BaseModel, Field

from .acquisition import AcquisitionCache, read_capped, wait_rate_slot

# Largest metadata response read from a provider; reading stops once a response passes it.
_MAX_METADATA_BYTES = 2_000_000
_ABSTRACT_CHARS = 1_500
Status = Literal["preprint", "journal", "accepted_conference", "conference_submission", "unknown"]

# The metadata API each provider is reached at.
SCHOLAR_HOSTS = {
    "openalex": "https://api.openalex.org",
    "crossref": "https://api.crossref.org",
    "arxiv": "https://export.arxiv.org",
}


class ScholarWork(BaseModel):
    provider: str
    title: str
    url: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    openalex_id: str | None = None
    published_at: str | None = None
    publication_status: Status = "unknown"
    status_basis: str | None = None
    venue: str | None = None
    authors: list[str] = Field(default_factory=list)
    abstract: str | None = None
    full_text_url: str | None = None
    is_retracted: bool | None = None
    cited_by_count: int | None = None
    record_updates: list[dict[str, str]] = Field(default_factory=list)


class ScholarResponse(BaseModel):
    works: list[ScholarWork] = Field(default_factory=list)
    provider_errors: list[str] = Field(default_factory=list)
    truncated: bool = False


def _year_within(published_at: str | None, year_from: int | None, year_to: int | None) -> bool:
    """Whether a record's year is inside the bounds; an undated record is kept."""
    if not published_at or not published_at[:4].isdigit():
        return True
    year = int(published_at[:4])
    return (not year_from or year >= year_from) and (not year_to or year <= year_to)


def _inverted_abstract(index: dict[str, list[int]] | None) -> str | None:
    """OpenAlex stores abstracts as {word: [positions]}; put the words back in order."""
    if not index:
        return None
    positions = sorted((position, word) for word, places in index.items() for position in places)
    return " ".join(word for _, word in positions)[:_ABSTRACT_CHARS] or None


def _openalex_work(raw: dict[str, Any]) -> ScholarWork:
    ids = raw.get("ids") or {}
    doi = (ids.get("doi") or raw.get("doi") or "").removeprefix("https://doi.org/") or None
    primary = raw.get("primary_location") or {}
    source = primary.get("source") or {}
    status: Status = "journal" if source.get("type") == "journal" else "unknown"
    if raw.get("type") == "preprint" or primary.get("version") == "submittedVersion":
        status = "preprint"
    return ScholarWork(
        provider="openalex", title=str(raw.get("display_name") or ""),
        url=primary.get("landing_page_url") or raw.get("id"), doi=doi, openalex_id=raw.get("id"),
        published_at=raw.get("publication_date"), publication_status=status,
        status_basis="OpenAlex type and primary location", venue=source.get("display_name"),
        authors=[str(a["author"].get("display_name")) for a in raw.get("authorships", [])[:20] if a.get("author")],
        abstract=_inverted_abstract(raw.get("abstract_inverted_index")),
        full_text_url=primary.get("pdf_url") or (raw.get("best_oa_location") or {}).get("pdf_url"),
        is_retracted=raw.get("is_retracted"), cited_by_count=raw.get("cited_by_count"),
    )


def _crossref_work(raw: dict[str, Any]) -> ScholarWork:
    date_parts = (raw.get("published") or raw.get("created") or {}).get("date-parts") or []
    published = "-".join(str(part) for part in date_parts[0]) if date_parts else None
    kind = raw.get("type")
    status: Status = "journal" if kind == "journal-article" else "preprint" if kind == "posted-content" else "unknown"
    titles = raw.get("title") or [""]
    abstract = re.sub(r"<[^>]+>", " ", raw.get("abstract") or "")  # Crossref abstracts are JATS XML
    return ScholarWork(
        provider="crossref", title=str(titles[0] if isinstance(titles, list) else titles),
        url=raw.get("URL"), doi=raw.get("DOI"), published_at=published, publication_status=status,
        status_basis=f"Crossref type: {kind}",
        venue=(raw.get("container-title") or [None])[0],
        authors=[" ".join((a.get("given", ""), a.get("family", ""))).strip() for a in raw.get("author", [])[:20]],
        abstract=" ".join(abstract.split())[:_ABSTRACT_CHARS] or None,
        record_updates=[
            {"direction": direction, "type": str(item.get("type") or "unknown"), "doi": str(item.get("DOI") or "")}
            for direction in ("update-to", "update-by")
            for item in (raw.get(direction) or [])[:5] if isinstance(item, dict)
        ],
    )


def _arxiv_works(xml: str) -> list[ScholarWork]:
    ns = {"a": "http://www.w3.org/2005/Atom", "ar": "http://arxiv.org/schemas/atom"}
    works = []
    for entry in ET.fromstring(xml).findall("a:entry", ns):
        url = entry.findtext("a:id", default="", namespaces=ns)
        identifier = url.rstrip("/").split("/")[-1]
        title = " ".join(entry.findtext("a:title", default="", namespaces=ns).split())
        if not identifier or not title:
            continue
        pdf = next((link.get("href") for link in entry.findall("a:link", ns) if link.get("title") == "pdf"), None)
        works.append(ScholarWork(
            provider="arxiv", title=title, url=url, arxiv_id=identifier,
            doi=entry.findtext("ar:doi", default=None, namespaces=ns),
            published_at=entry.findtext("a:published", default=None, namespaces=ns),
            publication_status="preprint", status_basis="arXiv repository record",
            authors=[a.findtext("a:name", default="", namespaces=ns) for a in entry.findall("a:author", ns)][:20],
            abstract=" ".join(entry.findtext("a:summary", default="", namespaces=ns).split())[:_ABSTRACT_CHARS],
            full_text_url=pdf,
        ))
    return works


_METADATA_ERRORS = (httpx.HTTPError, ValueError, LookupError, KeyError, TypeError, AttributeError, ET.ParseError)


class ScholarClient:
    def __init__(self, *, cache: AcquisitionCache, api_key: str | None = None,
                 contact_email: str | None = None, client: httpx.AsyncClient | None = None) -> None:
        self.cache, self.api_key, self.contact_email, self.client = cache, api_key, contact_email, client
        self._semaphores = {name: asyncio.Semaphore(2) for name in SCHOLAR_HOSTS}

    async def _request(self, provider: str, path: str, params: dict[str, Any] | None = None, *, text: bool = False) -> Any:
        params = dict(params or {})
        if provider == "openalex" and self.api_key:
            params["api_key"] = self.api_key
        if provider == "crossref" and self.contact_email:
            params["mailto"] = self.contact_email
        # The API key stays out of the cache key, so recordings replay without it.
        key = json.dumps({"path": path, "params": {k: v for k, v in params.items() if k != "api_key"}}, sort_keys=True)
        cached = self.cache.get(provider, key)
        if cached is not None:
            return cached
        if self.cache.mode == "replay":
            raise LookupError(f"{provider} cache miss in replay mode")
        headers = {"User-Agent": "research-loop (scholarly metadata client)"
                                 + (f" mailto:{self.contact_email}" if self.contact_email else "")}
        async with self._semaphores[provider]:
            for attempt in range(2):
                await wait_rate_slot(provider)
                if self.client is None:
                    async with httpx.AsyncClient(follow_redirects=False) as client:
                        response = await self._get(client, SCHOLAR_HOSTS[provider] + path, params, headers)
                else:
                    response = await self._get(self.client, SCHOLAR_HOSTS[provider] + path, params, headers)
                if response.status_code not in (429, 503) or attempt == 1:
                    break
                try:
                    delay = min(max(float(response.headers.get("retry-after", "1")), 0.0), 3.0)
                except ValueError:
                    delay = 1.0
                await asyncio.sleep(delay)
        response.raise_for_status()
        value = response.text if text else response.json()
        self.cache.put(provider, key, value)
        return value

    @staticmethod
    async def _get(client: httpx.AsyncClient, url: str, params: dict[str, Any], headers: dict[str, str]) -> httpx.Response:
        async with client.stream("GET", url, params=params, headers=headers, timeout=15) as response:
            return await read_capped(response, _MAX_METADATA_BYTES)

    async def search(self, query: str, year_from: int | None = None, year_to: int | None = None,
                     limit: int = 5) -> ScholarResponse:
        """OpenAlex works, then arXiv preprints, each within the year bounds."""
        result = ScholarResponse()
        limit = max(1, min(limit, 10))
        if not query.strip():
            result.provider_errors.append("query is empty")
            return result
        filters = [f for f in (year_from and f"from_publication_date:{year_from}-01-01",
                               year_to and f"to_publication_date:{year_to}-12-31") if f]
        params: dict[str, Any] = {"search": query[:300], "per_page": limit} | ({"filter": ",".join(filters)} if filters else {})
        try:
            raw = await self._request("openalex", "/works", params)
            result.works.extend(_openalex_work(item) for item in raw.get("results", [])[:limit])
            result.truncated = raw.get("meta", {}).get("count", 0) > limit
        except _METADATA_ERRORS as exc:
            result.provider_errors.append(f"openalex:{type(exc).__name__}")
        search_query = f"all:{query[:120]}"
        if year_from or year_to:
            # arXiv needs both ends of a submittedDate range; its archive starts in 1991.
            search_query += f" AND submittedDate:[{year_from or 1991}01010000 TO {year_to or 9999}12312359]"
        try:
            raw = await self._request("arxiv", "/api/query",
                                      {"search_query": search_query, "start": 0, "max_results": min(5, limit)}, text=True)
            result.works.extend(w for w in _arxiv_works(raw) if _year_within(w.published_at, year_from, year_to))
        except _METADATA_ERRORS as exc:
            result.provider_errors.append(f"arxiv:{type(exc).__name__}")
        return result

    async def get(self, identifier: str) -> ScholarResponse:
        """One work by DOI, OpenAlex W ID, or arXiv ID."""
        result = ScholarResponse()
        identifier = identifier.strip()
        try:
            if re.fullmatch(r"W\d+", identifier) or identifier.startswith("https://openalex.org/W"):
                work_id = identifier.rstrip("/").split("/")[-1]
                result.works.append(_openalex_work(await self._request("openalex", f"/works/{work_id}")))
            elif identifier.startswith(("10.", "https://doi.org/10.", "doi:10.")):
                doi = identifier.removeprefix("https://doi.org/").removeprefix("doi:")
                raw = await self._request("crossref", f"/works/{quote(doi, safe='')}")
                result.works.append(_crossref_work(raw["message"]))
            else:
                arxiv_id = identifier.removeprefix("arxiv:").removeprefix("https://arxiv.org/abs/")
                if not re.fullmatch(r"(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?", arxiv_id):
                    raise ValueError("unsupported scholarly identifier")
                raw = await self._request("arxiv", "/api/query", {"id_list": arxiv_id, "max_results": 1}, text=True)
                result.works.extend(_arxiv_works(raw)[:1])
        except _METADATA_ERRORS as exc:
            result.provider_errors.append(f"get:{type(exc).__name__}")
        return result

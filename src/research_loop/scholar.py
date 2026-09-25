"""Bounded, provider-neutral scholarly acquisition for research workers.

Provider records stay separate: a preprint and a later DOI publication are not
silently collapsed into one status claim. No adapter calls a model or writes to the
ledger. All endpoints are fixed provider hosts except the guarded OA fetch URL.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import xml.etree.ElementTree as ET
from typing import Any, Literal
from urllib.parse import quote, urlparse

import httpx
from pydantic import BaseModel, Field
from pydantic_ai import FunctionToolset

from .acquisition import (
    MAX_FETCH_CHARS,
    AcquisitionCache,
    BlockedSource,
    FetchMemo,
    SourcePolicy,
    bounded_public_get,
    fetch_cache_key,
    fetch_window,
    public_fetch_client,
    public_url,
    read_capped,
    wait_rate_slot,
)

_PDF_PAGE_LIMIT = 30
# Largest metadata response read from a provider; reading stops once a response passes it.
_MAX_METADATA_BYTES = 2_000_000
Status = Literal["preprint", "journal", "accepted_conference", "conference_submission", "unknown"]


def _year_within(published_at: str | None, year_from: int | None, year_to: int | None) -> bool:
    """Whether a record's year is inside the bounds; an undated record is kept."""
    if not published_at or not published_at[:4].isdigit():
        return True
    year = int(published_at[:4])
    return (not year_from or year >= year_from) and (not year_to or year <= year_to)


class ScholarWork(BaseModel):
    provider: str
    provider_id: str
    title: str
    url: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    openalex_id: str | None = None
    acl_id: str | None = None
    published_at: str | None = None
    publication_status: Status = "unknown"
    status_basis: str | None = None
    authors: list[str] = Field(default_factory=list)
    abstract: str | None = None
    full_text_url: str | None = None
    is_retracted: bool | None = None
    cited_by_count: int | None = None
    record_updates: list[dict[str, str]] = Field(default_factory=list)
    license_urls: list[str] = Field(default_factory=list)


# The metadata API each scholarly provider is reached at.
SCHOLAR_HOSTS = {
    "openalex": "https://api.openalex.org", "crossref": "https://api.crossref.org",
    "arxiv": "https://export.arxiv.org", "opencitations": "https://api.opencitations.net",
    "acl": "https://aclanthology.org",
}


class ScholarResponse(BaseModel):
    operation: str
    works: list[ScholarWork] = Field(default_factory=list)
    provider_errors: list[str] = Field(default_factory=list)
    truncated: bool = False
    cache_hits: int = 0
    text: str | None = None
    content_sha256: str | None = None
    extraction_method: str | None = None
    start: int | None = None
    total_chars: int | None = None
    next_start: int | None = None
    # Set when extraction itself stopped early (PDF page limit), so total_chars is not the whole document.
    extraction_truncated: bool | None = None
    # The task's blocked-source entry a refused fetch matched, directly or through a redirect.
    blocked_source: str | None = None


def _openalex_work(raw: dict[str, Any]) -> ScholarWork:
    ids = raw.get("ids") or {}
    doi = (ids.get("doi") or raw.get("doi") or "").removeprefix("https://doi.org/") or None
    primary = raw.get("primary_location") or {}
    source = primary.get("source") or {}
    status: Status = "journal" if source.get("type") == "journal" else "unknown"
    if raw.get("type") == "preprint" or primary.get("version") == "submittedVersion":
        status = "preprint"
    return ScholarWork(
        provider="openalex", provider_id=str(raw.get("id") or ""),
        title=str(raw.get("display_name") or ""), url=primary.get("landing_page_url") or raw.get("id"),
        doi=doi, openalex_id=raw.get("id"), published_at=raw.get("publication_date"),
        publication_status=status, status_basis="OpenAlex type and primary location",
        authors=[str(a.get("author", {}).get("display_name")) for a in raw.get("authorships", [])[:20] if a.get("author")],
        full_text_url=primary.get("pdf_url") or (raw.get("best_oa_location") or {}).get("pdf_url"),
        is_retracted=raw.get("is_retracted"), cited_by_count=raw.get("cited_by_count"),
    )


def _crossref_work(raw: dict[str, Any]) -> ScholarWork:
    date_parts = (raw.get("published") or raw.get("created") or {}).get("date-parts") or []
    published = "-".join(str(part) for part in date_parts[0]) if date_parts else None
    kind = raw.get("type")
    status: Status = "journal" if kind == "journal-article" else "unknown"
    if kind == "posted-content":
        status = "preprint"
    titles = raw.get("title") or [""]
    title = titles[0] if isinstance(titles, list) else str(titles)
    return ScholarWork(
        provider="crossref", provider_id=str(raw.get("DOI") or ""),
        title=str(title), url=raw.get("URL"), doi=raw.get("DOI"),
        published_at=published, publication_status=status, status_basis=f"Crossref type: {kind}",
        authors=[" ".join((a.get("given", ""), a.get("family", ""))).strip() for a in raw.get("author", [])[:20]],
        record_updates=[
            {"direction": direction, "type": str(item.get("type") or "unknown"),
             "doi": str(item.get("DOI") or ""), "source": str(item.get("source") or "")}
            for direction in ("update-to", "update-by")
            for item in (raw.get(direction) or [])[:5] if isinstance(item, dict)
        ],
        license_urls=[str(item.get("URL")) for item in (raw.get("license") or [])[:5] if item.get("URL")],
    )


def _arxiv_works(xml: str) -> list[ScholarWork]:
    ns = {"a": "http://www.w3.org/2005/Atom", "ar": "http://arxiv.org/schemas/atom"}
    root = ET.fromstring(xml)
    works = []
    for entry in root.findall("a:entry", ns):
        url = entry.findtext("a:id", default="", namespaces=ns)
        identifier = url.rstrip("/").split("/")[-1]
        if not identifier or not entry.findtext("a:title", default="", namespaces=ns).strip():
            continue
        pdf = next((link.get("href") for link in entry.findall("a:link", ns) if link.get("title") == "pdf"), None)
        works.append(ScholarWork(
            provider="arxiv", provider_id=identifier,
            title=" ".join(entry.findtext("a:title", default="", namespaces=ns).split()),
            url=url, arxiv_id=identifier,
            doi=entry.findtext("ar:doi", default=None, namespaces=ns),
            published_at=entry.findtext("a:published", default=None, namespaces=ns),
            publication_status="preprint", status_basis="arXiv repository record",
            authors=[a.findtext("a:name", default="", namespaces=ns) for a in entry.findall("a:author", ns)][:20],
            abstract=" ".join(entry.findtext("a:summary", default="", namespaces=ns).split())[:1500],
            full_text_url=pdf,
        ))
    return works


class ScholarClient:
    def __init__(self, *, cache: AcquisitionCache, api_key: str | None = None,
                 contact_email: str | None = None, client: httpx.AsyncClient | None = None,
                 grobid_url: str | None = None, memo: FetchMemo | None = None,
                 policy: SourcePolicy | None = None, fetch_client: httpx.AsyncClient | None = None) -> None:
        # `client` serves metadata requests and, without `fetch_client`, downloads too; a fetch
        # client should connect only to public addresses (public_fetch_client).
        self.cache, self.api_key, self.contact_email, self.client = cache, api_key, contact_email, client
        self.fetch_client = fetch_client or client
        self.grobid_url = grobid_url
        self.memo = memo or FetchMemo()
        self.policy = policy or SourcePolicy()
        self._semaphores = {name: asyncio.Semaphore(2) for name in ("openalex", "crossref", "arxiv", "opencitations", "acl")}
        self.cache_hits = 0

    async def _request(self, provider: str, path: str, params: dict[str, Any] | None = None, *, text: bool = False) -> Any:
        params = dict(params or {})
        if provider == "openalex" and self.api_key:
            params["api_key"] = self.api_key
        if provider == "crossref" and self.contact_email:
            params["mailto"] = self.contact_email
        key = json.dumps({"path": path, "params": params}, sort_keys=True)
        cached = self.cache.get(provider, key)
        if cached is not None:
            self.cache_hits += 1
            return cached
        if self.cache.mode == "replay":
            raise LookupError(f"{provider} cache miss in replay mode")
        headers = {"User-Agent": "research-loop/0.5 (scholarly metadata client)"}
        if self.contact_email:
            headers["User-Agent"] += f" mailto:{self.contact_email}"
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
                     limit: int = 5, include_arxiv: bool = True, include_crossref: bool = False) -> ScholarResponse:
        result = ScholarResponse(operation="search")
        limit = max(1, min(limit, 10))
        if not query.strip():
            result.provider_errors.append("query is empty")
            return result
        filters = []
        if year_from:
            filters.append(f"from_publication_date:{year_from}-01-01")
        if year_to:
            filters.append(f"to_publication_date:{year_to}-12-31")
        params: dict[str, Any] = {"search": query[:300], "per_page": limit}
        if filters:
            params["filter"] = ",".join(filters)
        hits_before = self.cache_hits
        try:
            raw = await self._request("openalex", "/works", params)
            result.works.extend(_openalex_work(item) for item in raw.get("results", [])[:limit])
            result.truncated = raw.get("meta", {}).get("count", 0) > limit
        except (httpx.HTTPError, ValueError, LookupError, KeyError, TypeError, AttributeError) as exc:
            result.provider_errors.append(f"openalex:{type(exc).__name__}")
        if include_arxiv:
            search_query = f"all:{query[:120]}"
            if year_from or year_to:
                # arXiv needs both ends of a submittedDate range; its archive starts in 1991.
                search_query += f" AND submittedDate:[{year_from or 1991}01010000 TO {year_to or 9999}12312359]"
            try:
                raw = await self._request("arxiv", "/api/query", {"search_query": search_query, "start": 0, "max_results": min(5, limit)}, text=True)
                # published is the first version's submission date, which the range also filters on.
                works = [work for work in _arxiv_works(raw) if _year_within(work.published_at, year_from, year_to)]
                result.works.extend(works[:limit])
            except (httpx.HTTPError, ValueError, LookupError, TypeError, AttributeError, ET.ParseError) as exc:
                result.provider_errors.append(f"arxiv:{type(exc).__name__}")
        if include_crossref:
            try:
                params = {"query.bibliographic": query[:300], "rows": min(5, limit)}
                crossref_filters = []
                if year_from:
                    crossref_filters.append(f"from-pub-date:{year_from}-01-01")
                if year_to:
                    crossref_filters.append(f"until-pub-date:{year_to}-12-31")
                if crossref_filters:
                    params["filter"] = ",".join(crossref_filters)
                raw = await self._request("crossref", "/works", params)
                result.works.extend(_crossref_work(item) for item in raw.get("message", {}).get("items", [])[:limit])
            except (httpx.HTTPError, ValueError, LookupError, KeyError, TypeError, AttributeError) as exc:
                result.provider_errors.append(f"crossref:{type(exc).__name__}")
        result.cache_hits = self.cache_hits - hits_before
        return result

    async def get(self, identifier: str) -> ScholarResponse:
        result = ScholarResponse(operation="get")
        identifier = identifier.strip()
        hits_before = self.cache_hits
        try:
            if re.fullmatch(r"W\d+", identifier) or identifier.startswith("https://openalex.org/W"):
                work_id = identifier.rstrip("/").split("/")[-1]
                result.works.append(_openalex_work(await self._request("openalex", f"/works/{work_id}")))
            elif identifier.startswith(("10.", "https://doi.org/10.")):
                doi = identifier.removeprefix("https://doi.org/")
                raw = await self._request("crossref", f"/works/{quote(doi, safe='')}")
                result.works.append(_crossref_work(raw["message"]))
            elif identifier.startswith("openreview:"):
                result.provider_errors.append("openreview:Disabled")
            elif identifier.startswith("acl:"):
                acl_id = identifier[4:]
                if not re.fullmatch(r"[A-Za-z0-9.\-]+", acl_id):
                    raise ValueError("invalid ACL ID")
                bib = await self._request("acl", f"/{acl_id}.bib", text=True)
                title = re.search(r"title\s*=\s*\{([^}]+)\}", bib, re.IGNORECASE)
                doi = re.search(r"doi\s*=\s*\{([^}]+)\}", bib, re.IGNORECASE)
                result.works.append(ScholarWork(provider="acl", provider_id=acl_id, acl_id=acl_id,
                    title=title.group(1) if title else acl_id, doi=doi.group(1) if doi else None,
                    url=f"https://aclanthology.org/{acl_id}/", publication_status="unknown",
                    status_basis="ACL Anthology BibTeX record; venue status requires verification"))
            else:
                arxiv_id = identifier.removeprefix("arxiv:").removeprefix("https://arxiv.org/abs/")
                if not re.fullmatch(r"(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?", arxiv_id):
                    raise ValueError("unsupported scholarly identifier")
                raw = await self._request("arxiv", "/api/query", {"id_list": arxiv_id, "max_results": 1}, text=True)
                result.works.extend(_arxiv_works(raw)[:1])
        except (httpx.HTTPError, ValueError, LookupError, KeyError, TypeError, AttributeError, ET.ParseError) as exc:
            result.provider_errors.append(f"get:{type(exc).__name__}")
        result.cache_hits = self.cache_hits - hits_before
        return result

    async def _relations(self, identifier: str, direction: Literal["references", "citations"], limit: int) -> ScholarResponse:
        result = ScholarResponse(operation=direction)
        limit = max(1, min(limit, 10))
        hits_before = self.cache_hits
        try:
            if identifier.startswith(("10.", "https://doi.org/10.")):
                doi = identifier.removeprefix("https://doi.org/")
                path = f"/index/v2/{direction}/doi:{quote(doi, safe='')}"
                raw = await self._request("opencitations", path)
                ids = [item.get("cited" if direction == "references" else "citing") for item in raw[:limit]]
                result.works = [ScholarWork(provider="opencitations", provider_id=str(id), title="Metadata unresolved", doi=str(id).removeprefix("doi:") if str(id).startswith("doi:") else None) for id in ids if id]
                result.truncated = len(raw) > limit
            else:
                work_id = identifier.rstrip("/").split("/")[-1]
                if not re.fullmatch(r"W\d+", work_id):
                    raise ValueError("use DOI or OpenAlex W ID")
                if direction == "citations":
                    raw = await self._request("openalex", "/works", {"filter": f"cites:{work_id}", "per_page": limit})
                    result.works = [_openalex_work(item) for item in raw.get("results", [])[:limit]]
                    result.truncated = raw.get("meta", {}).get("count", 0) > limit
                else:
                    raw = await self._request("openalex", f"/works/{work_id}")
                    ids = raw.get("referenced_works") or []
                    result.works = [ScholarWork(provider="openalex", provider_id=id, openalex_id=id, title="Metadata unresolved") for id in ids[:limit]]
                    result.truncated = len(ids) > limit
        except (httpx.HTTPError, ValueError, LookupError, KeyError, TypeError, AttributeError) as exc:
            result.provider_errors.append(f"{direction}:{type(exc).__name__}")
        result.cache_hits = self.cache_hits - hits_before
        return result

    async def references(self, identifier: str, limit: int = 10) -> ScholarResponse:
        return await self._relations(identifier, "references", limit)

    async def citations(self, identifier: str, limit: int = 10) -> ScholarResponse:
        return await self._relations(identifier, "citations", limit)

    async def fetch(self, url: str, max_chars: int = MAX_FETCH_CHARS, start: int = 0) -> ScholarResponse:
        result = ScholarResponse(operation="fetch")
        max_chars = max(1000, min(max_chars, MAX_FETCH_CHARS))
        start = max(0, start)
        # Blocked sources are refused before the cache, the memo, DNS, or any request.
        if entry := self.policy.blocks(url):
            result.provider_errors.append("fetch:BlockedSource")
            result.blocked_source = entry
            return result
        # Cache next: entries exist only for URLs that passed the public-URL check, and
        # replay must work offline, where the DNS check would otherwise fail.
        cache_key = fetch_cache_key(url, max_chars, start)
        cached = self.cache.get("fetch", cache_key)
        if cached is not None:
            self.cache_hits += 1
            return ScholarResponse.model_validate({**cached, "cache_hits": 1})
        if self.cache.mode == "replay":
            result.provider_errors.append("fetch:CacheMiss")
            return result
        document = self.memo.get("fetch", url)
        if document is None:
            if not await public_url(url):
                result.provider_errors.append("fetch:UnsafeURL")
                return result
            try:
                document = await self._extract(url)
            except BlockedSource as exc:  # redirected to a blocked source
                result.provider_errors.append("fetch:BlockedSource")
                result.blocked_source = exc.entry
                return result
            except Exception as exc:  # noqa: BLE001 - extractors raise their own types on arbitrary documents
                result.provider_errors.append(f"fetch:{type(exc).__name__}")
                return result
            if not document["text"].strip():
                result.provider_errors.append("fetch:EmptyExtraction")
                return result
            self.memo.put("fetch", url, document)
        result.content_sha256 = document["content_sha256"]
        result.extraction_method = document["extraction_method"]
        result.extraction_truncated = document["extraction_truncated"] or None
        window = fetch_window(document["text"], start, max_chars)
        if window is None:
            result.total_chars = len(document["text"])
            result.provider_errors.append("fetch:StartBeyondEnd")
            return result
        result.text, result.start, result.total_chars = window["text"], window["start"], window["total_chars"]
        result.truncated, result.next_start = window["truncated"], window["next_start"]
        self.cache.put("fetch", cache_key, result.model_dump(mode="json"))
        return result

    async def _extract(self, url: str) -> dict[str, Any]:
        """Download a public page or PDF and extract its full text."""
        if self.fetch_client:
            response = await bounded_public_get(self.fetch_client, url, 5_000_000, self.policy)
        else:
            async with public_fetch_client(timeout=15) as client:
                response = await bounded_public_get(client, url, 5_000_000, self.policy)
        response.raise_for_status()
        media = response.headers.get("content-type", "").split(";")[0].lower()
        extraction_truncated = False
        if media == "application/pdf":
            extracted, method = "", None
            if self.grobid_url:
                try:
                    parsed = urlparse(self.grobid_url)
                    if parsed.scheme != "http" or parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
                        raise ValueError("GROBID_URL must use local HTTP")
                    async with httpx.AsyncClient(timeout=30, follow_redirects=False) as grobid:
                        processed = await grobid.post(
                            self.grobid_url.rstrip("/") + "/api/processFulltextDocument",
                            files={"input": ("paper.pdf", response.content, "application/pdf")},
                        )
                    processed.raise_for_status()
                    if len(processed.content) > 2_000_000:
                        raise ValueError("GROBID response exceeded size limit")
                    tei = ET.fromstring(processed.content)
                    body = tei.find(".//{http://www.tei-c.org/ns/1.0}body")
                    if body is not None:
                        extracted = " ".join(" ".join(body.itertext()).split())
                        method = "grobid-tei"
                except (httpx.HTTPError, ValueError, ET.ParseError):
                    pass
            if not extracted:
                # Parsing is CPU-bound; worker threads keep a long paper from stalling the other agents.
                extracted, extraction_truncated = await asyncio.to_thread(_pdf_text, response.content)
                method = "pypdf"
        elif media in ("text/html", "application/xhtml+xml"):
            extracted, method = await asyncio.to_thread(_html_text, response.text)
        else:
            raise ValueError("unsupported content type")
        return {"text": extracted, "extraction_method": method, "extraction_truncated": extraction_truncated,
                "content_sha256": hashlib.sha256(response.content).hexdigest()}


def _pdf_text(content: bytes) -> tuple[str, bool]:
    """Text of a PDF's first pages, and whether it has more."""
    import io

    from pypdf import PdfReader
    pages = PdfReader(io.BytesIO(content)).pages
    return "\n\n".join(page.extract_text() or "" for page in pages[:_PDF_PAGE_LIMIT]), len(pages) > _PDF_PAGE_LIMIT


def _html_text(html: str) -> tuple[str, str]:
    """Main text of an HTML page and the extractor that produced it."""
    import trafilatura
    extracted = trafilatura.extract(html, include_comments=False, include_tables=True) or ""
    if extracted.strip():
        return extracted, "trafilatura"
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    for item in soup(["script", "style", "nav", "footer", "header"]):
        item.decompose()
    return soup.get_text(" ", strip=True), "beautifulsoup-fallback"


def build_scholar_toolset(client: ScholarClient) -> FunctionToolset:
    async def scholar_search(query: str, year_from: int | None = None, year_to: int | None = None,
                             limit: int = 5, include_arxiv: bool = True, include_crossref: bool = False) -> dict[str, Any]:
        """Search scholarly metadata; keep preprint and publisher records distinct."""
        return (await client.search(query, year_from, year_to, limit, include_arxiv, include_crossref)).model_dump(exclude_none=True)

    async def scholar_get(identifier: str) -> dict[str, Any]:
        """Resolve a DOI, OpenAlex W ID, arXiv ID, or acl:Anthology-ID."""
        return (await client.get(identifier)).model_dump(exclude_none=True)

    async def scholar_references(identifier: str, limit: int = 10) -> dict[str, Any]:
        """Find works referenced by a DOI or OpenAlex W ID."""
        return (await client.references(identifier, limit)).model_dump(exclude_none=True)

    async def scholar_citations(identifier: str, limit: int = 10) -> dict[str, Any]:
        """Find works citing a DOI or OpenAlex W ID."""
        return (await client.citations(identifier, limit)).model_dump(exclude_none=True)

    async def scholar_fetch(url: str, max_chars: int = MAX_FETCH_CHARS, start: int = 0) -> dict[str, Any]:
        """Extract text from a public HTTPS scholarly HTML page or PDF, up to max_chars characters from `start`.

        When the result has `next_start`, call again with start=next_start to read further.
        """
        return (await client.fetch(url, max_chars, start)).model_dump(exclude_none=True)

    return FunctionToolset(tools=[scholar_search, scholar_get, scholar_references, scholar_citations, scholar_fetch])

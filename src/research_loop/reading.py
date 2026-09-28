"""Reading a page our fetcher could not: its open-access copy, then Exa's crawl, then Firecrawl.

A bake-off on the 160 pages our fetcher failed on in production runs (docs/study-log.md) found that
this chain, tried in order after our own fetch, reads 139 of them and recovers 68 of the 79 quotes
scouts had cited from them. Each step returns a document in the shape `WebAcquisition` keeps, or
None, and the next step is tried only when the one before found nothing usable:

- open access: the paper's legitimate open copy, found from an identifier in the URL (a DOI, an RSC
  article ID, or an arXiv ID, or for an MDPI address the DOI OpenAlex gives its journal, volume, issue,
  and article number) through Europe PMC's full text, OpenAlex's best open-access location, or a PDF at
  another location OpenAlex lists, such as a repository's copy, and read with our own fetcher; last, the
  full text CORE (core.ac.uk) holds for the DOI, under CORE's rate limits; free.
- Exa `/contents`: the text of the page from Exa's crawl; $1 per 1,000 pages.
- Firecrawl scrape: the page rendered and extracted by Firecrawl, with basic proxies only, never its
  stealth or residential proxies; one credit a page.

The caller has already refused a blocked URL, so none of these is ever sent one. Text that is short or
looks like a challenge page counts as found nothing. A paid read reserves its price under a study
budget before it is sent and records what it cost in the run's external spend.
"""
from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from urllib.parse import quote

import httpx

from .acquisition import wait_rate_slot
from .study_budget import StudyBudget

ReaderName = Literal["oa", "exa", "firecrawl"]
READERS: tuple[ReaderName, ...] = ("oa", "exa", "firecrawl")
# Text shorter than this, or a challenge page, is not the page: the bake-off's own test.
MIN_CHARS = 500
CHALLENGE = re.compile(r"access denied|just a moment|captcha|enable javascript|are you a robot|"
                       r"verify you are human|request unsuccessful", re.IGNORECASE)
EXA_CONTENTS_URL = "https://api.exa.ai/contents"
# Exa lists $1 per 1,000 pages of text (https://exa.ai/pricing, 27 September 2026).
EXA_PAGE_RESERVE_USD = Decimal("0.002")
FIRECRAWL_SCRAPE_URL = "https://api.firecrawl.dev/v2/scrape"
# Firecrawl bills credits, one a basic page; its Hobby plan sells 3,000 for $16 (27 September 2026).
FIRECRAWL_PAGE_USD = Decimal("0.0054")
_DOI = re.compile(r"10\.\d{4,9}/[^\s\"<>?#]+")
_RSC = re.compile(r"pubs\.rsc\.org/.*?/([a-z]\d[a-z]{2}\d{5}[a-z])", re.IGNORECASE)
# MDPI serves scripted clients a bot challenge, and its addresses carry no DOI: ISSN, volume, issue, article.
_MDPI = re.compile(r"mdpi\.com/(\d{4}-\d{3}[\dX])/(\d+)/(\d+)/(\d+)", re.IGNORECASE)
# PDFs tried at OpenAlex's other locations, in its order, after its best one: repository copies and the like.
_MORE_LOCATIONS = 3
# CORE's repository full text. It allows 10 requests a minute and 100 a day without a key, and 25 a minute
# and 1,000 a day with a free personal key (https://api.core.ac.uk/docs/v3, 28 September 2026). Requests
# are spaced for the lower rate (acquisition._RATE_INTERVAL), and after a 429, or once CORE says none
# remain, none is sent until the reset time it gives. On 28 September 2026 CORE held full text for 6 of
# the 7 MDPI articles scouts could not read that Europe PMC did not have.
CORE_SEARCH_URL = "https://api.core.ac.uk/v3/search/works/"  # without the slash, CORE redirects
_CORE_PAUSE_SECONDS = 60.0  # when a 429 gives no reset time
_core_resume_at = 0.0  # time.time() before which CORE is not asked, shared by the process's runs
_ARXIV = re.compile(r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})")
_TAGS = re.compile(r"<[^>]+>")


@dataclass
class ExternalSpend:
    """What a run's paid searches and page reads cost, as reported; a run adds it to its model calls' cost."""

    usd: Decimal = Decimal(0)
    searches: int = 0
    pages: int = 0


Document = dict[str, Any]
Extract = Callable[[str], Awaitable[Document]]


def usable(text: str) -> bool:
    return len(text) >= MIN_CHARS and not (CHALLENGE.search(text[:3_000]) and len(text) < 20_000)


def _document(text: str, extraction: str, via: str) -> Document:
    return {"text": text, "extraction": extraction, "via": via,
            "content_sha256": hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()}


def _core_reset(value: str | None) -> float:
    """When CORE's limit resets, from its x-ratelimit-retry-after time, or a minute from now."""
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except ValueError:
        return time.time() + _CORE_PAUSE_SECONDS


def identifiers(url: str) -> tuple[str | None, str | None]:
    """The DOI and arXiv ID a URL names, as far as its address shows them."""
    doi = None
    if match := _DOI.search(url):
        doi = match.group(0).rstrip(".")
    elif match := _RSC.search(url):
        doi = f"10.1039/{match.group(1).lower()}"
    arxiv = match.group(1) if (match := _ARXIV.search(url)) else None
    return doi, arxiv


class OpenAccessReader:
    name = "oa"

    def __init__(self, client: httpx.AsyncClient, extract: Extract, core_key: str | None = None) -> None:
        self.client, self.extract, self.core_key = client, extract, core_key

    async def read(self, url: str) -> Document | None:
        doi, arxiv = identifiers(url)
        if arxiv and "/pdf/" not in url:
            document = await self._extract(f"https://arxiv.org/pdf/{arxiv}", "oa:arxiv")
            if document:
                return document
        if not doi and (match := _MDPI.search(url)):
            doi = await self._mdpi_doi(*match.groups())
        if not doi:
            return None
        found = await self.client.get("https://www.ebi.ac.uk/europepmc/webservices/rest/search",
                                      params={"query": f'DOI:"{doi}"', "format": "json", "resultType": "lite"})
        hits = ((found.json().get("resultList") or {}).get("result") or []) if found.status_code == 200 else []
        if hits and hits[0].get("pmcid"):
            xml = await self.client.get(f"https://www.ebi.ac.uk/europepmc/webservices/rest/{hits[0]['pmcid']}/fullTextXML")
            text = " ".join(_TAGS.sub(" ", xml.text).split()) if xml.status_code == 200 else ""
            if usable(text):
                return _document(text, "europepmc-xml", "oa:europepmc")
        return await self._openalex(doi, url) or await self._core(doi)

    async def _openalex(self, doi: str, url: str) -> Document | None:
        """The first readable copy among OpenAlex's open-access locations for `doi`, other than `url`."""
        work = await self.client.get(f"https://api.openalex.org/works/doi:{quote(doi, safe='/')}")
        if work.status_code != 200:
            return None
        data = work.json()
        location = data.get("best_oa_location") or {}
        others = [other["pdf_url"] for other in data.get("locations") or [] if other.get("pdf_url")]
        candidates = [location.get("pdf_url"), location.get("landing_page_url"),
                      (data.get("open_access") or {}).get("oa_url"), *others[:_MORE_LOCATIONS]]
        tried = {url.rstrip("/")}
        for candidate in candidates:
            if not candidate or candidate.rstrip("/") in tried:
                continue
            tried.add(candidate.rstrip("/"))
            document = await self._extract(candidate, "oa:openalex")
            if document:
                return document
        return None

    async def _core(self, doi: str) -> Document | None:
        """The full text CORE holds for exactly `doi`, unless CORE's limit is spent."""
        global _core_resume_at
        if time.time() < _core_resume_at:
            return None
        await wait_rate_slot("core")
        headers = {"Authorization": f"Bearer {self.core_key}"} if self.core_key else {}
        response = await self.client.get(CORE_SEARCH_URL, params={"q": f'doi:"{doi}"', "limit": 1},
                                         headers=headers)
        if response.status_code == 429 or response.headers.get("x-ratelimit-remaining") == "0":
            _core_resume_at = max(_core_resume_at, _core_reset(response.headers.get("x-ratelimit-retry-after")))
        if response.status_code != 200:
            return None
        try:
            works = response.json().get("results") or []
        except ValueError:
            return None
        for work in works:
            text = " ".join(str(work.get("fullText") or "").split())
            if str(work.get("doi") or "").lower() == doi.lower() and usable(text):
                return _document(text, "core-fulltext", "oa:core")
        return None

    async def _mdpi_doi(self, issn: str, volume: str, issue: str, article: str) -> str | None:
        """The DOI OpenAlex records for an MDPI article, found from the parts of its address."""
        found = await self.client.get("https://api.openalex.org/works", params={
            "filter": f"primary_location.source.issn:{issn},biblio.volume:{volume},biblio.issue:{issue},"
                      f"biblio.first_page:{article}", "select": "doi"})
        results = (found.json().get("results") or []) if found.status_code == 200 else []
        doi = (results[0].get("doi") or "") if len(results) == 1 else ""
        return doi.removeprefix("https://doi.org/") or None

    async def _extract(self, url: str, via: str) -> Document | None:
        try:
            document = await self.extract(url)
        except (httpx.HTTPError, ValueError, ImportError, TypeError):
            return None
        return {**document, "via": via} if usable(document["text"]) else None


class ExaContentsReader:
    name = "exa"

    def __init__(self, client: httpx.AsyncClient, api_key: str, spend: ExternalSpend,
                 budget: StudyBudget | None = None) -> None:
        self.client, self.api_key, self.spend, self.budget = client, api_key, spend, budget

    async def read(self, url: str) -> Document | None:
        charge = await self.budget.reserve_fixed(EXA_PAGE_RESERVE_USD, "Exa page") if self.budget else None
        response = await self.client.post(EXA_CONTENTS_URL, headers={"x-api-key": self.api_key}, timeout=30,
                                          json={"urls": [url], "text": True})
        if response.status_code == 429 and self.budget and charge is not None:
            self.budget.release(charge)
        response.raise_for_status()
        data = response.json()
        cost = Decimal(str((data.get("costDollars") or {}).get("total", EXA_PAGE_RESERVE_USD / 2)))
        self.spend.usd += cost
        self.spend.pages += 1
        if self.budget and charge is not None:
            self.budget.settle_fixed(charge, cost)
        results = data.get("results") or []
        text = (results[0].get("text") or "") if results else ""
        return _document(text, "exa-contents", "exa") if usable(text) else None


class FirecrawlReader:
    name = "firecrawl"

    def __init__(self, client: httpx.AsyncClient, api_key: str, spend: ExternalSpend,
                 budget: StudyBudget | None = None) -> None:
        self.client, self.api_key, self.spend, self.budget = client, api_key, spend, budget

    async def read(self, url: str) -> Document | None:
        charge = await self.budget.reserve_fixed(FIRECRAWL_PAGE_USD, "Firecrawl page") if self.budget else None
        # "basic" only: the default "auto" escalates to stealth proxies after a 401, 403, or 429.
        response = await self.client.post(FIRECRAWL_SCRAPE_URL, timeout=60,
                                          headers={"Authorization": f"Bearer {self.api_key}"},
                                          json={"url": url, "formats": ["markdown"], "proxy": "basic",
                                                "timeout": 45_000})
        if response.status_code == 429 and self.budget and charge is not None:
            self.budget.release(charge)
        response.raise_for_status()
        self.spend.usd += FIRECRAWL_PAGE_USD
        self.spend.pages += 1
        if self.budget and charge is not None:
            self.budget.settle_fixed(charge, FIRECRAWL_PAGE_USD)
        data = response.json().get("data") or {}
        status = (data.get("metadata") or {}).get("statusCode")
        text = data.get("markdown") or ""
        return _document(text, "firecrawl-markdown", "firecrawl") if usable(text) and not (status and status >= 400) else None


async def read_elsewhere(url: str, readers: list[Any]) -> tuple[Document | None, list[str]]:
    """The first usable document the readers find, in order, and what each one that failed said."""
    notes: list[str] = []
    for reader in readers:
        try:
            document = await reader.read(url)
        except Exception as exc:  # noqa: BLE001 - one reader's failure, such as a refused budget, moves to the next
            notes.append(f"{reader.name}: {type(exc).__name__}")
            continue
        if document is not None:
            return document, notes
        notes.append(f"{reader.name}: not found")
    return None, notes

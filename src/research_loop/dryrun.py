"""The bug-finding harness: scripted models, an offline world, and the invariants every run must keep.

It exists to find bugs in the workflow's plumbing, IDs, and error handling for free, before a paid study
spends money on them. Nothing here produces good research; outputs are generated to reach edge cases.

- `FuzzModel` stands in for every role. Seeded, it reads which role it plays from the output tool's
  schema and returns outputs aimed at edge cases: colliding and unknown IDs, quotes from the wrong
  source, empty and oversized fields, too many questions or gaps. It also injects provider faults: rate
  limits, server errors, refusals, one-time TLS faults on scout requests, text where a tool call belongs, and usage large enough to trip cost
  limits. It reports its usage under Luna's name, so every cost limit and budget guard is really tested.
- `World` answers the research tools offline from a seeded corpus whose pages share passages, with
  identity variants (Wayback copies, ar5iv pages, DOIs in URLs, a doi.org redirect) and faults: 403,
  404, 500, redirect loops and redirects to blocked or unsafe addresses, timeouts, empty and non-UTF-8
  bodies, oversized pages, PDFs whose text holds lone surrogates, and failing search and scholarly APIs.
- `check_record` checks a finished run's stored record and calls against the invariants.
- `fuzz` runs many seeded runs in-process and reports every violation with the seed that reproduces it.

`fake:` models and the offline world only run together (`Settings.route_problems`).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import random
import ssl
import traceback
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qs, quote, urlparse
from uuid import UUID

import httpx
from pydantic_ai.exceptions import ContentFilterError, ModelHTTPError
from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel
from pydantic_ai.usage import RequestUsage

# Fake calls are priced as this model (prices.price_per_million maps `fake:` to it).
PRICED_AS = ("openai", "gpt-6-luna")

_WORDS = ["materials", "database", "inverse", "design", "crystal", "structure", "alloy", "electrolyte", "survey", "method", "neural", "network", "genetic", "algorithm", "bayesian", "optimization", "scaling", "cloud", "workload", "threshold", "queue", "reinforcement", "fuzzy", "forecast", "benchmark", "dataset", "repository", "catalog", "property", "phase", "diagram", "review", "overview", "official", "portal", "archive", "computed", "experimental", "measured"]
_ODD_TEXT = ("", " ", "ÄÖÜ ß ﬁ ẞ", "𝐹 math italic", "emoji 🧪🔬", "a" * 3000, "line\nbreaks\n\n", "[s1] [s99]",
             "quote with \"quotes\" and 'apostrophes'", "RTL שלום", "tab\tseparated")


def _stable(*parts: Any) -> int:
    """A seed derived from `parts`, the same in every process."""
    return int.from_bytes(hashlib.sha256(json.dumps(parts, default=str).encode()).digest()[:8], "big")


def _sentence(rng: random.Random) -> str:
    words = [rng.choice(_WORDS) for _ in range(rng.randint(8, 16))]
    return " ".join(words).capitalize() + "."


# ---------------------------------------------------------------------------------------------
# The offline world

WORLD_HOSTS = frozenset({"docs.dry.example", "journal.dry.example", "blog.dry.example", "cdn.dry.example",
                         "web.archive.org", "ar5iv.labs.arxiv.org", "link.springer.com", "doi.org"})
_SCHOLAR_HOSTS = frozenset({"api.openalex.org", "export.arxiv.org", "api.crossref.org"})
PAGE_FAULTS = ("403", "403_then_ok", "404", "500", "redirect_loop", "redirect_blocked", "redirect_http", "timeout",
               "empty", "bad_utf8", "huge", "surrogate_pdf", "unsupported_type")
_PDF_MARK = b"%PDF-DRY\n"


@dataclass
class Page:
    url: str
    title: str
    text: str
    kind: str = "html"  # or "pdf"
    fault: str | None = None
    doi: str | None = None


class World:
    """A seeded offline web, search engine, and set of scholarly APIs."""

    def __init__(self, seed: int, fault_rate: float = 0.2) -> None:
        self.seed, self.fault_rate = seed, fault_rate
        rng = random.Random(seed)
        pool = [_sentence(rng) for _ in range(50)]
        self.pages: dict[str, Page] = {}
        for number in range(16):
            host = rng.choice(["docs.dry.example", "journal.dry.example", "blog.dry.example", "cdn.dry.example"])
            kind = "pdf" if host == "cdn.dry.example" or rng.random() < 0.2 else "html"
            doi = f"10.5555/dry.{seed % 1000}.{number}" if rng.random() < 0.5 else None
            # Pages share sentences from one pool, so a quote can come from more than one page.
            text = " ".join(rng.sample(pool, rng.randint(4, 12)))
            if doi and rng.random() < 0.7:
                text = f"DOI: {doi}\n{text}"
            if rng.random() < 0.15:
                text = (text + " ") * 40  # longer than one fetch window
            fault = rng.choice(PAGE_FAULTS) if rng.random() < fault_rate else None
            url = f"https://{host}/p{number}" + (".pdf" if kind == "pdf" else "")
            self.pages[url] = Page(url, f"Page {number}: {rng.choice(_WORDS)} {rng.choice(_WORDS)}", text, kind,
                                   fault, doi)
        # Every world holds one PDF whose text extracts with surrogates, as a real one did.
        tricky = list(self.pages.values())[1]
        tricky.kind, tricky.fault = "pdf", "surrogate_pdf"
        tricky.url = tricky.url if tricky.url.endswith(".pdf") else tricky.url + ".pdf"
        self.pages = {page.url: page for page in self.pages.values()}
        base = [page for page in list(self.pages.values()) if page.fault is None]
        for page in base[:3]:
            copy = f"https://web.archive.org/web/2024/{page.url}"
            self.pages[copy] = Page(copy, page.title, page.text, page.kind, None, page.doi)
        for index, page in enumerate(base[3:5]):
            ar5iv = f"https://ar5iv.labs.arxiv.org/html/2401.0{index:04d}"
            self.pages[ar5iv] = Page(ar5iv, page.title, page.text)
        for page in [p for p in base if p.doi][:2]:
            publisher = f"https://link.springer.com/article/{page.doi}"
            self.pages[publisher] = Page(publisher, page.title, page.text, "html", None, page.doi)
        self.blocked = next(iter(self.pages))

    # -- the search engine (web.WebSearch's `engine`)
    async def search(self, query: str) -> list[dict[str, str]]:
        rng = random.Random(_stable(self.seed, "search", query))
        roll = rng.random()
        if roll < self.fault_rate * 0.3:
            raise RuntimeError("No results found.")
        if roll < self.fault_rate * 0.45:
            raise RuntimeError("ratelimit exceeded")
        urls = list(self.pages)
        chosen = rng.sample(urls, min(len(urls), rng.randint(0, 6)))
        results = []
        for url in chosen:
            page = self.pages[url]
            # A snippet usually comes from its page, sometimes from another page.
            source = page if rng.random() > 0.2 else self.pages[rng.choice(urls)]
            snippet = rng.choice(source.text.split(". ")) if source.text else ""
            results.append({"title": page.title, "href": url, "body": snippet})
        if rng.random() < 0.1:
            results.append({"title": "No link", "href": "", "body": "dropped"})
        return results

    # -- pages and scholarly APIs, over one mock transport
    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self._handle), follow_redirects=False)

    async def _handle(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host in _SCHOLAR_HOSTS:
            return self._scholarly(request)
        if host == "api.exa.ai":
            return self._exa_contents(request) if request.url.path == "/contents" else self._exa(request)
        if host == "api.firecrawl.dev":
            return self._firecrawl(request)
        url = str(request.url)
        if host == "doi.org":
            doi = request.url.path.lstrip("/")
            target = next((p.url for p in self.pages.values() if p.doi and p.doi.lower() == doi.lower()
                           and "springer" in p.url), None)
            return httpx.Response(301, headers={"location": target}) if target else httpx.Response(404)
        page = self.pages.get(url.split("?")[0])
        if page is None:
            return httpx.Response(404)
        browser = "Mozilla" in request.headers.get("user-agent", "")
        match page.fault:
            case "403":
                return httpx.Response(403)
            case "403_then_ok" if not browser:
                return httpx.Response(403)
            case "404":
                return httpx.Response(404)
            case "500":
                return httpx.Response(500)
            case "redirect_loop":
                return httpx.Response(302, headers={"location": url})
            case "redirect_blocked":
                return httpx.Response(302, headers={"location": self.blocked})
            case "redirect_http":
                return httpx.Response(302, headers={"location": url.replace("https://", "http://")})
            case "timeout":
                raise httpx.ReadTimeout("dry-run timeout", request=request)
            case "empty":
                return httpx.Response(200, headers={"content-type": "text/html"}, content=b"")
            case "bad_utf8":
                return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"},
                                      content=b"<html><body><p>\xff\xfe broken \xc3\x28 bytes</p></body></html>")
            case "huge":
                return httpx.Response(200, headers={"content-type": "text/html"},
                                      content=b"<html><body>" + b"x " * 3_000_000 + b"</body></html>")
            case "unsupported_type":
                return httpx.Response(200, headers={"content-type": "application/zip"}, content=b"PK\x03\x04")
        if page.kind == "pdf":
            body = _PDF_MARK + page.text.encode()
            if page.fault == "surrogate_pdf":
                body += b"\x00SURROGATE"
            return httpx.Response(200, headers={"content-type": "application/pdf"}, content=body)
        html = f"<html><head><title>{page.title}</title></head><body><article><p>{page.text}</p></article></body></html>"
        return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, content=html.encode())

    def _reader_fault(self, rng: random.Random) -> httpx.Response | None:
        """A reading service's fault (reading.py): a rate limit, a server error, a broken body."""
        roll = rng.random()
        if roll < self.fault_rate * 0.2:
            return httpx.Response(429, json={"error": "rate limit"})
        if roll < self.fault_rate * 0.3:
            return httpx.Response(500)
        if roll < self.fault_rate * 0.4:
            return httpx.Response(200, content=b"{not json")
        return None

    def _page_text(self, url: str, rng: random.Random) -> str:
        """What a reading service returns for a page: its text, nothing, or a challenge page."""
        page = self.pages.get(url)
        roll = rng.random()
        if page is None or roll < 0.15:
            return ""
        if roll < 0.25:
            return "Just a moment... Please enable JavaScript and cookies to continue."
        return (page.text + " ") * 8  # long enough to count as the page

    def _exa_contents(self, request: httpx.Request) -> httpx.Response:
        url = (json.loads(request.content or b"{}").get("urls") or [""])[0]
        rng = random.Random(_stable(self.seed, "exa-contents", url))
        if fault := self._reader_fault(rng):
            return fault
        body: dict[str, Any] = {"results": [{"url": url, "text": self._page_text(url, rng)}],
                                "statuses": [{"id": url, "status": "success"}]}
        if rng.random() > self.fault_rate * 0.25:
            body["costDollars"] = {"total": 0.5 if rng.random() < self.fault_rate * 0.2 else 0.001}
        return httpx.Response(200, json=body)

    def _firecrawl(self, request: httpx.Request) -> httpx.Response:
        url = json.loads(request.content or b"{}").get("url", "")
        rng = random.Random(_stable(self.seed, "firecrawl", url))
        if fault := self._reader_fault(rng):
            return fault
        status = 403 if rng.random() < 0.1 else 200
        return httpx.Response(200, json={"success": True, "data": {"markdown": self._page_text(url, rng),
                                                                  "metadata": {"statusCode": status}}})

    def _exa(self, request: httpx.Request) -> httpx.Response:
        """Exa's search endpoint (web.exa_engine): results with highlights and a reported cost, or a fault.
        Its results include the blocked page with a passage of its text, which must never reach a scout."""
        query = json.loads(request.content or b"{}").get("query", "")
        rng = random.Random(_stable(self.seed, "exa", query))
        roll = rng.random()
        if roll < self.fault_rate * 0.2:
            return httpx.Response(429, json={"error": "rate limit"})
        if roll < self.fault_rate * 0.3:
            return httpx.Response(500)
        if roll < self.fault_rate * 0.35:
            return httpx.Response(401, json={"error": "invalid API key"})
        if roll < self.fault_rate * 0.4:
            return httpx.Response(200, content=b"{not json")
        urls = list(self.pages)
        chosen = rng.sample(urls, min(len(urls), rng.randint(0, 10)))
        if rng.random() < 0.3 and self.blocked not in chosen:
            chosen.append(self.blocked)
        results = []
        for url in chosen:
            page = self.pages[url]
            source = page if rng.random() > 0.2 else self.pages[rng.choice(urls)]
            sentences = [part for part in source.text.split(". ") if part]
            results.append({"title": page.title, "url": url, "publishedDate": "2024-01-01",
                            "highlights": rng.sample(sentences, min(len(sentences), rng.randint(0, 3)))})
        if rng.random() < 0.1:
            results.append({"title": "No link", "highlights": ["dropped"]})
        body: dict[str, Any] = {"requestId": f"dry-{rng.getrandbits(32)}", "results": results}
        cost = rng.random()
        if cost > self.fault_rate * 0.25:  # sometimes no reported cost, so the list price is charged
            # Sometimes far above the reservation, so settling can take a hard cap past its reserve.
            body["costDollars"] = {"total": 0.5 if cost < self.fault_rate * 0.4 else 0.007}
        return httpx.Response(200, json=body)

    def _scholarly(self, request: httpx.Request) -> httpx.Response:
        rng = random.Random(_stable(self.seed, "scholar", str(request.url)))
        roll = rng.random()
        if roll < self.fault_rate * 0.2:
            return httpx.Response(429, headers={"retry-after": "0"})
        if roll < self.fault_rate * 0.3:
            return httpx.Response(500)
        if roll < self.fault_rate * 0.4:
            return httpx.Response(200, content=b"{not json or xml")
        records = [p for p in self.pages.values() if p.doi]
        works = rng.sample(records, min(len(records), rng.randint(0, 4)))
        host, path = request.url.host, request.url.path
        if host == "api.openalex.org":
            if path.startswith("/works/"):
                return httpx.Response(200, json=self._openalex(works[0] if works else records[0], 0))
            return httpx.Response(200, json={"results": [self._openalex(w, i) for i, w in enumerate(works)],
                                             "meta": {"count": len(works) + rng.randint(0, 20)}})
        if host == "api.crossref.org":
            doi = request.url.path.removeprefix("/works/")
            match = next((p for p in records if quote(p.doi or "", safe="") == doi or p.doi == doi), None)
            if match is None:
                return httpx.Response(404)
            return httpx.Response(200, json={"message": {"DOI": match.doi, "URL": match.url, "title": [match.title],
                                                         "type": "journal-article", "abstract": match.text[:400]}})
        entries = "".join(
            f"<entry><id>http://arxiv.org/abs/2401.0{i:04d}v1</id><title>{w.title}</title>"
            f"<summary>{w.text[:300]}</summary><published>2024-01-0{i + 1}T00:00:00Z</published></entry>"
            for i, w in enumerate(works[:3]))
        query = parse_qs(urlparse(str(request.url)).query)
        if rng.random() < 0.1 or query.get("id_list") == ["garbage"]:
            entries = "<entry><id></id><title></title></entry>"
        xml = f'<feed xmlns="http://www.w3.org/2005/Atom">{entries}</feed>'
        return httpx.Response(200, content=xml.encode(), headers={"content-type": "application/atom+xml"})

    @staticmethod
    def _openalex(page: Page, index: int) -> dict[str, Any]:
        words = page.text.split()[:60]
        return {"id": f"https://openalex.org/W{9000 + index}", "display_name": page.title,
                "ids": {"doi": f"https://doi.org/{page.doi}"} if page.doi else {},
                "primary_location": {"landing_page_url": page.url, "source": {"type": "journal",
                                                                               "display_name": "Dry Journal"}},
                "publication_date": "2023-05-01",
                "abstract_inverted_index": {word: [i] for i, word in enumerate(words)} if words else None}

    def install(self, stack: AsyncExitStack, policy: Any = None) -> None:
        """Serve the world for the rest of `stack`: hosts count as public, rate slots never wait, and the
        world's PDFs extract to their text. Everything is restored when `stack` closes."""
        from . import acquisition, scholar, web

        async def public(url: str) -> bool:
            parsed = urlparse(url)
            return parsed.scheme == "https" and (parsed.hostname in WORLD_HOSTS or parsed.hostname in _SCHOLAR_HOSTS)

        async def no_wait(provider: str) -> None:
            return None

        original_pdf = web._pdf_text

        def pdf_text(content: bytes) -> tuple[str, bool]:
            if content.startswith(_PDF_MARK):
                text = content[len(_PDF_MARK):].decode("utf-8", "replace")
                if text.endswith("\x00SURROGATE"):
                    # pypdf can return a character outside the BMP as two surrogates, or half of one.
                    text = text.removesuffix("\x00SURROGATE") + " size𝐹 and a lone \ud835 here"
                return text, False
            return original_pdf(content)

        patches = [(acquisition, "public_url", public), (web, "public_url", public), (web, "wait_rate_slot", no_wait),
                   (scholar, "wait_rate_slot", no_wait), (web, "_pdf_text", pdf_text)]
        for module, name, value in patches:
            previous = getattr(module, name)
            setattr(module, name, value)
            stack.callback(setattr, module, name, previous)


# ---------------------------------------------------------------------------------------------
# The fuzz model

def _prompt(messages: list[ModelMessage]) -> dict[str, Any]:
    for message in messages:
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                    try:
                        value = json.loads(part.content)
                    except ValueError:
                        return {}
                    return value if isinstance(value, dict) else {}
    return {}


@dataclass
class _Seen:
    """What the research tools have returned in this call so far."""

    sources: list[dict[str, Any]] = field(default_factory=list)  # url/doi/arxiv_id/title/text

    @classmethod
    def of(cls, messages: list[ModelMessage]) -> _Seen:
        seen = cls()
        for message in messages:
            if not isinstance(message, ModelRequest):
                continue
            for part in message.parts:
                if not isinstance(part, ToolReturnPart):
                    continue
                data = part.content
                if isinstance(data, str):
                    try:
                        data = json.loads(data)
                    except ValueError:
                        continue
                if not isinstance(data, dict):
                    continue
                for item in data.get("results") or []:
                    seen.sources.append({"url": item.get("url"), "title": item.get("title") or "t",
                                         "text": item.get("snippet") or ""})
                if data.get("text"):
                    seen.sources.append({"url": data.get("url"), "title": "Fetched", "text": data["text"]})
                for work in data.get("works") or []:
                    seen.sources.append({"url": work.get("url"), "doi": work.get("doi"), "arxiv_id": work.get("arxiv_id"),
                                         "title": work.get("title") or "w", "text": work.get("abstract") or ""})
        return seen


class FuzzInjectedError(Exception):
    """An error the fuzz model raises on purpose, which no handler expects; the invariants tell it apart
    from a real one."""


class FuzzTransientNetworkError(ssl.SSLError):
    """A TLS fault on one attempt of a request, as the OpenAI client raised unwrapped in st07 run 7a7fc5b5.
    The fuzz model raises it once per request, so a call that ends on it was not retried."""


class FuzzModel(FunctionModel):
    """A seeded model that plays every role with edge-case outputs and injected provider faults."""

    def __init__(self, *, seed: int, fault_rate: float = 0.2, name: str = "fuzz") -> None:
        super().__init__(self._respond, stream_function=self._stream, model_name=PRICED_AS[1])
        self.seed, self.fault_rate, self.fuzz_name = seed, fault_rate, name
        self._network_faulted: set[tuple[str, int, str]] = set()

    # -- plumbing
    def _rng(self, messages: list[ModelMessage], info: AgentInfo) -> random.Random:
        prompt = _prompt(messages)
        return random.Random(_stable(self.seed, self._role(info), len(messages),
                                     hashlib.sha256(json.dumps(prompt, sort_keys=True, default=str).encode()).hexdigest()))

    @staticmethod
    def _role(info: AgentInfo) -> str:
        if not info.output_tools:
            return "text"
        properties = info.output_tools[0].parameters_json_schema.get("properties", {})
        for role, key in (("planner", "questions"), ("scout", "conclusion"), ("gap", "gaps"),
                          ("synthesizer", "executive_summary"), ("judge", "verdicts")):
            if key in properties:
                return role
        return "other"

    def _response(self, parts: list[Any], messages: list[ModelMessage], rng: random.Random,
                  huge: bool = False) -> ModelResponse:
        size = sum(len(str(m)) for m in messages)
        usage = RequestUsage(input_tokens=5_000_000 if huge else max(size // 4, 50),
                             output_tokens=max(sum(len(str(p)) for p in parts) // 4, 5))
        return ModelResponse(parts=parts, usage=usage, model_name=PRICED_AS[1], provider_name=PRICED_AS[0])

    async def _respond(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        # Encode the request as a provider client would, so text it cannot send fails here as it did live.
        ModelMessagesTypeAdapter.dump_json(messages)
        rng = self._rng(messages, info)
        role = self._role(info)
        if rng.random() < self.fault_rate:
            fault = rng.choice(["429", "429_retry", "500", "refusal", "text", "huge", "slow", "unexpected", "network"])
            if fault == "network" and role == "scout":
                # Fails the request's first attempt only; the same request sent again succeeds.
                key = (role, len(messages), hashlib.sha256(json.dumps(_prompt(messages), sort_keys=True,
                                                                     default=str).encode()).hexdigest())
                if key not in self._network_faulted:
                    self._network_faulted.add(key)
                    raise FuzzTransientNetworkError("[SSL: SSLV3_ALERT_BAD_RECORD_MAC] fuzz bad record mac")
            if fault == "unexpected" and role == "scout":
                # An error no handler expects, as the UnicodeEncodeError from a PDF was: one scout's bug
                # must cut off only its question.
                raise FuzzInjectedError("an unexpected error inside a scout")
            if fault == "429":
                raise ModelHTTPError(429, PRICED_AS[1])
            if fault == "429_retry":
                raise ModelHTTPError(429, PRICED_AS[1], headers={"retry-after": "0"})
            if fault == "500":
                raise ModelHTTPError(500, PRICED_AS[1])
            if fault == "refusal":
                raise ContentFilterError("fuzz refusal")
            if fault == "text":
                return self._response([TextPart("I will not use the output tool.")], messages, rng)
            if fault == "slow":
                await asyncio.sleep(rng.uniform(0.2, 1.5))
            if fault == "huge":
                return self._response(self._output(role, messages, info, rng), messages, rng, huge=True)
        if role == "scout" and info.function_tools:
            turns = sum(1 for m in messages if isinstance(m, ModelResponse)
                        and any(isinstance(p, ToolCallPart) and p.tool_name in {t.name for t in info.function_tools}
                                for p in m.parts))
            if turns < rng.randint(0, 4):
                return self._response(self._tool_calls(messages, rng), messages, rng)
        return self._response(self._output(role, messages, info, rng), messages, rng)

    async def _stream(self, messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[Any]:
        response = await self._respond(messages, info)
        for index, part in enumerate(response.parts):
            if isinstance(part, TextPart):
                yield part.content
            elif isinstance(part, ToolCallPart):
                args = part.args if isinstance(part.args, str) else json.dumps(part.args)
                yield {index: DeltaToolCall(name=part.tool_name, json_args=args)}

    # -- research tool calls
    def _tool_calls(self, messages: list[ModelMessage], rng: random.Random) -> list[ToolCallPart]:
        seen = _Seen.of(messages)
        urls = [s["url"] for s in seen.sources if s.get("url")] or ["https://docs.dry.example/p1"]
        calls = []
        count = 40 if rng.random() < 0.05 else rng.randint(1, 5)  # a batch past the tool-call limit, sometimes
        for _ in range(count):
            kind = rng.choice(["web_search", "web_search", "fetch", "fetch", "scholar_search", "scholar_get"])
            if kind == "web_search":
                args: dict[str, Any] = {"query": rng.choice([" ".join(rng.sample(_WORDS, 3)), "", "site:x \"q\""])}
            elif kind == "fetch":
                args = {"url": rng.choice(urls + ["http://insecure.dry.example/x", "not a url",
                                                  "https://unknown.example/", "https://doi.org/10.5555/dry.0.1"]),
                        "start": rng.choice([0, 0, 0, 12000, 10**9, -5])}
            elif kind == "scholar_search":
                args = {"query": " ".join(rng.sample(_WORDS, 2)), "limit": rng.choice([0, 5, 50]),
                        "year_from": rng.choice([None, 2020]), "year_to": rng.choice([None, 1990, 2024])}
            else:
                args = {"identifier": rng.choice(["10.5555/dry.1.2", "W9001", "2401.00001", "garbage", ""])}
            calls.append(ToolCallPart(kind, args, tool_call_id=f"call-{rng.getrandbits(32)}"))
        return calls

    # -- outputs by role
    def _output(self, role: str, messages: list[ModelMessage], info: AgentInfo, rng: random.Random) -> list[Any]:
        if not info.output_tools:
            return [TextPart(rng.choice(_ODD_TEXT) or "text")]
        prompt = _prompt(messages)
        builder = {"planner": self._plan, "scout": self._research, "gap": self._gaps, "synthesizer": self._report,
                   "judge": self._verdicts}.get(role)
        value = builder(prompt, messages, rng) if builder else _from_schema(
            info.output_tools[0].parameters_json_schema, rng, info.output_tools[0].parameters_json_schema)
        return [ToolCallPart(info.output_tools[0].name, value, tool_call_id=f"out-{rng.getrandbits(32)}")]

    def _text(self, rng: random.Random) -> str:
        return rng.choice(_ODD_TEXT) if rng.random() < 0.15 else _sentence(rng)

    def _plan(self, prompt: dict[str, Any], messages: list[ModelMessage], rng: random.Random) -> dict[str, Any]:
        """Mostly a valid plan, with IDs that collide with what code assigns (d1, o1, q1); a quarter of the
        time an invalid one: repeated IDs, unknown `covers`, no questions, or too many for the depth."""
        caps = prompt.get("max_questions") if isinstance(prompt.get("max_questions"), dict) else {"standard": 4}
        depth = prompt.get("depth") or rng.choice(list(caps))
        valid = rng.random() < 0.75
        item_ids = rng.sample(["k1", "d1", "d2", "o1", "o2", "q1", "k2", "c1"], rng.randint(0, 6))
        coverage = [{"id": item_id, "requirement": self._text(rng) or "r",
                     "kind": rng.choice(["category", "category", "dimension", "constraint", "assumption"])}
                    for item_id in item_ids]
        count = rng.randint(1, caps.get(depth, 4)) if valid else rng.choice([0, caps.get(depth, 4) + 2])
        question_ids = rng.sample(["a", "b", "c", "d", "q1", "q2", "d1", "o1"], min(count, 8))
        if not valid and question_ids:
            question_ids[-1] = question_ids[0]
        questions = [{"id": question_id, "question": self._text(rng) or "Q?",
                      "requires_primary_sources": rng.random() < 0.3,
                      "covers": rng.sample(item_ids, rng.randint(0, min(3, len(item_ids))))
                      + ([] if valid else ["zz"])}
                     for question_id in question_ids]
        return {"questions": questions, "coverage": coverage, "depth": depth}

    def _research(self, prompt: dict[str, Any], messages: list[ModelMessage], rng: random.Random) -> dict[str, Any]:
        seen = _Seen.of(messages)
        coverage_ids = [item.get("id") for item in prompt.get("coverage") or [] if isinstance(item, dict)]
        requirements = [item.get("requirement", "") for item in prompt.get("coverage") or [] if isinstance(item, dict)]
        claims = []
        for _ in range(rng.randint(0, 5)):
            evidence = []
            for _ in range(rng.randint(0, 3)):
                source = rng.choice(seen.sources) if seen.sources and rng.random() < 0.8 else {
                    "url": rng.choice(["https://invented.dry.example/x", "https://docs.dry.example/p99"]),
                    "title": "Invented", "text": ""}
                other = rng.choice(seen.sources) if seen.sources else source
                ref = {key: source[key] for key in ("url", "doi", "arxiv_id") if source.get(key)} or {"url": "https://x.dry.example"}
                ref["title"] = source.get("title") or "t"
                exact = _substring(source.get("text", ""), rng)
                quote = exact if rng.random() < 0.5 else rng.choice([_substring(other.get("text", ""), rng),
                                                                     _mutate(exact, rng), None, "", "..."])
                evidence.append({"source": ref, "excerpt": self._text(rng) or "e", "quote": quote,
                                 "supports": rng.random() > 0.1, "confidence": rng.random()})
            claims.append({"id": rng.choice(["c1", "c1", "c2", "q1/c1", "c3"]), "statement": self._text(rng) or "s",
                           "confidence": rng.random(), "evidence": evidence,
                           "covers": rng.sample(coverage_ids + ["zz", "o1", "d1"], rng.randint(0, 2))})
        open_items = [rng.choice(requirements + [self._text(rng), "ICSD", "ICSD ", "icsd", ""]) if requirements
                      else rng.choice(["ICSD", "CSD", "", self._text(rng)]) for _ in range(rng.randint(0, 4))]
        if "material_gap" in prompt:
            # A deep dive usually names something new, which is what shifted open-item IDs once.
            open_items.append(f"Newly named {rng.choice(_WORDS)} {rng.randint(1, 99)}")
        return {"question_id": "q?", "question": self._text(rng) or "q", "conclusion": self._text(rng),
                "claims": claims, "confidence": rng.random(), "open_items": open_items,
                "unresolved": [self._text(rng) for _ in range(rng.randint(0, 2))],
                "contradictions": [{"description": "c", "claim_ids": ["c1", "c99"]}] if rng.random() < 0.2 else []}

    def _gaps(self, prompt: dict[str, Any], messages: list[ModelMessage], rng: random.Random) -> dict[str, Any]:
        question_ids = [q.get("id") for q in (prompt.get("plan") or {}).get("questions", []) if isinstance(q, dict)]
        limit = prompt.get("max_gaps") or 3
        valid = rng.random() < 0.75 and question_ids
        count = rng.randint(0, limit) if valid else rng.randint(0, limit + 2)
        # The first question is favored: follow-up research under an earlier question is the harder case.
        if question_ids and rng.random() < 0.5:
            question_ids = question_ids[:1]
        open_ids = [item.get("id") for item in prompt.get("coverage") or []
                    if isinstance(item, dict) and item.get("status") == "open"]
        return {"gaps": [{"question_id": rng.choice(question_ids if valid else question_ids + ["q99"]) if question_ids else "q1",
                          "follow_up_question": self._text(rng) or "f", "reason": self._text(rng) or "r",
                          "coverage_id": rng.choice(open_ids + [None, "zz"]) if open_ids else rng.choice([None, "zz"])}
                         for _ in range(count)]}

    def _report(self, prompt: dict[str, Any], messages: list[ModelMessage], rng: random.Random) -> dict[str, Any]:
        research = prompt.get("research") or {}
        claims = [c for r in research.get("research", []) for c in r.get("claims", [])]
        sources = [s.get("id") for s in research.get("sources", []) if isinstance(s, dict)]
        ids = [c.get("id") for c in claims] + ["q9/c9"] * (rng.random() < 0.2)
        cite = lambda: f" [{rng.choice(sources + ['s99'])}]" if sources and rng.random() < 0.7 else ""
        lines = ["## Answer"] + [self._text(rng) + cite() for _ in range(rng.randint(0, 6))]
        if rng.random() < 0.3:
            lines += ["| A | B |", "|---|---|", f"| {self._text(rng)} | x{cite()} |"]
        coverage = [c.get("id") for c in prompt.get("coverage") or [] if isinstance(c, dict)]
        return {"title": self._text(rng) or "T", "executive_summary": self._text(rng) + cite(),
                "answer": "\n".join(lines),
                "claims": [{"statement": self._text(rng) or "s", "claim_ids": rng.sample(ids, min(len(ids), rng.randint(0, 2)))}
                           for _ in range(rng.randint(0, 5))],
                "caveats": [self._text(rng) + cite() for _ in range(rng.randint(0, 2))],
                "not_established": rng.sample(coverage + ["zz"], rng.randint(0, min(2, len(coverage) + 1)))}

    def _verdicts(self, prompt: dict[str, Any], messages: list[ModelMessage], rng: random.Random) -> dict[str, Any]:
        if "statements" in prompt:  # the support audit (audit.py), not the rubric judge
            verdicts = [{"id": item["id"], "verdict": rng.choice(["supported", "partial", "unsupported"]),
                         "reason": self._text(rng) or "r"} for item in prompt["statements"]]
            if verdicts and rng.random() < 0.2:
                # One missing or one invented verdict, which the audit's validator must send back.
                if rng.random() < 0.5:
                    verdicts.pop(rng.randrange(len(verdicts)))
                else:
                    verdicts.append({"id": "a999", "verdict": "supported", "reason": "invented"})
            return {"verdicts": verdicts}
        rubric = prompt.get("rubric") or {}
        verdicts = [{"category": category, "point": point["point"], "met": rng.random() < 0.5}
                    for category, points in rubric.items() for point in points]
        if verdicts and rng.random() < 0.2:
            # One missing or one invented verdict, which the judge's validator must send back.
            if rng.random() < 0.5:
                verdicts.pop(rng.randrange(len(verdicts)))
            else:
                verdicts.append({"category": "invented", "point": 99, "met": True})
        return {"verdicts": verdicts}


def _substring(text: str, rng: random.Random) -> str | None:
    if not text:
        return None
    words = text.split()
    start = rng.randint(0, max(len(words) - 1, 0))
    return " ".join(words[start:start + rng.randint(3, 12)]) or None


def _mutate(text: str | None, rng: random.Random) -> str | None:
    if not text:
        return text
    words = text.split()
    words[rng.randrange(len(words))] = "zzzmutated"
    return " ".join(words)


def _from_schema(schema: dict[str, Any], rng: random.Random, root: dict[str, Any], depth: int = 0) -> Any:
    """A value that fits a JSON schema, for roles without a dedicated generator."""
    if "$ref" in schema:
        return _from_schema(root.get("$defs", {}).get(schema["$ref"].split("/")[-1], {}), rng, root, depth + 1)
    for key in ("anyOf", "oneOf"):
        if key in schema:
            return _from_schema(rng.choice(schema[key]), rng, root, depth + 1)
    if "enum" in schema:
        return rng.choice(schema["enum"])
    kind = schema.get("type")
    if kind == "object" or "properties" in schema:
        return {name: _from_schema(sub, rng, root, depth + 1) for name, sub in schema.get("properties", {}).items()
                if name in schema.get("required", []) or rng.random() < 0.5}
    if kind == "array":
        return [] if depth > 4 else [_from_schema(schema.get("items", {}), rng, root, depth + 1)
                                     for _ in range(rng.randint(0, 3))]
    if kind == "integer":
        return rng.randint(0, 5)
    if kind == "number":
        return rng.random()
    if kind == "boolean":
        return rng.random() < 0.5
    if kind == "null":
        return None
    return _sentence(rng)


# ---------------------------------------------------------------------------------------------
# Invariants

def check_record(record: dict[str, Any], calls: list[dict[str, Any]]) -> list[str]:
    """Every invariant a finished run's stored record and calls break; empty when it keeps them all."""
    from .acquisition import SourcePolicy
    from .evidence import (
        EvidenceLedger,
        _key,
        _normal,
        _segments,
        check_result,
        coverage_items,
        inline_source_ids,
        source_identity,
    )
    from .render import render_markdown
    from .schemas import FinalReport, ResearchPlan, ResearchResult
    from .scout import _answer_support, _checks, _status
    from .tools import labeled_texts

    problems: list[str] = []
    status = record.get("status")
    if status not in ("complete", "partial", "failed", "cancelled"):
        problems.append(f"status {status!r} is not an end state")
    for call in calls:
        if call.get("status") == "running":
            problems.append(f"call {call.get('role')} {call.get('question_id')} was left running")
        elif not call.get("stop_reason"):
            problems.append(f"call {call.get('role')} {call.get('question_id')} has no stop reason")
    try:
        text = json.dumps(record, default=str)
        again = json.loads(text)
        ledger = EvidenceLedger.from_json(again.get("ledger") or {})
        if ledger.to_json() != (record.get("ledger") or {}):
            problems.append("the ledger does not survive a JSON round trip")
    except Exception as exc:  # noqa: BLE001 - any failure here is itself the finding
        return [*problems, f"the record does not round-trip: {type(exc).__name__}: {exc}"]

    claim_ids = [claim.id for claim in ledger.claims()]
    if len(claim_ids) != len(set(claim_ids)):
        problems.append("ledger claim IDs repeat")
    sources = [row["id"] for row in ledger.source_table()]
    if len(sources) != len(set(sources)):
        problems.append("source IDs repeat")
    plan = ResearchPlan.model_validate(record["plan"]) if record.get("plan") else None
    report = FinalReport.model_validate(record["report"]) if record.get("report") else None
    checks = record.get("checks") or {}
    if plan is not None:
        questions = [q.id for q in plan.questions]
        if len(questions) != len(set(questions)):
            problems.append("plan question IDs repeat")
        items = coverage_items(plan, ledger)
        item_ids = [item.id for item, _ in items]
        if len(item_ids) != len(set(item_ids)):
            problems.append(f"coverage item IDs repeat: {sorted(i for i in item_ids if item_ids.count(i) > 1)}")
        state_ids = [s["id"] for s in checks.get("coverage") or []]
        if len(state_ids) != len(set(state_ids)):
            problems.append("coverage states repeat an ID")
        # Open items keep their IDs when later research (a deep dive) adds results.
        first_round = EvidenceLedger()
        planned = {q.id: q.question for q in plan.questions}
        for result in ledger.all():
            if planned.get(result.question_id) == result.question:
                first_round.add(result)
        # Compared as normalized names: "ICSD" and "icsd " are one item, whichever spelling came first.
        before = {item.id: _normal(item.requirement) for item, _ in coverage_items(plan, first_round)}
        after = {item.id: _normal(item.requirement) for item in (i for i, _ in items)}
        if moved := sorted(i for i, requirement in before.items() if after.get(i) != requirement):
            problems.append(f"open-item IDs changed meaning after follow-up research: {moved}")
        # A deep dive's claims that name no coverage item count toward the item its gap targeted, so none
        # is left untagged. (Claims that name other items legitimately leave the target open.)
        # A question's first result is its scout; later ones are its deep dives, added in gap order. (Matching
        # by question text misfired when a planned question and a follow-up had the same text.)
        dives = {question_id: list(results[1:]) for question_id, results in ledger.results.items()}
        for gap in (checks.get("gap_analysis") or {}).get("gaps") or []:
            queue = dives.get(gap.get("question_id"), [])
            result = queue.pop(0) if queue else None
            if gap.get("coverage_id") and result is not None and any(not c.covers for c in result.claims):
                problems.append(f"a deep dive for {gap['coverage_id']} left claims with no coverage item")
        # Every result's open items passed through the scout's filter: short names, each once, at most five.
        from .agents import open_item_names
        for result in ledger.all():
            if result.open_items != open_item_names(result.open_items):
                problems.append(f"open items of {result.question_id} are not all short names: {result.open_items[:3]}")
        known = set(item_ids)
        for claim in ledger.claims():
            if unknown := sorted(set(claim.covers) - known):
                problems.append(f"claim {claim.id} covers unknown items {unknown}")
    if report is not None:
        missing = set(report.claim_ids_used) - set(claim_ids)
        if missing and not any("unknown claim IDs" in p for p in checks.get("citation_problems") or []):
            problems.append(f"report cites missing claims {sorted(missing)} without a citation problem")
        behind = {s for claim_id in report.claim_ids_used if claim_id in claim_ids
                  for s in ledger.claim_source_ids().get(claim_id, ())}
        stray = {s for text in report.cited_texts for s in inline_source_ids(text)} - behind
        if stray and not any("inline citations" in p for p in checks.get("citation_problems") or []):
            problems.append(f"stray inline citations {sorted(stray)} survived without a citation problem")

    # A TLS fault the fuzz model injects clears when the request is sent again, so a call may not end on it:
    # one such fault lost st07's decisive question.
    for call in calls:
        if "FuzzTransientNetworkError" in str(call.get("stop_reason") or ""):
            problems.append(f"call {call.get('role')} {call.get('question_id')} ended on a transient network "
                            "error that one retry would have cleared")
    # A run note that calls something a bug is one, unless the fuzz model injected it on purpose.
    for note in record.get("notes") or []:
        if "this is a bug" in note and "FuzzInjectedError" not in note:
            problems.append(f"the run reported a bug: {note}")
    # Every message a call exchanged must be sendable as UTF-8; a PDF's lone surrogate once failed a request.
    for call in calls:
        try:
            json.dumps(call.get("messages") or [], ensure_ascii=False).encode("utf-8")
        except UnicodeEncodeError:
            problems.append(f"call {call.get('role')} {call.get('question_id')} exchanged text that is not valid UTF-8")
    # Evidence checks recompute from the calls' messages, and a verified quote is in its cited source's
    # text: an independent check, since recomputing with the same code cannot catch a wrong rule.
    for call in calls:
        if call.get("role") not in ("scout", "deep_dive") or call.get("status") != "succeeded" or not call.get("output"):
            continue
        stored = ResearchResult.model_validate(call["output"])
        try:
            texts = labeled_texts(ModelMessagesTypeAdapter.validate_python(call.get("messages") or []))
        except Exception as exc:  # noqa: BLE001
            problems.append(f"call messages do not load: {type(exc).__name__}")
            continue
        for claim in stored.claims:
            for item in claim.evidence:
                if item.quote_check != "verified":
                    continue
                keys = source_identity(item.source)
                segments = _segments(item.quote or "")
                if not any(all(s in _key(t.text) for s in segments) for t in texts if t.keys & keys):
                    problems.append(f"a verified quote in {call.get('question_id')} is not in its cited source's text")
        again_checked = check_result(stored, texts)
        for before_claim, after_claim in zip(stored.claims, again_checked.claims, strict=True):
            for a, b in zip(before_claim.evidence, after_claim.evidence, strict=True):
                if (a.quote_check, a.source_check, a.source_access) != (b.quote_check, b.source_check, b.source_access):
                    problems.append(f"evidence checks of {call.get('question_id')} do not recompute")
                    break

    # Derived values recompute from the record.
    if plan is not None and status != "cancelled" and record.get("checks"):
        unpriced = any("no price" in reason for reason in checks.get("review_reasons") or [])
        fresh = _checks(plan, ledger, report, unpriced, synthesized=record.get("workflow_version", "").find("research") < 0)
        fresh.follow_up_unresolved = checks.get("follow_up_unresolved", False)
        if report is not None and _answer_support(report, fresh) != checks.get("answer_support"):
            problems.append(f"answer support {checks.get('answer_support')} recomputes as {_answer_support(report, fresh)}")
        if [s.model_dump(mode="json") for s in fresh.coverage] != checks.get("coverage", []):
            problems.append("coverage states do not recompute")
        for key in ("quotes", "quotes_verified", "quotes_misattributed", "quotes_short", "sentences",
                    "uncited_sentences"):
            if getattr(fresh, key) != checks.get(key, getattr(fresh, key)):
                problems.append(f"{key} {checks.get(key)} recomputes as {getattr(fresh, key)}")
        mode_research = "research" in record.get("workflow_version", "")
        gap_failed = bool(record.get("config", {}).get("follow_up")) and checks.get("gap_analysis") is None \
            and any("gap analysis did not finish" in r for r in checks.get("review_reasons") or [])
        expected = _status(ledger, None if mode_research else report, synthesized=not mode_research,
                           gap_analysis_failed=gap_failed)
        if status in ("complete", "partial", "failed") and expected != status:
            problems.append(f"status {status} recomputes as {expected}")

    # A run whose hard cap is far above its cost should not see every call refused by the budget guard;
    # that meant the guard could not price the model (found by a dry study).
    refused = [c for c in calls if "study budget refused" in str(c.get("stop_reason") or "")]
    cap = Decimal(str(((record.get("config") or {}).get("study_budget") or {}).get("cap_usd") or 0))
    if calls and len(refused) == len(calls) and cap >= Decimal(1):
        problems.append(f"every call was refused by the study budget under a ${cap} cap: {refused[0].get('stop_reason')}")
    # No search result or scholarly record a scout saw comes from a blocked source: an Exa highlight can carry a
    # blocked page's text, and an OpenAlex record a blocked paper's abstract, such as a frozen case's expert report.
    policy = SourcePolicy(tuple((record.get("config") or {}).get("blocked_urls") or []))
    for call in calls:
        for message in call.get("messages") or []:
            for part in message.get("parts") or []:
                content = part.get("content") if part.get("tool_name") == "web_search" else None
                shown = [item.get("url", "") for item in (content or {}).get("results") or []] \
                    if isinstance(content, dict) else []
                if blocked := [url for url in shown if url and policy.blocks(url)]:
                    problems.append(f"call {call.get('role')} {call.get('question_id')} was shown a blocked "
                                    f"search result: {blocked[0]}")
                scholarly = part.get("content") if part.get("tool_name") in ("scholar_search", "scholar_get") else None
                works = (scholarly.get("works") or []) if isinstance(scholarly, dict) else []
                if blocked := [work.get("title") for work in works
                               if policy.blocks_work((work.get("url"), work.get("full_text_url")), work.get("doi"),
                                                     work.get("arxiv_id"))]:
                    problems.append(f"call {call.get('role')} {call.get('question_id')} was shown a blocked "
                                    f"scholarly record: {blocked[0]}")
    # A blocked page is never read, by our fetcher or any fallback reader.
    for call in calls:
        for message in call.get("messages") or []:
            for part in message.get("parts") or []:
                content = part.get("content") if part.get("tool_name") == "fetch" else None
                if isinstance(content, dict) and content.get("text") and policy.blocks(str(content.get("url") or "")):
                    problems.append(f"call {call.get('role')} {call.get('question_id')} read a blocked page "
                                    f"{content.get('url')} via {content.get('via', 'our fetcher')}")
    # Money: the run's cost is its calls' costs, plus what its paid web searches cost.
    costs = [Decimal(str(c["cost_usd"])) for c in calls if c.get("cost_usd") is not None]
    run_checks = record.get("checks") or {}
    searches = Decimal(str(run_checks.get("external_usd", run_checks.get("search_usd")) or 0))
    if (record.get("cost_usd") is not None and costs
            and abs(Decimal(str(record["cost_usd"])) - sum(costs) - searches) > Decimal("0.000001")):
        problems.append(f"run cost {record['cost_usd']} is not its calls' {sum(costs)} and external services' {searches}")
    try:
        markdown = render_markdown(record)
        status_line = next((line for line in markdown.splitlines() if line.startswith("Scout run `")), "")
        if str(status) not in status_line:
            problems.append("the rendered status line does not show the status")
    except Exception as exc:  # noqa: BLE001
        problems.append(f"rendering failed: {type(exc).__name__}: {exc}")
    return problems


# ---------------------------------------------------------------------------------------------
# The in-process fuzz loop

@dataclass
class FuzzFinding:
    seed: int
    kind: str
    problems: list[str]


def fuzz_settings(seed: int, fault_rate: float, base: Any = None) -> Any:
    from .config import ScoutModels, Settings

    base = base or Settings()
    rng = random.Random(_stable("settings", seed))
    fake = "fake:fuzz@high"
    # Validated like any configuration, so the harness only explores limits Scout accepts. Deadlines are
    # upper bounds and fake calls are fast, so the 90 seconds kept for synthesis cost no time.
    times = {"research_seconds": rng.choice([3.0, 20.0, 20.0]), "deadline_seconds": 60.0,
             "followup_deadline_seconds": 130.0, "deep_dive_seconds": 8.0}
    limits = type(base.limits).model_validate(base.limits.model_dump() | times | {
        "gap_seconds": 5.0, "request_timeout_seconds": 1.0, "max_gaps": rng.choice([1, 3]),
        "quick": base.limits.quick.model_dump() | {"research_seconds": 3.0, "deadline_seconds": 30.0},
        "deep": base.limits.deep.model_dump() | times,
    })
    return base.model_copy(update={
        # A second scout model in some runs, so a deep run's scouts and dives split between two models' pacers,
        # prices, and budget reservations.
        "models": ScoutModels(planner=fake, scout=fake, synthesizer=fake, fallback=rng.choice([fake, None]), judge=fake,
                              scout_alt=rng.choice([None, "fake:fuzz-alt@high"])),
        "limits": limits, "offline_world": seed, "offline_fault_rate": fault_rate, "cache_mode": "off",
        # Both engines, so the paid search path, its budget reservations, and its costs are fuzzed too.
        "search_engine": rng.choice(["duckduckgo", "exa"]),
        # The reading fallback in several orders, off in some runs, so its money and results are fuzzed too.
        "read_fallback": rng.choice([(), ("oa", "exa", "firecrawl"), ("exa",), ("firecrawl", "exa")]),
        "tokens_per_minute": {}, "logfire": False,
    })


async def fuzz_one(seed: int, fault_rate: float = 0.2) -> list[FuzzFinding]:
    """One seeded run, then a rescout and a fixed-ledger synthesis of it, each checked."""
    from .scout import rescout_stored, scout, synthesize_stored
    from .store import MemoryStore
    from .study_budget import StudyBudget

    rng = random.Random(_stable("run", seed))
    settings = fuzz_settings(seed, fault_rate)
    store = MemoryStore()
    world = World(seed, fault_rate)
    budget = StudyBudget(Decimal(rng.choice(["0.02", "3.00", "3.00"]))) if rng.random() < 0.4 else None
    findings: list[FuzzFinding] = []

    def calls_of(run_id: UUID) -> list[dict[str, Any]]:
        return [call for call in store.calls.values() if call["run_id"] == run_id]

    try:
        run = await scout(rng.choice(["What materials databases are used?", "Q?", "ÄÖÜ 🧪 question"]),
                          settings=settings, store=store, follow_up=rng.random() < 0.4,
                          depth=rng.choice([None, None, "quick", "standard", "deep"]),
                          blocked_urls=[world.blocked] if rng.random() < 0.5 else [], budget=budget)
    except Exception:  # noqa: BLE001 - a crash is the finding
        return [FuzzFinding(seed, "scout crashed", [traceback.format_exc(limit=6)])]
    record = {**store.runs[run.run_id], **run.to_record()}
    if problems := check_record(record, calls_of(run.run_id)):
        findings.append(FuzzFinding(seed, "scout", problems))
    source = {**store.runs[run.run_id], "id": run.run_id}
    if source.get("plan"):
        try:
            again = await rescout_stored(source, settings=settings, store=store)
            if problems := check_record({**store.runs[again.run_id], **again.to_record()}, calls_of(again.run_id)):
                findings.append(FuzzFinding(seed, "rescout", problems))
        except Exception:  # noqa: BLE001
            findings.append(FuzzFinding(seed, "rescout crashed", [traceback.format_exc(limit=6)]))
    if run.ledger.claims():
        try:
            written = await synthesize_stored(source, settings=settings, store=store)
            if problems := check_record({**store.runs[written.run_id], **written.to_record()}, calls_of(written.run_id)):
                findings.append(FuzzFinding(seed, "synthesize", problems))
        except Exception:  # noqa: BLE001
            findings.append(FuzzFinding(seed, "synthesize crashed", [traceback.format_exc(limit=6)]))
    return findings


async def fuzz(runs: int, seed: int = 0, fault_rate: float = 0.2) -> list[FuzzFinding]:
    findings: list[FuzzFinding] = []
    for number in range(runs):
        findings += await fuzz_one(seed * 100_000 + number, fault_rate)
    return findings


def reproduce_command(finding: FuzzFinding, fault_rate: float) -> str:
    return f"research fuzz --one {finding.seed} --fault-rate {fault_rate}"


def fuzz_report(findings: list[FuzzFinding], runs: int, fault_rate: float) -> str:
    """Findings grouped by what broke, each with the seeds that reproduce it."""
    if not findings:
        return f"{runs} fuzz runs: every invariant held."
    groups: dict[str, list[int]] = {}
    for finding in findings:
        for problem in finding.problems:
            key = f"{finding.kind}: " + (problem.strip().splitlines()[-1] if "Traceback" in problem else problem)
            groups.setdefault(key[:300], []).append(finding.seed)
    lines = [f"{runs} fuzz runs: {len(groups)} distinct problems."]
    for key, seeds in sorted(groups.items(), key=lambda item: -len(item[1])):
        lines.append(f"- {len(seeds)}x {key}\n  reproduce: {reproduce_command(FuzzFinding(seeds[0], '', []), fault_rate)}")
    return "\n".join(lines)

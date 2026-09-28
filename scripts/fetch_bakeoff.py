"""Try other ways to read the pages our fetcher failed on in production runs, without a model call.

    .venv/bin/python scripts/fetch_bakeoff.py [--limit N] [--candidates own,oa,jina,exa,tavily,firecrawl]

It collects every distinct URL a production scout could not read (leaving out 404s, which are mostly
guessed addresses, and unsafe URLs), with the title the tools returned for it, its DOI or arXiv ID, and
any quote a scout cited from it. Each candidate then tries to read each URL:

- own: our fetcher again, as it is now (the 25 MB PDF cap came after some of these failures)
- oa: the paper's open-access copy, found through OpenAlex and Europe PMC, read with our fetcher
- jina: Jina Reader without a key; exa: Exa /contents; tavily: Tavily Extract (basic);
  firecrawl: Firecrawl scrape with basic proxies only. Services without a key in the environment
  (EXA_API_KEY, TAVILY_API_KEY, FIRECRAWL_API_KEY) are skipped.

A read counts when it returned at least 1,500 characters, has no challenge-page markers, and holds most
of the source's title. A cited quote counts as recovered when it verifies in the returned text by the
evidence check's rules. Results go to benchmark_outputs/fetch-bakeoff/.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import statistics
import sys
import time
from collections import Counter, defaultdict
from contextlib import AsyncExitStack
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import quote, urlparse

import httpx
from pydantic_ai.messages import ModelMessagesTypeAdapter

from research_loop.config import Settings
from research_loop.db import open_migrated_pool
from research_loop.evidence import _key, _segments, identity_keys
from research_loop.schemas import ResearchResult
from research_loop.tools import source_records
from research_loop.web import WebAcquisition

OUT = Path("benchmark_outputs/fetch-bakeoff")
MIN_CHARS = 1_500
CHALLENGE = re.compile(r"access denied|just a moment|captcha|enable javascript|are you a robot|"
                       r"verify you are human|request unsuccessful|cloudflare", re.IGNORECASE)
_DOI = re.compile(r"10\.\d{4,9}/[^\s\"<>?#]+")
_ARXIV = re.compile(r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})")
_TAGS = re.compile(r"<[^>]+>")
# RSC article IDs, such as d3mh00039g, are their DOI suffixes under 10.1039.
_RSC = re.compile(r"pubs\.rsc\.org/.*?/([a-z]\d[a-z]{2}\d{5}[a-z])", re.IGNORECASE)
# A site name a search result appends to a title, such as " - MDPI" or " | ScienceDirect".
_SITE_SUFFIX = re.compile(r"\s+[-|–]\s+[^-|–]{2,40}$")


def clean_title(title: str) -> str:
    return _SITE_SUFFIX.sub("", title).strip()


def same_title(ours: str, theirs: str) -> bool:
    a, b = _key(clean_title(ours)), _key(theirs)
    return bool(a) and bool(b) and (a == b or (min(len(a), len(b)) >= 20 and (a in b or b in a)))


@dataclass
class Target:
    url: str
    reason: str
    title: str = ""
    doi: str | None = None
    arxiv_id: str | None = None
    quotes: list[str] = field(default_factory=list)


@dataclass
class Attempt:
    candidate: str
    url: str
    ok: bool
    chars: int
    seconds: float
    quotes: int
    quotes_verified: int
    note: str = ""
    via: str = ""


# -- collecting the failures


async def failures(settings: Settings) -> list[Target]:
    targets: dict[str, Target] = {}
    async with AsyncExitStack() as stack:
        pool = await open_migrated_pool(stack, settings.database_dsn)
        async with pool.connection() as conn:
            cursor = await conn.execute(
                "select c.messages, c.output from run_calls c join runs r on r.id = c.run_id "
                "where c.role in ('scout', 'deep_dive') and c.messages is not null "
                "and r.config->'models'->'scout'->>'thinking' = 'high' and r.started_at > '2026-09-25 19:24+00'")
            rows = await cursor.fetchall()
    for messages, output in rows:
        parsed = ModelMessagesTypeAdapter.validate_python(messages)
        records = source_records(parsed)
        cited = ResearchResult.model_validate(output) if output else None
        for message in messages:
            for part in message.get("parts") or []:
                content = part.get("content")
                if part.get("tool_name") != "fetch" or part.get("part_kind") != "tool-return" \
                        or not isinstance(content, dict) or content.get("text") or not content.get("url"):
                    continue
                if content.get("status") == 404 or content.get("error") in ("UnsafeURL", "BlockedSource", "CacheMiss"):
                    continue
                url = content["url"]
                target = targets.setdefault(url, Target(url, content.get("error", "") + (
                    f" {content['status']}" if content.get("status") else "") + (
                    f": {content['detail']}" if content.get("detail") else "")))
                keys = identity_keys(url=url)
                for record_keys, record in records:
                    if keys & record_keys:
                        target.title = target.title or record.get("title") or ""
                        target.doi = target.doi or record.get("doi")
                        target.arxiv_id = target.arxiv_id or record.get("arxiv_id")
                if match := _DOI.search(url):
                    target.doi = target.doi or match.group(0).rstrip(".")
                if match := _ARXIV.search(url):
                    target.arxiv_id = target.arxiv_id or match.group(1)
                if match := _RSC.search(url):
                    target.doi = target.doi or f"10.1039/{match.group(1).lower()}"
                for claim in cited.claims if cited else []:
                    for item in claim.evidence:
                        if item.quote and item.source.url and keys & identity_keys(url=str(item.source.url)) \
                                and item.quote not in target.quotes:
                            target.quotes.append(item.quote)
    return list(targets.values())


# -- judging a read


def judge(target: Target, candidate: str, text: str, seconds: float, note: str = "", via: str = "") -> Attempt:
    key = _key(text)
    title_words = [w for w in re.findall(r"[A-Za-z0-9]{4,}", target.title)]
    title_ok = not title_words or sum(_key(w) in key for w in title_words) >= 0.6 * len(title_words)
    head = text[:3_000]
    challenge = bool(CHALLENGE.search(head)) and len(text) < 20_000
    ok = len(text) >= MIN_CHARS and not challenge and title_ok
    verified = sum(all(segment in key for segment in _segments(q)) for q in target.quotes) if text else 0
    if not ok and text:
        note = note or ("challenge page" if challenge else "short" if len(text) < MIN_CHARS else "title missing")
    return Attempt(candidate, target.url, ok, len(text), round(seconds, 2), len(target.quotes), verified, note, via)


# -- candidates


async def own(target: Target, pages: WebAcquisition, url: str | None = None) -> tuple[str, str]:
    url = url or target.url
    result = await pages.fetch(url)
    if not result.get("text"):
        return "", result.get("error", "") + (f": {result['detail']}" if result.get("detail") else "")
    document = pages.memo.get("web", url)
    return (document or {}).get("text") or result["text"], ""


async def open_access(target: Target, pages: WebAcquisition, client: httpx.AsyncClient) -> tuple[str, str, str]:
    """The open copy: arXiv's PDF, Europe PMC's full text, or OpenAlex's best open-access location."""
    tried: list[str] = []
    if target.arxiv_id:
        text, note = await own(target, pages, f"https://arxiv.org/pdf/{target.arxiv_id}")
        if text:
            return text, "", "oa:arxiv"
        tried.append(f"arxiv {note}")
    doi = target.doi
    if not doi and target.title:
        found = await client.get("https://api.openalex.org/works",
                                 params={"search": clean_title(target.title), "per-page": 3})
        results = found.json().get("results") if found.status_code == 200 else None
        match = next((r for r in results or [] if same_title(target.title, r.get("display_name") or "")), None)
        if match:
            doi = (match.get("doi") or "").removeprefix("https://doi.org/") or None
    if not doi:
        return "", "no DOI or arXiv ID; " + "; ".join(tried), ""
    pmc = await client.get("https://www.ebi.ac.uk/europepmc/webservices/rest/search",
                           params={"query": f'DOI:"{doi}"', "format": "json", "resultType": "lite"})
    hits = (pmc.json().get("resultList") or {}).get("result") or [] if pmc.status_code == 200 else []
    if hits and hits[0].get("pmcid"):
        xml = await client.get(f"https://www.ebi.ac.uk/europepmc/webservices/rest/{hits[0]['pmcid']}/fullTextXML")
        if xml.status_code == 200 and len(xml.text) > MIN_CHARS:
            return " ".join(_TAGS.sub(" ", xml.text).split()), "", "oa:europepmc"
        tried.append(f"europepmc {xml.status_code}")
    work = await client.get(f"https://api.openalex.org/works/doi:{quote(doi, safe='/')}")
    if work.status_code == 200:
        data = work.json()
        location = data.get("best_oa_location") or {}
        for url in (location.get("pdf_url"), location.get("landing_page_url"), (data.get("open_access") or {}).get("oa_url")):
            if url and url.rstrip("/") != target.url.rstrip("/"):
                text, note = await own(target, pages, url)
                if text:
                    return text, "", f"oa:openalex {urlparse(url).hostname}"
                tried.append(f"openalex {urlparse(url).hostname} {note}")
    else:
        tried.append(f"openalex {work.status_code}")
    return "", "; ".join(tried) or "no open copy", ""


async def jina(target: Target, client: httpx.AsyncClient) -> tuple[str, str]:
    response = await client.get(f"https://r.jina.ai/{target.url}", headers={"Accept": "text/plain"}, timeout=60)
    return (response.text, "") if response.status_code == 200 else ("", f"HTTP {response.status_code}")


async def exa(target: Target, client: httpx.AsyncClient, spent: Counter) -> tuple[str, str]:
    response = await client.post("https://api.exa.ai/contents", headers={"x-api-key": os.environ["EXA_API_KEY"]},
                                 json={"urls": [target.url], "text": True}, timeout=60)
    if response.status_code != 200:
        return "", f"HTTP {response.status_code}"
    data = response.json()
    spent["exa_usd"] += (data.get("costDollars") or {}).get("total", 0.001)
    results = data.get("results") or []
    status = next((s for s in data.get("statuses") or []), {})
    return ((results[0].get("text") or ""), "") if results else ("", str(status.get("error") or status.get("status")))


async def tavily(target: Target, client: httpx.AsyncClient, spent: Counter) -> tuple[str, str]:
    response = await client.post("https://api.tavily.com/extract", timeout=60,
                                 headers={"Authorization": f"Bearer {os.environ['TAVILY_API_KEY']}"},
                                 json={"urls": [target.url], "extract_depth": "basic", "format": "text",
                                       "include_usage": True})
    if response.status_code != 200:
        return "", f"HTTP {response.status_code}"
    data = response.json()
    spent["tavily_credits"] += (data.get("usage") or {}).get("credits", 0)
    results = data.get("results") or []
    failed = data.get("failed_results") or []
    return ((results[0].get("raw_content") or ""), "") if results else ("", str(failed[0] if failed else "no result"))


async def firecrawl(target: Target, client: httpx.AsyncClient, spent: Counter) -> tuple[str, str]:
    # "basic" only: the default "auto" escalates to enhanced (stealth) proxies on a 401, 403, or 429.
    response = await client.post("https://api.firecrawl.dev/v2/scrape", timeout=90,
                                 headers={"Authorization": f"Bearer {os.environ['FIRECRAWL_API_KEY']}"},
                                 json={"url": target.url, "formats": ["markdown"], "proxy": "basic",
                                       "timeout": 60_000})
    if response.status_code != 200:
        return "", f"HTTP {response.status_code}"
    data = response.json().get("data") or {}
    spent["firecrawl_credits"] += 1
    status = (data.get("metadata") or {}).get("statusCode")
    return (data.get("markdown") or ""), (f"site status {status}" if status and status >= 400 else "")


# -- running


async def run(candidates: list[str], limit: int | None) -> int:
    settings = Settings()
    if not settings.database_dsn:
        print("Set DATABASE_URL.", file=sys.stderr)
        return 2
    targets = (await failures(settings))[:limit]
    available = {"own", "oa", "jina"} | {name for name, env in (("exa", "EXA_API_KEY"), ("tavily", "TAVILY_API_KEY"),
                                                                 ("firecrawl", "FIRECRAWL_API_KEY")) if os.environ.get(env)}
    skipped = [c for c in candidates if c not in available]
    candidates = [c for c in candidates if c in available]
    print(f"{len(targets)} failed URLs; candidates {', '.join(candidates)}"
          + (f"; skipped without a key: {', '.join(skipped)}" if skipped else ""), file=sys.stderr)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "targets.jsonl").write_text("".join(json.dumps(asdict(t)) + "\n" for t in targets))
    attempts: list[Attempt] = []
    spent: Counter = Counter()
    headers = {"User-Agent": "research-loop/0.5 (fetch bake-off)"}
    async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=30) as client:
        pages = WebAcquisition(cache_root=OUT / "cache", cache_mode="off")
        for index, target in enumerate(targets, 1):
            for candidate in candidates:
                started = time.monotonic()
                via = ""
                try:
                    if candidate == "own":
                        text, note = await own(target, pages)
                    elif candidate == "oa":
                        text, note, via = await open_access(target, pages, client)
                    elif candidate == "jina":
                        text, note = await jina(target, client)
                        await asyncio.sleep(3.1)  # about 20 a minute without a key
                    elif candidate == "exa":
                        text, note = await exa(target, client, spent)
                    elif candidate == "tavily":
                        text, note = await tavily(target, client, spent)
                    else:
                        text, note = await firecrawl(target, client, spent)
                except (httpx.HTTPError, ValueError, KeyError) as exc:
                    text, note = "", type(exc).__name__
                attempts.append(judge(target, candidate, text, time.monotonic() - started, note, via))
            print(f"{index}/{len(targets)} {target.url[:90]}", file=sys.stderr)
    # Each candidate's attempts are kept apart, so one can be run again; the summary reads them all.
    for candidate in candidates:
        with (OUT / f"attempts-{candidate}.jsonl").open("w") as out:
            out.writelines(json.dumps(asdict(a)) + "\n" for a in attempts if a.candidate == candidate)
    attempts = [Attempt(**json.loads(line)) for path in sorted(OUT.glob("attempts-*.jsonl"))
                for line in path.read_text().splitlines() if line]
    candidates = list(dict.fromkeys(a.candidate for a in attempts))
    spent_path = OUT / "spent.json"
    spent = Counter(json.loads(spent_path.read_text()) if spent_path.exists() else {}) + spent
    spent_path.write_text(json.dumps(spent))
    summary = summarize(targets, attempts, candidates, spent)
    (OUT / "summary.md").write_text(summary)
    print(summary)
    return 0


def summarize(targets: list[Target], attempts: list[Attempt], candidates: list[str], spent: Counter) -> str:
    by = defaultdict(list)
    for attempt in attempts:
        by[attempt.candidate].append(attempt)
    host = {t.url: (urlparse(t.url).hostname or "").removeprefix("www.") for t in targets}
    quoted = sum(bool(t.quotes) for t in targets)
    lines = [f"# Fetch bake-off: {len(targets)} URLs our fetcher failed on ({quoted} with cited quotes)", "",
             "| Candidate | Read | Quotes recovered | Median seconds | Cost |", "|---|---|---|---|---|"]
    cost = {"exa": f"${spent['exa_usd']:.3f}", "tavily": f"{spent['tavily_credits']} credits",
            "firecrawl": f"{spent['firecrawl_credits']} credits"}
    for candidate in candidates:
        rows = by[candidate]
        read = sum(a.ok for a in rows)
        recovered = sum(a.quotes_verified for a in rows)
        total_quotes = sum(a.quotes for a in rows)
        lines.append(f"| {candidate} | {read}/{len(rows)} ({read / max(len(rows), 1):.0%}) | "
                     f"{recovered}/{total_quotes} | {statistics.median(a.seconds for a in rows) if rows else 0:.1f} | "
                     f"{cost.get(candidate, 'free')} |")
    union = {a.url for a in attempts if a.ok}
    lines += ["", f"Read by at least one candidate: {len(union)}/{len(targets)}.", "",
              "## By host (read / tried)", "", "| Host | " + " | ".join(candidates) + " |",
              "|---|" + "---|" * len(candidates)]
    hosts = Counter(host.values())
    for name, count in hosts.most_common(12):
        cells = [f"{sum(a.ok for a in by[c] if host[a.url] == name)}/{count}" for c in candidates]
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--limit", type=int, help="Try only the first N failed URLs")
    parser.add_argument("--candidates", default="own,oa,jina,exa,tavily,firecrawl")
    args = parser.parse_args()
    sys.exit(asyncio.run(run(args.candidates.split(","), args.limit)))


if __name__ == "__main__":
    main()

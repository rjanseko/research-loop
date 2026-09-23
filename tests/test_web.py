from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from research_loop.web import WebAcquisition, build_web_toolset


@pytest.mark.asyncio
async def test_web_fetch_extracts_and_caches_main_text(monkeypatch, tmp_path) -> None:
    async def safe(_: str) -> bool:
        return True
    monkeypatch.setattr("research_loop.web._public_url", safe)
    calls = 0
    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, headers={"content-type": "text/html"},
                              text="<html><body><nav>MENU</nav><article><h1>Research result</h1><p>Primary evidence is here.</p></article></body></html>")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        fetcher = WebAcquisition(cache_root=tmp_path, client=http)
        result = await fetcher.fetch("https://example.org/paper")
        again = await fetcher.fetch("https://example.org/paper")
    assert "Primary evidence" in result["text"]
    assert "MENU" not in result["text"]
    assert again["cache_hit"] is True
    assert calls == 1
    assert build_web_toolset(fetcher) is not None


@pytest.mark.asyncio
async def test_web_fetch_rejects_local_url(tmp_path) -> None:
    result = await WebAcquisition(cache_root=tmp_path).fetch("https://127.0.0.1/secret")
    assert result["error"] == "UnsafeURL"


@pytest.mark.asyncio
async def test_web_replay_skips_dns_check(monkeypatch, tmp_path) -> None:
    from research_loop.scholar import AcquisitionCache

    AcquisitionCache(tmp_path, "record").put("web", "https://example.org/paper|max_chars=12000",
                                             {"url": "https://example.org/paper", "text": "cached"})

    async def offline(_: str) -> bool:
        raise AssertionError("replay must not resolve DNS")

    monkeypatch.setattr("research_loop.web._public_url", offline)
    fetcher = WebAcquisition(cache_root=tmp_path, cache_mode="replay")
    assert (await fetcher.fetch("https://example.org/paper"))["text"] == "cached"
    assert (await fetcher.fetch("https://example.org/other"))["error"] == "CacheMiss"


@pytest.mark.asyncio
async def test_duckduckgo_failures_become_tool_results(monkeypatch) -> None:
    from research_loop.web import resilient_duckduckgo_tool

    calls: list[str] = []

    class FlakySearch:
        name = "duckduckgo_search"
        description = "Searches DuckDuckGo for the given query and returns the results."

        def __init__(self, failures: int):
            self.failures = failures

        async def function(self, query: str):
            calls.append(query)
            if len(calls) <= self.failures:
                raise RuntimeError("connection dropped")
            return [{"title": "SWE-bench", "href": "https://www.swebench.com", "body": "Leaderboard"}]

    async def no_wait(_provider: str) -> None:
        return None

    monkeypatch.setattr("research_loop.web._wait_rate_slot", no_wait)
    monkeypatch.setattr("pydantic_ai.common_tools.duckduckgo.duckduckgo_search_tool", lambda: FlakySearch(1))
    tool = resilient_duckduckgo_tool(retry_delay=0)
    assert tool.name == "duckduckgo_search"
    assert (await tool.function(query="SWE-bench"))[0]["title"] == "SWE-bench"

    calls.clear()
    monkeypatch.setattr("pydantic_ai.common_tools.duckduckgo.duckduckgo_search_tool", lambda: FlakySearch(2))
    result = await resilient_duckduckgo_tool(retry_delay=0).function(query="SWE-bench")
    assert result["error"] == "SearchUnavailable (RuntimeError)"
    assert len(calls) == 2


def test_research_capabilities_use_resilient_search() -> None:
    from research_loop.tools import ResearchToolMode, build_research_capabilities

    for mode in ResearchToolMode:
        search = build_research_capabilities(mode)[0]
        assert search.local.name == "duckduckgo_search"
        assert "resilient_duckduckgo_tool" in search.local.function.__qualname__


@pytest.mark.asyncio
async def test_compressed_pages_are_decoded_once(monkeypatch) -> None:
    import gzip

    from research_loop.scholar import _bounded_public_get

    async def safe(_: str) -> bool:
        return True

    monkeypatch.setattr("research_loop.scholar._public_url", safe)
    page = b"<html><body><article><p>Compressed evidence page.</p></article></body></html>"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["user-agent"].startswith("research-loop/")
        return httpx.Response(200, headers={"content-type": "text/html", "content-encoding": "gzip"},
                              content=gzip.compress(page))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        response = await _bounded_public_get(http, "https://example.org/page", 1_000_000)
        assert response.text == page.decode()
        assert "content-encoding" not in response.headers
        monkeypatch.setattr("research_loop.web._public_url", safe)
        result = await WebAcquisition(cache_root=Path("/nonexistent"), cache_mode="off", client=http).fetch("https://example.org/page")
    assert "Compressed evidence page." in result["text"]


@pytest.mark.asyncio
async def test_fetch_errors_tell_the_model_the_status(monkeypatch) -> None:
    async def safe(_: str) -> bool:
        return True

    monkeypatch.setattr("research_loop.scholar._public_url", safe)
    monkeypatch.setattr("research_loop.web._public_url", safe)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(403))) as http:
        result = await WebAcquisition(cache_root=Path("/nonexistent"), cache_mode="off", client=http).fetch("https://example.org/blocked")
    assert result["error"] == "HTTPStatusError"
    assert result["status"] == 403


def _long_page_handler(counter: list[int]):
    paragraphs = "".join(f"<p>Paragraph {index:04d} of the evidence body.</p>" for index in range(400))

    def handler(request: httpx.Request) -> httpx.Response:
        counter.append(1)
        return httpx.Response(200, headers={"content-type": "text/html"},
                              text=f"<html><body><article><h1>Long report</h1>{paragraphs}</article></body></html>")

    return handler


@pytest.mark.asyncio
async def test_web_fetch_pages_through_a_long_document_with_one_download(monkeypatch, tmp_path) -> None:
    async def safe(_: str) -> bool:
        return True
    monkeypatch.setattr("research_loop.web._public_url", safe)
    calls: list[int] = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(_long_page_handler(calls))) as http:
        fetcher = WebAcquisition(cache_root=tmp_path, cache_mode="off", client=http)
        first = await fetcher.fetch("https://example.org/report", max_chars=1000)
        second = await fetcher.fetch("https://example.org/report", max_chars=1000, start=first["next_start"])
        past_end = await fetcher.fetch("https://example.org/report", start=first["total_chars"])
    assert calls == [1]  # later pages come from the job's memo, even with the disk cache off
    assert first["start"] == 0 and first["truncated"] is True and first["next_start"] == 1000
    assert first["total_chars"] > 12_000  # longer than one full-size window
    assert second["start"] == 1000
    assert second["text"] and second["text"] not in first["text"]
    assert past_end["error"] == "StartBeyondEnd"
    assert past_end["total_chars"] == first["total_chars"]


@pytest.mark.asyncio
async def test_agents_in_one_job_share_fetches(monkeypatch, tmp_path) -> None:
    from research_loop.scholar import FetchMemo

    async def safe(_: str) -> bool:
        return True
    monkeypatch.setattr("research_loop.web._public_url", safe)
    calls: list[int] = []
    memo = FetchMemo()
    async with httpx.AsyncClient(transport=httpx.MockTransport(_long_page_handler(calls))) as http:
        # Each agent run builds its own acquisition object; the job hands them one memo.
        for _ in range(2):
            fetcher = WebAcquisition(cache_root=tmp_path, cache_mode="record", client=http, memo=memo)
            result = await fetcher.fetch("https://example.org/report")
    assert calls == [1]
    assert result["text"].startswith("Long report")
    other_job = WebAcquisition(cache_root=tmp_path, cache_mode="off", client=None, memo=FetchMemo())
    monkeypatch.setattr("research_loop.web._bounded_public_get", _long_page_client(calls))
    await other_job.fetch("https://example.org/report")
    assert calls == [1, 1]  # another job's memo starts empty


def _long_page_client(counter: list[int]):
    handler = _long_page_handler(counter)

    async def bounded(_client, url, _limit):
        response = handler(httpx.Request("GET", url))
        response.request = httpx.Request("GET", url)
        return response

    return bounded

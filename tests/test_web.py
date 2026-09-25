from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from research_loop.acquisition import AcquisitionCache, FetchMemo
from research_loop.web import NO_RESULTS_HINT, WebAcquisition, build_web_toolset


@pytest.mark.asyncio
async def test_web_fetch_extracts_and_caches_main_text(public_urls, tmp_path) -> None:
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
async def test_web_replay_skips_dns_check(go_offline, tmp_path) -> None:
    AcquisitionCache(tmp_path, "record").put("web", "https://example.org/paper|max_chars=12000",
                                             {"url": "https://example.org/paper", "text": "cached"})
    go_offline()
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

    monkeypatch.setattr("research_loop.web.wait_rate_slot", no_wait)
    monkeypatch.setattr("pydantic_ai.common_tools.duckduckgo.duckduckgo_search_tool", lambda: FlakySearch(1))
    tool = resilient_duckduckgo_tool(retry_delays=(0, 0))
    assert tool.name == "duckduckgo_search"
    assert (await tool.function(query="SWE-bench")).return_value[0]["title"] == "SWE-bench"

    calls.clear()
    monkeypatch.setattr("pydantic_ai.common_tools.duckduckgo.duckduckgo_search_tool", lambda: FlakySearch(2))
    assert (await resilient_duckduckgo_tool(retry_delays=(0, 0)).function(query="SWE-bench")).return_value[0]
    assert len(calls) == 3  # the third attempt answered

    calls.clear()
    monkeypatch.setattr("pydantic_ai.common_tools.duckduckgo.duckduckgo_search_tool", lambda: FlakySearch(3))
    result = (await resilient_duckduckgo_tool(retry_delays=(0, 0)).function(query="SWE-bench")).return_value
    assert result["error"] == "SearchUnavailable (RuntimeError)" and "3 times" in result["hint"]
    assert len(calls) == 3

    class Empty(FlakySearch):
        async def function(self, query: str):
            calls.append(query)
            raise RuntimeError("No results found.")

    calls.clear()
    monkeypatch.setattr("pydantic_ai.common_tools.duckduckgo.duckduckgo_search_tool", lambda: Empty(0))
    result = (await resilient_duckduckgo_tool(retry_delays=(0, 0)).function(query='"too narrow"')).return_value
    assert result == {"results": [], "hint": NO_RESULTS_HINT} and len(calls) == 1  # no retry


def test_research_capabilities_use_resilient_search() -> None:
    from research_loop.tools import ResearchToolMode, build_research_capabilities

    for mode in ResearchToolMode:
        search = build_research_capabilities(mode)[0]
        assert search.local.name == "duckduckgo_search"
        assert "resilient_duckduckgo_tool" in search.local.function.__qualname__


@pytest.mark.asyncio
async def test_compressed_pages_are_decoded_once(public_urls) -> None:
    import gzip

    from research_loop.acquisition import bounded_public_get

    page = b"<html><body><article><p>Compressed evidence page.</p></article></body></html>"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["user-agent"].startswith("research-loop/")
        return httpx.Response(200, headers={"content-type": "text/html", "content-encoding": "gzip"},
                              content=gzip.compress(page))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        response = await bounded_public_get(http, "https://example.org/page", 1_000_000)
        assert response.text == page.decode()
        assert "content-encoding" not in response.headers
        result = await WebAcquisition(cache_root=Path("/nonexistent"), cache_mode="off", client=http).fetch("https://example.org/page")
    assert "Compressed evidence page." in result["text"]


@pytest.mark.asyncio
async def test_fetch_errors_tell_the_model_the_status(public_urls) -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(403))) as http:
        result = await WebAcquisition(cache_root=Path("/nonexistent"), cache_mode="off", client=http).fetch("https://example.org/blocked")
    assert result["error"] == "HTTPStatusError"
    assert result["status"] == 403


def _long_page(counter: list[int]) -> httpx.Response:
    counter.append(1)
    paragraphs = "".join(f"<p>Paragraph {index:04d} of the evidence body.</p>" for index in range(400))
    return httpx.Response(200, headers={"content-type": "text/html"},
                          text=f"<html><body><article><h1>Long report</h1>{paragraphs}</article></body></html>")


@pytest.mark.asyncio
async def test_web_fetch_pages_through_a_long_document_with_one_download(public_urls, tmp_path) -> None:
    calls: list[int] = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _request: _long_page(calls))) as http:
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
async def test_agents_in_one_job_share_fetches(public_urls, serve, tmp_path) -> None:
    calls: list[int] = []
    memo = FetchMemo()
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _request: _long_page(calls))) as http:
        # Each agent run builds its own acquisition object; the job hands them one memo.
        for _ in range(2):
            fetcher = WebAcquisition(cache_root=tmp_path, cache_mode="record", client=http, memo=memo)
            result = await fetcher.fetch("https://example.org/report")
    assert calls == [1]
    assert result["text"].startswith("Long report")
    serve(lambda _url: _long_page(calls))
    await WebAcquisition(cache_root=tmp_path, cache_mode="off", memo=FetchMemo()).fetch("https://example.org/report")
    assert calls == [1, 1]  # another job's memo starts empty


def _resolve_to(monkeypatch: pytest.MonkeyPatch, *answers: list[str]) -> list[str]:
    """Answer successive DNS lookups with `answers`, as a rebinding server would."""
    import socket

    lookups: list[str] = []

    def getaddrinfo(host, port, *args, **kwargs):
        lookups.append(host)
        addresses = answers[min(len(lookups), len(answers)) - 1]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port)) for address in addresses]

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    return lookups


@pytest.fixture
def no_proxy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.asyncio
async def test_fetch_client_refuses_to_connect_to_a_private_address(monkeypatch, no_proxy_env) -> None:
    from research_loop.acquisition import public_fetch_client

    # The URL check saw a public address; the connection's own lookup gets a private one.
    _resolve_to(monkeypatch, ["10.0.0.5"])
    async with public_fetch_client(timeout=5) as client:
        with pytest.raises(ValueError, match="unsafe URL"):
            await client.get("https://rebind.example/")


@pytest.mark.asyncio
async def test_fetch_backend_connects_to_the_address_it_checked(monkeypatch) -> None:
    import httpcore

    from research_loop.acquisition import _PublicOnlyBackend

    lookups = _resolve_to(monkeypatch, ["93.184.216.34", "93.184.216.35"], ["127.0.0.1"])
    connected: list[str] = []

    class Inner:
        async def connect_tcp(self, host, port, *_args):
            connected.append(host)
            if host == "93.184.216.34":
                raise httpcore.ConnectError("refused")
            return "stream"

    backend = _PublicOnlyBackend()
    backend._inner = Inner()
    assert await backend.connect_tcp("example.org", 443) == "stream"
    assert lookups == ["example.org"]  # one lookup, then the checked addresses in order
    assert connected == ["93.184.216.34", "93.184.216.35"]
    with pytest.raises(ValueError, match="unsafe URL"):
        await backend.connect_tcp("example.org", 443)  # the next answer is loopback
    assert connected == ["93.184.216.34", "93.184.216.35"]


def test_fetch_client_leaves_a_configured_proxy_as_the_egress_boundary(monkeypatch, no_proxy_env) -> None:
    from research_loop.acquisition import _PublicOnlyTransport, public_fetch_client

    assert isinstance(public_fetch_client(timeout=5)._transport, _PublicOnlyTransport)
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.internal:3128")
    assert not isinstance(public_fetch_client(timeout=5)._transport, _PublicOnlyTransport)


class _CountingSearch:
    name = "duckduckgo_search"
    description = "Searches DuckDuckGo for the given query and returns the results."

    def __init__(self, calls: list[str], fail: bool = False) -> None:
        self.calls, self.fail = calls, fail

    async def function(self, query: str):
        self.calls.append(query)
        if self.fail:
            raise RuntimeError("connection dropped")
        return [{"title": f"Result for {query}", "href": "https://example.org", "body": "..."}]


@pytest.fixture
def counted_search(monkeypatch):
    """Replace DuckDuckGo with a stub that records each live query."""
    calls: list[str] = []

    async def no_wait(_provider: str) -> None:
        return None

    monkeypatch.setattr("research_loop.web.wait_rate_slot", no_wait)

    def install(fail: bool = False) -> list[str]:
        monkeypatch.setattr("pydantic_ai.common_tools.duckduckgo.duckduckgo_search_tool",
                            lambda: _CountingSearch(calls, fail))
        return calls

    return install


def _recorded(tmp_path, query: str, results: list) -> None:
    from research_loop.web import search_cache_key

    AcquisitionCache(tmp_path, "record").put("duckduckgo", search_cache_key(query), {"query": query, "results": results})


@pytest.mark.asyncio
async def test_searches_are_recorded_then_replayed_unchanged(counted_search, tmp_path) -> None:
    from research_loop.web import resilient_duckduckgo_tool

    calls = counted_search()
    record = resilient_duckduckgo_tool(retry_delays=(0, 0), cache=AcquisitionCache(tmp_path, "record"))
    live = await record.function(query="SWE-bench Verified")
    await record.function(query="SWE-bench Verified")  # record mode never reads
    assert calls == ["SWE-bench Verified", "SWE-bench Verified"]
    assert live.metadata == {"cache_hit": False}

    replay = resilient_duckduckgo_tool(retry_delays=(0, 0), cache=AcquisitionCache(tmp_path, "replay"))
    served = await replay.function(query="SWE-bench Verified")
    assert served.return_value == live.return_value and served.metadata == {"cache_hit": True}
    missed = await replay.function(query="SWE-bench Lite")
    assert (missed.return_value, missed.metadata) == ({"error": "CacheMiss"}, {"cache_hit": False})
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_queries_differing_only_in_case_or_spacing_share_a_recording(counted_search, tmp_path) -> None:
    from research_loop.web import resilient_duckduckgo_tool, search_cache_key

    calls = counted_search()
    _recorded(tmp_path, "SWE-bench Verified", [{"title": "recorded"}])
    tool = resilient_duckduckgo_tool(retry_delays=(0, 0), cache=AcquisitionCache(tmp_path, "replay"))
    for query in ("swe-bench verified", "  SWE-bench\tVerified ", "ＳＷＥ-bench Verified"):  # full-width letters too
        assert (await tool.function(query=query)).return_value == [{"title": "recorded"}]
    # Operators, quotes, punctuation, and word order change results, so they change the key.
    keys = {search_cache_key(q) for q in ("SWE-bench Verified", '"SWE-bench Verified"',
                                          "SWE-bench Verified site:openai.com", "Verified SWE-bench")}
    assert len(keys) == 4
    assert calls == []


@pytest.mark.asyncio
async def test_a_recording_keeps_the_query_as_the_model_wrote_it(counted_search, tmp_path) -> None:
    from research_loop.web import resilient_duckduckgo_tool, search_cache_key

    counted_search()
    await resilient_duckduckgo_tool(retry_delays=(0, 0), cache=AcquisitionCache(tmp_path, "record")).function(query="SWE-bench  Lite")
    entry = AcquisitionCache(tmp_path, "replay").get("duckduckgo", search_cache_key("swe-bench lite"))
    assert entry["query"] == "SWE-bench  Lite"


@pytest.mark.asyncio
async def test_reuse_serves_recorded_searches_and_records_new_ones(counted_search, tmp_path) -> None:
    from research_loop.web import resilient_duckduckgo_tool

    calls = counted_search()
    _recorded(tmp_path, "old query", [{"title": "recorded"}])
    reuse = AcquisitionCache(tmp_path, "reuse", ttl_seconds=0)  # any age is served
    tool = resilient_duckduckgo_tool(retry_delays=(0, 0), cache=reuse)
    assert (await tool.function(query="old query")).return_value == [{"title": "recorded"}]
    await tool.function(query="new query")
    assert (await tool.function(query="new query")).metadata == {"cache_hit": True}
    assert calls == ["new query"]  # a miss went live once and was recorded


@pytest.mark.asyncio
async def test_failed_searches_are_not_recorded(counted_search, tmp_path) -> None:
    from research_loop.web import resilient_duckduckgo_tool, search_cache_key

    calls = counted_search(fail=True)
    tool = resilient_duckduckgo_tool(retry_delays=(0, 0), cache=AcquisitionCache(tmp_path, "reuse"))
    assert (await tool.function(query="q")).return_value["error"].startswith("SearchUnavailable")
    assert AcquisitionCache(tmp_path, "replay").get("duckduckgo", search_cache_key("q")) is None
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_the_model_sees_search_results_without_the_cache_flag(tmp_path) -> None:
    from pydantic_ai import Agent
    from pydantic_ai.messages import (
        ModelMessage,
        ModelResponse,
        TextPart,
        ToolCallPart,
        ToolReturnPart,
    )
    from pydantic_ai.models.function import AgentInfo, FunctionModel

    from research_loop.telemetry import extract_tool_events
    from research_loop.web import resilient_duckduckgo_tool

    _recorded(tmp_path, "q", [{"title": "recorded", "href": "https://example.org", "body": "..."}])
    seen: list[ToolReturnPart] = []

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        returns = [p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)]
        if not returns:
            return ModelResponse(parts=[ToolCallPart("duckduckgo_search", {"query": "Q"})])
        seen.extend(returns)
        return ModelResponse(parts=[TextPart("done")])

    agent = Agent(FunctionModel(model), tools=[resilient_duckduckgo_tool(cache=AcquisitionCache(tmp_path, "replay"))])
    result = await agent.run("search")
    [part] = seen
    assert part.content == [{"title": "recorded", "href": "https://example.org", "body": "..."}]
    assert "cache_hit" not in part.model_response_str()
    [event] = extract_tool_events(result.all_messages())
    assert event.cache_hit is True


@pytest.mark.asyncio
async def test_web_fetch_reuse_goes_live_on_a_miss_and_keeps_old_windows(public_urls, tmp_path) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, headers={"content-type": "text/html"},
                              text="<html><body><article><p>Fresh evidence page.</p></article></body></html>")

    AcquisitionCache(tmp_path, "record").put("web", "https://example.org/paper|max_chars=12000",
                                             {"url": "https://example.org/paper", "text": "recorded"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        fetcher = WebAcquisition(cache_root=tmp_path, cache_mode="reuse", client=http)
        fetcher.cache.ttl_seconds = 0  # reuse ignores age
        assert (await fetcher.fetch("https://example.org/paper"))["text"] == "recorded"
        assert "Fresh evidence" in (await fetcher.fetch("https://example.org/new"))["text"]
    assert calls == 1


def test_cache_modes_are_listed_the_same_everywhere() -> None:
    from typing import get_args

    from research_loop.acquisition import CacheMode
    from research_loop.long_horizon_spec import Execution
    from research_loop.settings import ResearchSettings

    modes = set(get_args(CacheMode))
    assert set(get_args(ResearchSettings.model_fields["scholarly_cache_mode"].annotation)) == modes
    assert set(get_args(Execution.model_fields["scholarly_cache_mode"].annotation)) == modes


@pytest.mark.asyncio
async def test_research_capabilities_pass_the_search_cache_to_every_mode(tmp_path) -> None:
    from research_loop.tools import ResearchToolMode, build_research_capabilities

    cache = AcquisitionCache(tmp_path, "replay")
    _recorded(tmp_path, "q", [{"title": "recorded"}])
    for mode in ResearchToolMode:
        search = build_research_capabilities(mode, search_cache=cache)[0]
        assert (await search.local.function(query="q")).return_value == [{"title": "recorded"}]


@pytest.mark.asyncio
async def test_a_403_is_tried_once_more_as_a_browser(serve, tmp_path) -> None:
    sent: list[bool] = []

    def respond(url: str, *, as_browser: bool) -> httpx.Response:
        sent.append(as_browser)
        if as_browser:
            return httpx.Response(200, headers={"content-type": "text/html"},
                                  text="<html><body><p>Pension rules in full.</p></body></html>")
        return httpx.Response(403)

    serve(respond)
    result = await WebAcquisition(cache_root=tmp_path).fetch("https://example.org/refuses-bots")
    assert "Pension rules" in result["text"] and sent == [False, True]

    sent.clear()
    serve(lambda url, *, as_browser: (sent.append(as_browser), httpx.Response(403))[1])
    refused = await WebAcquisition(cache_root=tmp_path).fetch("https://example.org/refuses-all")
    assert refused["status"] == 403 and sent == [False, True]


@pytest.mark.asyncio
async def test_a_404_carries_a_hint_to_search_instead_of_guessing(serve, tmp_path) -> None:
    serve(lambda url: httpx.Response(404))
    result = await WebAcquisition(cache_root=tmp_path).fetch("https://example.org/guessed/path.pdf")
    assert result["status"] == 404 and "search" in result["hint"]


@pytest.mark.asyncio
async def test_a_host_that_does_not_answer_twice_is_not_tried_again_in_the_job(serve, tmp_path) -> None:
    from research_loop.acquisition import FetchMemo

    attempts: list[str] = []

    def respond(url: str) -> httpx.Response:
        attempts.append(url)
        raise httpx.ConnectTimeout("timed out")

    serve(respond)
    memo = FetchMemo()
    web = WebAcquisition(cache_root=tmp_path, memo=memo)
    first = [await web.fetch(f"https://down.example.gov/page{i}") for i in range(3)]
    assert [r["error"] for r in first] == ["ConnectTimeout", "ConnectTimeout", "HostUnreachable"]
    assert len(attempts) == 2 and "another source" in first[2]["hint"]
    # Another job's memo starts afresh.
    assert (await WebAcquisition(cache_root=tmp_path, memo=FetchMemo()).fetch("https://down.example.gov/x"))["error"] == "ConnectTimeout"

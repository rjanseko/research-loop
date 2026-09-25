from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from research_loop.acquisition import AcquisitionCache, FetchMemo
from research_loop.web import (
    NO_RESULTS_HINT,
    WebAcquisition,
    WebSearch,
    search_cache_key,
)


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
    assert again == result
    assert calls == 1


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


def _engine(calls: list[str], failures: int = 0, message: str = "connection dropped"):
    """A stand-in for DuckDuckGo that fails its first `failures` calls."""
    async def search(query: str) -> list[dict[str, str]]:
        calls.append(query)
        if len(calls) <= failures:
            raise RuntimeError(message)
        return [{"title": f"Result for {query}", "href": "https://www.swebench.com", "body": "Leaderboard"}]
    return search


@pytest.mark.asyncio
async def test_search_failures_become_results_the_model_can_act_on() -> None:
    calls: list[str] = []
    result = await WebSearch(engine=_engine(calls, failures=1), retry_delays=(0, 0)).search("SWE-bench")
    assert result == {"results": [{"title": "Result for SWE-bench", "url": "https://www.swebench.com",
                                   "snippet": "Leaderboard"}]}

    calls.clear()
    assert (await WebSearch(engine=_engine(calls, failures=2), retry_delays=(0, 0)).search("SWE-bench"))["results"]
    assert len(calls) == 3  # the third attempt answered

    calls.clear()
    result = await WebSearch(engine=_engine(calls, failures=3), retry_delays=(0, 0)).search("SWE-bench")
    assert result["error"] == "SearchUnavailable (RuntimeError)" and "3 times" in result["hint"]
    assert len(calls) == 3

    calls.clear()
    empty = WebSearch(engine=_engine(calls, failures=1, message="No results found."), retry_delays=(0, 0))
    assert await empty.search('"too narrow"') == {"results": [], "hint": NO_RESULTS_HINT}
    assert len(calls) == 1  # no retry


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


def _recorded(tmp_path, query: str, results: list) -> None:
    AcquisitionCache(tmp_path, "record").put("duckduckgo", search_cache_key(query), {"query": query, "results": results})


def _search(tmp_path, mode: str, calls: list[str], *, failures: int = 0) -> WebSearch:
    return WebSearch(cache=AcquisitionCache(tmp_path, mode), engine=_engine(calls, failures), retry_delays=(0, 0))


@pytest.mark.asyncio
async def test_searches_are_recorded_then_replayed_unchanged(tmp_path) -> None:
    calls: list[str] = []
    record = _search(tmp_path, "record", calls)
    live = await record.search("SWE-bench Verified")
    await record.search("SWE-bench Verified")  # record mode never reads
    assert calls == ["SWE-bench Verified", "SWE-bench Verified"]
    replay = _search(tmp_path, "replay", calls)
    assert await replay.search("SWE-bench Verified") == live
    assert await replay.search("SWE-bench Lite") == {"error": "CacheMiss"}
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_queries_differing_only_in_case_or_spacing_share_a_recording(tmp_path) -> None:
    calls: list[str] = []
    _recorded(tmp_path, "SWE-bench Verified", [{"title": "recorded", "url": "https://a.example", "snippet": ""}])
    replay = _search(tmp_path, "replay", calls)
    for query in ("swe-bench verified", "  SWE-bench\tVerified ", "ＳＷＥ-bench Verified"):  # full-width letters too
        assert (await replay.search(query))["results"][0]["title"] == "recorded"
    # Operators, quotes, punctuation, and word order change results, so they change the key.
    keys = {search_cache_key(q) for q in ("SWE-bench Verified", '"SWE-bench Verified"',
                                          "SWE-bench Verified site:openai.com", "Verified SWE-bench")}
    assert len(keys) == 4
    assert calls == []


@pytest.mark.asyncio
async def test_a_recording_keeps_the_query_as_the_model_wrote_it(tmp_path) -> None:
    await _search(tmp_path, "record", []).search("SWE-bench  Lite")
    entry = AcquisitionCache(tmp_path, "replay").get("duckduckgo", search_cache_key("swe-bench lite"))
    assert entry["query"] == "SWE-bench  Lite"


@pytest.mark.asyncio
async def test_reuse_serves_recorded_searches_and_records_new_ones(tmp_path) -> None:
    calls: list[str] = []
    _recorded(tmp_path, "old query", [{"title": "recorded", "url": "https://a.example", "snippet": ""}])
    reuse = WebSearch(cache=AcquisitionCache(tmp_path, "reuse", ttl_seconds=0), engine=_engine(calls), retry_delays=(0, 0))
    assert (await reuse.search("old query"))["results"][0]["title"] == "recorded"
    first = await reuse.search("new query")
    assert await reuse.search("new query") == first
    assert calls == ["new query"]  # a miss went live once and was recorded


@pytest.mark.asyncio
async def test_failed_searches_are_not_recorded(tmp_path) -> None:
    calls: list[str] = []
    result = await _search(tmp_path, "reuse", calls, failures=3).search("q")
    assert result["error"].startswith("SearchUnavailable")
    assert AcquisitionCache(tmp_path, "replay").get("duckduckgo", search_cache_key("q")) is None
    assert len(calls) == 3


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
    from research_loop.config import Settings

    assert Settings.model_fields["cache_mode"].annotation == CacheMode
    assert set(get_args(CacheMode)) == {"off", "live", "record", "replay", "reuse"}


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


def _pdf(pages: int) -> bytes:
    import io

    from reportlab.pdfgen import canvas

    stream = io.BytesIO()
    pdf = canvas.Canvas(stream)
    for page in range(pages):
        for line in range(40):
            pdf.drawString(72, 760 - line * 18, f"Page {page + 1} line {line + 1} carries scholarly evidence text.")
        pdf.showPage()
    pdf.save()
    return stream.getvalue()


def _pdf_response(pages: int, media: str = "application/pdf"):
    body = _pdf(pages)
    return lambda _url: httpx.Response(200, headers={"content-type": media}, content=body)


@pytest.mark.asyncio
@pytest.mark.parametrize("media", ["application/pdf", "application/octet-stream", "binary/octet-stream"])
async def test_a_pdf_is_read_whatever_type_the_server_declares(serve, tmp_path, media) -> None:
    serve(_pdf_response(1, media))
    result = await WebAcquisition(cache_root=tmp_path, cache_mode="off").fetch("https://example.org/r.pdf")
    assert "Page 1 line 1 carries scholarly evidence text." in result["text"] and result["extraction"] == "pypdf"


@pytest.mark.asyncio
async def test_a_long_pdf_pages_through_one_download_and_says_it_goes_on(serve, tmp_path) -> None:
    downloads: list[str] = []
    respond = _pdf_response(31)
    serve(lambda url: (downloads.append(url), respond(url))[1])
    fetcher = WebAcquisition(cache_root=tmp_path, cache_mode="off", memo=FetchMemo())
    first = await fetcher.fetch("https://arxiv.org/pdf/2601.01234")
    last = await fetcher.fetch("https://arxiv.org/pdf/2601.01234", max_chars=1000, start=first["total_chars"] - 500)
    assert downloads == ["https://arxiv.org/pdf/2601.01234"]
    assert first["truncated"] is True and "Page 1 line 1" in first["text"]
    assert last["next_start"] is None and "Page 30" in last["text"]
    # pypdf reads 30 pages; the extraction says the document goes on past them.
    assert first["extraction"] == "pypdf-first-pages"


@pytest.mark.asyncio
async def test_recorded_pdf_windows_replay_without_the_network(serve, go_offline, tmp_path) -> None:
    serve(_pdf_response(3))
    page_two = await WebAcquisition(cache_root=tmp_path, cache_mode="record").fetch(
        "https://arxiv.org/pdf/2601.01234", max_chars=1000, start=1000)
    go_offline()
    replayed = await WebAcquisition(cache_root=tmp_path, cache_mode="replay").fetch(
        "https://arxiv.org/pdf/2601.01234", max_chars=1000, start=1000)
    assert replayed == page_two and replayed["start"] == 1000


@pytest.mark.asyncio
async def test_other_binary_content_is_refused(serve, tmp_path) -> None:
    serve(lambda _url: httpx.Response(200, headers={"content-type": "application/zip"}, content=b"PK\x03\x04"))
    result = await WebAcquisition(cache_root=tmp_path, cache_mode="off").fetch("https://example.org/a.zip")
    assert result["error"] == "ValueError" and result["detail"] == "unsupported content type"


@pytest.mark.asyncio
async def test_document_parsing_leaves_the_event_loop_free(public_urls, monkeypatch, tmp_path) -> None:
    import asyncio
    import time

    def slow_pdf(_content: bytes) -> tuple[str, bool]:
        time.sleep(0.3)  # a long paper
        return "Parsed paper text.", False

    monkeypatch.setattr("research_loop.web._pdf_text", slow_pdf)
    ticks = 0

    async def tick() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    ticker = asyncio.create_task(tick())
    async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"%PDF-1.4"))) as http:
        result = await WebAcquisition(cache_root=tmp_path, cache_mode="off", client=http).fetch("https://example.org/p.pdf")
    ticker.cancel()
    assert result["text"] == "Parsed paper text."
    assert ticks >= 10  # other tasks kept running while the paper was parsed

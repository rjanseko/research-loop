from __future__ import annotations

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

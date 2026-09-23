from __future__ import annotations

import httpx
import pytest

from research_loop.scholar import AcquisitionCache, ScholarClient, _arxiv_works, build_scholar_toolset
from research_loop.telemetry import safe_tool_args, safe_tool_result


ARXIV_XML = '''<feed xmlns="http://www.w3.org/2005/Atom"><entry>
<id>http://arxiv.org/abs/2601.01234v2</id><title> Long Horizon Agents </title>
<published>2026-01-03T00:00:00Z</published><summary>Preprint summary</summary>
<author><name>A. Author</name></author><link title="pdf" href="https://arxiv.org/pdf/2601.01234v2"/>
</entry></feed>'''


def test_arxiv_versions_are_explicit_preprints() -> None:
    work = _arxiv_works(ARXIV_XML)[0]
    assert work.arxiv_id == "2601.01234v2"
    assert work.publication_status == "preprint"
    assert work.full_text_url.endswith("v2")


@pytest.mark.asyncio
async def test_scholarly_adapters_keep_provider_records_separate(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.openalex.org":
            return httpx.Response(200, json={"meta": {"count": 1}, "results": [{
                "id": "https://openalex.org/W123", "display_name": "Long Horizon Agents",
                "ids": {"doi": "https://doi.org/10.1234/paper"},
                "primary_location": {"version": "publishedVersion", "source": {"type": "journal"}},
            }]})
        if request.url.host == "export.arxiv.org":
            return httpx.Response(200, text=ARXIV_XML)
        if request.url.host == "api.crossref.org":
            return httpx.Response(200, json={"message": {"DOI": "10.1234/paper", "title": ["Long Horizon Agents"], "type": "journal-article"}})
        raise AssertionError(str(request.url))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = ScholarClient(cache=AcquisitionCache(tmp_path, "off"), client=http)
        result = await client.search("long horizon agents")
        assert [(w.provider, w.publication_status) for w in result.works] == [
            ("openalex", "journal"), ("arxiv", "preprint")
        ]
        doi = await client.get("10.1234/paper")
        assert doi.works[0].doi == "10.1234/paper"
        assert doi.works[0].provider == "crossref"
        assert build_scholar_toolset(client) is not None


def test_replay_cache_does_not_call_network(tmp_path) -> None:
    cache = AcquisitionCache(tmp_path, "record")
    cache.put("crossref", "k", {"value": 1})
    assert AcquisitionCache(tmp_path, "replay").get("crossref", "k") == {"value": 1}
    assert AcquisitionCache(tmp_path, "off").get("crossref", "k") is None


def test_scholar_telemetry_omits_full_text_and_query() -> None:
    secret = "PRIVATE_BENCHMARK_INPUT"
    body = {"operation": "fetch", "text": secret * 300, "works": [], "content_sha256": "abc"}
    summary = safe_tool_result("scholar_fetch", body)
    assert secret not in str(summary)
    args = safe_tool_args({"query": secret, "limit": 5})
    assert secret not in str(args)
    assert args["limit"] == 5


@pytest.mark.asyncio
async def test_openreview_is_explicitly_disabled_without_credentials(tmp_path) -> None:
    client = ScholarClient(cache=AcquisitionCache(tmp_path, "off"))
    result = await client.get("openreview:some-forum-id")
    assert result.works == []
    assert result.provider_errors == ["openreview:Disabled"]


@pytest.mark.asyncio
async def test_grobid_unavailable_falls_back_to_pdf_text(monkeypatch, tmp_path) -> None:
    import io
    from reportlab.pdfgen import canvas

    stream = io.BytesIO()
    pdf = canvas.Canvas(stream)
    pdf.drawString(72, 720, "Fallback PDF evidence")
    pdf.save()

    async def safe(_: str) -> bool:
        return True

    async def bounded(_client, url, _limit):
        request = httpx.Request("GET", url)
        return httpx.Response(200, headers={"content-type": "application/pdf"}, content=stream.getvalue(), request=request)

    monkeypatch.setattr("research_loop.scholar._public_url", safe)
    monkeypatch.setattr("research_loop.scholar._bounded_public_get", bounded)
    client = ScholarClient(cache=AcquisitionCache(tmp_path, "off"), grobid_url="http://127.0.0.1:1")
    result = await client.fetch("https://arxiv.org/pdf/2601.01234")
    assert result.extraction_method == "pypdf"
    assert "Fallback PDF evidence" in result.text


@pytest.mark.asyncio
async def test_fetch_records_then_replays_without_network(monkeypatch, tmp_path) -> None:
    async def safe(_: str) -> bool:
        return True

    async def bounded(_client, url, _limit):
        request = httpx.Request("GET", url)
        return httpx.Response(200, headers={"content-type": "text/html"}, request=request,
                              text="<html><body><article><p>Recorded full text evidence.</p></article></body></html>")

    monkeypatch.setattr("research_loop.scholar._public_url", safe)
    monkeypatch.setattr("research_loop.scholar._bounded_public_get", bounded)
    recorded = await ScholarClient(cache=AcquisitionCache(tmp_path, "record")).fetch("https://example.org/paper")
    assert "Recorded full text" in recorded.text

    async def offline(*_args, **_kwargs):
        raise AssertionError("replay must not touch DNS or network")

    monkeypatch.setattr("research_loop.scholar._public_url", offline)
    monkeypatch.setattr("research_loop.scholar._bounded_public_get", offline)
    replay = ScholarClient(cache=AcquisitionCache(tmp_path, "replay"))
    replayed = await replay.fetch("https://example.org/paper")
    assert replayed.text == recorded.text
    assert replayed.cache_hits == 1
    missing = await replay.fetch("https://example.org/other")
    assert missing.provider_errors == ["fetch:CacheMiss"]

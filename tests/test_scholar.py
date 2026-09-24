from __future__ import annotations

import httpx
import pytest

from research_loop.acquisition import AcquisitionCache, FetchMemo
from research_loop.scholar import ScholarClient, _arxiv_works, build_scholar_toolset

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


@pytest.mark.asyncio
async def test_year_bounds_filter_every_search_provider(tmp_path) -> None:
    seen: dict[str, httpx.QueryParams] = {}
    arxiv_queries: list[str] = []
    old_preprint = ARXIV_XML.replace("2601.01234", "2101.00001").replace("2026-01-03", "2021-01-03")

    def handler(request: httpx.Request) -> httpx.Response:
        seen[request.url.host] = request.url.params
        if request.url.host == "export.arxiv.org":
            arxiv_queries.append(request.url.params["search_query"])
        if request.url.host == "api.openalex.org":
            return httpx.Response(200, json={"meta": {"count": 0}, "results": []})
        if request.url.host == "export.arxiv.org":
            # The feed ignores the date range, so the returned dates are checked too.
            return httpx.Response(200, text=ARXIV_XML.replace("</feed>", old_preprint.split("<feed", 1)[1].split(">", 1)[1]))
        return httpx.Response(200, json={"message": {"items": []}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = ScholarClient(cache=AcquisitionCache(tmp_path, "off"), client=http)
        result = await client.search("agents", year_from=2024, include_crossref=True)
        await client.search("agents", year_from=2020, year_to=2022, include_crossref=True)

    assert [w.arxiv_id for w in result.works] == ["2601.01234v2"]
    assert seen["api.openalex.org"]["filter"] == "from_publication_date:2020-01-01,to_publication_date:2022-12-31"
    assert arxiv_queries == [
        "all:agents AND submittedDate:[202401010000 TO 999912312359]",
        "all:agents AND submittedDate:[202001010000 TO 202212312359]",
    ]
    assert seen["api.crossref.org"]["filter"] == "from-pub-date:2020-01-01,until-pub-date:2022-12-31"

def test_replay_cache_does_not_call_network(tmp_path) -> None:
    cache = AcquisitionCache(tmp_path, "record")
    cache.put("crossref", "k", {"value": 1})
    assert AcquisitionCache(tmp_path, "replay").get("crossref", "k") == {"value": 1}
    assert AcquisitionCache(tmp_path, "off").get("crossref", "k") is None


@pytest.mark.asyncio
async def test_openreview_is_explicitly_disabled_without_credentials(tmp_path) -> None:
    client = ScholarClient(cache=AcquisitionCache(tmp_path, "off"))
    result = await client.get("openreview:some-forum-id")
    assert result.works == []
    assert result.provider_errors == ["openreview:Disabled"]


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


def _pdf_response(pages: int):
    body = _pdf(pages)
    return lambda _url: httpx.Response(200, headers={"content-type": "application/pdf"}, content=body)


@pytest.mark.asyncio
async def test_grobid_unavailable_falls_back_to_pdf_text(serve, tmp_path) -> None:
    serve(_pdf_response(1))
    # Nothing listens on loopback port 1, so the GROBID request fails.
    client = ScholarClient(cache=AcquisitionCache(tmp_path, "off"), grobid_url="http://127.0.0.1:1")
    result = await client.fetch("https://arxiv.org/pdf/2601.01234")
    assert result.extraction_method == "pypdf"
    assert "Page 1 line 1 carries scholarly evidence text." in result.text


@pytest.mark.asyncio
async def test_fetch_records_then_replays_without_network(serve, go_offline, tmp_path) -> None:
    serve(lambda _url: httpx.Response(200, headers={"content-type": "text/html"},
                                      text="<html><body><article><p>Recorded full text evidence.</p></article></body></html>"))
    recorded = await ScholarClient(cache=AcquisitionCache(tmp_path, "record")).fetch("https://example.org/paper")
    assert "Recorded full text" in recorded.text

    go_offline()
    replay = ScholarClient(cache=AcquisitionCache(tmp_path, "replay"))
    replayed = await replay.fetch("https://example.org/paper")
    assert replayed.text == recorded.text
    assert replayed.cache_hits == 1
    missing = await replay.fetch("https://example.org/other")
    assert missing.provider_errors == ["fetch:CacheMiss"]


@pytest.mark.asyncio
async def test_scholar_fetch_pages_through_a_paper_and_flags_unextracted_pages(serve, tmp_path) -> None:
    downloads: list[str] = []
    respond = _pdf_response(31)
    serve(lambda url: (downloads.append(url), respond(url))[1])
    client = ScholarClient(cache=AcquisitionCache(tmp_path, "off"), memo=FetchMemo())
    first = await client.fetch("https://arxiv.org/pdf/2601.01234")
    assert first.start == 0 and first.truncated is True
    assert "Page 1 line 1" in first.text
    last_start = first.total_chars - 500
    last = await client.fetch("https://arxiv.org/pdf/2601.01234", max_chars=1000, start=last_start)
    assert downloads == ["https://arxiv.org/pdf/2601.01234"]
    assert last.truncated is False and last.next_start is None
    assert "Page 30" in last.text
    # pypdf reads 30 pages; the last window says the document goes on past them.
    assert first.extraction_truncated is True and last.extraction_truncated is True
    beyond = await client.fetch("https://arxiv.org/pdf/2601.01234", start=first.total_chars)
    assert beyond.provider_errors == ["fetch:StartBeyondEnd"]


@pytest.mark.asyncio
async def test_scholar_fetch_pages_replay_from_recorded_windows(serve, go_offline, tmp_path) -> None:
    serve(_pdf_response(3))
    recorder = ScholarClient(cache=AcquisitionCache(tmp_path, "record"))
    page_two = await recorder.fetch("https://arxiv.org/pdf/2601.01234", max_chars=1000, start=1000)

    go_offline()
    replay = ScholarClient(cache=AcquisitionCache(tmp_path, "replay"))
    replayed = await replay.fetch("https://arxiv.org/pdf/2601.01234", max_chars=1000, start=1000)
    assert replayed.text == page_two.text and replayed.start == 1000


@pytest.mark.asyncio
async def test_oversized_metadata_response_is_refused(tmp_path) -> None:
    streamed: list[int] = []

    class Chunks(httpx.AsyncByteStream):
        async def __aiter__(self):
            for _ in range(40):  # 40 x 100 KB, well past the 2 MB cap
                streamed.append(1)
                yield b"x" * 100_000

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "application/json"}, stream=Chunks())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = ScholarClient(cache=AcquisitionCache(tmp_path, "off"), client=http)
        result = await client.search("agents", include_arxiv=False)
    assert result.provider_errors == ["openalex:ValueError"]
    assert len(streamed) < 40  # reading stopped at the cap instead of buffering the whole body

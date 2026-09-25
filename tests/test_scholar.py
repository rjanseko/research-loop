from __future__ import annotations

import httpx
import pytest

from research_loop.acquisition import AcquisitionCache
from research_loop.scholar import ScholarClient, _arxiv_works, _inverted_abstract

ARXIV_XML = '''<feed xmlns="http://www.w3.org/2005/Atom"><entry>
<id>http://arxiv.org/abs/2601.01234v2</id><title> Long Horizon Agents </title>
<published>2026-01-03T00:00:00Z</published><summary>Preprint summary</summary>
<author><name>A. Author</name></author><link title="pdf" href="https://arxiv.org/pdf/2601.01234v2"/>
</entry></feed>'''


def test_arxiv_versions_are_explicit_preprints_with_their_abstract() -> None:
    work = _arxiv_works(ARXIV_XML)[0]
    assert work.arxiv_id == "2601.01234v2"
    assert work.publication_status == "preprint"
    assert work.abstract == "Preprint summary"
    assert work.full_text_url.endswith("v2")


def test_openalex_abstracts_are_put_back_in_order() -> None:
    assert _inverted_abstract({"agents": [1], "Long": [0], "fail": [2, 4], "often": [3]}) == "Long agents fail often fail"
    assert _inverted_abstract(None) is None


def _metadata(request: httpx.Request) -> httpx.Response:
    if request.url.host == "api.openalex.org":
        return httpx.Response(200, json={"meta": {"count": 1}, "results": [{
            "id": "https://openalex.org/W123", "display_name": "Long Horizon Agents",
            "ids": {"doi": "https://doi.org/10.1234/paper"},
            "primary_location": {"version": "publishedVersion", "source": {"type": "journal", "display_name": "J. Agents"}},
            "abstract_inverted_index": {"Agents": [0], "recover": [1]},
        }]})
    if request.url.host == "export.arxiv.org":
        return httpx.Response(200, text=ARXIV_XML)
    if request.url.host == "api.crossref.org":
        return httpx.Response(200, json={"message": {
            "DOI": "10.1234/paper", "title": ["Long Horizon Agents"], "type": "journal-article",
            "abstract": "<jats:p>Agents <jats:italic>recover</jats:italic>.</jats:p>"}})
    raise AssertionError(str(request.url))


@pytest.mark.asyncio
async def test_providers_keep_their_own_records(tmp_path) -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(_metadata)) as http:
        client = ScholarClient(cache=AcquisitionCache(tmp_path, "off"), client=http)
        result = await client.search("long horizon agents")
        doi = await client.get("10.1234/paper")
        same = await client.get("doi:10.1234/paper")
    assert [(w.provider, w.publication_status) for w in result.works] == [("openalex", "journal"), ("arxiv", "preprint")]
    assert (result.works[0].abstract, result.works[0].venue) == ("Agents recover", "J. Agents")
    assert (doi.works[0].provider, doi.works[0].doi, doi.works[0].abstract) == ("crossref", "10.1234/paper", "Agents recover .")
    assert same.works == doi.works


@pytest.mark.asyncio
async def test_year_bounds_filter_every_search_provider(tmp_path) -> None:
    seen: dict[str, httpx.QueryParams] = {}
    arxiv_queries: list[str] = []
    old_preprint = ARXIV_XML.replace("2601.01234", "2101.00001").replace("2026-01-03", "2021-01-03")

    def handler(request: httpx.Request) -> httpx.Response:
        seen[request.url.host] = request.url.params
        if request.url.host == "api.openalex.org":
            return httpx.Response(200, json={"meta": {"count": 0}, "results": []})
        arxiv_queries.append(request.url.params["search_query"])
        # The feed ignores the date range, so the returned dates are checked too.
        return httpx.Response(200, text=ARXIV_XML.replace("</feed>", old_preprint.split("<feed", 1)[1].split(">", 1)[1]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = ScholarClient(cache=AcquisitionCache(tmp_path, "off"), client=http)
        result = await client.search("agents", year_from=2024)
        await client.search("agents", year_from=2020, year_to=2022)
    assert [w.arxiv_id for w in result.works] == ["2601.01234v2"]
    assert seen["api.openalex.org"]["filter"] == "from_publication_date:2020-01-01,to_publication_date:2022-12-31"
    assert arxiv_queries == [
        "all:agents AND submittedDate:[202401010000 TO 999912312359]",
        "all:agents AND submittedDate:[202001010000 TO 202212312359]",
    ]


@pytest.mark.asyncio
async def test_recorded_metadata_replays_without_the_network(tmp_path) -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(_metadata)) as http:
        recorded = await ScholarClient(cache=AcquisitionCache(tmp_path, "record"), client=http).search("agents")

    def offline(request: httpx.Request) -> httpx.Response:
        raise AssertionError("replay must not reach a provider")

    async with httpx.AsyncClient(transport=httpx.MockTransport(offline)) as http:
        replay = ScholarClient(cache=AcquisitionCache(tmp_path, "replay"), client=http)
        assert (await replay.search("agents")).works == recorded.works
        missing = await replay.search("other")
    assert missing.provider_errors == ["openalex:LookupError", "arxiv:LookupError"]


@pytest.mark.asyncio
async def test_the_openalex_key_stays_out_of_recordings(tmp_path) -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(_metadata)) as http:
        await ScholarClient(cache=AcquisitionCache(tmp_path, "record"), client=http, api_key="secret-key").get("W123")
    stored = "".join(path.read_text() for path in tmp_path.rglob("*.json"))
    assert "secret-key" not in stored


@pytest.mark.asyncio
async def test_an_unsupported_identifier_is_an_error_result(tmp_path) -> None:
    result = await ScholarClient(cache=AcquisitionCache(tmp_path, "off")).get("openreview:some-forum-id")
    assert result.works == [] and result.provider_errors == ["get:ValueError"]


@pytest.mark.asyncio
async def test_oversized_metadata_response_is_refused(tmp_path) -> None:
    streamed: list[int] = []

    class Chunks(httpx.AsyncByteStream):
        async def __aiter__(self):
            for _ in range(40):  # 40 x 100 KB, well past the 2 MB cap
                streamed.append(1)
                yield b"x" * 100_000

    async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, headers={"content-type": "application/json"}, stream=Chunks()))) as http:
        result = await ScholarClient(cache=AcquisitionCache(tmp_path, "off"), client=http).get("W1")
    assert result.provider_errors == ["get:ValueError"]
    assert len(streamed) < 40  # reading stopped at the cap instead of buffering the whole body


@pytest.mark.asyncio
async def test_rate_slots_apply_with_an_injected_client(monkeypatch, tmp_path) -> None:
    waited: list[str] = []

    async def record(provider: str) -> None:
        waited.append(provider)

    monkeypatch.setattr("research_loop.scholar.wait_rate_slot", record)
    async with httpx.AsyncClient(transport=httpx.MockTransport(_metadata)) as http:
        await ScholarClient(cache=AcquisitionCache(tmp_path, "off"), client=http).search("agents")
    # A run passes its shared client, and providers are still paced.
    assert waited == ["openalex", "arxiv"]

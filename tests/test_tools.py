from __future__ import annotations

import httpx
import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from research_loop.acquisition import AcquisitionCache
from research_loop.evidence import check_result
from research_loop.schemas import Claim, Evidence, ResearchResult, SourceRef
from research_loop.scholar import ScholarClient
from research_loop.tools import labeled_texts, research_toolset, tool_outcomes
from research_loop.web import WebAcquisition, WebSearch

PAGE = "<html><body><article><p>SWE-bench Verified has 500 human-validated tasks.</p></article></body></html>"


_WORK = {
    "id": "https://openalex.org/W1", "display_name": "SWE-bench", "ids": {"doi": "https://doi.org/10.1/swe"},
    "primary_location": {"landing_page_url": "https://proceedings.example/swe"},
    "abstract_inverted_index": {"Real": [0], "GitHub": [1], "issues": [2]},
}


def _metadata(request: httpx.Request) -> httpx.Response:
    if request.url.host == "api.openalex.org":
        if request.url.path == "/works/W1":
            return httpx.Response(200, json=_WORK)
        return httpx.Response(200, json={"meta": {"count": 1}, "results": [_WORK]})
    return httpx.Response(200, text='<feed xmlns="http://www.w3.org/2005/Atom"></feed>')


async def _research(serve, tmp_path, calls: list[ToolCallPart]):
    """Run a scripted scout that makes `calls` in one turn, then stops; return its messages."""
    serve(lambda url: httpx.Response(404) if "missing" in url else
          httpx.Response(200, headers={"content-type": "text/html"}, text=PAGE))

    async def engine(query: str) -> list[dict[str, str]]:
        return [{"title": "SWE-bench Verified", "href": "https://openai.com/index/swe-bench-verified",
                 "body": "A human-validated subset"}]

    def respond(messages, info: AgentInfo) -> ModelResponse:
        if len(messages) == 1:
            return ModelResponse(parts=calls)
        return ModelResponse(parts=[TextPart("done")])

    async with httpx.AsyncClient(transport=httpx.MockTransport(_metadata)) as http:
        toolset = research_toolset(
            WebSearch(engine=engine, retry_delays=()),
            WebAcquisition(cache_root=tmp_path, cache_mode="off"),
            ScholarClient(cache=AcquisitionCache(tmp_path, "off"), client=http),
        )
        result = await Agent(FunctionModel(respond), toolsets=[toolset]).run("research")
    return result.all_messages()


async def test_every_result_says_how_much_of_a_source_it_holds(serve, tmp_path) -> None:
    messages = await _research(serve, tmp_path, [
        ToolCallPart("web_search", {"query": "SWE-bench Verified"}, tool_call_id="a"),
        ToolCallPart("fetch", {"url": "https://openai.com/index/swe-bench-verified"}, tool_call_id="b"),
        ToolCallPart("fetch", {"url": "https://example.org/missing"}, tool_call_id="c"),
        ToolCallPart("scholar_search", {"query": "SWE-bench"}, tool_call_id="d"),
    ])
    returns = {part.tool_call_id: part.content for message in messages for part in message.parts
               if isinstance(part, ToolReturnPart)}
    assert returns["a"]["access"] == "snippet"
    assert returns["b"]["access"] == "full_text" and "500 human-validated" in returns["b"]["text"]
    assert "access" not in returns["c"] and returns["c"]["status"] == 404
    assert returns["d"]["works"][0]["access"] == "abstract"
    assert returns["d"]["works"][0]["abstract"] == "Real GitHub issues"

    texts = labeled_texts(messages)
    assert [text.access for text in texts] == ["snippet", "full_text", "abstract"]

    outcomes = tool_outcomes(messages)
    assert outcomes.searches == ["SWE-bench Verified", "SWE-bench"]
    assert outcomes.pages_read == ["https://openai.com/index/swe-bench-verified"]
    assert [(u.target, u.reason) for u in outcomes.unreached] == [("https://example.org/missing", "HTTPStatusError 404")]
    assert (outcomes.calls, outcomes.misses) == (4, 1)


async def test_a_result_is_checked_against_the_labeled_tool_output(serve, tmp_path) -> None:
    messages = await _research(serve, tmp_path, [
        ToolCallPart("web_search", {"query": "SWE-bench Verified"}, tool_call_id="a"),
        ToolCallPart("fetch", {"url": "https://openai.com/index/swe-bench-verified"}, tool_call_id="b"),
        ToolCallPart("scholar_get", {"identifier": "W1"}, tool_call_id="c"),
    ])

    def evidence(source: SourceRef, quote: str | None = None) -> Evidence:
        return Evidence(source=source, excerpt="e", quote=quote, confidence=0.8)

    result = check_result(ResearchResult(
        question_id="q1", question="Q?", conclusion="c", confidence=0.8, claims=[Claim(
            id="c1", statement="s", confidence=0.8, evidence=[
                evidence(SourceRef(url="https://openai.com/index/swe-bench-verified/", title="t"),
                         "500 human-validated tasks"),
                evidence(SourceRef(doi="10.1/swe", title="SWE-bench"), "Real GitHub issues"),
                evidence(SourceRef(url="https://unseen.example/report", title="t"), "invented"),
            ])]), labeled_texts(messages))
    read, abstract, unseen = result.claims[0].evidence
    assert (read.quote_access, read.source_access) == ("full_text", "full_text")
    assert (abstract.quote_access, abstract.source_access) == ("abstract", "abstract")
    assert (unseen.quote_check, unseen.source_check) == ("not_found", "not_found")


@pytest.mark.parametrize(("tool", "content"), [
    ("web_search", {"error": "SearchUnavailable (RuntimeError)"}),
    ("scholar_get", {"works": [], "provider_errors": ["get:HTTPStatusError"]}),
])
def test_failed_lookups_are_listed_as_unreached(tool: str, content: dict) -> None:
    from pydantic_ai.messages import ModelRequest

    args = {"query": "q"} if tool == "web_search" else {"identifier": "10.1/x"}
    messages = [ModelResponse(parts=[ToolCallPart(tool, args, tool_call_id="x")]),
                ModelRequest(parts=[ToolReturnPart(tool, content, tool_call_id="x")])]
    (unreached,) = tool_outcomes(messages).unreached
    assert unreached.reason in ("SearchUnavailable (RuntimeError)", "get:HTTPStatusError")

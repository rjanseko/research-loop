from __future__ import annotations

import pytest

from research_loop.quotes import check_quotes, quote_found, tool_texts
from research_loop.schemas import Claim, Evidence, ResearchResult, SourceRef, ToolEvent

PAGE = (
    "SWE-bench contains 2,294 task instances drawn from 12 popular Python repos-\n"
    "itories. Each instance pairs a GitHub issue with the pull request that re-\nsolved it."
)


@pytest.mark.parametrize("quote", [
    "SWE-bench contains 2,294 task instances",                       # exact
    "swe-bench   CONTAINS 2,294\ttask instances",                    # case and whitespace
    "drawn from 12 popular Python repositories",                    # a PDF line-break hyphen
    "“Each instance pairs a GitHub issue”",               # curly quotation marks
    "SWE-bench contains 2,294 task instances … 12 popular Python repositories",  # ellipsis
    "Each instance pairs [an] issue ... the pull request",          # editorial insertion
])
def test_quotes_survive_formatting_differences(quote: str) -> None:
    assert quote_found(quote, [PAGE])


@pytest.mark.parametrize("quote", [
    "SWE-bench contains 2,500 task instances",                      # altered number
    "the pull request that resolved it ... SWE-bench contains",     # segments out of order
    "",
])
def test_altered_or_reordered_quotes_are_not_found(quote: str) -> None:
    assert not quote_found(quote, [PAGE])


def test_segments_must_come_from_one_source_text() -> None:
    assert not quote_found("task instances ... benchmark leaderboard", [PAGE, "benchmark leaderboard"])


def test_tool_texts_collect_every_string_the_tools_returned() -> None:
    events = [
        ToolEvent(tool_name="web_fetch", result={"url": "https://example.org", "text": "page body", "start": 0}),
        ToolEvent(tool_name="duckduckgo_search", result=[{"title": "A title", "body": "a snippet"}]),
        ToolEvent(tool_name="scholar_search", result=None),
    ]
    assert tool_texts(events) == ["https://example.org", "page body", "A title", "a snippet"]


def test_check_quotes_sets_the_check_and_ignores_a_model_supplied_verdict() -> None:
    source = SourceRef(url="https://example.org/swe-bench", title="SWE-bench")

    def evidence(quote: str | None, claimed: str | None = None) -> Evidence:
        return Evidence(source=source, excerpt="summary", quote=quote, confidence=0.9, quote_check=claimed)

    result = ResearchResult(
        question_id="q1", question="What is SWE-bench?", conclusion="c", confidence=0.9,
        claims=[Claim(id="c1", statement="s", confidence=0.9, evidence=[
            evidence("contains 2,294 task instances"),
            evidence("contains 5,000 task instances", claimed="verified"),
            evidence(None, claimed="verified"),
            evidence("   "),
        ])],
    )
    checked = check_quotes(result, [PAGE])
    assert [item.quote_check for item in checked.claims[0].evidence] == ["verified", "not_found", None, None]


def test_quote_check_is_hidden_from_the_model_schema() -> None:
    evidence_schema = ResearchResult.model_json_schema()["$defs"]["Evidence"]["properties"]
    assert "quote" in evidence_schema
    assert "quote_check" not in evidence_schema


@pytest.mark.asyncio
async def test_research_runs_check_quotes_against_their_own_tool_output() -> None:
    from uuid import uuid4

    from pydantic_ai import Agent
    from pydantic_ai.messages import ModelResponse, ToolCallPart
    from pydantic_ai.models.function import AgentInfo, FunctionModel

    from research_loop.async_orchestrator import AsyncResearchLoop
    from research_loop.policy import ModelPolicy, ModelRoute
    from research_loop.repository import InMemoryResearchRepository
    from research_loop.schemas import ResearchRole

    route = ModelRoute("test", 5, 5, 50_000)
    loop = AsyncResearchLoop(ModelPolicy("quotes", {role: route for role in ResearchRole}),
                             repository=InMemoryResearchRepository())
    agent = Agent(output_type=ResearchResult)

    @agent.tool_plain
    def read_page(url: str) -> dict:
        return {"url": url, "text": PAGE}

    source = {"url": "https://example.org/swe-bench", "title": "SWE-bench"}

    def respond(messages, info: AgentInfo) -> ModelResponse:
        if len(messages) == 1:
            return ModelResponse(parts=[ToolCallPart("read_page", {"url": source["url"]})])
        evidence = [
            {"source": source, "excerpt": "size", "quote": "contains 2,294 task instances", "confidence": 0.9},
            # A model that marks its own invented quote as verified is overruled.
            {"source": source, "excerpt": "size", "quote": "contains 4,000 task instances", "confidence": 0.9,
             "quote_check": "verified"},
        ]
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {
            "question_id": "q1", "question": "What is SWE-bench?", "conclusion": "c", "confidence": 0.9,
            "claims": [{"id": "c1", "statement": "s", "confidence": 0.9, "evidence": evidence}],
        })])

    with agent.override(model=FunctionModel(respond)):
        result = await loop._run_agent(job_id=uuid4(), agent=agent, role=ResearchRole.SCOUT, route=route, prompt="p")
    assert [item.quote_check for item in result.claims[0].evidence] == ["verified", "not_found"]
    (task,) = loop.repository.tasks.values()  # Postgres history keeps the same verdicts
    assert [item["quote_check"] for item in task["output"]["claims"][0]["evidence"]] == ["verified", "not_found"]

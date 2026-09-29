"""The synthesizer's Claude citations, offline: a CitingAnthropicModel against a scripted Anthropic event
stream, served in-process, and the report code builds from the citations it keeps."""
from __future__ import annotations

import json
from typing import Any

import httpx2
import pytest
from anthropic import AsyncAnthropic
from pydantic_ai import models
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.providers.anthropic import AnthropicProvider

from research_loop.agents import synthesizer_agent
from research_loop.citations import CitingAnthropicModel, cited_report, search_results
from research_loop.evidence import EvidenceLedger
from research_loop.schemas import Claim, Evidence, ResearchResult, SourceRef


def _ledger() -> EvidenceLedger:
    ledger = EvidenceLedger()
    page = SourceRef(url="https://example.org/pdb", title="The Protein Data Bank")
    other = SourceRef(url="https://example.org/review", title="A review")
    ledger.add(ResearchResult(question_id="q1", question="What does the PDB hold?", conclusion="c", confidence=0.8,
                              claims=[
        Claim(id="c1", statement="The PDB holds protein structures", confidence=0.8, evidence=[
            Evidence(source=page, excerpt="summary", quote="The PDB holds 3D structures of proteins.",
                     confidence=0.8, quote_check="verified", quote_access="full_text", source_access="full_text")]),
        Claim(id="c2", statement="It is updated weekly", confidence=0.8, evidence=[
            Evidence(source=page, excerpt="Weekly updates.", confidence=0.8, source_access="snippet"),
            Evidence(source=other, excerpt="Not weekly.", supports=False, confidence=0.5, source_access="abstract")]),
    ]))
    return ledger


def test_passages_are_each_sources_supporting_evidence_labelled_with_claim_and_check() -> None:
    passages = _ledger().passages()
    assert [(p.source_id, p.title) for p in passages] == [("s1", "The Protein Data Bank")]
    assert [(p.claim_id, p.text) for p in passages[0].passages] == [
        ("q1/c1", "(q1/c1; full_text; quote verified) The PDB holds 3D structures of proteins."),
        ("q1/c2", "(q1/c2; snippet; the researcher's summary; no quote) Weekly updates."),
    ]
    # The prompt view keeps each item's checks, and contradicting evidence's text, but not the cited text.
    view = _ledger().prompt_view(passages=False)
    evidence = [item for result in view["research"] for claim in result["claims"] for item in claim["evidence"]]
    assert [("quote" in item or "excerpt" in item) for item in evidence] == [False, False, True]
    assert evidence[0]["quote_check"] == "verified"


def _sse(events: list[dict[str, Any]]) -> bytes:
    return "".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events).encode()


def _text_block(index: int, text: str, citations: list[dict[str, Any]] | None = None, *,
                citations_first: bool = False) -> list[dict[str, Any]]:
    """One text block's events as Anthropic's streaming docs show them: it starts empty with no citations
    field, and each citation arrives as a citations_delta, after its text unless `citations_first`."""
    start = {"type": "content_block_start", "index": index, "content_block": {"type": "text", "text": ""}}
    text_delta = [{"type": "content_block_delta", "index": index, "delta": {"type": "text_delta", "text": text}}]
    cited = [{"type": "content_block_delta", "index": index, "delta": {"type": "citations_delta", "citation": c}}
             for c in citations or []]
    body = [*cited, *text_delta] if citations_first else [*text_delta, *cited]
    return [start, *body, {"type": "content_block_stop", "index": index}]


def _citation(source: str, index: int, start: int, end: int) -> dict[str, Any]:
    return {"type": "search_result_location", "source": source, "title": "The Protein Data Bank",
            "cited_text": "...", "search_result_index": index, "start_block_index": start, "end_block_index": end}


REPLY = [
    {"type": "message_start", "message": {"id": "msg_1", "type": "message", "role": "assistant",
                                          "model": "claude-opus-5-5", "content": [], "stop_reason": None,
                                          "stop_sequence": None, "usage": {"input_tokens": 900, "output_tokens": 1}}},
    *_text_block(0, "<title>What the PDB holds</title>\n<summary>"),
    *_text_block(1, "The PDB holds protein structures", [_citation("s1", 0, 0, 1)]),
    *_text_block(2, ".</summary>\n<answer>## Contents\n\nIt holds 3D structures [s9]"),
    {"type": "ping"},
    *_text_block(3, " and is updated weekly", [_citation("s1", 0, 0, 2)], citations_first=True),
    *_text_block(4, ".\n</answer>\n<caveats>\n- The update schedule rests on a snippet.\n</caveats>\n"
                    "<not_established>k2</not_established>"),
    {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
     "usage": {"output_tokens": 120}},
    {"type": "message_stop"},
]


@pytest.fixture
def claude(monkeypatch: pytest.MonkeyPatch) -> tuple[CitingAnthropicModel, list[dict[str, Any]]]:
    """A CitingAnthropicModel whose client is served in-process; the requests it sends are kept."""
    monkeypatch.setattr(models, "ALLOW_MODEL_REQUESTS", True)
    sent: list[dict[str, Any]] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        sent.append(json.loads(request.content))
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=_sse(REPLY))

    client = AsyncAnthropic(api_key="test-key", base_url="http://127.0.0.1:9",
                            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(respond)))
    model = CitingAnthropicModel("claude-opus-5-5", provider=AnthropicProvider(anthropic_client=client),
                                 settings={"thinking": "medium", "max_tokens": 32_000, "timeout": 120})
    return model, sent


async def test_claude_receives_search_results_and_its_citations_become_the_reports_references(claude) -> None:
    model, sent = claude
    ledger = _ledger()
    passages = ledger.passages()
    result = await synthesizer_agent.run([json.dumps({"research": ledger.prompt_view(passages=False)}),
                                          *search_results(passages)], model=model)

    # One request, streamed, with the passages as a citable search result after the JSON brief.
    assert len(sent) == 1 and sent[0]["stream"] is True
    content = sent[0]["messages"][0]["content"]
    assert content[0]["type"] == "text" and json.loads(content[0]["text"])["research"]
    assert content[1] == {"type": "search_result", "source": "s1", "title": "The Protein Data Bank",
                          "citations": {"enabled": True},
                          "content": [{"type": "text", "text": p.text} for p in passages[0].passages]}

    # The citations Claude streamed stay on the text blocks that carry them.
    cited = [part for part in result.response.parts
             if isinstance(part, TextPart) and (part.provider_details or {}).get("citations")]
    assert [part.content for part in cited] == ["The PDB holds protein structures", " and is updated weekly"]

    report = cited_report(result.response, passages)
    assert report.title == "What the PDB holds"
    assert report.executive_summary == "The PDB holds protein structures [s1]."
    # The model's own [s9] is removed; code writes each citation from the blocks Claude cited.
    assert report.answer == "## Contents\n\nIt holds 3D structures and is updated weekly [s1]."
    assert [(c.statement, c.claim_ids) for c in report.claims] == [
        ("The PDB holds protein structures", ["q1/c1"]),
        ("and is updated weekly", ["q1/c1", "q1/c2"]),
    ]
    assert report.caveats == ["The update schedule rests on a snippet."]
    assert report.not_established == ["k2"]
    assert result.response.usage.input_tokens == 900 and result.response.usage.output_tokens == 120


async def test_streaming_a_synthesis_through_pydantic_ai_is_refused(claude) -> None:
    model, sent = claude
    with pytest.raises(Exception, match="citations only through request"):
        async with synthesizer_agent.run_stream("q", model=model):
            pass
    assert sent == []


def test_a_citation_of_an_unknown_source_or_block_adds_no_reference() -> None:
    passages = _ledger().passages()
    response = ModelResponse(parts=[
        TextPart("<title>T</title><summary>S</summary><answer>"),
        TextPart("A claim", provider_name="anthropic",
                 provider_details={"citations": [_citation("s7", 3, 0, 1), _citation("s1", 0, 5, 6)]}),
        TextPart("</answer>"),
    ])
    report = cited_report(response, passages)
    assert report.answer == "A claim" and report.claims == []


def test_a_search_result_title_stays_within_the_apis_limit() -> None:
    from research_loop.citations import TITLE_CHARS
    from research_loop.evidence import Passage, SourcePassages

    [content] = search_results([SourcePassages("s1", "T" * 900, (Passage("q1/c1", "(q1/c1; abstract; quote verified) x"),))])
    assert len(content.metadata["title"]) == TITLE_CHARS and content.metadata["title"].endswith("...")


def test_the_passages_are_kept_once_so_the_budget_guard_counts_them_once() -> None:
    # The first live check was refused before dispatch: each passage was in both the text and the metadata,
    # and the guard reserves by the bytes of the serialized messages (run 8e4bfa1c).
    passages = _ledger().passages()
    [content] = search_results(passages)
    assert not any(passage.text in content.content for passage in passages[0].passages)
    assert content.metadata["blocks"] == [passage.text for passage in passages[0].passages]


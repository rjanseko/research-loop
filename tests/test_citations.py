"""The synthesizer's Claude citations, offline: a CitingAnthropicModel against a scripted Anthropic event
stream, served in-process, and the report code builds from the citations it keeps."""
from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import httpx2
import pytest
from anthropic import AsyncAnthropic
from pydantic_ai import models
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.providers.anthropic import AnthropicProvider

from research_loop.agents import synthesizer_agent
from research_loop.citations import (
    CitingAnthropicModel,
    cited_report,
    mismatched_citations,
    search_results,
)
from research_loop.config import Settings
from research_loop.evidence import EvidenceLedger
from research_loop.models import model_settings
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
        Claim(id="c3", statement="It is free to use", confidence=0.4, evidence=[
            Evidence(source=other, excerpt="Access is paid.", supports=False, confidence=0.5, source_access="abstract")]),
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
    claims = [claim for result in view["research"] for claim in result["claims"]]
    evidence = [item for claim in claims for item in claim["evidence"]]
    assert [("quote" in item or "excerpt" in item) for item in evidence] == [False, False, True, True]
    assert evidence[0]["quote_check"] == "verified"
    # A claim with a passage is listed without its statement, so the passage is where the fact comes from; a
    # claim with only contradicting evidence has no passage and keeps it.
    assert [claim.get("statement") for claim in claims] == [None, None, "It is free to use"]
    assert all(claim["id"] for claim in claims)
    # Other prompts keep every statement.
    assert all("statement" in claim for result in _ledger().prompt_view()["research"] for claim in result["claims"])


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


def _citation(source: str, index: int, start: int, end: int, cited_text: str | None = None) -> dict[str, Any]:
    """A citation as Anthropic sends it: its cited text is the cited blocks joined, as the live check showed
    for all 21 of its citations (run 54315592)."""
    passages = _ledger().passages()
    blocks = passages[index].passages[start:end] if index < len(passages) else ()
    return {"type": "search_result_location", "source": source, "title": "The Protein Data Bank",
            "cited_text": "".join(p.text for p in blocks) if cited_text is None else cited_text,
            "search_result_index": index, "start_block_index": start, "end_block_index": end}


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


def _claude(monkeypatch: pytest.MonkeyPatch, reply: list[dict[str, Any]],
            settings: dict[str, Any] | None = None) -> tuple[CitingAnthropicModel, list[httpx2.Request]]:
    """A CitingAnthropicModel whose client is served `reply` in-process; the requests it sends are kept."""
    monkeypatch.setattr(models, "ALLOW_MODEL_REQUESTS", True)
    sent: list[httpx2.Request] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        sent.append(request)
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=_sse(reply))

    client = AsyncAnthropic(api_key="test-key", base_url="http://127.0.0.1:9",
                            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(respond)))
    model = CitingAnthropicModel("claude-opus-5-5", provider=AnthropicProvider(anthropic_client=client),
                                 settings=settings or {"thinking": "medium", "max_tokens": 32_000, "timeout": 120})
    return model, sent


@pytest.fixture
def claude(monkeypatch: pytest.MonkeyPatch) -> tuple[CitingAnthropicModel, list[httpx2.Request]]:
    return _claude(monkeypatch, REPLY)


def _prompt(ledger: EvidenceLedger) -> list[Any]:
    """The synthesizer's prompt as `_Run._synthesize` builds it: the search results, then the brief."""
    return [*search_results(ledger.passages()), json.dumps({"research": ledger.prompt_view(passages=False)})]


async def test_claude_receives_search_results_and_its_citations_become_the_reports_references(claude) -> None:
    model, sent = claude
    ledger = _ledger()
    passages = ledger.passages()
    result = await synthesizer_agent.run(_prompt(ledger), model=model)

    # One request, streamed, with the passages as a citable search result before the JSON brief.
    assert len(sent) == 1 and json.loads(sent[0].content)["stream"] is True
    content = json.loads(sent[0].content)["messages"][0]["content"]
    assert content[0] == {"type": "search_result", "source": "s1", "title": "The Protein Data Bank",
                          "citations": {"enabled": True},
                          "content": [{"type": "text", "text": p.text} for p in passages[0].passages]}
    assert content[1]["type"] == "text" and json.loads(content[1]["text"])["research"]

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
    assert mismatched_citations(result.response, passages) == []
    assert result.response.usage.input_tokens == 900 and result.response.usage.output_tokens == 120
    assert "fallback" not in (result.response.provider_details or {})


async def test_streaming_a_synthesis_through_pydantic_ai_is_refused(claude) -> None:
    model, sent = claude
    with pytest.raises(Exception, match="citations only through request"):
        async with synthesizer_agent.run_stream("q", model=model):
            pass
    assert sent == []


def test_a_citation_that_does_not_match_the_passages_sent_adds_no_reference_and_is_named() -> None:
    passages = _ledger().passages()
    wrong = [
        _citation("s7", 3, 0, 1),  # an unknown source
        _citation("s1", 0, 5, 6),  # blocks the search result does not have
        _citation("s1", 1, 0, 1),  # a search result position that is not s1's
        _citation("s1", 0, 1, 1),  # an empty block range
        _citation("s1", 0, 0, 1, cited_text="(q1/c2; snippet) Weekly updates."),  # text that is not block 0's
        {**_citation("s1", 0, 0, 1), "type": "char_location"},
    ]
    response = ModelResponse(parts=[
        TextPart("<title>T</title><summary>S</summary><answer>"),
        TextPart("A claim", provider_name="anthropic", provider_details={"citations": wrong}),
        TextPart(" and a matching one", provider_name="anthropic", provider_details={"citations": [_citation("s1", 0, 1, 2)]}),
        TextPart("</answer>"),
    ])
    report = cited_report(response, passages)
    assert report.answer == "A claim and a matching one [s1]"
    assert [(claim.statement, claim.claim_ids) for claim in report.claims] == [("and a matching one", ["q1/c2"])]
    assert mismatched_citations(response, passages) == [
        "search result 3 is not s7", "s1 has no blocks 5 to 6", "search result 1 is not s1", "s1 has no blocks 1 to 1",
        "the cited text of s1 blocks 0 to 1 is not theirs", "a char_location citation"]


def _fallback_block(index: int, category: str | None) -> list[dict[str, Any]]:
    block = {"type": "fallback", "from": {"model": "claude-opus-5-5"}, "to": {"model": "claude-opus-5"},
             "trigger": {"type": "refusal", "category": category}}
    return [{"type": "content_block_start", "index": index, "content_block": block},
            {"type": "content_block_stop", "index": index}]


def _iteration(kind: str, model: str, input_tokens: int, output_tokens: int) -> dict[str, Any]:
    return {"type": kind, "model": model, "input_tokens": input_tokens, "output_tokens": output_tokens,
            "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}


# Opus 5.5 declines after writing the title and one cited sentence; Opus 5 continues from that text, as
# Anthropic's streaming fallback does, and its usage replaces the top-level usage in the final message_delta.
FALLBACK_REPLY = [
    REPLY[0],
    *_text_block(0, "<title>What the PDB holds</title>\n<summary>"),
    *_text_block(1, "The PDB holds protein structures", [_citation("s1", 0, 0, 1)]),
    *_fallback_block(2, "bio"),
    *_text_block(3, ".</summary>\n<answer>It is updated weekly", [_citation("s1", 0, 1, 2)]),
    *_text_block(4, ".</answer>"),
    {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
     "usage": {"input_tokens": 940, "output_tokens": 80,
               "iterations": [_iteration("message", "claude-opus-5-5", 900, 40),
                              _iteration("fallback_message", "claude-opus-5", 940, 80)]}},
    {"type": "message_stop"},
]


async def test_a_declined_synthesis_continues_on_the_fallback_and_every_attempt_is_priced(monkeypatch) -> None:
    settings = model_settings("anthropic:claude-opus-5-5@medium", "synthesizer", Settings())
    model, sent = _claude(monkeypatch, FALLBACK_REPLY, settings)
    ledger = _ledger()
    passages = ledger.passages()
    result = await synthesizer_agent.run(_prompt(ledger), model=model)

    # The request names Opus 5 as its fallback under the fallback beta.
    body = json.loads(sent[0].content)
    assert body["fallbacks"] == [{"model": "claude-opus-5", "output_config": {"effort": "medium"}}]
    assert "server-side-fallback-2026-07-01" in sent[0].headers["anthropic-beta"]

    # The text before and after the handoff is one report, and both attempts' citations are kept.
    report = cited_report(result.response, passages)
    assert report.executive_summary == "The PDB holds protein structures [s1]."
    assert report.answer == "It is updated weekly [s1]."
    assert [claim.claim_ids for claim in report.claims] == [["q1/c1"], ["q1/c2"]]

    # Each attempt is priced at its own model's rates: Opus 5.5 at $4/$20 and Opus 5 at $5/$25 a million.
    response = result.response
    expected = (Decimal(900 * 4 + 40 * 20) + Decimal(940 * 5 + 80 * 25)) / 1_000_000
    assert response.model_name == "claude-opus-5" and response.usage.cost == expected
    assert result.usage.cost == expected  # PydanticAI keeps the cost the response carries
    fallback = response.provider_details["fallback"]
    assert [handoff["trigger"]["category"] for handoff in fallback["handoffs"]] == ["bio"]
    assert [(a["model"], a["served"], a["cost_usd"]) for a in fallback["attempts"]] == [
        ("claude-opus-5-5", False, "0.0044"), ("claude-opus-5", True, "0.0067")]


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


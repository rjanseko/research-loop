"""The reader-visible assertion must trace only to its displayed source passage."""
from __future__ import annotations

from pydantic_ai.messages import ModelResponse, TextPart

from research_loop.audit import audit_items
from research_loop.citations import cited_report
from research_loop.evidence import EvidenceLedger, ToolText, check_result, identity_keys
from research_loop.render import render_markdown
from research_loop.schemas import (
    Claim,
    Evidence,
    ResearchPlan,
    ResearchQuestion,
    ResearchResult,
    SourceRef,
)
from research_loop.scout import _checks


def _ledger() -> EvidenceLedger:
    full = SourceRef(url="https://example.org/full", title="Full page")
    snippet = SourceRef(url="https://example.org/snippet", title="Snippet")
    result = ResearchResult(question_id="q1", question="What rate?", conclusion="12 percent", confidence=0.8,
                            claims=[Claim(id="c1", statement="The rate is 12 percent", confidence=0.8, evidence=[
        Evidence(source=full, excerpt="a full page", quote="The rate was 1.2 percent", confidence=0.8),
        Evidence(source=snippet, excerpt="a search result", quote="The rate was 12 percent", confidence=0.8),
    ])])
    checked = check_result(result, [
        ToolText("full_text", identity_keys(url=str(full.url)), "The rate was 1.2 percent.",
                 "call-full", "fetch.text", {"url": str(full.url)}),
        ToolText("snippet", identity_keys(url=str(snippet.url)), "The rate was 12 percent.",
                 "call-search", "web_search.results[0]", {"title": "Snippet", "url": str(snippet.url)}),
    ], observed_at="2026-09-29T20:00:00Z")
    ledger = EvidenceLedger()
    ledger.add(checked)
    return ledger


def _citation(source_id: str, index: int, block: str, *, block_index: int = 0) -> dict:
    return {"type": "search_result_location", "source": source_id, "search_result_index": index,
            "start_block_index": block_index, "end_block_index": block_index + 1, "cited_text": block}


def test_a_report_is_supported_only_by_the_exact_source_it_displays() -> None:
    ledger = _ledger()
    passages = ledger.passages()
    assert [source.source_id for source in passages] == ["s1", "s2"]
    citation = _citation("s2", 1, passages[1].passages[0].text)
    response = ModelResponse(parts=[
        TextPart("<title>Rate</title><summary>"),
        TextPart("The rate was 12 percent", provider_details={"citations": [citation]}),
        TextPart(".</summary><answer>"),
        TextPart("The rate was 12 percent", provider_details={"citations": [citation]}),
        TextPart(".</answer>"),
    ])
    report = cited_report(response, passages)
    assert [(item.section, item.citation_scope, item.source_ids) for item in report.assertions] == [
        ("summary", "exact", ["s2"]), ("answer", "exact", ["s2"])]
    checks = _checks(ResearchPlan(questions=[ResearchQuestion(id="q1", question="What rate?")]),
                     ledger, report, False)
    assert [item.support for item in checks.statements] == ["shallow", "shallow"]
    assert checks.answer_support is None  # _checks records provenance before the run computes final support
    items = audit_items(report, ledger)
    assert [[quote["source"] for quote in item["quotes"]] for item in items] == [["s2"], ["s2"]]
    assert all(item["quotes"][0]["quote"] == "The rate was 12 percent" for item in items)
    rendered = render_markdown({"run_id": "test", "status": "complete", "question": "What rate?",
                                "report": report.model_dump(mode="json"), "ledger": ledger.to_json(),
                                "checks": checks.model_dump(mode="json")}, include_provenance=True)
    assert "## Provenance" in rendered and "call-search at web_search.results[0]" in rendered
    assert "The rate was 12 percent" in rendered and "SHA-256" in rendered


def test_uncited_summary_table_row_and_caveat_are_explicit_assertions() -> None:
    ledger = _ledger()
    passages = ledger.passages()
    citation = _citation("s2", 1, passages[1].passages[0].text)
    response = ModelResponse(parts=[
        TextPart("<title>Rate</title><summary>This summary makes an uncited factual assertion.</summary>"),
        TextPart("<answer>| Item | Rate |\n|---|---|\n"),
        TextPart("| A | 12 percent |", provider_details={"citations": [citation]}),
        TextPart("</answer><caveats>- This caveat is not supported by a citation.</caveats>"),
    ])
    report = cited_report(response, passages)
    assert [(a.section, a.citation_scope) for a in report.assertions] == [
        ("summary", "uncited"), ("answer", "exact"), ("caveat", "uncited")]
    items = audit_items(report, ledger)
    assert [len(item["quotes"]) for item in items] == [0, 1, 0]


def test_a_cited_paraphrase_does_not_borrow_another_passages_verified_quote() -> None:
    source = SourceRef(url="https://example.org/study", title="Study")
    result = ResearchResult(question_id="q1", question="What happened?", conclusion="Two observations", confidence=0.8,
                            claims=[Claim(id="c1", statement="Two observations", confidence=0.8, evidence=[
        Evidence(source=source, excerpt="A quoted observation", quote="The first result was measured",
                 confidence=0.8),
        Evidence(source=source, excerpt="A researcher summary of the second result", confidence=0.8),
    ])])
    checked = check_result(result, [ToolText(
        "full_text", identity_keys(url=str(source.url)), "The first result was measured. More source text.",
        "call-full", "fetch.text", {"url": str(source.url)})])
    ledger = EvidenceLedger()
    ledger.add(checked)
    passages = ledger.passages()[0].passages
    assert len(passages) == 2 and passages[0].source_text and passages[1].source_text is None
    response = ModelResponse(parts=[
        TextPart("<title>Study</title><summary>"),
        TextPart("The second result was favorable", provider_details={"citations": [
            _citation("s1", 0, passages[1].text, block_index=1)]}),
        TextPart(".</summary><answer>"),
        TextPart("The second result was favorable", provider_details={"citations": [
            _citation("s1", 0, passages[1].text, block_index=1)]}),
        TextPart(".</answer>"),
    ])
    report = cited_report(response, ledger.passages())
    assert [a.citation_scope for a in report.assertions] == ["exact", "exact"]
    checks = _checks(ResearchPlan(questions=[ResearchQuestion(id="q1", question="What happened?")]),
                     ledger, report, False)
    assert [s.support for s in checks.statements] == ["paraphrase", "paraphrase"]
    assert all(not item["quotes"] for item in audit_items(report, ledger))

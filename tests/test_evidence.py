from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from research_loop.evidence import (
    EvidenceLedger,
    ToolOutputIndex,
    ToolText,
    check_result,
    citation_problems,
    identity_keys,
    support_level,
)
from research_loop.schemas import (
    Claim,
    Contradiction,
    Evidence,
    FinalReport,
    ReportClaim,
    ResearchResult,
    SourceRef,
)

PAGE = (
    "SWE-bench contains 2,294 task instances drawn from 12 popular Python repos-\n"
    "itories. Each instance pairs a GitHub issue with the pull request that re-\nsolved it."
)
PAPER = "https://arxiv.org/abs/2310.06770"


def _full(text: str, url: str = PAPER) -> ToolText:
    return ToolText("full_text", identity_keys(url=url), text)


def _found(quote: str, *texts: str) -> bool:
    return ToolOutputIndex([_full(text) for text in texts]).quote_access(quote) is not None


@pytest.mark.parametrize("quote", [
    "SWE-bench contains 2,294 task instances",                       # exact
    "swe-bench   CONTAINS 2,294\ttask instances",                    # case and whitespace
    "drawn from 12 popular Python repositories",                    # a PDF line-break hyphen
    "“Each instance pairs a GitHub issue”",                         # curly quotation marks
    "SWE-bench contains 2,294 task instances … 12 popular Python repositories",  # ellipsis
    "Each instance pairs [an] issue ... the pull request",          # editorial insertion
    "the pull request that resolved it ... SWE-bench contains",     # segments in either order
])
def test_quotes_survive_formatting_differences(quote: str) -> None:
    assert _found(quote, PAGE)


# Tool output as extraction returned it, and the faithful quotes the p01 pilot had marked not_found.
@pytest.mark.parametrize(("text", "quote"), [
    (   # pypdf puts spaces inside words; the ligature is NFKC-folded
        ("32.67% of the successful patches involve “cheating” as the s olutions were directly\n"
         "provided in the issue report. SWE-bench Lite and SWE-Bench\nV eriﬁed. over 94% of the "
         "issues were created before LLM’ s knowl-\nedge cutoff dates"),
        ("the solutions were directly provided in the issue report ... SWE-Bench Verified. "
         "over 94% of the issues were created before LLM's knowledge cutoff dates"),
    ),
    (   # markdown bullets quoted as semicolons
        "- removed instances with images\n- removed instances that edit more than 1 file",
        "removed instances with images; removed instances that edit more than 1 file",
    ),
])
def test_quotes_survive_extraction_artifacts(text: str, quote: str) -> None:
    assert _found(quote, text)


@pytest.mark.parametrize("quote", [
    "SWE-bench contains 2,500 task instances",                      # altered number
    "SWE-bench contains 2,294 task instances from 12 repositories", # words added
    "",
    "...",
])
def test_altered_or_empty_quotes_are_not_found(quote: str) -> None:
    assert not _found(quote, PAGE)


def test_segments_must_come_from_one_text() -> None:
    assert not _found("task instances ... benchmark leaderboard", PAGE, "benchmark leaderboard")


def test_a_quote_records_the_most_complete_text_it_was_found_in() -> None:
    snippet = ToolText("snippet", identity_keys(url=PAPER), "SWE-bench contains 2,294 task instances")
    abstract = ToolText("abstract", identity_keys(arxiv_id="2310.06770"), "Each instance pairs a GitHub issue")
    index = ToolOutputIndex([snippet, abstract, _full(PAGE)])
    assert index.quote_access("SWE-bench contains 2,294 task instances") == "full_text"
    assert ToolOutputIndex([snippet]).quote_access("SWE-bench contains 2,294") == "snippet"
    assert ToolOutputIndex([snippet, abstract]).quote_access("pairs a GitHub issue") == "abstract"


@pytest.mark.parametrize(("cited", "returned"), [
    ({"url": "https://swebench.com"}, {"url": "https://www.swebench.com/"}),              # www, trailing slash
    ({"url": "https://openai.com/index/verified#results"}, {"url": "https://openai.com/index/verified"}),
    ({"url": "https://arxiv.org/pdf/2310.06770"}, {"url": "http://arxiv.org/abs/2310.06770v2"}),  # arXiv forms
    ({"url": "https://publisher.example/a", "doi": "10.1234/Agents.2024"}, {"url": "https://doi.org/10.1234/agents.2024"}),
    ({"url": "https://arxiv.org/abs/2310.06770"}, {"arxiv_id": "2310.06770"}),
])
def test_a_source_is_observed_when_a_tool_returned_it_as_an_item(cited: dict, returned: dict) -> None:
    index = ToolOutputIndex([ToolText("snippet", identity_keys(**returned), "title")])
    assert index.source_access(SourceRef(title="t", **cited)) == "snippet"


def test_a_source_only_linked_inside_another_page_was_not_read() -> None:
    # The first design counted a URL anywhere in tool output; now the research must have returned the source itself.
    index = ToolOutputIndex([_full("See https://example.org/report for the audit.", url="https://news.example/a")])
    assert index.source_access(SourceRef(url="https://example.org/report", title="t")) is None


def test_source_access_is_the_most_any_tool_returned() -> None:
    index = ToolOutputIndex([ToolText("snippet", identity_keys(url=PAPER), "t"),
                             ToolText("abstract", identity_keys(arxiv_id="2310.06770"), "t")])
    assert index.source_access(SourceRef(url=PAPER, title="t")) == "abstract"


def _result(*evidence: Evidence) -> ResearchResult:
    return ResearchResult(question_id="q1", question="What is SWE-bench?", conclusion="c", confidence=0.9,
                          claims=[Claim(id="c1", statement="s", confidence=0.9, evidence=list(evidence))])


def test_check_result_sets_every_check_and_overrules_the_model() -> None:
    source = SourceRef(url=PAPER, title="SWE-bench")
    unseen = SourceRef(url="https://example.org/invented", title="Invented")
    checked = check_result(_result(
        Evidence(source=source, excerpt="e", quote="contains 2,294 task instances", confidence=0.9),
        Evidence(source=source, excerpt="e", quote="contains 5,000 task instances", confidence=0.9,
                 quote_check="verified", quote_access="full_text"),
        Evidence(source=unseen, excerpt="e", confidence=0.9, source_check="observed", source_access="full_text"),
        Evidence(source=source, excerpt="e", quote="   ", confidence=0.9),
    ), [_full(PAGE)])
    items = checked.claims[0].evidence
    assert [(i.quote_check, i.quote_access) for i in items] == [
        ("verified", "full_text"), ("not_found", None), (None, None), (None, None)]
    assert [(i.source_check, i.source_access) for i in items] == [
        ("observed", "full_text"), ("observed", "full_text"), ("not_found", None), ("observed", "full_text")]


def test_checks_are_hidden_from_the_model_schema() -> None:
    schema = ResearchResult.model_json_schema()
    evidence = schema["$defs"]["Evidence"]["properties"]
    assert "quote" in evidence
    assert not {"quote_check", "quote_access", "source_check", "source_access"} & set(evidence)
    assert not {"searches", "pages_read", "unreached", "cut_off"} & set(schema["properties"])


def _evidence(access: str | None, *, quote_check: str | None = None, supports: bool = True) -> Evidence:
    return Evidence(source=SourceRef(url=PAPER, title="t"), excerpt="e", confidence=0.8, supports=supports,
                    source_access=access, source_check="observed" if access else "not_found", quote_check=quote_check)


def test_support_level_says_whether_a_statement_rests_on_something_read() -> None:
    def claim(*evidence: Evidence) -> Claim:
        return Claim(id="c", statement="s", confidence=0.8, evidence=list(evidence))

    assert support_level([claim(_evidence("full_text"))]) == "read"
    assert support_level([claim(_evidence("abstract", quote_check="verified"))]) == "read"
    assert support_level([claim(_evidence("snippet")), claim(_evidence("metadata"))]) == "shallow"
    assert support_level([claim(_evidence("full_text", quote_check="not_found"))]) == "shallow"
    assert support_level([claim(_evidence(None))]) == "shallow"
    assert support_level([claim(_evidence("full_text", supports=False))]) == "unsupported"
    assert support_level([]) == "unsupported"


def test_ledger_makes_claim_ids_unique_and_remaps_contradictions() -> None:
    ledger = EvidenceLedger()
    first = ResearchResult(
        question_id="q1", question="Q?", conclusion="first", confidence=0.6,
        claims=[Claim(id="c1", statement="a", confidence=0.6), Claim(id="c2", statement="b", confidence=0.6)],
        contradictions=[Contradiction(description="a vs b", claim_ids=["c1", "c2"])],
    )
    second = ResearchResult(
        question_id="q1", question="Q?", conclusion="second", confidence=0.8,
        claims=[Claim(id="c1", statement="c", confidence=0.8), Claim(id="c1", statement="d", confidence=0.8)],
        contradictions=[Contradiction(description="two copies", claim_ids=["c1", "q1/c2"])],
    )
    for result in (first, second, ResearchResult(question_id="q2", question="Q2?", conclusion="x", confidence=0.7,
                                                 claims=[Claim(id="c1", statement="e", confidence=0.7)])):
        ledger.add(result)
    assert [claim.id for claim in ledger.claims()] == ["q1/c1", "q1/c2", "q1/c1~2", "q1/c1~3", "q2/c1"]
    assert ledger.results["q1"][0].contradictions[0].claim_ids == ["q1/c1", "q1/c2"]
    assert ledger.results["q1"][1].contradictions[0].claim_ids == ["q1/c1~2", "q1/c1~3", "q1/c2"]
    assert first.claims[0].id == "c1"  # the caller's result is not changed
    assert ledger.question_ids_with_claims() == {"q1", "q2"}


def _cited(question_id: str, *urls: str) -> ResearchResult:
    return ResearchResult(question_id=question_id, question=f"{question_id}?", conclusion="c", confidence=0.8, claims=[
        Claim(id="c1", statement="s", confidence=0.8,
              evidence=[Evidence(source=SourceRef(url=url, title=url), excerpt="e", confidence=0.8) for url in urls])])


def test_source_ids_follow_question_order_and_survive_a_reload() -> None:
    ledger = EvidenceLedger()
    for result in (_cited("q10", "https://c.example"), _cited("q2", "https://a.example", "https://b.example")):
        ledger.add(result)
    assert [(row["id"], row["url"]) for row in ledger.source_table()] == [
        ("s1", "https://a.example/"), ("s2", "https://b.example/"), ("s3", "https://c.example/")]
    assert ledger.claim_source_ids() == {"q10/c1": frozenset({"s3"}), "q2/c1": frozenset({"s1", "s2"})}
    # Postgres does not keep JSON key order; a reloaded ledger numbers its sources the same way.
    reloaded = EvidenceLedger.from_json(json.loads(json.dumps(dict(reversed(ledger.to_json().items())))))
    assert reloaded.source_table() == ledger.source_table()
    assert reloaded.claim_ids() == ledger.claim_ids()


def test_prompt_view_keeps_the_checks_and_drops_bookkeeping() -> None:
    paper = SourceRef(url=PAPER, title="Paper", doi="10.1234/paper")
    ledger = EvidenceLedger()
    ledger.add(ResearchResult(
        question_id="q1", question="Q?", conclusion="c", confidence=0.4, unresolved=["still open"],
        searches=["swe-bench"], pages_read=[PAPER], cut_off="request limit",
        claims=[Claim(id="c1", statement="kept", confidence=0.4, evidence=[
            Evidence(source=paper, excerpt="paraphrase", quote="exact words", confidence=1.0,
                     quote_check="verified", quote_access="full_text", source_check="observed", source_access="full_text"),
            Evidence(source=paper, excerpt="x" * 400, confidence=1.0, source_access="snippet"),
            Evidence(source=paper, excerpt="kept beside it", quote="invented", quote_check="not_found",
                     confidence=1.0, supports=False),
        ])],
    ))
    view = ledger.prompt_view()
    assert view["sources"] == [{"id": "s1", "url": PAPER, "title": "Paper", "doi": "10.1234/paper",
                                "source_type": "unknown", "publication_status": "unknown", "access": "full_text"}]
    (research,) = view["research"]
    assert research["unresolved"] == ["still open"] and research["cut_off"] == "request limit"
    assert not {"searches", "pages_read", "unreached"} & set(research)
    read, glimpsed, invented = research["claims"][0]["evidence"]
    assert read == {"source_id": "s1", "quote": "exact words", "confidence": 1.0, "quote_check": "verified",
                    "quote_access": "full_text", "source_check": "observed", "source_access": "full_text"}
    assert glimpsed["excerpt"] == "x" * 297 + "..." and glimpsed["source_access"] == "snippet"
    assert invented["excerpt"] == "kept beside it" and invented["supports"] is False


def test_citation_problems_find_unknown_claims_and_stray_inline_citations() -> None:
    ledger = EvidenceLedger()
    ledger.add(ResearchResult(question_id="q1", question="Q?", conclusion="c", confidence=0.7, claims=[
        Claim(id="c1", statement="X is true", confidence=0.7, evidence=[
            Evidence(source=SourceRef(url="https://for.example", title="for"), excerpt="yes", confidence=0.7),
            Evidence(source=SourceRef(url="https://against.example", title="against"), excerpt="no",
                     confidence=0.7, supports=False),
        ]),
    ]))

    def report(answer: str, *claim_ids: str) -> FinalReport:
        return FinalReport(title="t", executive_summary="", answer=answer,
                           claims=[ReportClaim(statement="X", claim_ids=list(claim_ids))])

    assert citation_problems(report("X is true [s1].", "q1/c1"), ledger) == []
    # Contradicting evidence does not back an inline citation.
    assert citation_problems(report("X is true [s1, s2].", "q1/c1"), ledger) == [
        "inline citations with no listed claim behind them: s2"]
    assert citation_problems(report("X.", "q1/c9"), ledger) == ["unknown claim IDs: q1/c9"]


def test_an_unlisted_label_becomes_unknown_and_a_source_needs_an_identity() -> None:
    source = SourceRef(url="https://a.example", title="t", source_type="dataset", publication_status="magazine")
    assert (source.source_type, source.publication_status) == ("unknown", "unknown")
    assert SourceRef(title="t", doi="10.1/x").url is None
    with pytest.raises(ValidationError, match="url, doi, or arxiv_id"):
        SourceRef(title="no identity")

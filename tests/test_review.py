from __future__ import annotations

import pytest

from research_loop.async_orchestrator import review_reasons
from research_loop.ledger import EvidenceLedger
from research_loop.schemas import (
    Claim,
    ClaimCheck,
    FinalReport,
    ReportClaim,
    ResearchResult,
    VerificationReport,
)


def _ledger(claims: int = 1) -> EvidenceLedger:
    ledger = EvidenceLedger()
    ledger.add(ResearchResult(question_id="q1", question="Q?", conclusion="c", confidence=0.9, claims=[
        Claim(id=f"c{index}", statement="s", confidence=0.9) for index in range(1, claims + 1)
    ]))
    return ledger


def _report(*claim_ids: str) -> FinalReport:
    return FinalReport(answer="a", claims=[ReportClaim(statement="s", claim_ids=list(claim_ids))])


def _check(supported: bool = True, severity: str = "none") -> ClaimCheck:
    return ClaimCheck(statement="s", claim_ids=["q1/c1"], supported=supported, severity=severity, explanation="e")


def test_a_supported_grounded_report_needs_no_review() -> None:
    assert review_reasons(_report("q1/c1"), VerificationReport(checks=[_check()]), _ledger()) == []


@pytest.mark.parametrize(("report", "verification", "ledger", "expected"), [
    (FinalReport(answer="a"), VerificationReport(), _ledger(0), ["no evidence claims were gathered"]),
    (FinalReport(answer="a"), VerificationReport(), _ledger(), ["the report cites no evidence claims"]),
    (_report("q1/c1"), VerificationReport(), _ledger(), ["the verifier checked none of the report's statements"]),
    (_report("q1/c1"), VerificationReport(checks=[_check(), _check(False, "minor"), _check(False, "major")]), _ledger(),
     ["2 of 3 verifier checks are unsupported", "1 verifier check is rated major"]),
    (_report("q1/c1"), VerificationReport(checks=[_check(True, "major")], needs_research=True), _ledger(),
     ["1 verifier check is rated major", "the verifier still asked for more research"]),
    (FinalReport(answer="a", claims=[ReportClaim(statement="Verified has 500 tasks.", claim_ids=["q1/c1"]),
                                     ReportClaim(statement="Scores are inflated.", claim_ids=["q1/c2"])]),
     VerificationReport(checks=[_check()]), _ledger(2), ["the verifier checked 1 of the report's 2 statements"]),
], ids=["no-evidence", "uncited", "unassessed", "unsupported", "major-and-open", "partly-assessed"])

def test_unresolved_results_are_named(report, verification, ledger, expected) -> None:
    assert review_reasons(report, verification, ledger) == expected


def test_merged_split_and_reworded_checks_cover_their_statements() -> None:
    report = FinalReport(answer="a", claims=[
        ReportClaim(statement="Verified has 500 tasks.", claim_ids=["q1/c1"]),
        ReportClaim(statement="It was curated in 2024.", claim_ids=["q1/c2"]),         # merged into one check with c1
        ReportClaim(statement="Gains do not transfer.", claim_ids=["q1/c3", "q1/c4"]),  # split over two checks
        ReportClaim(statement="Scores are inflated!", claim_ids=[]),                   # same words, no claims
    ])
    checks = [ClaimCheck(statement=statement, claim_ids=ids, supported=True, severity="none", explanation="e")
              for statement, ids in (("Verified: 500 tasks, curated 2024", ["q1/c1", "q1/c2"]),
                                     ("On freelance tasks", ["q1/c3"]), ("In C#", ["q1/c4"]),
                                     ("scores are inflated", []))]
    assert review_reasons(report, VerificationReport(checks=checks), _ledger(4)) == []


def _synthesis(answer: str, *claim_ids: str, caveats: tuple[str, ...] = ()) -> FinalReport:
    return FinalReport(answer=answer, caveats=list(caveats),
                       claims=[ReportClaim(statement="s", claim_ids=list(claim_ids))] if claim_ids else [])


@pytest.mark.parametrize(("report", "dropped"), [
    (_synthesis("Verified has 500 tasks [s1, s2].", "q1/c1"), None),
    (_synthesis("Verified has 500 tasks [s1;s2]; see the S3 bucket s3 docs.", "q1/c1"), None),  # bare s3 is not a citation
    (_synthesis("Scores are inflated [s3].", "q1/c1"), "s3"),  # a real source, but not behind a listed claim
    (_synthesis("Scores are inflated [s45].", "q1/c1"), "s45"),  # no such source
    (_synthesis("Clean answer.", "q1/c1", caveats=("Vendor claim [s9].",)), "s9"),  # caveats are checked too
], ids=["cited", "separators", "not-behind-claims", "unknown", "caveat"])
def test_synthesis_drops_inline_citations_the_listed_claims_do_not_back(report, dropped) -> None:
    from types import SimpleNamespace

    from research_loop.agents import (
        DROPPED_CITATIONS_CAVEAT,
        LedgerRefs,
        _report_cites_ledger_claims,
    )

    refs = LedgerRefs(claim_ids=frozenset({"q1/c1", "q2/c1"}), question_ids=frozenset({"q1", "q2"}),
                      claim_sources={"q1/c1": frozenset({"s1", "s2"}), "q2/c1": frozenset({"s3"})})
    kept = _report_cites_ledger_claims(SimpleNamespace(deps=refs, last_attempt=False), report)
    if dropped is None:
        assert kept is report
    else:
        assert f"[{dropped}]" not in kept.answer + "".join(kept.caveats[:-1])
        assert kept.caveats[-1] == DROPPED_CITATIONS_CAVEAT.format(ids=dropped)


def test_stray_citations_beside_an_unknown_claim_get_a_retry_that_names_both() -> None:
    from types import SimpleNamespace

    from pydantic_ai import ModelRetry

    from research_loop.agents import LedgerRefs, _report_cites_ledger_claims

    refs = LedgerRefs(claim_ids=frozenset({"q1/c1"}), question_ids=frozenset({"q1"}),
                      claim_sources={"q1/c1": frozenset({"s1"})})
    report = _synthesis("Scores are inflated [s3].", "q1/c1", "q9/c9")
    with pytest.raises(ModelRetry, match=r"(?s)q9/c9.*name no source behind the claim IDs listed in `claims`: s3\."):
        _report_cites_ledger_claims(SimpleNamespace(deps=refs, last_attempt=False), report)


def test_stray_citations_are_dropped_in_citation_order_and_named_in_a_caveat() -> None:
    from types import SimpleNamespace

    from research_loop.agents import LedgerRefs, _report_cites_ledger_claims

    refs = LedgerRefs(claim_ids=frozenset({"q1/c1"}), question_ids=frozenset({"q1"}),
                      claim_sources={"q1/c1": frozenset({"s1", "s2"})})
    report = _synthesis("Verified has 500 tasks [s1, s45]. Scores rose [s9].", "q1/c1", caveats=("Vendor claim [s2].",))
    kept = _report_cites_ledger_claims(SimpleNamespace(deps=refs, last_attempt=True), report)
    assert kept.answer == "Verified has 500 tasks [s1]. Scores rose."
    assert kept.caveats[0] == "Vendor claim [s2]." and "s9, s45" in kept.caveats[1]


def test_the_executive_summary_is_checked_for_stray_citations_too() -> None:
    from types import SimpleNamespace

    from research_loop.agents import LedgerRefs, _report_cites_ledger_claims

    refs = LedgerRefs(claim_ids=frozenset({"q1/c1"}), question_ids=frozenset({"q1"}),
                      claim_sources={"q1/c1": frozenset({"s1"})})
    report = _synthesis("Body [s1].", "q1/c1").model_copy(update={"executive_summary": "Gist [s1, s7]."})
    kept = _report_cites_ledger_claims(SimpleNamespace(deps=refs, last_attempt=False), report)
    assert kept.executive_summary == "Gist [s1]." and "s7" in kept.caveats[-1]

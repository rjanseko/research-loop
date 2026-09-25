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
], ids=["no-evidence", "uncited", "unassessed", "unsupported", "major-and-open"])
def test_unresolved_results_are_named(report, verification, ledger, expected) -> None:
    assert review_reasons(report, verification, ledger) == expected


def _synthesis(answer: str, *claim_ids: str, caveats: tuple[str, ...] = ()) -> FinalReport:
    return FinalReport(answer=answer, caveats=list(caveats),
                       claims=[ReportClaim(statement="s", claim_ids=list(claim_ids))] if claim_ids else [])


@pytest.mark.parametrize(("report", "problem"), [
    (_synthesis("Verified has 500 tasks [s1, s2].", "q1/c1"), None),
    (_synthesis("Verified has 500 tasks [s1;s2]; see the S3 bucket s3 docs.", "q1/c1"), None),  # bare s3 is not a citation
    (_synthesis("Scores are inflated [s3].", "q1/c1"), "s3"),  # a real source, but not behind a listed claim
    (_synthesis("Scores are inflated [s45].", "q1/c1"), "s45"),  # no such source
    (_synthesis("Clean answer.", "q1/c1", caveats=("Vendor claim [s9].",)), "s9"),  # caveats are checked too
], ids=["cited", "separators", "not-behind-claims", "unknown", "caveat"])
def test_synthesis_retries_inline_citations_the_listed_claims_do_not_back(report, problem) -> None:
    from types import SimpleNamespace

    from pydantic_ai import ModelRetry

    from research_loop.agents import LedgerRefs, _report_cites_ledger_claims

    refs = LedgerRefs(claim_ids=frozenset({"q1/c1", "q2/c1"}), question_ids=frozenset({"q1", "q2"}),
                      claim_sources={"q1/c1": frozenset({"s1", "s2"}), "q2/c1": frozenset({"s3"})})
    ctx = SimpleNamespace(deps=refs, last_attempt=False)
    if problem is None:
        assert _report_cites_ledger_claims(ctx, report) is report
    else:
        with pytest.raises(ModelRetry, match=f"name no source behind the claim IDs listed in `claims`: {problem}\\."):
            _report_cites_ledger_claims(ctx, report)



def test_last_synthesis_attempt_drops_stray_citations_instead_of_failing_the_run() -> None:
    from types import SimpleNamespace

    from research_loop.agents import LedgerRefs, _report_cites_ledger_claims

    refs = LedgerRefs(claim_ids=frozenset({"q1/c1"}), question_ids=frozenset({"q1"}),
                      claim_sources={"q1/c1": frozenset({"s1", "s2"})})
    report = _synthesis("Verified has 500 tasks [s1, s45]. Scores rose [s9].", "q1/c1", caveats=("Vendor claim [s2].",))
    kept = _report_cites_ledger_claims(SimpleNamespace(deps=refs, last_attempt=True), report)
    assert kept.answer == "Verified has 500 tasks [s1]. Scores rose."
    assert kept.caveats == ["Vendor claim [s2]."]

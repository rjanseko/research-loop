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

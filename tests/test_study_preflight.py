"""scripts/study_preflight.py: a stored job's finishing prompts against the settings study's token limits."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from research_loop import get_policy
from research_loop.ledger import EvidenceLedger
from research_loop.policy import retry_token_budget
from research_loop.schemas import (
    Claim,
    Evidence,
    FinalReport,
    ReportClaim,
    ResearchPlan,
    ResearchResult,
    ResearchRole,
    SourceRef,
)

_SPEC = importlib.util.spec_from_file_location("study_preflight", Path(__file__).parents[1] / "scripts" / "study_preflight.py")
study_preflight = importlib.util.module_from_spec(_SPEC)
sys.modules["study_preflight"] = study_preflight  # its dataclasses look their module up while being defined
_SPEC.loader.exec_module(study_preflight)

_PLAN = ResearchPlan(objective="o", questions=[])


def _ledger(claims: int, excerpt_chars: int = 300) -> EvidenceLedger:
    ledger = EvidenceLedger()
    for q in range(5):
        ledger.add(ResearchResult(
            question_id=f"q{q}", question="q", conclusion="c", confidence=0.5, search_queries_used=["query"] * 20,
            claims=[Claim(id=f"c{i}", statement="s" * 200, confidence=0.5, evidence=[Evidence(
                source=SourceRef(url=f"https://example.org/{q}/{i}", title="t"), excerpt="x" * excerpt_chars,
                confidence=0.5)]) for i in range(claims)]))
    return ledger


def test_a_ledger_the_preset_refuses_fits_the_study_limits() -> None:
    ledger = _ledger(claims=60)
    preset = study_preflight.check_job(get_policy("value"), "o", _PLAN, ledger, {}, None)
    assert not preset[0].fits and preset[0].role is ResearchRole.GAP_ANALYST
    study = study_preflight.check_job(study_preflight.study_policy("value"), "o", _PLAN, ledger, {}, None)
    assert all(check.fits for check in study)
    assert "NO" in study_preflight.render(preset) and "NO" not in study_preflight.render(study)


def test_verification_without_a_report_is_bounded_by_every_claim_and_a_full_answer() -> None:
    policy = study_preflight.study_policy("value")
    ledger = _ledger(claims=3)
    report = FinalReport(answer="a" * 2000, claims=[ReportClaim(statement="s", claim_ids=["q0/c0"])])
    bound = study_preflight.check_job(policy, "o", _PLAN, ledger, {}, None)[2]
    exact = study_preflight.check_job(policy, "o", _PLAN, ledger, {}, report)[2]
    assert exact.needed < bound.needed
    assert exact.needed == retry_token_budget(
        study_preflight.verification_prompt("o", report, ledger, {}), policy.for_role(ResearchRole.VERIFIER),
        ResearchRole.VERIFIER)


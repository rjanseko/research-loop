"""Auditing whether quotes support report statements, offline: the auditor is a scripted model."""
from __future__ import annotations

import json
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from research_loop.audit import AUDIT_VERSION, NO_QUOTE, audit, audit_items, audit_row
from research_loop.config import Settings
from research_loop.evidence import EvidenceLedger
from research_loop.schemas import (
    Claim,
    Evidence,
    FinalReport,
    ReportClaim,
    ResearchResult,
    SourceRef,
)
from research_loop.study_budget import BUDGET_POLICY_VERSION, StudyBudget

pytestmark = pytest.mark.filterwarnings("ignore::pydantic_ai.exceptions.CostNotFoundWarning")
QUOTE = "algorithm effectiveness is problem-specific."


def _evidence(quote: str | None, check: str | None, *, supports: bool = True) -> Evidence:
    return Evidence(source=SourceRef(url="https://example.org/bench", title="Benchmark"), excerpt="summary",
                    quote=quote, quote_check=check, supports=supports, source_access="full_text", confidence=0.8)


def _ledger() -> EvidenceLedger:
    ledger = EvidenceLedger()
    ledger.add(ResearchResult(question_id="q1", question="Q?", conclusion="c", confidence=0.8, claims=[
        Claim(id="c1", statement="No algorithm wins everywhere.", confidence=0.8,
              evidence=[_evidence(QUOTE, "verified"), _evidence(QUOTE, "verified"),
                        _evidence("words from another page", "misattributed"),
                        _evidence("a quote that says the opposite", "verified", supports=False)]),
        Claim(id="c2", statement="PSO is not a machine learning model.", confidence=0.8,
              evidence=[_evidence(None, None)]),
    ]))
    return ledger


REPORT = FinalReport(title="T", executive_summary="S", answer="A", claims=[
    ReportClaim(statement="Algorithm effectiveness depends on the problem.", claim_ids=["q1/c1"]),
    ReportClaim(statement="PSO is not a machine learning model by itself.", claim_ids=["q1/c2"]),
])


def _auditor(verdicts_per_attempt: list[list[dict]], prompts: list[dict]) -> FunctionModel:
    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        first = messages[0]
        assert isinstance(first, ModelRequest)
        prompts.append(json.loads(next(p.content for p in first.parts if isinstance(p, UserPromptPart))))
        attempt = sum(isinstance(m, ModelResponse) for m in messages)
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"verdicts": verdicts_per_attempt[attempt]})])

    return FunctionModel(respond)


def test_a_statement_gets_only_the_verified_supporting_quotes_behind_its_claims() -> None:
    first, second = audit_items(REPORT, _ledger())
    # Deduplicated; the misattributed quote and the evidence against the claim are left out.
    assert first["quotes"] == [{"claim": "q1/c1", "source": "Benchmark", "quote": QUOTE}]
    assert (second["id"], second["quotes"]) == ("a2", [])


async def test_only_statements_with_quotes_are_sent_and_every_one_gets_a_verdict() -> None:
    prompts: list[dict] = []
    model = _auditor([
        [],  # a reply that leaves a statement out is retried
        [{"id": "a1", "verdict": "partial", "reason": "The quote does not name a problem class."}],
    ], prompts)
    record = await audit(REPORT, _ledger(), "Which algorithm is best?", uuid4(), Settings(), model=model,
                         evidence_version=7)
    assert record.status == "succeeded" and len(prompts) == 2
    assert [item["id"] for item in prompts[0]["statements"]] == ["a1"]
    assert prompts[0]["statements"][0]["quotes"] == [{"source": "Benchmark", "quote": QUOTE}]
    assert [(v["id"], v["verdict"]) for v in record.verdicts] == [("a1", "partial"), ("a2", NO_QUOTE)]
    assert record.counts == {"partial": 1, NO_QUOTE: 1}
    row = audit_row(record)
    assert (row["audit_version"], row["evidence_version"], row["counts"]) == (AUDIT_VERSION, 7, record.counts)
    assert row["verdicts"][0]["claim_ids"] == ["q1/c1"] and row["verdicts"][0]["quotes"] == 1


async def test_a_report_with_no_quotes_makes_no_call() -> None:
    def refuse(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise AssertionError("nothing should be sent")

    report = REPORT.model_copy(update={"claims": REPORT.claims[1:]})
    record = await audit(report, _ledger(), "Q?", uuid4(), Settings(), model=FunctionModel(refuse))
    assert record.status == "succeeded" and record.counts == {NO_QUOTE: 1} and record.cost_usd is None


async def test_a_failed_audit_is_recorded_with_its_cap() -> None:
    def fail(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise RuntimeError("provider down")

    budget = StudyBudget(Decimal("0.50"))
    record = await audit(REPORT, _ledger(), "Q?", uuid4(), Settings(), model=FunctionModel(fail), budget=budget)
    row = audit_row(record)
    assert (row["status"], row["error"]["type"], row["verdicts"]) == ("failed", "RuntimeError", [])
    assert (row["budget_cap_usd"], row["budget_policy"]) == (Decimal("0.50"), BUDGET_POLICY_VERSION)

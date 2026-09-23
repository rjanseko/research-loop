from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from typing import Any

import pytest

pytest.importorskip("pydantic_graph")

from research_loop.async_orchestrator import AsyncResearchLoop, ResearchConfig
from research_loop.orchestrator import ResearchLoop
from research_loop.parity import parity_differences
from research_loop.policy import ModelPolicy, ModelRoute
from research_loop.repository import InMemoryResearchRepository
from research_loop.schemas import (
    Claim,
    ClaimCheck,
    Evidence,
    FinalReport,
    Gap,
    GapAnalysis,
    ReportClaim,
    ResearchPlan,
    ResearchQuestion,
    ResearchResult,
    ResearchRole,
    SourceRef,
    VerificationReport,
)



def _policy() -> ModelPolicy:
    route = ModelRoute("test:model", 20, 40, 100_000, None, None)
    return ModelPolicy(
        "parity-test",
        {role: route for role in ResearchRole},
        cheap_scout=route,
        alternate_deep_dive=route,
        planner_question_range=(2, 2),
    )


def _result(question_id: str, *, confidence: float, suffix: str) -> ResearchResult:
    source = SourceRef(
        url="https://example.com/source",
        title="Synthetic source",
        source_type="primary",
    )
    return ResearchResult(
        question_id=question_id,
        question=f"Question {question_id}",
        conclusion=f"Conclusion {question_id} {suffix}",
        claims=[
            Claim(
                id=f"{question_id}.{suffix}",
                statement=f"Claim {question_id} {suffix}",
                evidence=[Evidence(source=source, excerpt="synthetic evidence", confidence=1.0)],
                confidence=confidence,
            )
        ],
        confidence=confidence,
    )


class _DeterministicMixin:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._verification_calls = 0
        self.call_trace: list[tuple[str, str | None, int]] = []

    async def _run_agent(self, **kwargs: Any) -> Any:
        role: ResearchRole = kwargs["role"]
        prompt = json.loads(kwargs["prompt"])
        question_id = kwargs.get("question_id")
        attempt = kwargs.get("attempt", 0)
        self.call_trace.append((role.value, question_id, attempt))

        if role is ResearchRole.PLANNER:
            return ResearchPlan(
                objective=prompt["objective"],
                questions=[
                    ResearchQuestion(id="q1", question="Question q1", priority=4),
                    ResearchQuestion(id="q2", question="Question q2", priority=3),
                ],
            )
        if role is ResearchRole.SCOUT:
            qid = prompt["question"]["id"]
            # Force completion order to differ from plan order. The graph join must
            # restore deterministic input order before mutating the evidence ledger.
            if qid == "q1":
                await asyncio.sleep(0.01)
            return _result(qid, confidence=0.95 if qid == "q1" else 0.40, suffix="scout")
        if role is ResearchRole.GAP_ANALYST:
            return GapAnalysis()
        if role is ResearchRole.DEEP_DIVE:
            qid = prompt["question"]["id"]
            return _result(qid, confidence=0.98, suffix=f"deep{attempt}")
        if role is ResearchRole.SYNTHESIZER:
            claim_ids = sorted(
                claim["id"]
                for result in prompt["evidence"]
                for claim in result.get("claims", [])
            )
            return FinalReport(
                answer="Synthetic final answer",
                claims=[ReportClaim(statement="Synthetic final answer", claim_ids=claim_ids)],
            )
        if role is ResearchRole.VERIFIER:
            self._verification_calls += 1
            if self._verification_calls == 1:
                return VerificationReport(
                    checks=[
                        ClaimCheck(
                            statement="Synthetic final answer",
                            claim_ids=prompt["report"]["claims"][0]["claim_ids"],
                            supported=False,
                            severity="minor",
                            explanation="Exercise the verification research loop.",
                        )
                    ],
                    needs_research=True,
                    followups=[
                        Gap(
                            question_id="q1",
                            reason="missing_evidence",
                            followup="Add one independent check",
                            severity=4,
                        )
                    ],
                )
            return VerificationReport(
                checks=[
                    ClaimCheck(
                        statement="Synthetic final answer",
                        claim_ids=prompt["report"]["claims"][0]["claim_ids"],
                        supported=True,
                        severity="none",
                        explanation="Supported after follow-up research.",
                    )
                ]
            )
        raise AssertionError(f"unexpected role: {role}")


class DeterministicGraphLoop(_DeterministicMixin, ResearchLoop):
    pass


class DeterministicAsyncLoop(_DeterministicMixin, AsyncResearchLoop):
    pass


@pytest.mark.asyncio
async def test_graph_matches_legacy_async_semantics() -> None:
    config = ResearchConfig(max_verification_rounds=2, min_scout_confidence=0.70)
    graph_repo = InMemoryResearchRepository()
    legacy_repo = InMemoryResearchRepository()
    graph = DeterministicGraphLoop(_policy(), config, graph_repo)
    legacy = DeterministicAsyncLoop(_policy(), config, legacy_repo)

    graph_outcome = await graph.run("Synthetic parity objective")
    legacy_outcome = await legacy.run("Synthetic parity objective")

    assert parity_differences(graph_outcome, legacy_outcome) == []
    assert sorted(graph.call_trace) == sorted(legacy.call_trace)
    assert len(graph_outcome.ledger.for_question("q1")) == 2
    assert len(graph_outcome.ledger.for_question("q2")) == 2
    assert graph_repo.jobs[graph_outcome.job_id]["config"]["orchestrator"] == {
        "kind": "pydantic-graph",
        "graph_version": "research-graph-v1",
    }
    assert legacy_repo.jobs[legacy_outcome.job_id]["config"]["orchestrator"]["kind"] == "async-legacy"


@pytest.mark.asyncio
async def test_parity_catches_changed_evidence_not_just_claim_ids() -> None:
    from research_loop.ledger import EvidenceLedger

    outcome = await DeterministicGraphLoop(_policy(), ResearchConfig(max_verification_rounds=2)).run("Parity probe")
    changed_ledger = EvidenceLedger.from_json(outcome.ledger.to_json())
    result = changed_ledger.for_question("q1")[0]
    claim = result.claims[0]
    edited = claim.model_copy(update={
        "statement": "A different statement",
        "evidence": [claim.evidence[0].model_copy(update={"excerpt": "a different excerpt"})],
    })
    changed_ledger.results["q1"][0] = result.model_copy(update={"claims": [edited]})
    changed = replace(outcome, ledger=changed_ledger)

    assert parity_differences(outcome, outcome) == []
    assert parity_differences(outcome, changed) == ["evidence"]

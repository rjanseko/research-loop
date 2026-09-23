from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .async_orchestrator import ResearchOutcome


@dataclass(frozen=True)
class ParityFingerprint:
    """Order-insensitive semantic fingerprint for orchestration parity checks."""

    plan: tuple[tuple[Any, ...], ...]
    evidence: tuple[tuple[Any, ...], ...]
    report: dict[str, Any]
    verification: dict[str, Any]


def outcome_fingerprint(outcome: ResearchOutcome) -> ParityFingerprint:
    plan = tuple(
        sorted(
            (
                q.id,
                q.question,
                q.priority,
                q.requires_primary_sources,
                q.requires_multimodal,
                q.expected_difficulty,
            )
            for q in outcome.plan.questions
        )
    )
    evidence = tuple(
        sorted(
            (
                result.question_id,
                result.question,
                result.conclusion,
                result.confidence,
                tuple(sorted(claim.id for claim in result.claims)),
                tuple(sorted(result.unresolved_questions)),
            )
            for result in outcome.ledger.all()
        )
    )
    return ParityFingerprint(
        plan=plan,
        evidence=evidence,
        report=outcome.report.model_dump(mode="json"),
        verification=outcome.verification.model_dump(mode="json"),
    )


def parity_differences(
    left: ResearchOutcome,
    right: ResearchOutcome,
) -> list[str]:
    a = outcome_fingerprint(left)
    b = outcome_fingerprint(right)
    differences: list[str] = []
    if a.plan != b.plan:
        differences.append("plan")
    if a.evidence != b.evidence:
        differences.append("evidence")
    if a.report != b.report:
        differences.append("report")
    if a.verification != b.verification:
        differences.append("verification")
    return differences

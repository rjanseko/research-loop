from __future__ import annotations

from dataclasses import dataclass, field

from .schemas import Claim, ResearchResult, SourceRef


@dataclass
class EvidenceLedger:
    """Append-only by question so escalations preserve provenance and disagreement."""

    results: dict[str, list[ResearchResult]] = field(default_factory=dict)

    def add(self, result: ResearchResult) -> None:
        self.results.setdefault(result.question_id, []).append(result)

    def all(self) -> list[ResearchResult]:
        return [item for group in self.results.values() for item in group]

    def for_question(self, question_id: str) -> list[ResearchResult]:
        return list(self.results.get(question_id, []))

    def claims(self) -> list[Claim]:
        return [claim for result in self.all() for claim in result.claims]

    def claim_ids(self) -> set[str]:
        return {claim.id for claim in self.claims()}

    def sources(self) -> list[SourceRef]:
        return [e.source for claim in self.claims() for e in claim.evidence]

    def claim_count(self) -> int:
        return len(self.claims())

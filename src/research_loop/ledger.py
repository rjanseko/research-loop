from __future__ import annotations

from dataclasses import dataclass, field

from .schemas import Claim, ResearchResult, SourceRef


@dataclass
class EvidenceLedger:
    """Append-only by question so escalations preserve provenance and disagreement."""

    results: dict[str, list[ResearchResult]] = field(default_factory=dict)

    def add(self, result: ResearchResult) -> None:
        self.results.setdefault(result.question_id, []).append(self._with_unique_claim_ids(result))

    def _with_unique_claim_ids(self, result: ResearchResult) -> ResearchResult:
        """Rename claims to '<question>/<claim>' so every ID is unique across the ledger.

        Workers number claims independently (a scout and a deep dive on one question both
        emit c1, c2, ...), so repeated IDs get a '~2', '~3' suffix. Contradictions within
        the result are remapped to the new IDs.
        """
        taken = self.claim_ids()
        prefix = f"{result.question_id}/"
        renamed: dict[str, str] = {}
        claims: list[Claim] = []
        for claim in result.claims:
            base = claim.id if claim.id.startswith(prefix) else prefix + claim.id
            new_id, suffix = base, 2
            while new_id in taken:
                new_id, suffix = f"{base}~{suffix}", suffix + 1
            taken.add(new_id)
            renamed.setdefault(claim.id, new_id)
            claims.append(claim.model_copy(update={"id": new_id}))
        contradictions = [
            item.model_copy(update={"claim_ids": [renamed.get(claim_id, claim_id) for claim_id in item.claim_ids]})
            for item in result.contradictions
        ]
        return result.model_copy(update={"claims": claims, "contradictions": contradictions})

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

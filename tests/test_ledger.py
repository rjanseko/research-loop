from research_loop.ledger import EvidenceLedger
from research_loop.schemas import ResearchResult


def test_ledger_preserves_multiple_attempts_for_same_question():
    ledger = EvidenceLedger()
    a = ResearchResult(question_id="q1", question="Q?", conclusion="a", confidence=0.5)
    b = ResearchResult(question_id="q1", question="Q?", conclusion="b", confidence=0.9)
    ledger.add(a)
    ledger.add(b)
    assert [x.conclusion for x in ledger.for_question("q1")] == ["a", "b"]


def test_ledger_makes_claim_ids_unique_and_remaps_contradictions():
    from research_loop.schemas import Claim, Contradiction

    ledger = EvidenceLedger()
    scout = ResearchResult(
        question_id="q1", question="Q?", conclusion="scout", confidence=0.6,
        claims=[Claim(id="c1", statement="a", confidence=0.6), Claim(id="c2", statement="b", confidence=0.6)],
        contradictions=[Contradiction(description="a vs b", claim_ids=["c1", "c2"])],
    )
    deep_dive = ResearchResult(
        question_id="q1", question="Q?", conclusion="deep", confidence=0.8,
        claims=[Claim(id="c1", statement="c", confidence=0.8)],
        contradictions=[Contradiction(description="c vs earlier", claim_ids=["c1", "q1/c2"])],
    )
    other = ResearchResult(question_id="q2", question="Q2?", conclusion="x", confidence=0.7,
                           claims=[Claim(id="c1", statement="d", confidence=0.7)])
    for result in (scout, deep_dive, other):
        ledger.add(result)

    assert [claim.id for claim in ledger.claims()] == ["q1/c1", "q1/c2", "q1/c1~2", "q2/c1"]
    first, second = ledger.for_question("q1")
    assert first.contradictions[0].claim_ids == ["q1/c1", "q1/c2"]
    # Within a result, a claim's own ID wins; an already-prefixed reference is kept as given.
    assert second.contradictions[0].claim_ids == ["q1/c1~2", "q1/c2"]
    assert scout.claims[0].id == "c1"  # the worker's object is not mutated


def test_ledger_round_trips_through_json_without_renaming_claims():
    from research_loop.schemas import Claim

    ledger = EvidenceLedger()
    for conclusion in ("scout", "deep dive"):
        ledger.add(ResearchResult(question_id="q1", question="Q?", conclusion=conclusion, confidence=0.8,
                                  claims=[Claim(id="c1", statement="s", confidence=0.8)]))
    restored = EvidenceLedger.from_json(ledger.to_json())
    assert restored.claim_ids() == ledger.claim_ids() == {"q1/c1", "q1/c1~2"}
    assert [result.conclusion for result in restored.for_question("q1")] == ["scout", "deep dive"]

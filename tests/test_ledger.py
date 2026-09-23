from research_loop.ledger import EvidenceLedger
from research_loop.schemas import ResearchResult


def test_ledger_preserves_multiple_attempts_for_same_question():
    ledger = EvidenceLedger()
    a = ResearchResult(question_id="q1", question="Q?", conclusion="a", confidence=0.5)
    b = ResearchResult(question_id="q1", question="Q?", conclusion="b", confidence=0.9)
    ledger.add(a)
    ledger.add(b)
    assert [x.conclusion for x in ledger.for_question("q1")] == ["a", "b"]

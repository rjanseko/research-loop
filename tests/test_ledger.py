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


def test_two_claims_with_one_local_id_expand_the_contradiction():
    from research_loop.schemas import Claim, Contradiction

    ledger = EvidenceLedger()
    ledger.add(ResearchResult(
        question_id="q1", question="Q?", conclusion="both", confidence=0.5,
        claims=[
            Claim(id="c1", statement="first", confidence=0.5),
            Claim(id="c1", statement="second", confidence=0.5),
        ],
        contradictions=[Contradiction(description="two copies", claim_ids=["c1"])],
    ))
    assert [claim.id for claim in ledger.claims()] == ["q1/c1", "q1/c1~2"]
    assert ledger.for_question("q1")[0].contradictions[0].claim_ids == ["q1/c1", "q1/c1~2"]


def test_prompt_view_compacts_the_ledger_and_can_keep_only_cited_claims():
    from research_loop.schemas import Claim, Contradiction, Evidence, SourceRef

    paper = SourceRef(
        url="https://example.org/paper", title="Paper", is_retracted=False,
        doi="10.1234/paper", provider="openalex", accessed_at="2026-09-23",
    )
    blog = SourceRef(url="https://example.org/blog", title="Blog", source_type="secondary")
    cited = ResearchResult(
        question_id="q1", question="Q?", conclusion="cited", confidence=0.4,
        claims=[
            Claim(id="c1", statement="kept", confidence=0.4, evidence=[
                Evidence(source=paper, excerpt="same paper", confidence=0.0, supports=False,
                         quote_check="not_found", source_check="observed"),
                Evidence(source=paper, excerpt="again", quote="exact words", confidence=1.0),
                Evidence(
                    source=paper,
                    excerpt="x" * 400,
                    confidence=1.0,
                ),
                Evidence(source=paper, excerpt="paraphrase", quote="invented words", quote_check="not_found",
                         confidence=1.0),
            ]),
            Claim(id="c2", statement="dropped from the verifier", confidence=0.2, evidence=[
                Evidence(source=blog, excerpt="blog says", confidence=0.5, source_check="not_found"),
            ]),
        ],
        contradictions=[Contradiction(description="disagreement", claim_ids=["c1", "c2"])],
        unresolved_questions=["still open"],
        suggested_followups=["look further"],
        search_queries_used=["swe-bench"],
    )
    uncited = ResearchResult(
        question_id="q2", question="Other?", conclusion="uncited", confidence=0.8,
        claims=[Claim(id="c1", statement="elsewhere", confidence=0.8, evidence=[
            Evidence(source=blog, excerpt="blog says", confidence=0.5),
        ])],
    )
    ledger = EvidenceLedger()
    ledger.add(cited)
    ledger.add(uncited)

    stored = ledger.to_json()
    stored_source = stored["q1"][0]["claims"][0]["evidence"][0]["source"]
    assert stored_source["provider"] == "openalex"
    assert stored_source["arxiv_id"] is None
    assert stored["q1"][0]["search_queries_used"] == ["swe-bench"]

    synthesis = ledger.prompt_view("evidence")
    assert [row["id"] for row in synthesis["sources"]] == ["s1", "s2"]
    assert synthesis["sources"][0]["url"] == "https://example.org/paper"
    assert synthesis["sources"][0]["doi"] == "10.1234/paper"
    assert synthesis["sources"][0]["is_retracted"] is False
    assert synthesis["sources"][0]["publication_status"] == "unknown"
    assert "provider" not in synthesis["sources"][0]
    assert "accessed_at" not in synthesis["sources"][0]
    paper_items = synthesis["evidence"][0]["claims"][0]["evidence"]
    assert [item["source_id"] for item in paper_items] == ["s1", "s1", "s1", "s1"]
    assert "source" not in paper_items[0]
    assert paper_items[0]["quote_check"] == "not_found"
    assert paper_items[0]["source_check"] == "observed"
    assert paper_items[0]["supports"] is False
    assert paper_items[0]["excerpt"] == "same paper"
    assert paper_items[0]["confidence"] == 0.0
    assert paper_items[1]["quote"] == "exact words"
    assert "excerpt" not in paper_items[1]
    assert "supports" not in paper_items[1]
    assert paper_items[2]["excerpt"] == "x" * 297 + "..."
    assert "supports" not in paper_items[2]
    # A quote that was not found keeps its paraphrase.
    assert paper_items[3]["quote"] == "invented words"
    assert paper_items[3]["excerpt"] == "paraphrase"
    assert synthesis["evidence"][0]["contradictions"] == [
        {"description": "disagreement", "claim_ids": ["q1/c1", "q1/c2"]}
    ]
    assert synthesis["evidence"][0]["unresolved_questions"] == ["still open"]
    assert "search_queries_used" not in synthesis["evidence"][0]
    assert "suggested_followups" not in synthesis["evidence"][0]
    assert [result["question_id"] for result in synthesis["evidence"]] == ["q1", "q2"]

    gap = ledger.prompt_view("results", include_search=True)
    assert gap["results"][0]["search_queries_used"] == ["swe-bench"]
    assert gap["results"][0]["suggested_followups"] == ["look further"]
    assert "search_queries_used" not in gap["results"][1]
    assert gap["sources"] == synthesis["sources"]

    # The report cites only q1/c1. The contradiction keeps q1/c2 so the verifier can judge it,
    # and q2 keeps its question and conclusion so an omitted question can get a followup.
    verifier = ledger.prompt_view("evidence", claim_ids=["q1/c1"])
    assert [row["id"] for row in verifier["sources"]] == ["s1", "s2"]
    kept, summary = verifier["evidence"]
    assert kept["question_id"] == "q1"
    assert kept["conclusion"] == "cited"
    assert [claim["id"] for claim in kept["claims"]] == ["q1/c1", "q1/c2"]
    assert kept["unresolved_questions"] == ["still open"]
    assert kept["contradictions"][0]["description"] == "disagreement"
    assert summary == {"question_id": "q2", "question": "Other?", "conclusion": "uncited", "confidence": 0.8}
    assert ledger.prompt_view("evidence", claim_ids=[])["evidence"][1] == summary


def test_prompt_view_merges_sources_that_differ_only_in_hidden_fields():
    from research_loop.schemas import Claim, Evidence, SourceRef

    via_openalex = SourceRef(url="https://example.org/paper", title="Paper", doi="10.1234/paper",
                             provider="openalex", openalex_id="https://openalex.org/W1", accessed_at="2026-09-01")
    via_crossref = via_openalex.model_copy(update={"provider": "crossref", "openalex_id": None,
                                                   "accessed_at": "2026-09-02"})
    page_seven = via_openalex.model_copy(update={"locator": "p. 7"})
    ledger = EvidenceLedger()
    ledger.add(ResearchResult(
        question_id="q1", question="Q?", conclusion="one paper", confidence=0.5,
        claims=[Claim(id="c1", statement="s", confidence=0.5, evidence=[
            Evidence(source=source, excerpt="e", confidence=0.5) for source in (via_openalex, via_crossref, page_seven)
        ])],
    ))
    view = ledger.prompt_view("evidence")
    assert [item["source_id"] for item in view["evidence"][0]["claims"][0]["evidence"]] == ["s1", "s1", "s2"]
    assert view["sources"][1]["locator"] == "p. 7"


def test_ledger_round_trips_through_json_without_renaming_claims():
    from research_loop.schemas import Claim

    ledger = EvidenceLedger()
    for conclusion in ("scout", "deep dive"):
        ledger.add(ResearchResult(question_id="q1", question="Q?", conclusion=conclusion, confidence=0.8,
                                  claims=[Claim(id="c1", statement="s", confidence=0.8)]))
    restored = EvidenceLedger.from_json(ledger.to_json())
    assert restored.claim_ids() == ledger.claim_ids() == {"q1/c1", "q1/c1~2"}
    assert [result.conclusion for result in restored.for_question("q1")] == ["scout", "deep dive"]


def _cited(question_id: str, *urls: str) -> ResearchResult:
    from research_loop.schemas import Claim, Evidence, SourceRef

    return ResearchResult(question_id=question_id, question=f"{question_id}?", conclusion="c", confidence=0.8, claims=[
        Claim(id="c1", statement="s", confidence=0.8,
              evidence=[Evidence(source=SourceRef(url=url, title=url), excerpt="e", confidence=0.8) for url in urls]),
    ])


def test_source_ids_are_ledger_wide_so_every_prompt_and_the_report_agree():
    import json

    ledger = EvidenceLedger()
    # Added q10 first: IDs follow natural question order (q2 before q10), not insertion order.
    for result in (_cited("q10", "https://c.example"), _cited("q2", "https://a.example", "https://b.example")):
        ledger.add(result)
    assert [(row["id"], row["url"]) for row in ledger.source_table()] == [
        ("s1", "https://a.example/"), ("s2", "https://b.example/"), ("s3", "https://c.example/")]

    # A view of some claims lists only their sources, under the same IDs; before, it renumbered from s1.
    verifier = ledger.prompt_view("evidence", claim_ids=["q10/c1"])
    assert [row["id"] for row in verifier["sources"]] == ["s3"]
    assert verifier["evidence"][0]["claims"][0]["evidence"][0]["source_id"] == "s3"
    assert ledger.claim_source_ids() == {"q10/c1": frozenset({"s3"}), "q2/c1": frozenset({"s1", "s2"})}

    # Postgres does not keep JSON key order; a reloaded ledger numbers its sources the same way.
    stored = ledger.to_json()
    reloaded = EvidenceLedger.from_json(json.loads(json.dumps(dict(reversed(stored.items())))))
    assert reloaded.source_table() == ledger.source_table()



def test_contradicting_evidence_does_not_back_an_inline_citation() -> None:
    from research_loop.schemas import Claim, Evidence, SourceRef

    ledger = EvidenceLedger()
    ledger.add(ResearchResult(question_id="q1", question="Q?", conclusion="c", confidence=0.7, claims=[
        Claim(id="c1", statement="X is true", confidence=0.7, evidence=[
            Evidence(source=SourceRef(url="https://for.example", title="for"), excerpt="yes", confidence=0.7),
            Evidence(source=SourceRef(url="https://against.example", title="against"), excerpt="no",
                     confidence=0.7, supports=False),
        ]),
    ]))
    assert ledger.claim_source_ids() == {"q1/c1": frozenset({"s1"})}

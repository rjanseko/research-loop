"""Properties of the pure functions, under inputs Hypothesis generates and shrinks to a minimal failure."""
from __future__ import annotations

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from research_loop.evidence import (
    EvidenceLedger,
    ToolOutputIndex,
    ToolText,
    _key,
    _normal,
    _segments,
    check_evidence,
    coverage_items,
    identity_keys,
    inline_source_ids,
    printed_dois,
    source_identity,
    strip_inline_citations,
    uncited_sentences,
)
from research_loop.schemas import (
    Claim,
    CoverageItem,
    Evidence,
    ResearchPlan,
    ResearchQuestion,
    ResearchResult,
    SourceRef,
)

SETTINGS = settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
words = st.text(alphabet=st.characters(codec="utf-8", exclude_categories=("Cs",)), min_size=0, max_size=40)
hosts = st.sampled_from(["docs.example", "journal.example", "arxiv.org", "nature.com", "link.springer.com"])
paths = st.lists(st.from_regex(r"[a-z0-9._-]{1,12}", fullmatch=True), min_size=0, max_size=3).map("/".join)
urls = st.builds(lambda host, path: f"https://{host}/{path}", hosts, paths)
item_ids = st.sampled_from(["k1", "k2", "d1", "d2", "o1", "o2", "o3", "q1", "c1"])


@SETTINGS
@given(urls)
def test_a_wayback_copy_is_also_the_page_it_archived(url: str) -> None:
    assert identity_keys(url=url) <= identity_keys(url=f"https://web.archive.org/web/2024/{url}")


@SETTINGS
@given(st.text(max_size=4000), st.from_regex(r"10\.\d{4,6}/[a-z0-9]{1,8}", fullmatch=True))
def test_only_a_documents_opening_names_its_doi(prefix: str, doi: str) -> None:
    padded = "x" * 3000 + prefix
    assert f"doi:{doi}" not in printed_dois(padded + " " + doi)


def _plan(ids: list[str]) -> ResearchPlan:
    return ResearchPlan(questions=[ResearchQuestion(id=f"q{i}", question=f"Q{i}?") for i in (1, 2)],
                        coverage=[CoverageItem(id=item_id, requirement=f"planned {item_id}") for item_id in ids])


def _result(question_id: str, question: str, open_items: list[str]) -> ResearchResult:
    return ResearchResult(question_id=question_id, question=question, conclusion="c", confidence=0.5,
                          open_items=open_items)


@SETTINGS
@given(st.lists(item_ids, unique=True, max_size=5), st.lists(words, max_size=6), st.lists(words, max_size=6))
def test_coverage_item_ids_are_unique(plan_ids: list[str], first: list[str], second: list[str]) -> None:
    ledger = EvidenceLedger()
    ledger.add(_result("q1", "Q1?", first))
    ledger.add(_result("q2", "Q2?", second))
    ids = [item.id for item, _ in coverage_items(_plan(plan_ids), ledger)]
    assert len(ids) == len(set(ids))


@SETTINGS
@given(st.lists(item_ids, unique=True, max_size=4), st.lists(words, min_size=1, max_size=4),
       st.lists(words, min_size=1, max_size=4), st.lists(words, min_size=1, max_size=4))
def test_open_item_ids_keep_their_meaning_as_research_is_added(plan_ids: list[str], first: list[str],
                                                               second: list[str], later: list[str]) -> None:
    plan = _plan(plan_ids)
    ledger = EvidenceLedger()
    ledger.add(_result("q1", "Q1?", first))
    ledger.add(_result("q2", "Q2?", second))
    # Names compare normalized: "0" and "0:" are one item, labeled by whichever spelling comes first.
    before = {item.id: _normal(item.requirement) for item, _ in coverage_items(plan, ledger)}
    ledger.add(_result("q1", "A deep dive on Q1?", later))  # follow-up research under an earlier question
    after = {item.id: _normal(item.requirement) for item, _ in coverage_items(plan, ledger)}
    assert all(after.get(item_id) == requirement for item_id, requirement in before.items())


# One line of a cited block, as the synthesizer writes it: prose, markdown, and section tags.
_BLOCK_LINE = st.text(alphabet=st.sampled_from(list("abcAB 19.!?\"'()#*-|:<>/_,")), max_size=120)


@SETTINGS
@given(_BLOCK_LINE)
def test_marking_a_cited_block_only_adds_citations_and_cites_every_sentence(text: str) -> None:
    from hypothesis import assume

    from research_loop.citations import _marked

    assume(not inline_source_ids(text))  # the model's own citations are removed before marking
    marked = _marked(text, ["s1"])
    assert strip_inline_citations(marked) == text
    assert uncited_sentences(marked)[1] == 0


@SETTINGS
@given(st.lists(st.text(max_size=80), max_size=12))
def test_citing_a_line_never_adds_uncited_sentences(lines: list[str]) -> None:
    answer = "\n".join(lines)
    cited = "\n".join(line + " [s1]" for line in lines)
    assert uncited_sentences(cited)[1] <= uncited_sentences(answer)[1]
    total, uncited = uncited_sentences(answer)
    assert 0 <= uncited <= total


@SETTINGS
@given(st.text(max_size=200), st.sets(st.sampled_from(["s1", "s2", "s3", "s10"])))
def test_stripping_citations_keeps_only_the_kept_ids(text: str, keep: set[str]) -> None:
    tagged = f"{text} [s1] and [s2, s3] and [s10]."
    assert set(inline_source_ids(strip_inline_citations(tagged, keep))) <= keep


@SETTINGS
@given(st.lists(st.tuples(urls, st.text(min_size=1, max_size=200)), min_size=1, max_size=4),
       st.integers(0, 3), st.text(max_size=60))
def test_a_verified_quote_is_in_its_cited_sources_text(pages: list[tuple[str, str]], cite: int, quote: str) -> None:
    texts = [ToolText("full_text", identity_keys(url=url), text) for url, text in pages]
    cited_url = pages[cite % len(pages)][0]
    item = check_evidence(Evidence(source=SourceRef(url=cited_url, title="t"), excerpt="e", quote=quote,
                                   confidence=0.5), ToolOutputIndex(texts))
    if item.quote_check == "verified":
        keys = source_identity(item.source)
        # The check compares text normalized as `_key` does (NFKC, case folded, letters and digits only).
        normal = [_key(t.text) for t in texts if t.keys & keys]
        assert any(all(segment in haystack for segment in _segments(quote)) for haystack in normal)


@SETTINGS
@given(st.lists(st.sampled_from(["c1", "c2", "q1/c1", "c1~2"]), min_size=1, max_size=6), st.integers(1, 3))
def test_the_ledger_gives_every_claim_a_unique_id(claim_ids: list[str], results: int) -> None:
    ledger = EvidenceLedger()
    for _ in range(results):
        ledger.add(ResearchResult(question_id="q1", question="Q?", conclusion="c", confidence=0.5, claims=[
            Claim(id=claim_id, statement="s", confidence=0.5) for claim_id in claim_ids]))
    ids = [claim.id for claim in ledger.claims()]
    assert len(ids) == len(set(ids)) == len(claim_ids) * results


@SETTINGS
@given(st.integers(0, 999), st.integers(1, 99), st.floats(0, 1), st.floats(0, 5))
def test_the_study_runner_reads_every_grade_line(met: int, extra: int, score: float, cost: float) -> None:
    from research_loop.study import _grade_with

    points = met + extra
    line = f"st05-scaling-table: {met} of {points} points ({score:.3f}), ${cost:.4f}. Unmet: none. Recorded as x.\n"
    grade = _grade_with(lambda args, env: (0, line))("run", "st05-scaling-table", {}, 1.0)
    assert grade == {"met": met, "points": points, "score": float(f"{score:.3f}"), "cost_usd": float(f"{cost:.4f}")}


@SETTINGS
@given(st.lists(st.sampled_from(["a", "b", "c"]), min_size=1, max_size=3, unique=True), st.integers(1, 5))
def test_every_replicate_runs_each_arm_once_in_rotating_order(arms: list[str], replicates: int) -> None:
    from research_loop.study import StudySpec, schedule

    spec = StudySpec.model_validate({"study": "s", "cases": ["c"], "replicates": replicates, "cap_usd": 1,
                                     "estimate_usd": 0.1, "ceiling_usd": 100, "arms": [{"name": a} for a in arms]})
    runs = schedule(spec)
    for replicate in range(1, replicates + 1):
        order = [run.arm.name for run in runs if run.replicate == replicate]
        turn = (replicate - 1) % len(arms)
        assert order == arms[turn:] + arms[:turn]  # two arms alternate (ABBA)

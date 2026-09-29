from __future__ import annotations

from uuid import uuid4

import pytest

from research_loop.diagnose import (
    RunDiagnosis,
    claims_text,
    detectable_difference,
    equivalent_urls,
    judge_agreement,
    never_met,
    render,
    research_text,
    stage_grade_row,
)
from research_loop.evals import GradeRecord, StudyCase
from research_loop.evidence import EvidenceLedger
from research_loop.schemas import Claim, Evidence, ResearchResult, SourceRef


def _case() -> StudyCase:
    """A rubric case of the shape the diagnosis reads: 16 facts, two of them addresses, and 4 analysis points."""
    facts = [f"States fact {n}." for n in range(1, 15)] + [
        "Gives the OQMD's address: https://www.oqmd.org/",
        "Gives the Materials Project's address: https://next-gen.materialsproject.org/"]
    return StudyCase(id="case-diagnose", objective="Which materials databases matter?", output_mode="report",
                     rubric_version="1", rubrics={"info_recall": facts,
                                                  "analysis": [f"Explains point {n}." for n in range(1, 5)]})


def _ledger() -> EvidenceLedger:
    ledger = EvidenceLedger()
    evidence = Evidence(source=SourceRef(url="https://www.oqmd.org/", title="OQMD"), excerpt="e", confidence=0.9,
                        quote="Exploration-based methods have a high computational cost and converge slowly.")
    ledger.add(ResearchResult(question_id="q1", question="Exploration-based design?", conclusion="c", confidence=0.9,
                              claims=[Claim(id="c1", statement="A DQN favoured exploitation", evidence=[evidence],
                                            confidence=0.9)],
                              open_items=["Cambridge Structural Database"], unresolved=["DDSE was not found"]))
    return ledger


def test_the_claims_view_holds_statements_and_sources_and_the_research_view_everything_synthesis_saw() -> None:
    ledger = _ledger()
    claims = claims_text(ledger)
    assert claims == ("## q1: Exploration-based design?\n"
                      "- A DQN favoured exploitation (sources: OQMD https://www.oqmd.org/)")
    # What only a quote or an open item holds is in the research view, not the claims view: the audit's
    # "computational cost" and CSD.
    research = research_text(ledger)
    for text in ("high computational cost", "Cambridge Structural Database", "DDSE was not found"):
        assert text in research and text not in claims


def test_a_missed_point_takes_the_earliest_stage_that_met_it() -> None:
    case = _case()
    report = {("info_recall", 1): True, ("info_recall", 2): False, ("info_recall", 3): False,
              ("info_recall", 4): False, ("info_recall", 5): True}
    claims = {("info_recall", 1): True, ("info_recall", 2): True}
    research = {("info_recall", 1): True, ("info_recall", 2): True, ("info_recall", 3): True}
    run = RunDiagnosis(uuid4(), case, "report", report, claims=claims, research=research)
    assert run.stages() == {("info_recall", 1): "reported", ("info_recall", 2): "claimed",
                            ("info_recall", 3): "seen", ("info_recall", 4): "not_found",
                            ("info_recall", 5): "reported"}
    # Met in the report and nowhere earlier: judge noise, or the synthesizer writing from its own knowledge.
    assert run.beyond_research() == [("info_recall", 5)]
    assert RunDiagnosis(uuid4(), case, "report", report).stages() is None


def test_a_missed_url_point_is_flagged_when_the_report_gives_the_same_site() -> None:
    case = _case()
    url_points = [(category, n) for category, texts in case.rubrics.items() for n, text in enumerate(texts, 1)
                  if "https://www.oqmd.org/" in text or "next-gen.materialsproject.org" in text]
    assert len(url_points) == 2
    report = dict.fromkeys(url_points, False)
    text = "OQMD: https://oqmd.org/ and Materials Project: https://materialsproject.org/ [s1]"
    assert sorted(equivalent_urls(case, text, report)) == sorted(url_points)
    assert equivalent_urls(case, "OQMD: https://example.org/oqmd", report) == []
    # A point already met is not a miss.
    assert equivalent_urls(case, text, dict.fromkeys(url_points, True)) == []


def test_judges_are_compared_point_by_point_on_the_runs_both_graded() -> None:
    first, second, third = uuid4(), uuid4(), uuid4()

    def row(run_id, model, met):
        return {"run_id": run_id, "judge_model": model, "judge_thinking": "high", "status": "succeeded",
                "points": [{"category": "info_recall", "point": n, "met": m} for n, m in enumerate(met, 1)]}

    grades = [row(first, "openai:gpt-6-sol", [True, False, False]), row(first, "zai:glm-5.3", [True, True, True]),
              row(second, "openai:gpt-6-sol", [True, True, False]), row(second, "zai:glm-5.3", [True, True, False]),
              row(third, "zai:glm-5.3", [False, False, False])]
    agreement = judge_agreement(grades)
    assert agreement.judges == ("openai:gpt-6-sol@high", "zai:glm-5.3@high")
    assert (agreement.compared, agreement.differ) == (6, 2)
    assert agreement.credited_by == {"zai:glm-5.3@high": 2}
    assert agreement.points == {("info_recall", 2): 1, ("info_recall", 3): 1}
    assert judge_agreement(grades[-1:]) is None


def test_never_met_points_and_the_detectable_difference() -> None:
    case = _case()
    everything = [{"category": c, "point": n, "met": True} for c, texts in case.rubrics.items()
                  for n in range(1, len(texts) + 1)]
    but_one = [dict(p, met=not (p["category"] == "info_recall" and p["point"] == 3)) for p in everything]
    assert never_met(case, [but_one]) == [("info_recall", 3)]
    assert never_met(case, [but_one, everything]) == []
    # The study of 28 September 2026: Sol's scores for standard and deep.
    assert detectable_difference([24, 16, 17], [26, 24, 22]) == pytest.approx(7.75, abs=0.01)
    assert detectable_difference([24], [26, 24]) is None


def test_the_rendered_diagnosis_counts_stages_by_arm_and_says_what_the_score_can_show() -> None:
    case = _case()
    points = [(c, n) for c, texts in case.rubrics.items() for n in range(1, len(texts) + 1)]
    runs, grades = [], []
    for arm, reported in (("standard", 6), ("standard", 8), ("deep", 10), ("deep", 12)):
        report = {p: i < reported for i, p in enumerate(points)}
        claims = {p: i < reported + 2 for i, p in enumerate(points)}
        research = {p: i < reported + 4 for i, p in enumerate(points)}
        run = RunDiagnosis(uuid4(), case, "no urls", report, claims=claims, research=research, arm=arm)
        runs.append(run)
        grades.append({"run_id": run.run_id, "judge_model": "zai:glm-5.3", "judge_thinking": "high",
                       "status": "succeeded", "points": [{"category": c, "point": n, "met": m}
                                                          for (c, n), m in report.items()]})
    text = render(runs, "zai:glm-5.3@high", grades, {case.id: [g["points"] for g in grades]})
    assert "| Run | Arm | Reported | Claimed, not reported | Seen, not claimed | Not found |" in text
    assert "| mean of 2 | standard | 7.0 | 2.0 | 2.0 | 9.0 | | |" in text
    assert "| mean of 2 | deep | 11.0 | 2.0 | 2.0 | 5.0 | | |" in text
    assert "only one judge has graded these reports" in text
    assert "**Never met:** 8 of 20 points" in text
    assert "differ by less than about" in text


def test_a_stage_grade_row_is_a_grade_row_with_its_view() -> None:
    grade = GradeRecord(uuid4(), _case(), "succeeded", points=[], score=0.5,
                        judge_model="zai:glm-5.3", judge_thinking="high")
    row = stage_grade_row(grade, "claims")
    assert (row["view"], row["diagnose_version"], row["case_id"]) == ("claims", 1, "case-diagnose")


def test_a_rescout_is_diagnosed_on_its_claims_and_arms_are_compared_on_points_claimed() -> None:
    # A fixed-plan rescout writes no report, so scout models are compared on what their claims met.
    case = _case()
    points = [("info_recall", n) for n in range(1, 5)]
    runs = []
    for arm, met in (("luna", 2), ("luna", 3), ("flash", 1), ("flash", 2)):
        claims = {point: n < met for n, point in enumerate(points)}
        research = {point: n < met + 1 for n, point in enumerate(points)}
        runs.append(RunDiagnosis(uuid4(), case, "", None, claims=claims, research=research, arm=arm))
    first = runs[0]
    assert first.score() == 2 and first.beyond_research() == [] and first.equivalent_urls() == []
    assert first.stages() == {points[0]: "claimed", points[1]: "claimed", points[2]: "seen", points[3]: "not_found"}
    text = render(runs, "zai:glm-5.3@high", [], {})
    assert "| no report | 2 | 1 | 1 |" in text
    assert "luna 2.5 over 2 rescouts (2, 3); flash 1.5 over 2 rescouts (1, 2)" in text
    assert "flash against luna: arms that differ by less than about" in text
    assert "| mean of 2 | luna | no report | 2.5 |" in text
    # A rescout whose research grade failed is left out of the comparison, as it is of the stage table.
    runs.append(RunDiagnosis(uuid4(), case, "", None, claims=dict.fromkeys(points, True), arm="flash"))
    assert "flash 1.5 over 2 rescouts" in render(runs, "zai:glm-5.3@high", [], {})


def test_a_rescout_study_takes_no_grade_or_audit() -> None:
    from research_loop.study import StudySpec

    spec = {"study": "s", "kind": "rescout", "sources": ["x"], "cap_usd": 1, "estimate_usd": 1, "ceiling_usd": 2,
            "arms": [{"name": "luna", "args": ["--model", "openai:gpt-6-luna@high"]}]}
    with pytest.raises(ValueError, match="use `diagnose`"):
        StudySpec(**spec, grade=True)
    assert StudySpec(**spec, diagnose=True).diagnose

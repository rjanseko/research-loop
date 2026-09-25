"""Grading stored reports, offline: the judge is a scripted model, and nothing is stored."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
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

from research_loop.breakdown import breakdown
from research_loop.config import Settings
from research_loop.evals import (
    JUDGE_VERSION,
    StoredReport,
    find_case,
    grade_reports,
    grade_row,
    reader_text,
    study_cases,
)
from research_loop.evidence import EvidenceLedger
from research_loop.schemas import (
    Claim,
    Evidence,
    FinalReport,
    ReportClaim,
    ResearchResult,
    SourceRef,
)

pytestmark = pytest.mark.filterwarnings("ignore::pydantic_ai.exceptions.CostNotFoundWarning")


def test_the_study_cases_ship_frozen_and_are_found_by_short_id() -> None:
    cases = study_cases()
    assert [case_id.split("-")[0] for case_id in cases] == [f"st0{n}" for n in range(1, 8)]
    st05 = find_case("st05")
    assert st05.rubric_version == "1" and sum(len(points) for points in st05.rubrics.values()) == 11
    with pytest.raises(KeyError):
        find_case("st99")


def _report_and_ledger() -> tuple[FinalReport, EvidenceLedger]:
    ledger = EvidenceLedger()
    evidence = [Evidence(source=SourceRef(url="https://example.org/gpt3", title="GPT-3 paper"), excerpt="e", confidence=0.9),
                Evidence(source=SourceRef(url="https://example.org/unused", title="Unused"), excerpt="e", confidence=0.9)]
    ledger.add(ResearchResult(question_id="q1", question="GPT-3?", conclusion="c", confidence=0.9,
                              claims=[Claim(id="c1", statement="175B", evidence=evidence[:1], confidence=0.9),
                                      Claim(id="c2", statement="other", evidence=evidence[1:], confidence=0.9)]))
    report = FinalReport(title="t", executive_summary="Summary the old judge never saw.",
                         answer="GPT-3 has 175 billion parameters [s1].",
                         claims=[ReportClaim(statement="GPT-3 has 175B parameters", claim_ids=["q1/c1"])],
                         caveats=["Only one model was checked."])
    return report, ledger


def test_the_judge_reads_the_report_as_the_first_design_gave_it() -> None:
    text = reader_text(*_report_and_ledger())
    assert text == ("GPT-3 has 175 billion parameters [s1].\n\n"
                    "## Key statements\n\n1. GPT-3 has 175B parameters\n\n"
                    "## Caveats\n\n- Only one model was checked.\n\n"
                    "## Sources\n\n- [s1] GPT-3 paper https://example.org/gpt3\n")


def _judge(verdicts_per_attempt: list[list[dict]], prompts: list[dict]) -> FunctionModel:
    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        first = messages[0]
        assert isinstance(first, ModelRequest)
        prompts.append(json.loads(next(p.content for p in first.parts if isinstance(p, UserPromptPart))))
        attempt = sum(isinstance(m, ModelResponse) for m in messages)
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name,
                                                 {"verdicts": verdicts_per_attempt[attempt]})])

    return FunctionModel(respond)


def _verdicts(met: dict[tuple[str, int], bool]) -> list[dict]:
    return [{"category": c, "point": n, "met": m} for (c, n), m in met.items()]


async def test_a_grade_needs_one_verdict_per_point_and_records_the_judge_call(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    case = find_case("st04")
    full = {("information_recall", 1): True, ("information_recall", 2): False, ("information_recall", 3): True,
            ("analysis", 1): True}
    missing_one = dict(list(full.items())[:3])
    prompts: list[dict] = []
    run_id = uuid4()
    result, grades = await grade_reports([StoredReport(case, run_id, "No captioning track ran in 2016.")], Settings(),
                                         model=_judge([_verdicts(missing_one), _verdicts(full)], prompts))
    (grade,) = grades
    assert grade.status == "succeeded" and grade.score == 0.75
    assert grade.unmet() == ["information_recall 2"]
    assert prompts[0]["report"] == "No captioning track ran in 2016." and set(prompts[0]["rubric"]) == set(case.rubrics)
    assert grade.usage.requests == 2  # the first reply missed a point and was sent back

    (case_result,) = result.cases
    assert case_result.scores["rubric"].value == 0.75
    assert case_result.scores["rubric:analysis"].value == 1.0

    row = grade_row(grade)
    assert (row["run_id"], row["case_id"], row["judge_version"], row["rubric_version"]) == \
        (run_id, case.id, JUDGE_VERSION, "1")
    assert row["judge_thinking"] == "high" and row["messages"] and row["error"] is None


async def test_a_failed_grade_is_returned_with_what_it_cost(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    never_complete = [_verdicts({("analysis", 1): True})] * 3
    _, (grade,) = await grade_reports([StoredReport(find_case("st04"), uuid4(), "report")], Settings(),
                                      model=_judge(never_complete, []))
    assert grade.status == "failed" and grade.score is None and grade.usage.requests == 2
    assert grade_row(grade)["error"]["type"] == "UnexpectedModelBehavior"


def test_a_breakdown_shows_each_calls_time_cost_and_stop_reason() -> None:
    start = datetime(2026, 9, 25, 12, tzinfo=UTC)

    def at(seconds: float) -> datetime:
        return start + timedelta(seconds=seconds)

    run = {"id": "r1", "status": "partial", "cost_usd": 0.5, "started_at": start, "finished_at": at(300),
           "input_hash": "abc", "study_id": None, "config": {"limits": {"research_seconds": 270, "deadline_seconds": 360}},
           "cache": {"mode": "reuse", "by_provider": {"web": {"hits": 3, "misses": 1, "writes": 1}}}}
    usage = {"requests": 4, "input_tokens": 1000, "cache_read_tokens": 400, "output_tokens": 200,
             "details": {"reasoning_tokens": 150}}
    calls = [
        {"role": "planner", "question_id": None, "status": "succeeded", "started_at": at(1), "finished_at": at(10),
         "usage": usage, "cost_usd": 0.01, "stop_reason": "returned a result", "tool_seconds": None},
        {"role": "scout", "question_id": "q1", "status": "cancelled", "started_at": at(10), "finished_at": at(270),
         "usage": usage, "cost_usd": 0.04, "stop_reason": "the research deadline passed", "tool_seconds": 60},
        {"role": "synthesizer", "question_id": None, "status": "succeeded", "started_at": at(271),
         "finished_at": at(300), "usage": usage, "cost_usd": 0.45, "stop_reason": "returned a result",
         "tool_seconds": None},
    ]
    text = breakdown(run, calls)
    scout_line = next(line for line in text.splitlines() if line.strip().startswith("scout"))
    assert "260s" in scout_line and "60s" in scout_line and "200s" in scout_line  # duration, tools, model
    assert scout_line.endswith("the research deadline passed")
    assert "synthesis: 271s to 300s, 89s left before the run deadline at 360s" in text
    assert "web         3 hits, 1 misses (75%), 1 written" in text
    assert "  synthesizer $0.4500" in text

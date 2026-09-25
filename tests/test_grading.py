from __future__ import annotations

import json
import sys
from typing import Any
from uuid import uuid4

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_evals.evaluators import EvaluatorContext

from research_loop.benchmarks import BenchmarkCaseSpec
from research_loop.evals import JUDGE_VERSION, BenchmarkOutput, RubricJudge, parse_judge
from research_loop.grading import StoredJob, grade_rows, grade_stored, stored_output

RUBRIC = {"information_recall": ["GPT-3 has 175 billion parameters", "Chinchilla saw 1.4 trillion tokens"],
          "presentation": ["Presents the numbers as a table"]}


def _spec(**changes: Any) -> BenchmarkCaseSpec:
    fields = {"benchmark_id": "study", "case_id": "st05", "objective": "Compare the three models.", "rubrics": RUBRIC}
    return BenchmarkCaseSpec(**(fields | changes))


def _output(**changes: Any) -> BenchmarkOutput:
    base = {"benchmark_id": "study", "case_id": "st05", "graph_version": "research-graph-v1", "job_id": "job-1",
                "root_run_id": "root", "answer": "GPT-3 has 175B parameters [s1].", "extracted_answer": None,
                "source_urls": [], "primary_source_urls": [], "unsupported_claims": 0, "major_unsupported_claims": 0,
                "total_claims": 1, "tool_calls": 0, "research_tool_calls": 0, "total_tokens": 0, "cost_usd": 0.0,
                "search_queries": [], "report_text": "GPT-3 has 175B parameters [s1].\n\n## Sources\n\n- [s1] GPT-3 paper https://arxiv.org/abs/2005.14165\n"}
    return BenchmarkOutput(**(base | changes))


def _judge_model(verdicts: list[tuple[str, int, bool]], *, first: list[tuple[str, int, bool]] | None = None,
                 prompts: list[dict[str, Any]] | None = None) -> FunctionModel:
    """A judge that returns `verdicts`, or `first` on its first call."""
    calls = 0

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal calls
        calls += 1
        if prompts is not None and calls == 1:
            prompts.append(json.loads(messages[0].parts[-1].content))
        chosen = first if first is not None and calls == 1 else verdicts
        args = {"verdicts": [{"category": c, "point": p, "met": m} for c, p, m in chosen]}
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, args)])

    return FunctionModel(respond)


def _ctx(spec: BenchmarkCaseSpec, output: BenchmarkOutput) -> EvaluatorContext:
    return EvaluatorContext(name="case", inputs=spec, metadata=None, expected_output=None, output=output,
                            duration=0.0, _span_tree=None, attributes={}, metrics={})


ALL_POINTS = [("information_recall", 1, True), ("information_recall", 2, False), ("presentation", 1, True)]


@pytest.mark.asyncio
async def test_rubric_judge_scores_each_category_and_names_unmet_points_by_number() -> None:
    prompts: list[dict[str, Any]] = []
    judge = RubricJudge(model=_judge_model(ALL_POINTS, prompts=prompts))
    scores = await judge.evaluate(_ctx(_spec(), _output()))
    assert scores["rubric"].value == pytest.approx(2 / 3)
    assert scores["rubric:information_recall"] == 0.5 and scores["rubric:presentation"] == 1.0
    assert scores["rubric"].reason == f"judge v{JUDGE_VERSION}; unmet: information_recall 2"
    assert "Chinchilla" not in scores["rubric"].reason  # point numbers only; rubric text can be a benchmark input
    # The judge sees the question, numbered points, and the report with its sources list.
    [prompt] = prompts
    assert prompt["question"] == "Compare the three models."
    assert prompt["rubric"]["presentation"] == [{"point": 1, "text": "Presents the numbers as a table"}]
    assert "175B" in prompt["report"] and "## Sources" in prompt["report"]


@pytest.mark.asyncio
async def test_rubric_judge_retries_until_every_point_has_one_verdict() -> None:
    judge = RubricJudge(model=_judge_model(ALL_POINTS, first=ALL_POINTS[:2] + [("analysis", 1, True)]))
    scores = await judge.evaluate(_ctx(_spec(), _output()))
    assert scores["rubric"].value == pytest.approx(2 / 3)


@pytest.mark.asyncio
async def test_rubric_judge_skips_cases_without_a_rubric() -> None:
    def unexpected(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise AssertionError("judged a case without a rubric")

    judge = RubricJudge(model=FunctionModel(unexpected))
    assert await judge.evaluate(_ctx(_spec(rubrics={}), _output())) == {}


def test_judge_specs_name_a_second_judge() -> None:
    assert (parse_judge("openai:gpt-6-sol").name, parse_judge("openai:gpt-6-sol").model) == ("rubric", "openai:gpt-6-sol")
    second = parse_judge("opus=anthropic:claude-opus-5-5")
    assert (second.name, second.model) == ("opus", "anthropic:claude-opus-5-5")
    # Low effort missed plainly stated points, so judges think hard unless told otherwise.
    assert parse_judge("openai:gpt-6-sol").thinking == second.thinking == "high"


async def _synthetic_job(**changes: Any) -> StoredJob:
    """A finished synthetic run, stored the way Postgres keeps it."""
    from research_loop.policy import get_policy
    from research_loop.settings import ResearchSettings
    from research_loop.synthetic import SyntheticResearchLoop

    outcome = await SyntheticResearchLoop(get_policy("synthetic"), settings=ResearchSettings.from_env({})).run("q")
    task = str(uuid4())
    fields = {
        "job_id": str(outcome.job_id), "root_run_id": str(uuid4()), "status": "succeeded", "effective_config": {},
        "report": outcome.report.model_dump(mode="json"), "verification": outcome.verification.model_dump(mode="json"),
        "ledger": outcome.ledger.to_json(), "review_reasons": outcome.review_reasons,
        "task_usage": [{"requests": 1, "tool_calls": 1, "total_tokens": 40, "cost": 0.25},
                    {"requests": 1, "tool_calls": 0, "total_tokens": 10, "cost": 0.05}],
        "tool_events": [{"tool_name": "duckduckgo_search", "args": {"query_sha256": "ab", "query_chars": 5}}],
        "transcripts": {}, "tasks_with_tools": {task}, "attachment_count": 0,
    }
    return StoredJob(**(fields | changes))


def _transcript(query: str) -> list[Any]:
    from pydantic_ai.messages import (
        ModelMessagesTypeAdapter,
        ModelRequest,
        ToolReturnPart,
    )

    messages = [ModelResponse(parts=[ToolCallPart("duckduckgo_search", {"query": query}, tool_call_id="c")]),
                ModelRequest(parts=[ToolReturnPart("duckduckgo_search", [], tool_call_id="c")])]
    return ModelMessagesTypeAdapter.dump_python(messages, mode="json")


@pytest.mark.asyncio
async def test_a_stored_job_without_transcripts_skips_the_checks_that_need_tool_arguments() -> None:
    job = await _synthetic_job()
    output = stored_output(_spec(), job)
    assert not output.tool_args_known and output.search_queries == []
    assert (output.tool_calls, output.total_tokens, output.research_tool_calls) == (1, 50, 1)
    assert output.cost_usd == pytest.approx(0.30)
    assert output.answer == job.report["answer"] and output.total_claims == len(job.verification["checks"])
    report = await grade_stored([(_spec(), output)])
    assert "UniqueSearchRate" not in report.cases[0].scores  # unknown, not zero


@pytest.mark.asyncio
async def test_a_stored_job_with_transcripts_is_graded_from_its_real_tool_calls() -> None:
    job = await _synthetic_job()
    job.transcripts = {next(iter(job.tasks_with_tools)): _transcript("SWE-bench Verified")}
    output = stored_output(_spec(), job)
    assert output.tool_args_known and output.search_queries == ["SWE-bench Verified"]


@pytest.mark.asyncio
async def test_stored_cost_is_unknown_when_a_billed_call_was_not_priced() -> None:
    job = await _synthetic_job(task_usage=[{"requests": 1, "cost": 0.25}, {"requests": 2, "cost": None},
                                           {"requests": 0, "cost": None}])
    assert stored_output(_spec(), job).cost_usd is None


@pytest.mark.asyncio
async def test_only_a_finished_job_can_be_graded() -> None:
    job = await _synthetic_job(status="failed", report=None)
    with pytest.raises(LookupError, match="failed"):
        stored_output(_spec(), job)


@pytest.mark.asyncio
async def test_grading_stored_jobs_repeats_each_and_keeps_jobs_of_one_case_apart() -> None:
    first, second = _output(job_id="job-a"), _output(job_id="job-b", answer="Nothing useful.")
    judge = RubricJudge(model=_judge_model(ALL_POINTS))
    second_judge = RubricJudge(model=_judge_model([(c, p, True) for c, p, _ in ALL_POINTS]), name="opus")
    report = await grade_stored([(_spec(), first), (_spec(), second)], judges=[judge, second_judge], repeat=2)
    rows = grade_rows(report, [judge, second_judge])
    assert len(rows) == 4 and {row["job_id"] for row in rows} == {"job-a", "job-b"}
    row = rows[0]
    assert row["scores"]["rubric"] == pytest.approx(2 / 3) and row["scores"]["opus"] == 1.0
    assert row["reasons"] == {"rubric": f"judge v{JUDGE_VERSION}; unmet: information_recall 2",
                              "opus": f"judge v{JUDGE_VERSION}; unmet: none"}
    assert [j["name"] for j in row["judges"]] == ["rubric", "opus"] and row["judges"][0]["version"] == JUDGE_VERSION
    assert "SupportedClaimRate" in row["scores"]  # the default metrics run too


def test_research_bench_refuses_a_judge_without_paid(monkeypatch, capsys) -> None:
    from research_loop.benchmark import main

    monkeypatch.setattr(sys, "argv", ["research-bench", "unused.toml", "--judge", "openai:gpt-6-sol"])
    with pytest.raises(SystemExit):
        main()
    assert "--judge calls a model; add --paid" in capsys.readouterr().err


@pytest.mark.parametrize("argv, message", [
    (["suite.toml", "--job", "st05"], "expected CASE_ID=JOB_ID"),
    (["suite.toml", "--job", "st05=not-a-uuid"], "not a job ID"),
    (["suite.toml", "--job", f"st05={uuid4()}", "--repeat", "0"], "--repeat must be at least 1"),
    (["suite.toml", "--job", f"st05={uuid4()}", "--judge", "openai:a", "--judge", "openai:b"], "share a name"),
])
def test_research_grade_refuses_unusable_options(argv, message, capsys) -> None:
    from research_loop.grading import main

    with pytest.raises(SystemExit):
        main(argv)
    assert message in capsys.readouterr().err


def test_the_judge_reads_key_statements_and_caveats_but_not_verdicts() -> None:
    from research_loop.benchmark import reader_text
    from research_loop.ledger import EvidenceLedger
    from research_loop.schemas import FinalReport, ReportClaim

    report = FinalReport(answer="Short answer.", caveats=["Scores are vendor claims."],
                         claims=[ReportClaim(statement="Verified has 500 instances.", claim_ids=["q1/c1"])])
    text = reader_text(report, EvidenceLedger())
    assert text.startswith("Short answer.\n\n## Key statements\n\n1. Verified has 500 instances.")
    assert "## Caveats\n\n- Scores are vendor claims." in text
    assert "q1/c1" not in text and "supported" not in text

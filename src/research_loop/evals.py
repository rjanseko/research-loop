"""Grading stored runs against the study cases' rubrics, with Pydantic Evals.

The rubric judge is the first design's version 2, ported unchanged: the same instructions, verdict
schema, and one-verdict-per-point check, on `gpt-6-sol` at high effort. It reads the report as the first
design gave it to the judge (`reader_text`), so st07's grades stay comparable with the old baseline.

Every judge call is recorded in the `grades` table, a failed one too, with its usage, cost, and messages.
Grading calls a paid model; it runs from `research grade`, never from the tests.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from importlib.resources import files
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field
from pydantic_ai import Agent, ModelRetry, capture_run_messages
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models import Model
from pydantic_ai.usage import RunUsage
from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import EvaluationReason, Evaluator, EvaluatorContext

from .config import Settings
from .evidence import EvidenceLedger, inline_source_ids
from .models import build_model
from .schemas import FinalReport
from .store import error_record, transcript, usage_record

# Recorded with every rubric score; bump when JUDGE_INSTRUCTIONS or the verdict schema changes, since
# scores are comparable only under one judge prompt. 1: one call per report, a verdict for every point,
# on the answer and its Sources list. 2: the whole report a reader sees, key statements and caveats too;
# v1 missed rubric points a report stated only as a key statement.
JUDGE_VERSION = 2
JUDGE_INSTRUCTIONS = """\
You grade a research report against a rubric. Each rubric point says one thing a good answer contains.

For every point, decide whether the report meets it:
- A point is met when the report itself states what the point describes, and states it consistently
  with the point. Do not credit what the report only implies, or what you know but the report does not say.
- A point that asks for something to be absent, such as not naming something, is met when the report
  does not do it.
- A point about sources or citations is judged from the report's inline [sN] citations and its Sources list.
- Judge each point on its own; how the report does on one point says nothing about another.

Return one verdict for every point, identified by its category and number, and no others.
"""
JUDGE_MODEL = "openai:gpt-6-sol"
# At low effort it missed points reports stated plainly (the settings study).
JUDGE_THINKING = "high"
# Longer than a model request in a run: the judge's reply is not streamed.
_JUDGE_TIMEOUT_SECONDS = 600


class StudyCase(BaseModel):
    """One study question and how it is scored. A case is frozen once graded; a correction is a new rubric_version."""

    id: str
    objective: str
    output_mode: str
    rubric_version: str
    answer: list[str] = Field(default_factory=list)
    rubrics: dict[str, list[str]] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


def study_cases() -> dict[str, StudyCase]:
    """The study cases by ID, as shipped with the package."""
    text = files("research_loop").joinpath("study_cases.jsonl").read_text(encoding="utf-8")
    cases = [StudyCase.model_validate_json(line) for line in text.splitlines() if line.strip()]
    return {case.id: case for case in cases}


def find_case(key: str) -> StudyCase:
    """A case by its full ID or its short prefix, such as `st05`."""
    cases = study_cases()
    matches = [case for case_id, case in cases.items() if case_id == key or case_id.split("-")[0] == key]
    if len(matches) != 1:
        raise KeyError(f"no single study case {key!r}; choose from {', '.join(cases)}")
    return matches[0]


def reader_text(report: FinalReport, ledger: EvidenceLedger) -> str:
    """The report as the first design gave it to the judge: answer, key statements, caveats, and cited sources.

    It leaves out the executive summary, as the first design's did, so grades compare with the old baseline.
    """
    parts = [report.answer]
    if report.claims:
        parts.append("## Key statements\n\n" + "\n".join(f"{n}. {c.statement}" for n, c in enumerate(report.claims, 1)))
    if report.caveats:
        parts.append("## Caveats\n\n" + "\n".join(f"- {caveat}" for caveat in report.caveats))
    cited = {source_id for text in report.cited_texts for source_id in inline_source_ids(text)}
    rows = [row for row in ledger.source_table() if row["id"] in cited]
    sources = ""
    if rows:
        where = [row.get("url") or (f"https://doi.org/{row['doi']}" if row.get("doi") else "") for row in rows]
        sources = "\n\n## Sources\n\n" + "\n".join(f"- [{row['id']}] {row.get('title') or ''} {url}".rstrip()
                                                   for row, url in zip(rows, where, strict=True)) + "\n"
    return "\n\n".join(parts) + sources


class RubricVerdict(BaseModel):
    category: str
    point: int
    met: bool


class RubricGrade(BaseModel):
    verdicts: list[RubricVerdict]


@dataclass
class GradeRecord:
    """One judge call on one run's report: what the `grades` table stores."""

    run_id: UUID
    case: StudyCase
    status: str
    points: list[dict[str, Any]] = field(default_factory=list)
    score: float | None = None
    usage: RunUsage | None = None
    messages: list[ModelMessage] = field(default_factory=list)
    error: BaseException | None = None
    id: UUID = field(default_factory=uuid4)

    @property
    def cost_usd(self) -> Decimal | None:
        return self.usage.cost if self.usage else None

    def unmet(self) -> list[str]:
        return [f"{p['category']} {p['point']}" for p in self.points if not p["met"]]


async def judge(report_text: str, case: StudyCase, run_id: UUID, settings: Settings,
                model: Model | None = None) -> GradeRecord:
    """Grade `report_text` against `case`'s rubric with one judge call; a failure is returned, not raised.

    `model` replaces the judge's model, for tests.
    """
    rubric = {category: points for category, points in case.rubrics.items() if points}
    if not rubric:
        raise ValueError(f"{case.id} has no rubric")
    expected = {(category, number) for category, points in rubric.items() for number in range(1, len(points) + 1)}
    agent = Agent(output_type=RubricGrade, instructions=JUDGE_INSTRUCTIONS)

    @agent.output_validator
    def every_point_once(grade: RubricGrade) -> RubricGrade:
        given = [(v.category, v.point) for v in grade.verdicts]
        missing, extra = sorted(expected - set(given)), sorted(set(given) - expected)
        if missing or extra or len(given) != len(set(given)):
            raise ModelRetry(f"Give exactly one verdict per rubric point. Missing: {missing}; unknown: {extra}.")
        return grade

    prompt = json.dumps({
        "question": case.objective,
        "rubric": {category: [{"point": number, "text": text} for number, text in enumerate(points, 1)]
                   for category, points in rubric.items()},
        "report": report_text,
    }, ensure_ascii=False)
    usage = RunUsage()
    messages: list[ModelMessage] = []
    try:
        with capture_run_messages() as messages:
            # The scout role's model carries no output cap, as the judge's did; the run settings set its effort.
            result = await agent.run(prompt, model=model or build_model(JUDGE_MODEL, "scout", settings), usage=usage,
                                     model_settings={"thinking": JUDGE_THINKING, "timeout": _JUDGE_TIMEOUT_SECONDS})
    except Exception as exc:  # noqa: BLE001 - a failed grade is recorded with what it cost, then reported
        return GradeRecord(run_id, case, "failed", usage=usage, messages=list(messages), error=exc)
    met = {(v.category, v.point): v.met for v in result.output.verdicts}
    points = [{"category": category, "point": number, "met": met[(category, number)]}
              for category, number in sorted(expected)]
    return GradeRecord(run_id, case, "succeeded", points=points, score=sum(met.values()) / len(met),
                       usage=usage, messages=result.all_messages())


def grade_row(grade: GradeRecord) -> dict[str, Any]:
    """The `grades` row for `grade`, in the shape Postgres stores it."""
    return {
        "id": grade.id, "run_id": grade.run_id, "case_id": grade.case.id, "judge_model": JUDGE_MODEL,
        "judge_thinking": JUDGE_THINKING, "judge_version": JUDGE_VERSION, "rubric_version": grade.case.rubric_version,
        "status": grade.status, "score": grade.score, "points": grade.points,
        "usage": usage_record(grade.usage) if grade.usage else None, "cost_usd": grade.cost_usd,
        "messages": transcript(grade.messages) if grade.messages else None,
        "error": error_record(grade.error) if grade.error else None,
    }


@dataclass
class StoredReport:
    """A stored run's report, as the judge reads it, and the case it answers."""

    case: StudyCase
    run_id: UUID
    text: str


@dataclass
class RubricJudge(Evaluator[StoredReport, StoredReport]):
    """The judge as a Pydantic Evals evaluator. Scores `rubric` (share of points met) and `rubric:<category>`;
    the reason names unmet points by category and number, never their text. `grades` collects each call."""

    settings: Settings
    model: Model | None = None
    grades: list[GradeRecord] = field(default_factory=list)

    def get_default_evaluation_name(self) -> str:
        return "rubric"

    async def evaluate(self, ctx: EvaluatorContext[StoredReport, StoredReport]) -> dict[str, Any]:
        report = ctx.output
        grade = await judge(report.text, report.case, report.run_id, self.settings, self.model)
        self.grades.append(grade)
        if grade.status != "succeeded":
            return {}
        scores: dict[str, Any] = {"rubric": EvaluationReason(
            value=grade.score, reason=f"judge v{JUDGE_VERSION}; unmet: {', '.join(grade.unmet()) or 'none'}")}
        for category in report.case.rubrics:
            points = [p["met"] for p in grade.points if p["category"] == category]
            if points:
                scores[f"rubric:{category}"] = sum(points) / len(points)
        if grade.cost_usd is not None:
            scores["rubric_cost_usd"] = float(grade.cost_usd)
        return scores


async def grade_reports(reports: list[StoredReport], settings: Settings,
                        model: Model | None = None) -> tuple[Any, list[GradeRecord]]:
    """Grade stored reports one at a time as a Pydantic Evals experiment; return its report and every judge call."""
    evaluator = RubricJudge(settings, model)
    dataset = Dataset[StoredReport, StoredReport, None](
        name="scout-study", cases=[Case(name=f"{r.case.id}:{r.run_id}", inputs=r) for r in reports],
        evaluators=[evaluator])

    async def stored(report: StoredReport) -> StoredReport:
        return report

    result = await dataset.evaluate(stored, max_concurrency=1, progress=False)
    return result, evaluator.grades

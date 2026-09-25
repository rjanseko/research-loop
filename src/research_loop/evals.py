from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel
from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import EvaluationReason, Evaluator, EvaluatorContext

from .benchmarks.models import BenchmarkCaseSpec

# Recorded in manifests and the configuration fingerprint; bump when a metric's definition changes,
# since scores are comparable only under one definition. 1: the metrics in make_dataset below.
EVALUATOR_VERSION = 1


@dataclass
class BenchmarkOutput:
    benchmark_id: str
    case_id: str
    graph_version: str
    job_id: str
    root_run_id: str
    answer: str
    extracted_answer: str | None
    source_urls: list[str]
    primary_source_urls: list[str]
    unsupported_claims: int
    major_unsupported_claims: int
    total_claims: int
    tool_calls: int
    research_tool_calls: int
    total_tokens: int
    cost_usd: float | None
    search_queries: list[str]
    # Blocked-source entries by kind of contact (benchmark._audit_blocked_sources).
    blocked_fetches_refused: list[str] = field(default_factory=list)
    blocked_fetches_completed: list[str] = field(default_factory=list)
    blocked_sources_in_search: list[str] = field(default_factory=list)
    blocked_sources_cited: list[str] = field(default_factory=list)
    integrity_flags: list[str] = field(default_factory=list)
    attachment_count: int = 0
    attachment_tool_calls: int = 0
    attachment_ids_cited: list[str] = field(default_factory=list)
    # Evidence quotes, and those not found in text the research tools returned (quotes.py).
    quotes: int = 0
    quotes_not_found: int = 0
    # URL-cited evidence, and that citing sources no research tool returned (quotes.check_sources).
    sources: int = 0
    sources_not_found: int = 0
    # What the finished run left unresolved (async_orchestrator.review_reasons).
    review_reasons: list[str] = field(default_factory=list)
    # The report as a reader reads it: the answer, its key statements and caveats, and the Sources list
    # its [sN] citations name, without the verifier's verdicts. The rubric judge grades this.
    report_text: str = ""
    # False for a run reloaded from Postgres without a transcript, whose tool arguments are hashes:
    # search queries and fetched URLs are then unknown, and the checks built on them are skipped.
    tool_args_known: bool = True


def normalize_answer(value: str) -> str:
    value = value.strip().casefold()
    value = re.sub(r"\s+", " ", value)
    value = value.strip(" .,:;!?\"'`[](){}")
    return value


# Evaluators return {} (no score) when their check does not apply to a case, so an
# unassessed case is never averaged in as a pass or a failure.


class SupportedClaimRate(Evaluator[Any, BenchmarkOutput]):
    def evaluate(self, ctx: EvaluatorContext[Any, BenchmarkOutput]) -> float | dict[str, float]:
        total = ctx.output.total_claims
        if total == 0:
            return {}
        return 1.0 - (ctx.output.unsupported_claims / total)


class MajorErrorFreeRate(Evaluator[Any, BenchmarkOutput]):
    def evaluate(self, ctx: EvaluatorContext[Any, BenchmarkOutput]) -> float | dict[str, float]:
        if ctx.output.total_claims == 0:
            return {}
        return 1.0 if ctx.output.major_unsupported_claims == 0 else 0.0


class PrimarySourceRate(Evaluator[Any, BenchmarkOutput]):
    def evaluate(self, ctx: EvaluatorContext[Any, BenchmarkOutput]) -> float:
        urls = set(ctx.output.source_urls)
        if not urls:
            return 0.0
        return len(set(ctx.output.primary_source_urls)) / len(urls)


class ToolEfficiency(Evaluator[Any, BenchmarkOutput]):
    def evaluate(self, ctx: EvaluatorContext[Any, BenchmarkOutput]) -> float:
        supported = max(ctx.output.total_claims - ctx.output.unsupported_claims, 0)
        return supported / max(ctx.output.research_tool_calls, 1)


class CostEfficiency(Evaluator[Any, BenchmarkOutput]):
    def evaluate(self, ctx: EvaluatorContext[Any, BenchmarkOutput]) -> float | dict[str, float]:
        if ctx.output.cost_usd is None or ctx.output.cost_usd <= 0:
            return {}  # unpriced or free: claims per dollar is undefined
        supported = max(ctx.output.total_claims - ctx.output.unsupported_claims, 0)
        return supported / ctx.output.cost_usd


class UniqueSearchRate(Evaluator[Any, BenchmarkOutput]):
    def evaluate(self, ctx: EvaluatorContext[Any, BenchmarkOutput]) -> float | dict[str, float]:
        if not ctx.output.tool_args_known:
            return {}
        queries = [q.strip().casefold() for q in ctx.output.search_queries if q.strip()]
        if not queries:
            return 0.0
        return len(set(queries)) / len(queries)


class ReferenceAnswerMatch(Evaluator[BenchmarkCaseSpec, BenchmarkOutput]):
    """Cheap deterministic score for short-answer datasets.

    This is intentionally conservative and is not a replacement for the official BrowseComp
    or GAIA semantic graders. It is useful for local iteration and catches exact/near-exact
    normalized matches without introducing another judge model into every smoke run.
    """

    def evaluate(self, ctx: EvaluatorContext[BenchmarkCaseSpec, BenchmarkOutput]) -> float | dict[str, float]:
        expected = ctx.expected_output
        if expected is None:
            return {}
        candidates = expected if isinstance(expected, list) else [expected]
        actual = ctx.output.extracted_answer
        if not actual:
            return 0.0
        norm_actual = normalize_answer(actual)
        return float(any(norm_actual == normalize_answer(str(x)) for x in candidates))


class BlockedSourceCompliance(Evaluator[BenchmarkCaseSpec, BenchmarkOutput]):
    """Fails when a blocked source was fetched or cited; refusals and search sightings do not count."""

    def evaluate(self, ctx: EvaluatorContext[BenchmarkCaseSpec, BenchmarkOutput]) -> float | dict[str, float]:
        if not ctx.inputs.blocked_urls:
            return {}
        if ctx.output.blocked_fetches_completed or ctx.output.blocked_sources_cited:
            return 0.0
        # Without the tool arguments, a completed fetch of a blocked source cannot be ruled out.
        return 1.0 if ctx.output.tool_args_known else {}


class EvalIntegrity(Evaluator[BenchmarkCaseSpec, BenchmarkOutput]):
    def evaluate(self, ctx: EvaluatorContext[BenchmarkCaseSpec, BenchmarkOutput]) -> float | dict[str, float]:
        if not ctx.inputs.leakage_sensitive or not ctx.output.tool_args_known:
            return {}
        return 1.0 if not ctx.output.integrity_flags else 0.0


class VerbatimQuoteRate(Evaluator[Any, BenchmarkOutput]):
    """Share of evidence quotes found in text the research tools returned; code checks it, not a model."""

    def evaluate(self, ctx: EvaluatorContext[Any, BenchmarkOutput]) -> float | dict[str, float]:
        if ctx.output.quotes == 0:
            return {}
        return 1.0 - ctx.output.quotes_not_found / ctx.output.quotes


class ObservedSourceRate(Evaluator[Any, BenchmarkOutput]):
    """Share of URL-cited evidence whose source appeared in text the research tools returned."""

    def evaluate(self, ctx: EvaluatorContext[Any, BenchmarkOutput]) -> float | dict[str, float]:
        if ctx.output.sources == 0:
            return {}
        return 1.0 - ctx.output.sources_not_found / ctx.output.sources


class AttachmentCitationCoverage(Evaluator[Any, BenchmarkOutput]):
    def evaluate(self, ctx: EvaluatorContext[Any, BenchmarkOutput]) -> float | dict[str, float]:
        if ctx.output.attachment_count <= 0:
            return {}
        return min(len(set(ctx.output.attachment_ids_cited)) / ctx.output.attachment_count, 1.0)


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


class RubricVerdict(BaseModel):
    category: str
    point: int
    met: bool


class RubricGrade(BaseModel):
    verdicts: list[RubricVerdict]


@dataclass
class RubricJudge(Evaluator[BenchmarkCaseSpec, BenchmarkOutput]):
    """Grades the report against the case's rubric with one model call that returns a verdict per point.

    Scores `<name>` (share of all points met), `<name>:<category>` for each rubric category, and
    `<name>_cost_usd` for the judge's own call. The overall score's reason lists unmet points by
    category and number only, never their text, since rubrics can be benchmark inputs. Cases without
    a rubric get no score. Give a second judge its own `name` so both keep their scores.
    """

    model: Any = "openai:gpt-6-sol"
    # At low effort it missed points reports stated plainly, one of them in a section heading (step 1 of
    # docs/settings-study.md). The effort is recorded with each judge, so scores stay comparable by it.
    thinking: str | bool | None = "high"
    name: str = "rubric"

    def get_default_evaluation_name(self) -> str:
        return self.name

    async def evaluate(self, ctx: EvaluatorContext[BenchmarkCaseSpec, BenchmarkOutput]) -> dict[str, Any]:
        from pydantic_ai import Agent, ModelRetry

        from .prices import install_price_overrides

        rubric = {category: points for category, points in ctx.inputs.rubrics.items() if points}
        if not rubric:
            return {}
        install_price_overrides()
        expected = {(category, number) for category, points in rubric.items() for number in range(1, len(points) + 1)}
        agent = Agent(self.model, output_type=RubricGrade, instructions=JUDGE_INSTRUCTIONS)

        @agent.output_validator
        def every_point_once(grade: RubricGrade) -> RubricGrade:
            given = [(v.category, v.point) for v in grade.verdicts]
            missing, extra = sorted(expected - set(given)), sorted(set(given) - expected)
            if missing or extra or len(given) != len(set(given)):
                raise ModelRetry(f"Give exactly one verdict per rubric point. Missing: {missing}; unknown: {extra}.")
            return grade

        prompt = json.dumps({
            "question": ctx.inputs.objective,
            "rubric": {category: [{"point": number, "text": text} for number, text in enumerate(points, 1)]
                       for category, points in rubric.items()},
            "report": ctx.output.report_text or ctx.output.answer,
        }, ensure_ascii=False)
        settings = {"thinking": self.thinking} if self.thinking is not None else None
        result = await agent.run(prompt, model_settings=settings)
        met = {(v.category, v.point): v.met for v in result.output.verdicts}

        unmet = [f"{category} {number}" for category, number in sorted(expected) if not met[(category, number)]]
        scores: dict[str, Any] = {
            self.name: EvaluationReason(
                value=sum(met.values()) / len(met),
                reason=f"judge v{JUDGE_VERSION}; unmet: {', '.join(unmet) or 'none'}",
            ),
        }
        for category, points in rubric.items():
            scores[f"{self.name}:{category}"] = sum(met[(category, n)] for n in range(1, len(points) + 1)) / len(points)
        cost = getattr(result.usage, "cost", None)
        if cost is not None:
            scores[f"{self.name}_cost_usd"] = float(cost)
        return scores


def parse_judge(value: str) -> RubricJudge:
    """A `--judge` value: MODEL, or NAME=MODEL to keep a second judge's scores apart from the first."""
    name, sep, model = value.partition("=")
    if sep and name and ":" not in name and model:
        return RubricJudge(model=model, name=name)
    return RubricJudge(model=value)


def judge_records(judges: Sequence[RubricJudge]) -> list[dict[str, Any]]:
    """What a manifest or grade file records about its judges, so their scores can be compared."""
    return [{"name": j.name, "model": str(j.model), "thinking": j.thinking, "version": JUDGE_VERSION} for j in judges]


def default_evaluators() -> list[Evaluator]:
    """The code-computed metrics every benchmark run gets; no model calls."""
    return [
        ReferenceAnswerMatch(),
        SupportedClaimRate(),
        MajorErrorFreeRate(),
        PrimarySourceRate(),
        ToolEfficiency(),
        CostEfficiency(),
        UniqueSearchRate(),
        BlockedSourceCompliance(),
        EvalIntegrity(),
        AttachmentCitationCoverage(),
        VerbatimQuoteRate(),
        ObservedSourceRate(),
    ]


def make_dataset(cases: list[Case], *, judges: Sequence[RubricJudge] = ()) -> Dataset:
    """The benchmark dataset: every default metric, plus any rubric judges, which call a model."""
    return Dataset(name="research_loop", cases=cases, evaluators=[*default_evaluators(), *judges])

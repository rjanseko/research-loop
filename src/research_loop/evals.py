from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import Evaluator, EvaluatorContext

from .benchmarks.models import BenchmarkCaseSpec


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
    blocked_source_accesses: list[str] = field(default_factory=list)
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
    def evaluate(self, ctx: EvaluatorContext[Any, BenchmarkOutput]) -> float:
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
    def evaluate(self, ctx: EvaluatorContext[BenchmarkCaseSpec, BenchmarkOutput]) -> float | dict[str, float]:
        if not ctx.inputs.blocked_urls:
            return {}
        return 1.0 if not ctx.output.blocked_source_accesses else 0.0


class EvalIntegrity(Evaluator[BenchmarkCaseSpec, BenchmarkOutput]):
    def evaluate(self, ctx: EvaluatorContext[BenchmarkCaseSpec, BenchmarkOutput]) -> float | dict[str, float]:
        if not ctx.inputs.leakage_sensitive:
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


def make_dataset(cases: list[Case]) -> Dataset:
    return Dataset(
        name="research_loop",
        cases=cases,
        evaluators=[
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
        ],
    )

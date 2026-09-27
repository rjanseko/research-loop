"""Scout agents and the checks their outputs must pass.

An output that fails a check gets one retry that names the problem; a second failure ends the call
(UnexpectedModelBehavior). Models are chosen per run (models.py), not here.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from pydantic_ai import Agent, ModelRetry, RunContext

from .acquisition import SourcePolicy
from .evidence import inline_source_ids, strip_inline_citations
from .prompts import INSTRUCTIONS
from .schemas import (
    Depth,
    FinalReport,
    GapAnalysis,
    ResearchPlan,
    ResearchQuestion,
    ResearchResult,
)


@dataclass(frozen=True)
class PlanLimits:
    """The most questions a plan may have at each depth, and the depth the user chose, if any."""

    max_questions: Mapping[str, int]
    depth: Depth | None = None


@dataclass(frozen=True)
class Assignment:
    """A scout's question and the sources it may not use."""

    question: ResearchQuestion
    source_policy: SourcePolicy = field(default_factory=SourcePolicy)
    # The coverage items a claim may say it addresses.
    coverage_ids: frozenset[str] = frozenset()


@dataclass(frozen=True)
class GapRefs:
    """The planned question IDs a gap may refer to, and how many gaps may be chosen."""

    question_ids: frozenset[str]
    max_gaps: int = 1


@dataclass(frozen=True)
class LedgerRefs:
    """What the synthesizer may cite: ledger claim IDs, and the source IDs behind each claim."""

    claim_ids: frozenset[str]
    claim_sources: Mapping[str, frozenset[str]] = field(default_factory=dict)


planner_agent = Agent(name="planner", output_type=ResearchPlan, deps_type=PlanLimits, instructions=INSTRUCTIONS["planner"])
scout_agent = Agent(name="scout", output_type=ResearchResult, deps_type=Assignment, instructions=INSTRUCTIONS["scout"])
gap_agent = Agent(name="gap_analyzer", output_type=GapAnalysis, deps_type=GapRefs, instructions=INSTRUCTIONS["gap_analyzer"])
synthesizer_agent = Agent(name="synthesizer", output_type=FinalReport, deps_type=LedgerRefs, instructions=INSTRUCTIONS["synthesizer"])


def _retry_on(problems: Iterable[str]) -> None:
    if problems := list(problems):
        raise ModelRetry(" ".join(problems))


@planner_agent.output_validator
def _plan_is_workable(ctx: RunContext[PlanLimits], output: ResearchPlan) -> ResearchPlan:
    ids = [question.id for question in output.questions]
    problems = []
    if not ids:
        problems.append("The plan has no research questions; return at least one.")
    if repeated := sorted({question_id for question_id in ids if ids.count(question_id) > 1}):
        problems.append(f"Question IDs must be unique; repeated: {', '.join(repeated)}.")
    item_ids = [item.id for item in output.coverage]
    if repeated := sorted({item_id for item_id in item_ids if item_ids.count(item_id) > 1}):
        problems.append(f"Coverage item IDs must be unique; repeated: {', '.join(repeated)}.")
    if unknown := sorted({item for q in output.questions for item in q.covers} - set(item_ids)):
        problems.append(f"Questions name coverage items that do not exist: {', '.join(unknown)}.")
    depth = ctx.deps.depth or output.depth
    if len(ids) > (cap := ctx.deps.max_questions[depth]):
        problems.append(f"The plan has {len(ids)} questions; a {depth} plan has at most {cap}, "
                        "merging overlapping ones.")
    _retry_on(problems)
    # The user's choice of depth wins over the planner's.
    return output if output.depth == depth else output.model_copy(update={"depth": depth})


@gap_agent.output_validator
def _gaps_name_plan_questions(ctx: RunContext[GapRefs], output: GapAnalysis) -> GapAnalysis:
    problems = []
    if unknown := sorted({gap.question_id for gap in output.gaps} - ctx.deps.question_ids):
        problems.append(f"Unknown question IDs: {', '.join(unknown)}. Use an ID in the supplied plan.")
    if len(output.gaps) > ctx.deps.max_gaps:
        problems.append(f"{len(output.gaps)} gaps were selected; select at most {ctx.deps.max_gaps}, "
                        "keeping those most likely to change the answer.")
    _retry_on(problems)
    return output


@scout_agent.output_validator
def _result_fits_assignment(ctx: RunContext[Assignment], output: ResearchResult) -> ResearchResult:
    """File the result under the question asked, and refuse evidence from blocked sources."""
    blocked = sorted({entry for claim in output.claims for item in claim.evidence
                      if item.source.url and (entry := ctx.deps.source_policy.blocks(str(item.source.url)))})
    if blocked:
        _retry_on([(f"These sources are blocked for this task: {', '.join(blocked)}. Drop the evidence that cites "
                    "them, or support the claim from other sources.")])
    question = ctx.deps.question
    # A claim's unknown coverage IDs are dropped, not retried: a retry would redo the whole research call.
    known = ctx.deps.coverage_ids
    claims = [claim.model_copy(update={"covers": [item for item in claim.covers if item in known]})
              for claim in output.claims]
    return output.model_copy(update={"question_id": question.id, "question": question.question, "claims": claims})


# Added to a report whose inline citations named sources that none of its listed claims rest on.
DROPPED_CITATIONS_CAVEAT = (
    "Inline citations to {ids} were removed, because none of the evidence claims this report lists rests on "
    "those sources; the statements they followed may have less support than first cited."
)


@synthesizer_agent.output_validator
def _report_cites_ledger_claims(ctx: RunContext[LedgerRefs], output: FinalReport) -> FinalReport:
    unknown = sorted(set(output.claim_ids_used) - ctx.deps.claim_ids)
    if unknown:
        _retry_on([(f"These claim IDs do not exist: {', '.join(unknown[:25])}. Cite only claim IDs that appear in "
                    "the supplied research, copied exactly, or drop the citation.")])
    behind = frozenset(source_id for claim_id in output.claim_ids_used for source_id in ctx.deps.claim_sources.get(claim_id, ()))
    cited = {source_id for text in output.cited_texts for source_id in inline_source_ids(text)}
    if stray := sorted(cited - behind, key=lambda source_id: int(source_id[1:])):
        # Dropped rather than retried: a retry resends the whole ledger, and one retry for five stray citations
        # doubled a settings-study synthesis's cost. Dropping a citation never makes a statement look better
        # supported than it is, and the caveat says which were dropped.
        return output.model_copy(update={
            "answer": strip_inline_citations(output.answer, behind),
            "executive_summary": strip_inline_citations(output.executive_summary, behind),
            "caveats": [*(strip_inline_citations(caveat, behind) for caveat in output.caveats),
                        DROPPED_CITATIONS_CAVEAT.format(ids=", ".join(stray))],
        })
    return output

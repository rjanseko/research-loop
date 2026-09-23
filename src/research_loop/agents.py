from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from pydantic_ai import Agent, ModelRetry, RunContext

from .schemas import (
    CampaignSynthesis,
    FinalReport,
    GapAnalysis,
    ResearchPlan,
    ResearchQuestion,
    ResearchResult,
    VerificationReport,
)


# What each role's output is checked against. A mismatch gets one retry that names it; a
# repeated mismatch fails the run (UnexpectedModelBehavior).


@dataclass(frozen=True)
class PlanLimits:
    max_questions: int


@dataclass(frozen=True)
class ResearchAssignment:
    """A scout's or deep dive's question, and the attachments its evidence may cite."""

    question: ResearchQuestion
    attachment_ids: frozenset[str] = frozenset()


@dataclass(frozen=True)
class LedgerRefs:
    """What gap analysis, synthesis, and verification may cite: ledger claim and question IDs."""

    claim_ids: frozenset[str]
    question_ids: frozenset[str]


planner_agent = Agent(
    output_type=ResearchPlan,
    deps_type=PlanLimits,
    instructions=(
        "Decompose the user's objective into independent, evidence-seeking research questions. "
        "Prefer questions answerable from primary or authoritative sources. Questions should be "
        "non-overlapping enough to parallelize. Inspect attachment metadata/tools when local materials are supplied, "
        "and mark image-dependent questions as requires_multimodal. Treat any supplied constraints as hard requirements. "
        "Do not answer the questions yourself."
    ),
)

scout_agent = Agent(
    output_type=ResearchResult,
    deps_type=ResearchAssignment,
    instructions=(
        "Investigate exactly one research question. Use web and scholar tools when evidence is needed. "
        "Return atomic claims with source-backed evidence. Prefer primary, official, paper, or "
        "documentation sources over summaries. Summarize each piece of evidence briefly in `excerpt`; when a "
        "claim rests on specific wording, also copy that wording exactly into `quote` from text a tool returned. "
        "Quotes are checked against your tool output, so leave `quote` empty rather than reconstruct it. For papers preserve DOI/arXiv/OpenAlex/ACL IDs, provider, publication status, and locator; never infer peer review from an arXiv DOI hint, and flag retracted records. Explicitly record "
        "contradictions and unresolved questions. When local attachments are available, use list_attachments, "
        "search_attachments, and read_attachment rather than guessing file contents. For attachment evidence, "
        "set source.attachment_id to the stable attachment ID, source.locator to the page/sheet/row/chunk location, "
        "source.title to the file name, and source.source_type='attachment'; do not invent file:// URLs. "
        "Treat supplied constraints as hard requirements: never open or rely on blocked URLs, and never search for "
        "benchmark answer datasets or evaluator artifacts. Do not write a polished report."
    ),
)

gap_agent = Agent(
    output_type=GapAnalysis,
    deps_type=LedgerRefs,
    instructions=(
        "Inspect the evidence ledger for missing evidence, contradictions, weak sourcing, stale evidence, "
        "and low confidence. Escalate only gaps that could materially change the final answer."
    ),
)

deep_dive_agent = Agent(
    output_type=ResearchResult,
    deps_type=ResearchAssignment,
    instructions=(
        "Resolve one difficult research gap. Use web and scholar tools efficiently. Preserve preprint versus published status and scholarly IDs. Favor primary "
        "or authoritative sources, look for disconfirming evidence, and explicitly state when the evidence "
        "remains inconclusive. Copy exact wording into evidence `quote` only from text a tool returned; quotes "
        "are checked against your tool output. Use normalized attachment tools when local materials are supplied; cite attachment "
        "evidence with attachment_id + locator and never invent local URLs. Treat supplied constraints as hard "
        "requirements: never open or rely on blocked URLs, and never search for benchmark answer datasets or "
        "evaluator artifacts. Return structured evidence, not prose polish."
    ),
)

synthesizer_agent = Agent(
    output_type=FinalReport,
    deps_type=LedgerRefs,
    instructions=(
        "Synthesize only from the supplied evidence ledger. In `claims`, attach every material factual "
        "statement to the exact evidence-ledger claim IDs that support it. Evidence marked "
        "`quote_check: not_found` quotes wording that no research tool returned; do not rest a statement "
        "on it alone. Preserve uncertainty and disagreement. Respect supplied benchmark/source constraints "
        "and do not cite blocked sources. Do not invent missing evidence or citations."
    ),
)

verifier_agent = Agent(
    output_type=VerificationReport,
    deps_type=LedgerRefs,
    instructions=(
        "Audit the proposed report claim-by-claim against the supplied evidence. Flag unsupported, "
        "overstated, stale, mismatched, or contradictory statements. Verify that cited claim IDs exist "
        "and really support each statement. Evidence marked `quote_check: not_found` quotes wording that "
        "no research tool returned: treat it as unsupported unless other evidence supports the claim. Any "
        "follow-up gap must reference an existing question_id from the supplied evidence ledger. Treat supplied constraints as hard requirements and flag any "
        "evidence that appears to violate them. Recommend more research only when the issue is material."
    ),
)

def _unknown(kind: str, ids: Iterable[str], known: frozenset[str], fix: str) -> list[str]:
    unknown = sorted(set(ids) - known)
    return [f"These {kind} do not exist: {', '.join(unknown[:25])}. {fix}"] if unknown else []


def _unknown_claims(ids: Iterable[str], refs: LedgerRefs) -> list[str]:
    return _unknown("claim IDs", ids, refs.claim_ids,
                    "Cite only claim IDs that appear in the supplied evidence, copied exactly, or drop the citation.")


def _unknown_questions(ids: Iterable[str], refs: LedgerRefs) -> list[str]:
    return _unknown("question IDs", ids, refs.question_ids,
                    "Name only question IDs from the supplied plan or evidence, copied exactly, or drop the gap.")


def _retry_on(problems: list[str]) -> None:
    if problems:
        raise ModelRetry(" ".join(problems))


@planner_agent.output_validator
def _plan_is_workable(ctx: RunContext[PlanLimits], output: ResearchPlan) -> ResearchPlan:
    ids = [question.id for question in output.questions]
    problems = []
    if not ids:
        problems.append("The plan has no research questions; return at least one.")
    if repeated := sorted({question_id for question_id in ids if ids.count(question_id) > 1}):
        problems.append(f"Question IDs must be unique; repeated: {', '.join(repeated)}.")
    if len(ids) > ctx.deps.max_questions:
        problems.append(f"The plan has {len(ids)} questions; return at most {ctx.deps.max_questions}, "
                        "merging overlapping ones.")
    _retry_on(problems)
    return output


def _result_fits_assignment(ctx: RunContext[ResearchAssignment], output: ResearchResult) -> ResearchResult:
    """File the result under the question asked, and reject attachments the run does not have."""
    _retry_on(_unknown(
        "attachment IDs",
        (item.source.attachment_id for claim in output.claims for item in claim.evidence if item.source.attachment_id),
        ctx.deps.attachment_ids,
        "Cite attachments only by the IDs list_attachments returns, or drop that evidence.",
    ))
    question = ctx.deps.question
    return output.model_copy(update={"question_id": question.id, "question": question.question})


scout_agent.output_validator(_result_fits_assignment)
deep_dive_agent.output_validator(_result_fits_assignment)


@gap_agent.output_validator
def _gaps_name_plan_questions(ctx: RunContext[LedgerRefs], output: GapAnalysis) -> GapAnalysis:
    _retry_on(_unknown_questions((gap.question_id for gap in output.gaps), ctx.deps))
    return output


@synthesizer_agent.output_validator
def _report_cites_ledger_claims(ctx: RunContext[LedgerRefs], output: FinalReport) -> FinalReport:
    _retry_on(_unknown_claims(output.claim_ids_used, ctx.deps))
    return output


@verifier_agent.output_validator
def _verification_cites_ledger(ctx: RunContext[LedgerRefs], output: VerificationReport) -> VerificationReport:
    _retry_on(_unknown_claims((claim_id for check in output.checks for claim_id in check.claim_ids), ctx.deps)
              + _unknown_questions((gap.question_id for gap in output.followups), ctx.deps))
    return output


# Campaign-level only: runs once over completed campaign questions, outside research-graph-v1.
campaign_synthesizer_agent = Agent(
    output_type=CampaignSynthesis,
    deps_type=frozenset[str],
    # Two citation-fix retries; campaign synthesis.max_requests bounds the total.
    retries={"output": 2},
    instructions=(
        "Synthesize a research campaign from the supplied per-question reports and evidence only; do not "
        "research further or use outside knowledge. Cite evidence with the exact claim refs supplied (for "
        "example 'q01/q1/c3') and never invent refs. Classify findings as well_supported (consistent evidence "
        "from multiple independent tier A-C sources), preliminary (single source, preprint-only, or narrow "
        "evaluation), vendor_claims (results reported by a vendor without independent replication), "
        "contradictory (cite both sides), or unknowns. Keep preprint, submission, and published status "
        "distinct. Evidence with `quote_check: not_found` quotes wording that no research tool returned; it "
        "cannot make a finding well_supported. Each question's `verification.findings` lists statements its "
        "verifier could not support or rated major: never present those as well_supported; carry material ones into preliminary, "
        "contradictory, or unknowns. In the benchmark catalog, record versions, scope, evaluation method, "
        "and limits only as the evidence states them. Hypotheses must be falsifiable, cite supporting and "
        "any contradicting refs, and propose an experiment with an expected metric and an estimated cost. "
        "Prefer fewer, well-grounded entries over broad coverage."
    ),
)


@campaign_synthesizer_agent.output_validator
def _campaign_refs_exist(ctx: RunContext[frozenset[str]], output: CampaignSynthesis) -> CampaignSynthesis:
    problems = output.citation_problems(ctx.deps)
    if problems:
        raise ModelRetry("Fix these citation problems using only supplied claim refs: " + "; ".join(problems[:25]))
    return output

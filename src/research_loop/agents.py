from __future__ import annotations

from collections.abc import Iterable

from pydantic_ai import Agent, ModelRetry, RunContext

from .schemas import (
    CampaignSynthesis,
    FinalReport,
    GapAnalysis,
    ResearchPlan,
    ResearchResult,
    VerificationReport,
)


planner_agent = Agent(
    output_type=ResearchPlan,
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
    instructions=(
        "Inspect the evidence ledger for missing evidence, contradictions, weak sourcing, stale evidence, "
        "and low confidence. Escalate only gaps that could materially change the final answer."
    ),
)

deep_dive_agent = Agent(
    output_type=ResearchResult,
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
    deps_type=frozenset[str],  # the evidence ledger's claim IDs
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
    deps_type=frozenset[str],  # the evidence ledger's claim IDs
    instructions=(
        "Audit the proposed report claim-by-claim against the supplied evidence. Flag unsupported, "
        "overstated, stale, mismatched, or contradictory statements. Verify that cited claim IDs exist "
        "and really support each statement. Evidence marked `quote_check: not_found` quotes wording that "
        "no research tool returned: treat it as unsupported unless other evidence supports the claim. Any "
        "follow-up gap must reference an existing question_id from the supplied evidence ledger. Treat supplied constraints as hard requirements and flag any "
        "evidence that appears to violate them. Recommend more research only when the issue is material."
    ),
)

def _require_ledger_claims(cited: Iterable[str], ledger_claim_ids: frozenset[str]) -> None:
    """Ask for another answer when output cites claim IDs the evidence ledger does not have."""
    unknown = sorted(set(cited) - ledger_claim_ids)
    if unknown:
        raise ModelRetry(
            f"These claim IDs are not in the evidence ledger: {', '.join(unknown[:25])}. Cite only claim "
            "IDs that appear in the supplied evidence, copied exactly, or drop the citation."
        )


@synthesizer_agent.output_validator
def _report_cites_ledger_claims(ctx: RunContext[frozenset[str]], output: FinalReport) -> FinalReport:
    _require_ledger_claims(output.claim_ids_used, ctx.deps)
    return output


@verifier_agent.output_validator
def _checks_cite_ledger_claims(ctx: RunContext[frozenset[str]], output: VerificationReport) -> VerificationReport:
    _require_ledger_claims((claim_id for check in output.checks for claim_id in check.claim_ids), ctx.deps)
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

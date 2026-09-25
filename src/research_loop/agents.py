from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from pydantic_ai import Agent, ModelRetry, RunContext

from .acquisition import SourcePolicy
from .ledger import inline_source_ids, strip_inline_citations
from .schemas import (
    FinalReport,
    GapAnalysis,
    LongHorizonSynthesis,
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
    """A scout's or deep dive's question, the attachments its evidence may cite, and the blocked sources it may not."""

    question: ResearchQuestion
    attachment_ids: frozenset[str] = frozenset()
    source_policy: SourcePolicy = field(default_factory=SourcePolicy)


@dataclass(frozen=True)
class LedgerRefs:
    """What gap analysis, synthesis, and verification may cite: ledger claim and question IDs.

    `claim_sources` maps each claim ID to the source IDs its evidence cites, so a report's inline
    [sN] citations can be checked against the claims it lists.
    """

    claim_ids: frozenset[str]
    question_ids: frozenset[str]
    claim_sources: Mapping[str, frozenset[str]] = field(default_factory=dict)


# What each agent is told. Kept in one table so prompt_fingerprint() can record it: manifests change
# whenever an instruction or an output schema does.
INSTRUCTIONS: dict[str, str] = {
    "planner": (
        "Decompose the user's objective into independent, evidence-seeking research questions. "
        "Prefer questions answerable from primary or authoritative sources. Questions should be "
        "non-overlapping enough to parallelize. Inspect attachment metadata/tools when local materials are supplied, "
        "and mark image-dependent questions as requires_multimodal. Treat any supplied constraints as hard requirements. "
        "When the prompt includes a `budget`, every question costs one scout call and may get a deep dive, all "
        "paid from `research_usd`: keep each question to one focused inquiry rather than a bundle of sub-questions, "
        "and plan fewer questions when the budget is small relative to the per-call caps. "
        "Do not answer the questions yourself."
    ),
    "scout": (
        "Investigate exactly one research question. Use web and scholar tools when evidence is needed. "
        "Return atomic claims with source-backed evidence. Prefer primary, official, paper, or "
        "documentation sources over summaries. Summarize each piece of evidence briefly in `excerpt`; when a "
        "claim rests on specific wording, also copy that wording exactly into `quote` from text a tool returned. "
        "Quotes and cited source URLs are checked against your tool output, so leave `quote` empty rather than reconstruct it, and cite only sources a tool returned. For papers preserve DOI/arXiv/OpenAlex/ACL IDs, provider, publication status, and locator; never infer peer review from an arXiv DOI hint, and flag retracted records. Explicitly record "
        "contradictions and unresolved questions. When local attachments are available, use list_attachments, "
        "search_attachments, and read_attachment rather than guessing file contents. For attachment evidence, "
        "set source.attachment_id to the stable attachment ID, source.locator to the page/sheet/row/chunk location, "
        "source.title to the file name, and source.source_type='attachment'; do not invent file:// URLs. "
        "Treat supplied constraints as hard requirements: never open or rely on blocked URLs, and never search for "
        "benchmark answer datasets or evaluator artifacts. Do not write a polished report."
    ),
    "gap_analyst": (
        "Inspect the evidence ledger for missing evidence, contradictions, weak sourcing, stale evidence, "
        "and low confidence. Evidence cites `source_id` from the prompt's `sources` list. "
        "When an evidence item has a `quote`, its excerpt is omitted unless the quote was not found. "
        "`supports` is omitted when "
        "the evidence supports the claim. Escalate only gaps that could materially change the final answer."
    ),
    "deep_dive": (
        "Resolve one difficult research gap. Use web and scholar tools efficiently. Preserve preprint versus published status and scholarly IDs. Favor primary "
        "or authoritative sources, look for disconfirming evidence, and explicitly state when the evidence "
        "remains inconclusive. Copy exact wording into evidence `quote` only from text a tool returned; quotes "
        "and cited source URLs are checked against your tool output, so cite only sources a tool returned. Use normalized attachment tools when local materials are supplied; cite attachment "
        "evidence with attachment_id + locator and never invent local URLs. Treat supplied constraints as hard "
        "requirements: never open or rely on blocked URLs, and never search for benchmark answer datasets or "
        "evaluator artifacts. Return structured evidence, not prose polish."
    ),
    "synthesizer": (
        "Synthesize only from the supplied evidence ledger. Evidence cites `source_id` from the prompt's "
        "`sources` list. When an evidence item has a `quote`, its excerpt is omitted unless the quote was "
        "not found. `supports` is "
        "omitted when the evidence supports the claim. In `claims`, attach every material factual "
        "statement to the exact evidence-ledger claim IDs that support it. Evidence marked "
        "`quote_check: not_found` quotes wording, and evidence marked `source_check: not_found` cites a source, "
        "that no research tool returned; do not rest a statement on such evidence alone. Preserve uncertainty and disagreement. Respect supplied benchmark/source constraints "
        "and do not cite blocked sources. Do not invent missing evidence or citations. In `answer` and `caveats`, "
        "cite sources inline as [s1] or [s1, s4], using only the `source_id`s of the evidence behind claim IDs you "
        "list in `claims`."
    ),
    "verifier": (
        "Audit the proposed report claim-by-claim against the supplied evidence. Evidence cites "
        "`source_id` from the prompt's `sources` list. When an evidence item has a `quote`, its excerpt "
        "is omitted unless the quote was not found. `supports` is omitted when the evidence supports the "
        "claim. The evidence lists the "
        "claims the report cites and every claim a contradiction names; other results show only their "
        "question and conclusion. Flag unsupported, "
        "overstated, stale, mismatched, or contradictory statements. Verify that cited claim IDs exist "
        "and really support each statement. Evidence marked `quote_check: not_found` quotes wording, and "
        "evidence marked `source_check: not_found` cites a source, that no research tool returned: treat it as "
        "unsupported unless other evidence supports the claim. Any "
        "follow-up gap must reference an existing question_id from the supplied evidence ledger. Treat supplied constraints as hard requirements and flag any "
        "evidence that appears to violate them. Recommend more research only when the issue is material. The report cites "
        "sources inline as [sN] with the same IDs as the `sources` list; check that each cited source supports the "
        "statement it follows. Rate `severity` by how much the problem matters to the report: `none` when the "
        "statement is supported and correctly cited, `minor` when a citation or wording flaw leaves the statement's "
        "meaning intact, `major` when the statement is unsupported, wrong, or misleading as written."
    ),
    "long_horizon_synthesizer": (
        "Synthesize a multi-question research study from the supplied per-question claims only; do not "
        "research further or use outside knowledge. `sources` lists each cited work once: an id, its title, "
        "url or attachment id, and publication date when known. Each question's `claims` give "
        "a statement, the `claim_refs` that support it, `min_confidence` (the lowest confidence among the "
        "research claims those refs name), `source_ids` (the works whose evidence supports the statement), and "
        "`source_count`, how many there are; one work cited at two locations counts once. "
        "`contradicting_source_ids` and `contradicting_source_count`, when present, name the works whose "
        "evidence does not support the statement. Use a work's url and title to tell a vendor's own report from "
        "independent work, and its date to place it in the publication window. "
        "Cite claim refs exactly (for "
        "example 'q01/q1/c3') and never invent refs; source ids are not refs. `source_types` and `publication_statuses` describe the "
        "supporting sources. A missing `publication_statuses` list means every supporting source is unknown. "
        "Unknown statuses are left out of a list that is present, so a listed status is not the status of every source. `caveats` are the question "
        "report's limits, and `unresolved_questions` are what its research left open; both feed unknowns. `contradictions` name a disagreement and the refs on each side. A `retracted` flag "
        "means one cited source is retracted. Classify findings as well_supported (consistent evidence "
        "from multiple independent tier A-C sources, so `source_count` at least 2 and no "
        "`contradicting_source_count`, not merely more than one `source_types` string), preliminary (single source, preprint-only, or narrow "
        "evaluation), vendor_claims (results reported by a vendor without independent replication), "
        "contradictory (cite both sides), or unknowns. Keep preprint, submission, and published status "
        "distinct. A claim marked `quote_check: not_found`, `source_check: not_found`, or `retracted` cannot "
        "make a finding well_supported; the flag covers the whole report claim, even when other evidence on "
        "it is clean. Each question's `verification.findings` lists statements its "
        "verifier could not support or rated major: never present those as well_supported; carry material ones into preliminary, "
        "contradictory, or unknowns. In the benchmark catalog, record versions, scope, evaluation method, "
        "and limits only as the evidence states them. Hypotheses must be falsifiable, cite supporting and "
        "any contradicting refs, and propose an experiment with an expected metric and an estimated cost. "
        "Prefer fewer, well-grounded entries over broad coverage."
    ),
}


planner_agent = Agent(
    output_type=ResearchPlan,
    deps_type=PlanLimits,
    instructions=INSTRUCTIONS["planner"],
)

scout_agent = Agent(
    output_type=ResearchResult,
    deps_type=ResearchAssignment,
    instructions=INSTRUCTIONS["scout"],
)

gap_agent = Agent(
    output_type=GapAnalysis,
    deps_type=LedgerRefs,
    instructions=INSTRUCTIONS["gap_analyst"],
)

deep_dive_agent = Agent(
    output_type=ResearchResult,
    deps_type=ResearchAssignment,
    instructions=INSTRUCTIONS["deep_dive"],
)

synthesizer_agent = Agent(
    output_type=FinalReport,
    deps_type=LedgerRefs,
    instructions=INSTRUCTIONS["synthesizer"],
)

verifier_agent = Agent(
    output_type=VerificationReport,
    deps_type=LedgerRefs,
    instructions=INSTRUCTIONS["verifier"],
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
    """File the result under the question asked; reject unknown attachments and blocked sources."""
    sources = [item.source for claim in output.claims for item in claim.evidence]
    blocked = sorted({entry for source in sources
                      if source.url and (entry := ctx.deps.source_policy.blocks(str(source.url)))})
    _retry_on(_unknown(
        "attachment IDs",
        (source.attachment_id for source in sources if source.attachment_id),
        ctx.deps.attachment_ids,
        "Cite attachments only by the IDs list_attachments returns, or drop that evidence.",
    ) + ([(f"These sources are blocked for this task: {', '.join(blocked)}. Drop the evidence that cites "
           "them, or support the claim from other sources.")] if blocked else []))
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
    cited = {source_id for text in (output.answer, *output.caveats) for source_id in inline_source_ids(text)}
    behind_claims = frozenset(source_id for claim_id in output.claim_ids_used
                              for source_id in ctx.deps.claim_sources.get(claim_id, ()))
    problems = _unknown_claims(output.claim_ids_used, ctx.deps)
    stray = sorted(cited - behind_claims, key=lambda source_id: int(source_id[1:]))
    if stray and ctx.last_attempt and not problems:
        # A stray citation should not cost a finished run its report: drop it and keep the rest.
        return output.model_copy(update={
            "answer": strip_inline_citations(output.answer, behind_claims),
            "caveats": [strip_inline_citations(caveat, behind_claims) for caveat in output.caveats],
        })
    if stray:
        problems.append(
            f"These inline citations name no source behind the claim IDs listed in `claims`: {', '.join(stray[:25])}. "
            "Cite inline only the source_ids of evidence behind claim IDs you list, adding the claim if it supports "
            "the statement, or drop the citation."
        )
    _retry_on(problems)
    return output


@verifier_agent.output_validator
def _verification_cites_ledger(ctx: RunContext[LedgerRefs], output: VerificationReport) -> VerificationReport:
    _retry_on(_unknown_claims((claim_id for check in output.checks for claim_id in check.claim_ids), ctx.deps)
              + _unknown_questions((gap.question_id for gap in output.followups), ctx.deps))
    return output


# Study-level only: runs once over completed long-horizon questions, outside research-graph-v1.
long_horizon_synthesizer_agent = Agent(
    output_type=LongHorizonSynthesis,
    deps_type=frozenset[str],
    # One citation-fix retry (two requests). A second retry would send the prompt a third
    # time, and three sends of an eleven-question prompt do not fit synthesis.total_tokens_limit.
    retries={"output": 1},
    instructions=INSTRUCTIONS["long_horizon_synthesizer"],
)


@long_horizon_synthesizer_agent.output_validator
def _long_horizon_refs_exist(ctx: RunContext[frozenset[str]], output: LongHorizonSynthesis) -> LongHorizonSynthesis:
    problems = output.citation_problems(ctx.deps)
    if problems:
        raise ModelRetry("Fix these citation problems using only supplied claim refs: " + "; ".join(problems[:25]))
    return output


AGENTS: dict[str, Agent] = {
    "planner": planner_agent,
    "scout": scout_agent,
    "gap_analyst": gap_agent,
    "deep_dive": deep_dive_agent,
    "synthesizer": synthesizer_agent,
    "verifier": verifier_agent,
    "long_horizon_synthesizer": long_horizon_synthesizer_agent,
}


def prompt_fingerprint() -> str:
    """SHA-256 of every agent's instructions and output schema: what models are told and must return."""
    spec = {role: {"instructions": INSTRUCTIONS[role], "output_schema": agent.output_type.model_json_schema()}
            for role, agent in AGENTS.items()}
    return hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()

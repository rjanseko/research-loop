from __future__ import annotations

from pydantic_ai import Agent

from .schemas import FinalReport, GapAnalysis, ResearchPlan, ResearchResult, VerificationReport


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
        "Investigate exactly one research question. Use the web tools when evidence is needed. "
        "Return atomic claims with source-backed evidence. Prefer primary, official, paper, or "
        "documentation sources over summaries. Keep excerpts short and faithful. Explicitly record "
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
        "Resolve one difficult research gap. Use web tools persistently but efficiently. Favor primary "
        "or authoritative sources, look for disconfirming evidence, and explicitly state when the evidence "
        "remains inconclusive. Use normalized attachment tools when local materials are supplied; cite attachment "
        "evidence with attachment_id + locator and never invent local URLs. Treat supplied constraints as hard "
        "requirements: never open or rely on blocked URLs, and never search for benchmark answer datasets or "
        "evaluator artifacts. Return structured evidence, not prose polish."
    ),
)

synthesizer_agent = Agent(
    output_type=FinalReport,
    instructions=(
        "Synthesize only from the supplied evidence ledger. In `claims`, attach every material factual "
        "statement to the exact evidence-ledger claim IDs that support it. Preserve uncertainty and "
        "disagreement. Respect supplied benchmark/source constraints and do not cite blocked sources. "
        "Do not invent missing evidence or citations."
    ),
)

verifier_agent = Agent(
    output_type=VerificationReport,
    instructions=(
        "Audit the proposed report claim-by-claim against the supplied evidence. Flag unsupported, "
        "overstated, stale, mismatched, or contradictory statements. Verify that cited claim IDs exist "
        "and really support each statement. Any follow-up gap must reference an existing question_id "
        "from the supplied evidence ledger. Treat supplied constraints as hard requirements and flag any "
        "evidence that appears to violate them. Recommend more research only when the issue is material."
    ),
)

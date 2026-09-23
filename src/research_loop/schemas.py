from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl, model_validator
from pydantic.json_schema import SkipJsonSchema

# Recorded in manifests. 1: one `excerpt` field, excerpt or paraphrase.
# 2: `excerpt` summarizes; a verbatim `quote` is checked against the research run's tool output.
# 3: report and verifier citations must be ledger claim IDs; an unknown ID gets one retry, then fails.
EVIDENCE_VERSION = 3


class ResearchRole(StrEnum):
    PLANNER = "planner"
    SCOUT = "scout"
    GAP_ANALYST = "gap_analyst"
    DEEP_DIVE = "deep_dive"
    SYNTHESIZER = "synthesizer"
    VERIFIER = "verifier"


class SourceRef(BaseModel):
    url: HttpUrl | None = None
    attachment_id: str | None = None
    locator: str | None = None
    title: str
    source_type: Literal[
        "primary",
        "paper",
        "official",
        "news",
        "documentation",
        "secondary",
        "attachment",
        "unknown",
    ] = "unknown"
    published_at: str | None = None
    accessed_at: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    openalex_id: str | None = None
    acl_id: str | None = None
    publication_status: Literal[
        "peer_reviewed", "accepted_conference", "journal", "preprint",
        "conference_submission", "review", "official_documentation",
        "benchmark_repository", "vendor_technical_report", "blog",
        "general_web", "dataset", "unknown",
    ] = "unknown"
    provider: str | None = None
    full_text_url: HttpUrl | None = None
    is_retracted: bool | None = None

    @model_validator(mode="after")
    def validate_source_identity(self) -> "SourceRef":
        if self.url is None and not self.attachment_id:
            raise ValueError("source must provide either url or attachment_id")
        if self.attachment_id and not self.locator:
            self.locator = "unspecified location"
        return self

    @property
    def is_attachment(self) -> bool:
        return bool(self.attachment_id)


class Evidence(BaseModel):
    source: SourceRef
    excerpt: str = Field(description="Short summary or paraphrase of what the source says")
    quote: str | None = Field(
        default=None,
        description=(
            "Exact words copied from text one of your tools returned (a fetched page or paper, a search "
            "result, an abstract, or an attachment) when the claim rests on specific wording. Mark omissions "
            "with '...'. Quotes are checked against the tool output; leave this empty rather than reconstruct "
            "wording from memory."
        ),
    )
    supports: bool = True
    confidence: float = Field(ge=0.0, le=1.0)
    # Set by code after the research run (see quotes.py), never by the model: hidden from its schema,
    # and overwritten if a model supplies it anyway.
    quote_check: SkipJsonSchema[Literal["verified", "not_found"] | None] = None


class Claim(BaseModel):
    id: str
    statement: str
    evidence: list[Evidence] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class Contradiction(BaseModel):
    description: str
    claim_ids: list[str] = Field(default_factory=list)
    source_urls: list[HttpUrl] = Field(default_factory=list)


class ResearchConstraints(BaseModel):
    blocked_urls: list[str] = Field(default_factory=list)
    attachment_paths: list[str] = Field(default_factory=list)
    benchmark_id: str | None = None
    benchmark_case_id: str | None = None
    benchmark_suite: str | None = None
    notes: list[str] = Field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (
            self.blocked_urls or self.attachment_paths or self.benchmark_id
            or self.benchmark_case_id or self.benchmark_suite or self.notes
        )


class ResearchQuestion(BaseModel):
    id: str
    question: str
    priority: int = Field(default=3, ge=1, le=5)
    requires_primary_sources: bool = False
    requires_multimodal: bool = False
    expected_difficulty: Literal["low", "medium", "high"] = "medium"


class ResearchPlan(BaseModel):
    objective: str
    questions: list[ResearchQuestion]
    stop_conditions: list[str] = Field(default_factory=list)


class ResearchResult(BaseModel):
    question_id: str
    question: str
    conclusion: str
    claims: list[Claim] = Field(default_factory=list)
    contradictions: list[Contradiction] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)
    suggested_followups: list[str] = Field(default_factory=list)
    search_queries_used: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class Gap(BaseModel):
    question_id: str
    reason: Literal[
        "low_confidence",
        "missing_primary_source",
        "contradiction",
        "missing_evidence",
        "multimodal_needed",
    ]
    followup: str
    severity: int = Field(ge=1, le=5)


class GapAnalysis(BaseModel):
    resolved_question_ids: list[str] = Field(default_factory=list)
    gaps: list[Gap] = Field(default_factory=list)


class ReportClaim(BaseModel):
    statement: str
    claim_ids: list[str] = Field(default_factory=list)


class FinalReport(BaseModel):
    answer: str
    claims: list[ReportClaim] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)

    @property
    def claim_ids_used(self) -> list[str]:
        return sorted({claim_id for claim in self.claims for claim_id in claim.claim_ids})


class ClaimCheck(BaseModel):
    statement: str
    claim_ids: list[str] = Field(default_factory=list)
    supported: bool
    severity: Literal["none", "minor", "major"]
    explanation: str


class VerificationReport(BaseModel):
    checks: list[ClaimCheck] = Field(default_factory=list)
    needs_research: bool = False
    followups: list[Gap] = Field(default_factory=list)


# Tool-name fragments that mark acquisition tools: web, scholarly, and attachment.
_RESEARCH_TOOL_TOKENS = ("search", "fetch", "page", "url", "attachment")


def is_research_tool(tool_name: str) -> bool:
    name = tool_name.lower()
    return any(token in name for token in _RESEARCH_TOOL_TOKENS)


class ToolEvent(BaseModel):
    tool_name: str
    tool_call_id: str | None = None
    tool_kind: str | None = None
    provider_name: str | None = None
    args: Any = None
    result: Any = None
    outcome: str | None = None
    called_at: datetime | None = None
    returned_at: datetime | None = None

    @property
    def is_research_tool(self) -> bool:
        return is_research_tool(self.tool_name)


# Campaign-level synthesis over completed campaign questions. Claim refs such as
# "q01/q1/c3" identify claims in the aggregated campaign evidence ledger.
_CLAIM_REFS = "Campaign claim refs such as 'q01/q1/c3', copied exactly from the supplied evidence"


class CampaignFinding(BaseModel):
    statement: str
    claim_refs: list[str] = Field(default_factory=list, description=_CLAIM_REFS)


class CampaignFindings(BaseModel):
    well_supported: list[CampaignFinding] = Field(
        default_factory=list, description="Consistent evidence from multiple independent tier A-C sources"
    )
    preliminary: list[CampaignFinding] = Field(
        default_factory=list, description="Single-source, preprint-only, or narrowly evaluated results"
    )
    vendor_claims: list[CampaignFinding] = Field(
        default_factory=list, description="Results reported by a model or product vendor without independent replication"
    )
    contradictory: list[CampaignFinding] = Field(
        default_factory=list, description="Points where cited sources disagree; cite both sides"
    )
    unknowns: list[CampaignFinding] = Field(
        default_factory=list, description="Questions the evidence could not settle; refs optional"
    )


class BenchmarkEntry(BaseModel):
    name: str
    versions: list[str] = Field(default_factory=list)
    scope: str
    evaluation_method: str
    limits: list[str] = Field(default_factory=list)
    claim_refs: list[str] = Field(default_factory=list, description=_CLAIM_REFS)


class ArchitecturePattern(BaseModel):
    name: str
    description: str
    reported_effects: list[str] = Field(
        default_factory=list, description="Measured effects together with their evaluation conditions"
    )
    claim_refs: list[str] = Field(default_factory=list, description=_CLAIM_REFS)


class FailureMode(BaseModel):
    name: str
    description: str
    mitigations: list[str] = Field(default_factory=list)
    claim_refs: list[str] = Field(default_factory=list, description=_CLAIM_REFS)


class OpenQuestion(BaseModel):
    question: str
    why_open: str
    claim_refs: list[str] = Field(default_factory=list, description=_CLAIM_REFS)


class Hypothesis(BaseModel):
    id: str
    statement: str = Field(description="Falsifiable claim about coding-agent behavior")
    supporting_evidence: list[str] = Field(default_factory=list, description=_CLAIM_REFS)
    contradicting_evidence: list[str] = Field(default_factory=list, description=_CLAIM_REFS)
    confidence: float = Field(ge=0.0, le=1.0)
    proposed_experiment: str
    expected_metric: str
    estimated_cost: str = Field(description="Rough API, compute, and time cost of the experiment")


class CampaignSynthesis(BaseModel):
    summary: str = Field(description="Markdown overview of the campaign's conclusions")
    findings: CampaignFindings
    benchmark_catalog: list[BenchmarkEntry] = Field(default_factory=list)
    architecture_patterns: list[ArchitecturePattern] = Field(default_factory=list)
    failure_modes: list[FailureMode] = Field(default_factory=list)
    open_questions: list[OpenQuestion] = Field(default_factory=list)
    hypotheses: list[Hypothesis] = Field(default_factory=list)

    def citation_problems(self, known_refs: set[str] | frozenset[str]) -> list[str]:
        """Unknown claim refs anywhere, and evidence-bearing entries that cite nothing."""
        problems: list[str] = []

        def check(label: str, refs: list[str], *, required: bool) -> None:
            unknown = [ref for ref in refs if ref not in known_refs]
            if unknown:
                problems.append(f"{label} cites unknown refs {unknown}")
            elif required and not refs:
                problems.append(f"{label} cites no evidence")

        for section in CampaignFindings.model_fields:
            for index, finding in enumerate(getattr(self.findings, section)):
                check(f"findings.{section}[{index}]", finding.claim_refs, required=section != "unknowns")
        for field_name in ("benchmark_catalog", "architecture_patterns", "failure_modes"):
            for index, entry in enumerate(getattr(self, field_name)):
                check(f"{field_name}[{index}] {entry.name!r}", entry.claim_refs, required=True)
        for index, open_question in enumerate(self.open_questions):
            check(f"open_questions[{index}]", open_question.claim_refs, required=False)
        seen: set[str] = set()
        for hypothesis in self.hypotheses:
            if hypothesis.id in seen:
                problems.append(f"hypothesis id {hypothesis.id!r} is duplicated")
            seen.add(hypothesis.id)
            check(f"hypothesis {hypothesis.id!r} supporting_evidence", hypothesis.supporting_evidence, required=True)
            check(f"hypothesis {hypothesis.id!r} contradicting_evidence", hypothesis.contradicting_evidence, required=False)
        return problems

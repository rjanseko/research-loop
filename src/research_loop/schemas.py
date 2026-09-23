from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl, model_validator


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
    excerpt: str = Field(description="Short evidence excerpt or faithful paraphrase")
    supports: bool = True
    confidence: float = Field(ge=0.0, le=1.0)


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
        name = self.tool_name.lower()
        return any(token in name for token in ("search", "fetch", "page", "url", "attachment"))

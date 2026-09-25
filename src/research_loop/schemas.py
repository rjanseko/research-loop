"""The typed values a Scout run passes between its steps, and what it returns.

Models fill in plans, research results, and reports. Fields marked "set by code" are hidden from
the model's schema (`SkipJsonSchema`) and overwritten after each call, so a model can never claim
that a quote was verified or that it read a page in full.
"""
from __future__ import annotations

from typing import Any, Literal, get_args

from pydantic import (
    BaseModel,
    Field,
    HttpUrl,
    ValidationInfo,
    field_validator,
    model_validator,
)
from pydantic.json_schema import SkipJsonSchema

# Recorded on every run. 5: evidence records the access level it rests on (snippet, metadata, abstract,
# full text), and quotes and sources are checked against labeled tool output (evidence.py).
EVIDENCE_VERSION = 5

# How much of a source a tool returned, from least to most. Search results give a snippet, a scholarly
# record without an abstract gives metadata, arXiv and some OpenAlex records give an abstract, and a
# fetched page or PDF window gives full text.
Access = Literal["snippet", "metadata", "abstract", "full_text"]
ACCESS_ORDER: tuple[Access, ...] = get_args(Access)


class SourceRef(BaseModel):
    url: HttpUrl | None = None
    title: str
    doi: str | None = None
    arxiv_id: str | None = None
    publisher: str | None = Field(default=None, description="Who published the source, such as a vendor, lab, or journal")
    published_at: str | None = None
    source_type: Literal[
        "primary", "paper", "official", "documentation", "news", "secondary", "unknown",
    ] = "unknown"
    publication_status: Literal[
        "peer_reviewed", "accepted_conference", "journal", "preprint", "conference_submission",
        "official_documentation", "vendor_technical_report", "blog", "general_web", "dataset", "unknown",
    ] = "unknown"
    is_retracted: bool | None = None

    @field_validator("source_type", "publication_status", mode="before")
    @classmethod
    def _unlisted_label_is_unknown(cls, value: Any, info: ValidationInfo) -> Any:
        """A label outside the field's list is "unknown": rejecting it would redo a whole research call for one mislabel."""
        allowed = get_args(cls.model_fields[info.field_name].annotation)
        return value if value in allowed or not isinstance(value, str) else "unknown"

    @model_validator(mode="after")
    def _identified(self) -> SourceRef:
        if self.url is None and not self.doi and not self.arxiv_id:
            raise ValueError("a source needs a url, doi, or arxiv_id")
        return self


class Evidence(BaseModel):
    source: SourceRef
    excerpt: str = Field(description="Short summary or paraphrase of what the source says")
    quote: str | None = Field(
        default=None,
        description=(
            "Exact words copied from text one of your tools returned, when the claim rests on specific wording. "
            "Mark omissions with '...'. Quotes are checked against the tool output; leave this empty rather "
            "than reconstruct wording from memory."
        ),
    )
    supports: bool = True
    confidence: float = Field(ge=0.0, le=1.0)
    # Set by code (evidence.check_result), never by the model.
    quote_check: SkipJsonSchema[Literal["verified", "not_found"] | None] = None
    # The most complete tool output the quote was found in.
    quote_access: SkipJsonSchema[Access | None] = None
    source_check: SkipJsonSchema[Literal["observed", "not_found"] | None] = None
    # The most a tool returned of the cited source: a search snippet up to its full text.
    source_access: SkipJsonSchema[Access | None] = None


class Claim(BaseModel):
    id: str
    statement: str
    evidence: list[Evidence] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class Contradiction(BaseModel):
    description: str
    claim_ids: list[str] = Field(default_factory=list)


class ResearchQuestion(BaseModel):
    id: str
    question: str
    requires_primary_sources: bool = False


class ResearchPlan(BaseModel):
    questions: list[ResearchQuestion]


class UnreachedSource(BaseModel):
    """A fetch or lookup that returned nothing usable, and why."""

    target: str
    reason: str


class ResearchResult(BaseModel):
    question_id: str
    question: str
    conclusion: str
    claims: list[Claim] = Field(default_factory=list)
    contradictions: list[Contradiction] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list, description="What this research could not establish")
    confidence: float = Field(ge=0.0, le=1.0)
    # Set by code: queries and pages the research tried, which stay useful when it was cut off without claims.
    searches: SkipJsonSchema[list[str]] = Field(default_factory=list)
    pages_read: SkipJsonSchema[list[str]] = Field(default_factory=list)
    unreached: SkipJsonSchema[list[UnreachedSource]] = Field(default_factory=list)
    # Why the research stopped before returning a result, when it did: a limit, the deadline, or an error.
    cut_off: SkipJsonSchema[str | None] = None


class ReportClaim(BaseModel):
    statement: str
    claim_ids: list[str] = Field(default_factory=list)


class FinalReport(BaseModel):
    title: str = Field(description="A short, specific title for the report, without a subtitle")
    executive_summary: str = Field(description="The main findings in three to six sentences, cited inline like `answer`")
    answer: str
    claims: list[ReportClaim] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)

    @property
    def claim_ids_used(self) -> list[str]:
        return sorted({claim_id for claim in self.claims for claim_id in claim.claim_ids})

    @property
    def cited_texts(self) -> list[str]:
        """The parts of the report that cite sources inline as [sN]."""
        return [text for text in (self.executive_summary, self.answer, *self.caveats) if text]

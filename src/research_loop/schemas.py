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
# 6: a quote is verified only in its cited source's text; found elsewhere, it is misattributed.
# 7: a statement is `read` only when read evidence behind it carries a verified quote; one resting only on
# the research's summary of a read source is `paraphrase`, which makes the answer weak. Quote checks are
# unchanged, so stored v6 evidence marks still hold; only statement support is stricter.
EVIDENCE_VERSION = 7

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
    quote_check: SkipJsonSchema[Literal["verified", "misattributed", "not_found"] | None] = None
    # For a misattributed quote, the source whose text it was found in.
    quote_found_in: SkipJsonSchema[str | None] = None
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
    covers: list[str] = Field(default_factory=list, description="IDs of the coverage items this claim addresses")


class Contradiction(BaseModel):
    description: str
    claim_ids: list[str] = Field(default_factory=list)


class ResearchQuestion(BaseModel):
    id: str
    question: str
    requires_primary_sources: bool = False
    covers: list[str] = Field(default_factory=list, description="IDs of the coverage items this question serves")


class CoverageItem(BaseModel):
    """One thing a sufficient answer must address. The planner writes them from the question; code adds
    the members and categories that research found named but did not establish (`ResearchResult.open_items`)."""

    id: str
    requirement: str = Field(description="What the answer must address, such as one category of a requested set")
    kind: Literal["category", "dimension", "constraint", "assumption"] = Field(
        "category", description="category: a member or group of a requested set; dimension: something to compare "
        "across items; constraint: a limit such as a date range; assumption: how an ambiguity was read")


# How much research a question warrants; each depth has its own limits (config.ScoutLimits.for_depth).
Depth = Literal["quick", "standard", "deep"]


class CoverageState(BaseModel):
    """Where one coverage item stands, as code finds it in the ledger and the report."""

    id: str
    requirement: str
    kind: str
    # "plan", or the question whose research named it as an open item.
    origin: str = "plan"
    status: Literal["covered", "open"]
    claim_ids: list[str] = Field(default_factory=list)
    # With a report: whether it cites a covering claim, lists the item as not established, or does neither.
    in_report: Literal["cited", "not_established", "missing"] | None = None


class ResearchPlan(BaseModel):
    questions: list[ResearchQuestion]
    # Plans made before coverage items existed have none, and run as they did.
    coverage: list[CoverageItem] = Field(default_factory=list)
    # Plans made before depths existed read as standard, which is what they ran with.
    depth: Depth = Field("standard", description="quick, standard, or deep: how much research the question warrants")


class MaterialGap(BaseModel):
    """One missing piece of evidence that could change the answer."""

    question_id: str = Field(description="ID of an existing planned research question")
    follow_up_question: str = Field(description="One precise question for a researcher to investigate")
    reason: str = Field(description="How resolving this gap could change the answer")
    coverage_id: str | None = Field(None, description="ID of the coverage item this follow-up targets, if any")


class GapAnalysis(BaseModel):
    """Up to `max_gaps` decisive follow-ups, each researched in parallel; an empty list means synthesize
    what is known."""

    gaps: list[MaterialGap] = Field(default_factory=list)


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
    open_items: list[str] = Field(default_factory=list, description=(
        "Members or categories of the requested set that sources name but this research did not establish, "
        "each as a short name such as 'Inorganic Crystal Structure Database (ICSD)'"))
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
    not_established: list[str] = Field(default_factory=list, description=(
        "IDs of coverage items the report could not establish from the research, which it says so about"))

    @property
    def claim_ids_used(self) -> list[str]:
        return sorted({claim_id for claim in self.claims for claim_id in claim.claim_ids})

    @property
    def cited_texts(self) -> list[str]:
        """The parts of the report that cite sources inline as [sN]."""
        return [text for text in (self.executive_summary, self.answer, *self.caveats) if text]

"""The evidence ledger, and the checks that tie each claim to what the tools actually returned.

Research tools label every result with how much of a source it holds (`schemas.Access`): a search
snippet, scholarly metadata, an abstract, or full text. After each research call, code checks the
result against those labeled texts, and the model cannot set the outcome:

- A quote is `verified` when each of its segments, split at '...' and bracketed insertions, appears
  in one labeled text, comparing only letters and digits after NFKC normalization and case folding.
  PDF extraction spacing, list bullets, and comment signs therefore never decide the check.
  `quote_access` is the most complete kind of text it was found in.
- A cited source is `observed` when a tool returned it as an item (a search result, a scholarly
  record, or a fetched page), matched by URL (ignoring scheme, `www.`, query, fragment, and a trailing
  slash), DOI, or arXiv ID. `source_access` is the most any tool returned of it. A source that only
  appeared as a link inside another page is `not_found`: the research never read it.

The ledger collects results in plan order and gives every claim a unique ID such as `q2/c3`, which
reports cite. Sources get IDs such as `s4` from the ledger's contents, so a report's inline
citations name the same source in every prompt and rendering built from one ledger.
"""
from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Callable, Collection, Iterable
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import unquote, urlparse

from .schemas import (
    ACCESS_ORDER,
    Access,
    Claim,
    Evidence,
    FinalReport,
    ResearchResult,
    SourceRef,
)

# ---------------------------------------------------------------------------------------------
# Labeled tool output and the checks against it

# Only letters and digits are compared. NFKC folds ligatures and the ellipsis character.
_NON_WORD = re.compile(r"[\W_]+")
_GAP = re.compile(r"\.\.\.|\[[^\]]*\]")
_ARXIV_ID = re.compile(r"\d{4}\.\d{4,5}")


def _key(text: str) -> str:
    return _NON_WORD.sub("", unicodedata.normalize("NFKC", text).casefold())


def _segments(quote: str) -> list[str]:
    return [segment for segment in map(_key, _GAP.split(unicodedata.normalize("NFKC", quote))) if segment]


def _url_key(url: str) -> str:
    parsed = urlparse(url.strip().rstrip(".,;:)]"))
    return (parsed.hostname or "").removeprefix("www.") + unquote(parsed.path).rstrip("/")


def identity_keys(*, url: str | None = None, doi: str | None = None, arxiv_id: str | None = None) -> frozenset[str]:
    """Keys that identify one work, so a citation matches the tool result it came from.

    A doi.org URL also gives the DOI, and an arXiv URL its paper ID, so an abs page, its PDF, and a
    record citing the arXiv ID all match. Versions (v2) are ignored.
    """
    keys: set[str] = set()
    if url:
        location = _url_key(url)
        keys.add(f"url:{location.lower()}")
        if location.startswith("doi.org/"):
            doi = doi or location.removeprefix("doi.org/")
        if location.startswith("arxiv.org/") and (match := _ARXIV_ID.search(location)):
            keys.add(f"arxiv:{match.group(0)}")
    if doi:
        keys.add("doi:" + doi.strip().lower().removeprefix("https://doi.org/").removeprefix("doi:"))
    if arxiv_id and (match := _ARXIV_ID.search(arxiv_id)):
        keys.add(f"arxiv:{match.group(0)}")
    return frozenset(keys)


def source_identity(source: SourceRef) -> frozenset[str]:
    return identity_keys(url=str(source.url) if source.url else None, doi=source.doi, arxiv_id=source.arxiv_id)


@dataclass(frozen=True)
class ToolText:
    """One source as a research tool returned it: how much of it, its identity, and its text."""

    access: Access
    keys: frozenset[str]
    text: str


def _more(a: Access | None, b: Access) -> Access:
    return b if a is None or ACCESS_ORDER.index(b) > ACCESS_ORDER.index(a) else a


class ToolOutputIndex:
    """What one research call's tools returned, for checking its result."""

    def __init__(self, texts: Iterable[ToolText]) -> None:
        self.texts = list(texts)
        self._haystacks = [(item.access, _key(item.text)) for item in self.texts]

    def quote_access(self, quote: str | None) -> Access | None:
        """The most complete kind of text containing `quote`; None when none does or it has no words."""
        segments = _segments(quote) if quote else []
        if not segments:
            return None
        found: Access | None = None
        for access, haystack in self._haystacks:
            if all(segment in haystack for segment in segments):
                found = _more(found, access)
        return found

    def source_access(self, source: SourceRef) -> Access | None:
        """The most any tool returned of `source`; None when no tool returned it."""
        keys = source_identity(source)
        found: Access | None = None
        for item in self.texts:
            if keys & item.keys:
                found = _more(found, item.access)
        return found


def check_evidence(item: Evidence, index: ToolOutputIndex) -> Evidence:
    quote_access = index.quote_access(item.quote)
    has_quote = bool(item.quote and _segments(item.quote))
    source_access = index.source_access(item.source)
    return item.model_copy(update={
        "quote_check": ("verified" if quote_access else "not_found") if has_quote else None,
        "quote_access": quote_access,
        "source_check": "observed" if source_access else "not_found",
        "source_access": source_access,
    })


def check_result(result: ResearchResult, texts: Iterable[ToolText]) -> ResearchResult:
    """`result` with every evidence item's checks set from `texts`, the labeled output its call's tools returned."""
    index = ToolOutputIndex(texts)
    claims = [claim.model_copy(update={"evidence": [check_evidence(item, index) for item in claim.evidence]})
              for claim in result.claims]
    return result.model_copy(update={"claims": claims})


# How well a report statement's evidence was read.
Support = Literal["read", "shallow", "unsupported"]


def evidence_is_read(item: Evidence) -> bool:
    """Supporting evidence from a source read as an abstract or in full, whose quote, if any, was found."""
    return (item.supports and item.source_access in ("abstract", "full_text")
            and item.quote_check != "not_found")


def support_level(claims: Iterable[Claim]) -> Support:
    """`read` when some supporting evidence was read (evidence_is_read); `shallow` when the only support is
    a snippet, metadata, an unverified quote, or a source no tool returned; `unsupported` when nothing supports it."""
    supporting = [item for claim in claims for item in claim.evidence if item.supports]
    if not supporting:
        return "unsupported"
    return "read" if any(evidence_is_read(item) for item in supporting) else "shallow"


# ---------------------------------------------------------------------------------------------
# The ledger

# Research bookkeeping that prompts for synthesis leave out.
_BOOKKEEPING_FIELDS = ("searches", "pages_read", "unreached")
# Paraphrases longer than this are cut in prompts. A quote replaces the excerpt unless it was not found.
_EXCERPT_CHARS = 300


@dataclass
class EvidenceLedger:
    """Research results by question, in the order they were added; claim IDs are unique across it."""

    results: dict[str, list[ResearchResult]] = field(default_factory=dict)

    def add(self, result: ResearchResult) -> None:
        self.results.setdefault(result.question_id, []).append(self._with_unique_claim_ids(result))

    def _with_unique_claim_ids(self, result: ResearchResult) -> ResearchResult:
        """Rename claims to '<question>/<claim>' so every ID is unique across the ledger.

        Research calls number claims independently, so a repeated ID gets a '~2', '~3' suffix.
        Contradictions are remapped to the new IDs; two claims that shared a local ID expand a
        contradiction citing it to both.
        """
        taken = self.claim_ids()
        prefix = f"{result.question_id}/"
        renamed: dict[str, list[str]] = {}
        claims: list[Claim] = []
        for claim in result.claims:
            base = claim.id if claim.id.startswith(prefix) else prefix + claim.id
            new_id, suffix = base, 2
            while new_id in taken:
                new_id, suffix = f"{base}~{suffix}", suffix + 1
            taken.add(new_id)
            renamed.setdefault(claim.id, []).append(new_id)
            claims.append(claim.model_copy(update={"id": new_id}))
        contradictions = []
        for item in result.contradictions:
            claim_ids = list(dict.fromkeys(new for old in item.claim_ids for new in renamed.get(old, [old])))
            contradictions.append(item.model_copy(update={"claim_ids": claim_ids}))
        return result.model_copy(update={"claims": claims, "contradictions": contradictions})

    def all(self) -> list[ResearchResult]:
        return [result for question_id in sorted(self.results, key=_natural_key) for result in self.results[question_id]]

    def claims(self) -> list[Claim]:
        return [claim for result in self.all() for claim in result.claims]

    def claim_ids(self) -> set[str]:
        return {claim.id for claim in self.claims()}

    def claims_by_id(self) -> dict[str, Claim]:
        return {claim.id: claim for claim in self.claims()}

    def question_ids_with_claims(self) -> set[str]:
        return {question_id for question_id, results in self.results.items() if any(r.claims for r in results)}

    def _source_numbering(self) -> dict[str, str]:
        """Every source's ID for the ledger's current contents: s1, s2, ... in question order."""
        numbering: dict[str, str] = {}
        for claim in self.claims():
            for item in claim.evidence:
                numbering.setdefault(_source_key(item.source), f"s{len(numbering) + 1}")
        return numbering

    def source_id(self, source: SourceRef) -> str:
        return self._source_numbering()[_source_key(source)]

    def source_table(self) -> list[dict[str, Any]]:
        """Every source with its ID and the most any tool returned of it, in ID order."""
        numbering = self._source_numbering()
        rows: dict[str, dict[str, Any]] = {}
        for claim in self.claims():
            for item in claim.evidence:
                source_id = numbering[_source_key(item.source)]
                row = rows.setdefault(source_id, {"id": source_id, **_omit_empty(item.source.model_dump(mode="json"))})
                if item.source_access:
                    row["access"] = _more(row.get("access"), item.source_access)
        return sorted(rows.values(), key=lambda row: int(row["id"][1:]))

    def claim_source_ids(self) -> dict[str, frozenset[str]]:
        """The source IDs of each claim's supporting evidence; contradicting evidence is left out."""
        numbering = self._source_numbering()
        return {claim.id: frozenset(numbering[_source_key(item.source)] for item in claim.evidence if item.supports)
                for claim in self.claims()}

    def to_json(self) -> dict[str, list[dict[str, Any]]]:
        return {question_id: [result.model_dump(mode="json") for result in results]
                for question_id, results in self.results.items()}

    @classmethod
    def from_json(cls, data: dict[str, list[dict[str, Any]]]) -> EvidenceLedger:
        """Reload a stored ledger as is; its claim IDs are already unique, so nothing is renamed."""
        return cls(results={question_id: [ResearchResult.model_validate(item) for item in results]
                            for question_id, results in data.items()})

    def prompt_view(self) -> dict[str, Any]:
        """The ledger compacted for the synthesizer's prompt; `to_json` is unchanged.

        Sources are listed once, under `sources`, and evidence cites them by `source_id`. Null and
        empty values are left out, as is `supports` when the evidence supports its claim. A quote
        replaces the excerpt unless it was not found, and excerpts are cut at 300 characters. The
        checks code set (quote_check, quote_access, source_check, source_access) stay, so the
        synthesizer can tell evidence it read from evidence it only glimpsed.
        """
        numbering = self._source_numbering()
        return {"sources": self.source_table(),
                "research": [_project_result(result, numbering) for result in self.all()]}


def _natural_key(text: str) -> list[Any]:
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", text)]


def _is_empty(value: Any) -> bool:
    return value is None or value == "" or value == []


def _omit_empty(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: compacted for key, item in value.items() if not _is_empty(compacted := _omit_empty(item))}
    if isinstance(value, list):
        return [compacted for item in value if not _is_empty(compacted := _omit_empty(item))]
    return value


def _source_key(source: SourceRef) -> str:
    return json.dumps(_omit_empty(source.model_dump(mode="json")), sort_keys=True)


def _project_evidence(item: Evidence, numbering: dict[str, str]) -> dict[str, Any]:
    body = _omit_empty(item.model_dump(mode="json", exclude={"source"}))
    if body.get("quote") and body.get("quote_check") != "not_found":
        body.pop("excerpt", None)
    elif excerpt := body.get("excerpt"):
        body["excerpt"] = excerpt if len(excerpt) <= _EXCERPT_CHARS else excerpt[: _EXCERPT_CHARS - 3].rstrip() + "..."
    if body.get("supports") is True:
        body.pop("supports")
    return {"source_id": numbering[_source_key(item.source)], **body}


def _project_result(result: ResearchResult, numbering: dict[str, str]) -> dict[str, Any]:
    claims = []
    for claim in result.claims:
        body = _omit_empty(claim.model_dump(mode="json", exclude={"evidence"}))
        if claim.evidence:
            body["evidence"] = [_project_evidence(item, numbering) for item in claim.evidence]
        claims.append(body)
    rest = _omit_empty(result.model_dump(mode="json", exclude={"claims", *_BOOKKEEPING_FIELDS}))
    projected = {key: rest.pop(key) for key in ("question_id", "question", "conclusion") if key in rest}
    if claims:
        projected["claims"] = claims
    return projected | rest


# ---------------------------------------------------------------------------------------------
# Inline citations

# An inline citation such as [s3] or [s3, s12].
_INLINE_CITATION = re.compile(r"\[\s*(s\d+(?:\s*[,;]\s*s\d+)*)\s*\]")


def inline_source_ids(text: str) -> list[str]:
    """The source IDs a text cites inline, in order."""
    return [source_id for group in _INLINE_CITATION.findall(text) for source_id in re.findall(r"s\d+", group)]


def rewrite_inline_citations(text: str, rewrite: Callable[[list[str]], str]) -> str:
    """`text` with each inline citation replaced by `rewrite` of its source IDs."""
    return _INLINE_CITATION.sub(lambda match: rewrite(re.findall(r"s\d+", match.group(1))), text)


def strip_inline_citations(text: str, keep: Collection[str] | None = None) -> str:
    """`text` without its inline [sN] citations, or with only the IDs in `keep` left in them."""
    def rewrite(match: re.Match[str]) -> str:
        kept = [source_id for source_id in re.findall(r"s\d+", match.group(2)) if keep is not None and source_id in keep]
        return f"{match.group(1)}[{', '.join(kept)}]" if kept else ""

    return re.sub(r"([ \t]*)" + _INLINE_CITATION.pattern, rewrite, text)


def citation_problems(report: FinalReport, ledger: EvidenceLedger) -> list[str]:
    """What in `report` does not resolve against `ledger`: unknown claim IDs, and inline [sN] citations
    that name no source behind the claims the report lists. Empty means every citation traces to evidence."""
    problems = []
    known = ledger.claim_ids()
    if unknown := sorted(set(report.claim_ids_used) - known):
        problems.append(f"unknown claim IDs: {', '.join(unknown)}")
    behind = {source_id for claim_id in report.claim_ids_used if claim_id in known
              for source_id in ledger.claim_source_ids()[claim_id]}
    cited = {source_id for text in report.cited_texts for source_id in inline_source_ids(text)}
    if stray := sorted(cited - behind, key=lambda source_id: int(source_id[1:])):
        problems.append(f"inline citations with no listed claim behind them: {', '.join(stray)}")
    return problems

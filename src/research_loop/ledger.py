from __future__ import annotations

import json
import re
from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from typing import Any

from .schemas import Claim, Evidence, FinalReport, ResearchResult, SourceRef

# Scout bookkeeping. Gap analysis still sees it; synthesis and verification do not.
_SEARCH_FIELDS = ("search_queries_used", "suggested_followups")

# Source fields a finishing prompt does not need. The source table keys rows on what the
# model sees, so sources that differ only in these fields share one row.
_HIDDEN_SOURCE_FIELDS = frozenset({
    "provider", "full_text_url", "openalex_id", "acl_id", "accessed_at",
})

# Paraphrases longer than this are cut. A quote replaces the excerpt unless the quote was not found.
_EXCERPT_CHARS = 300


@dataclass
class EvidenceLedger:
    """Append-only by question so escalations preserve provenance and disagreement."""

    results: dict[str, list[ResearchResult]] = field(default_factory=dict)

    def add(self, result: ResearchResult) -> None:
        self.results.setdefault(result.question_id, []).append(self._with_unique_claim_ids(result))

    def _with_unique_claim_ids(self, result: ResearchResult) -> ResearchResult:
        """Rename claims to '<question>/<claim>' so every ID is unique across the ledger.

        Workers number claims independently (a scout and a deep dive on one question both
        emit c1, c2, ...), so repeated IDs get a '~2', '~3' suffix. Contradictions within
        the result are remapped to the new IDs. Two claims in this result that shared a local
        id expand a contradiction citing that id to every new id.
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

        def mapped(claim_id: str) -> list[str]:
            return renamed.get(claim_id, [claim_id])

        contradictions = []
        for item in result.contradictions:
            claim_ids: list[str] = []
            for claim_id in item.claim_ids:
                for new_id in mapped(claim_id):
                    if new_id not in claim_ids:
                        claim_ids.append(new_id)
            contradictions.append(item.model_copy(update={"claim_ids": claim_ids}))
        return result.model_copy(update={"claims": claims, "contradictions": contradictions})

    def all(self) -> list[ResearchResult]:
        return [item for group in self.results.values() for item in group]

    def for_question(self, question_id: str) -> list[ResearchResult]:
        return list(self.results.get(question_id, []))

    def claims(self) -> list[Claim]:
        return [claim for result in self.all() for claim in result.claims]

    def claim_ids(self) -> set[str]:
        return {claim.id for claim in self.claims()}

    def sources(self) -> list[SourceRef]:
        return [e.source for claim in self.claims() for e in claim.evidence]

    def claim_count(self) -> int:
        return len(self.claims())

    def _in_source_order(self) -> list[ResearchResult]:
        """Results by question ID in natural order (q2 before q10), each question's in ledger order.

        Source IDs follow this order, not insertion order, so a ledger reloaded from Postgres,
        whose JSON does not keep key order, numbers its sources the same way.
        """
        return [result for question_id in sorted(self.results, key=_natural_key)
                for result in self.results[question_id]]

    def _source_numbering(self) -> dict[str, str]:
        """Every source's ID for this ledger's current contents: s1, s2, ... in `_in_source_order`."""
        numbering: dict[str, str] = {}
        for result in self._in_source_order():
            for claim in result.claims:
                for item in claim.evidence:
                    numbering.setdefault(_source_key(item.source)[0], f"s{len(numbering) + 1}")
        return numbering

    def source_table(self) -> list[dict[str, Any]]:
        """Every source with the ID prompts and reports cite it by, in ID order.

        IDs are fixed for the ledger's contents: every prompt built from it, and the report written
        from it, use the same ID for the same source. Adding results can renumber later sources.
        """
        table = _SourceTable(self._source_numbering())
        for result in self._in_source_order():
            for claim in result.claims:
                for item in claim.evidence:
                    table.id_for(item.source)
        return table.sorted_rows()

    def claim_source_ids(self) -> dict[str, frozenset[str]]:
        """The source IDs of each claim's supporting evidence, by claim ID; contradicting evidence is left out."""
        numbering = self._source_numbering()
        return {claim.id: frozenset(numbering[_source_key(item.source)[0]] for item in claim.evidence if item.supports)
                for claim in self.claims()}

    def evidence_source_ids(self) -> dict[tuple[str, int], str]:
        """The source ID of every evidence item, keyed by claim ID and the item's index in that claim."""
        numbering = self._source_numbering()
        return {(claim.id, index): numbering[_source_key(item.source)[0]]
                for claim in self.claims() for index, item in enumerate(claim.evidence)}

    def to_json(self) -> dict[str, list[dict[str, Any]]]:
        """Results by question, as stored in Postgres and exported as evidence_ledger.json."""
        return {question_id: [result.model_dump(mode="json") for result in results]
                for question_id, results in self.results.items()}

    @classmethod
    def from_json(cls, data: dict[str, list[dict[str, Any]]]) -> EvidenceLedger:
        """Reload a stored ledger as is; its claim IDs are already unique, so nothing is renamed."""
        return cls(results={question_id: [ResearchResult.model_validate(item) for item in results]
                            for question_id, results in data.items()})

    def prompt_view(
        self,
        record_key: str,
        *,
        include_search: bool = False,
        claim_ids: Collection[str] | None = None,
    ) -> dict[str, Any]:
        """Compact this ledger for a model prompt. ``to_json`` is unchanged.

        Sources are listed once, under ``sources``; evidence cites them by ``source_id``, the
        ID ``source_table`` gives the source, so a view of some claims keeps the same IDs. Provider, fetch time, and extra catalog ids are left off
        the source row, and sources that differ only in those share a row. Null and empty
        strings and lists are omitted, as is ``supports`` when the evidence supports the
        claim. A ``quote`` replaces the excerpt unless its ``quote_check`` is ``not_found``;
        excerpts that stay are cut at 300 characters.
        ``false``, ``0``, and defaults such as ``"unknown"`` stay. ``include_search`` keeps
        ``search_queries_used`` and ``suggested_followups``. When ``claim_ids`` is given,
        only those claims and the claims any contradiction names are included, so every
        contradiction shown can be judged. A result left with no claims keeps its question
        and conclusion, so a question the cited claims miss is still visible.
        """
        wanted = None
        if claim_ids is not None:
            wanted = set(claim_ids) | {
                claim_id for result in self.all()
                for contradiction in result.contradictions for claim_id in contradiction.claim_ids
            }
        sources = _SourceTable(self._source_numbering())
        records = [
            _project_result(result, sources, include_search=include_search, wanted=wanted)
            for result in self.all()
        ]
        view: dict[str, Any] = {}
        if sources.rows:
            view["sources"] = sources.sorted_rows()
        view[record_key] = records
        return view


def _is_empty(value: Any) -> bool:
    return value is None or value == "" or value == []


def _omit_empty(value: Any) -> Any:
    """Drop null and empty strings and lists, and the same inside nested values."""
    if isinstance(value, dict):
        return {key: compacted for key, item in value.items() if not _is_empty(compacted := _omit_empty(item))}
    if isinstance(value, list):
        return [compacted for item in value if not _is_empty(compacted := _omit_empty(item))]
    return value


def _natural_key(text: str) -> list[Any]:
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", text)]


def _source_key(source: SourceRef) -> tuple[str, dict[str, Any]]:
    """What a model sees of a source, and the key rows share: sources differing only in hidden fields match."""
    visible = _omit_empty(source.model_dump(mode="json", exclude=_HIDDEN_SOURCE_FIELDS))
    return json.dumps(visible, sort_keys=True), visible


class _SourceTable:
    """The rows one prompt uses, under the ledger-wide IDs in `numbering`."""

    def __init__(self, numbering: dict[str, str]) -> None:
        self._numbering = numbering
        self._seen: set[str] = set()
        self.rows: list[dict[str, Any]] = []

    def id_for(self, source: SourceRef) -> str:
        key, visible = _source_key(source)
        source_id = self._numbering[key]
        if key not in self._seen:
            self._seen.add(key)
            self.rows.append({"id": source_id, **visible})
        return source_id

    def sorted_rows(self) -> list[dict[str, Any]]:
        return sorted(self.rows, key=lambda row: int(row["id"][1:]))


def _project_evidence(item: Evidence, sources: _SourceTable) -> dict[str, Any]:
    body = _omit_empty(item.model_dump(mode="json", exclude={"source"}))
    # A quote no tool output contained may be invented, so its paraphrase stays beside it.
    if body.get("quote") and body.get("quote_check") != "not_found":
        body.pop("excerpt", None)
    elif excerpt := body.get("excerpt"):
        body["excerpt"] = _cut_excerpt(excerpt)
    if body.get("supports") is True:
        body.pop("supports")
    return {"source_id": sources.id_for(item.source), **body}


def _cut_excerpt(text: str) -> str:
    if len(text) <= _EXCERPT_CHARS:
        return text
    return text[: _EXCERPT_CHARS - 3].rstrip() + "..."


def _project_result(
    result: ResearchResult,
    sources: _SourceTable,
    *,
    include_search: bool,
    wanted: set[str] | None,
) -> dict[str, Any]:
    claims = result.claims
    if wanted is not None:
        claims = [claim for claim in claims if claim.id in wanted]
    projected_claims: list[dict[str, Any]] = []
    for claim in claims:
        evidence = [_project_evidence(item, sources) for item in claim.evidence]
        claim_body = _omit_empty(claim.model_dump(mode="json", exclude={"evidence"}))
        if evidence:
            claim_body["evidence"] = evidence
        projected_claims.append(claim_body)
    excluded = set() if include_search else set(_SEARCH_FIELDS)
    rest = _omit_empty(result.model_dump(mode="json", exclude={"claims", *excluded}))
    projected: dict[str, Any] = {}
    for key in ("question_id", "question", "conclusion"):
        if key in rest:
            projected[key] = rest.pop(key)
    if projected_claims:
        projected["claims"] = projected_claims
    projected.update(rest)
    return projected


# An inline citation such as [s3] or [s3, s12]; its source IDs are checked against the report's claims.
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


def sources_markdown(report: FinalReport, ledger: EvidenceLedger) -> str:
    """A "Sources" section listing what the report's inline [sN] citations name; empty when it cites none."""
    cited = {source_id for text in report.cited_texts for source_id in inline_source_ids(text)}
    rows = [row for row in ledger.source_table() if row["id"] in cited]
    if not rows:
        return ""
    lines = ["", "", "## Sources", ""]
    for row in rows:
        where = row.get("url") or row.get("attachment_id") or ""
        lines.append(f"- [{row['id']}] {row.get('title') or ''} {where}".rstrip())
    return "\n".join(lines) + "\n"

"""Render a finished run as a document: LaTeX and PDF, Markdown, HTML, BibTeX, or JSON.

Presentation only: nothing here calls a model or changes a run. A `ReportDocument` holds
what one run produced (its objective, plan, report, verification, evidence ledger, and review
reasons). Every format lays it out the same way: a status summary, the report with its key
statements and caveats, a bibliography of every source in the ledger (scholarly works apart
from other sources and attachments), and appendices for the plan, the ledger, and the
verification. Sources keep the [sN] IDs the report cites them by, so a citation names the same
source in every format, and claims keep their ledger IDs.

`research-report` renders a saved record (`ReportDocument.to_record`, or the JSON that
`examples/readme_example.py` writes) or a job stored in Postgres. PDF output needs a LaTeX
engine on PATH: latexmk, pdflatex, lualatex, xelatex, or tectonic.
"""
from __future__ import annotations

import argparse
import asyncio
import html
import json
import re
import shutil
import subprocess
import unicodedata
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import quote, urlsplit
from uuid import UUID

from markdown_it import MarkdownIt

from .ledger import EvidenceLedger, inline_source_ids, rewrite_inline_citations
from .schemas import (
    ClaimCheck,
    FinalReport,
    ReportClaim,
    ResearchPlan,
    VerificationReport,
)

if TYPE_CHECKING:
    from markdown_it.token import Token

    from .async_orchestrator import ResearchOutcome

FORMATS = ("pdf", "tex", "md", "html", "bib", "json")
ENGINES = ("latexmk", "pdflatex", "lualatex", "xelatex", "tectonic")
# Engines that print text from system fonts, best first for scripts beyond Latin Modern: XeTeX breaks
# Thai and Chinese lines by locale, and LuaLaTeX needs luaotfload for system fonts.
_UNICODE_ENGINES = ("xelatex", "lualatex", "tectonic")
_SUFFIX = {"pdf": ".pdf", "tex": ".tex", "md": ".md", "html": ".html", "bib": ".bib", "json": ".json"}

SourceGroup = Literal["scholarly", "web", "attachment"]
GROUP_TITLES: dict[SourceGroup, str] = {
    "scholarly": "Scholarly literature",
    "web": "Web and other sources",
    "attachment": "Attachments",
}
_GROUP_COUNTS: dict[SourceGroup, str] = {"scholarly": "scholarly", "web": "web and other", "attachment": "attachment"}
_SCHOLARLY_STATUSES = frozenset({
    "peer_reviewed", "accepted_conference", "journal", "preprint", "conference_submission", "review",
})
_STATUS_LABELS = {
    "peer_reviewed": "Peer-reviewed",
    "accepted_conference": "Conference paper",
    "journal": "Journal article",
    "preprint": "Preprint",
    "conference_submission": "Conference submission",
    "review": "Review article",
    "official_documentation": "Official documentation",
    "benchmark_repository": "Benchmark repository",
    "vendor_technical_report": "Vendor technical report",
    "blog": "Blog post",
    "general_web": "Web page",
    "dataset": "Dataset",
}
_SAFE_LINK_SCHEMES = ("http", "https", "mailto")
_CITE_OPEN, _CITE_CLOSE = "\ue000", "\ue001"
_CITE_PLACEHOLDER = re.compile(f"{_CITE_OPEN}([^{_CITE_CLOSE}]*){_CITE_CLOSE}")


# --- the document ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceEntry:
    """One ledger source as a bibliography entry, with how the run used it."""

    id: str
    row: Mapping[str, Any]
    group: SourceGroup
    cited: bool  # the report's answer or caveats cite it inline
    behind_report: bool  # evidence for a claim the report cites
    claim_ids: tuple[str, ...]  # ledger claims whose evidence names it
    not_observed: bool  # some evidence cites it, but no research tool returned it

    @property
    def title(self) -> str:
        return str(self.row.get("title") or self.row.get("url") or self.id)

    @property
    def usage(self) -> str:
        if self.cited:
            return "Cited in the report"
        if self.behind_report:
            return "Behind a cited claim"
        return "Gathered, not cited"


@dataclass
class ReportDocument:
    """Everything one run produced that a rendered report shows."""

    objective: str
    report: FinalReport
    verification: VerificationReport
    ledger: EvidenceLedger
    plan: ResearchPlan | None = None
    review_reasons: list[str] = field(default_factory=list)
    job_id: str | None = None
    cost_usd: float | None = None
    generated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def from_outcome(cls, outcome: ResearchOutcome, *, generated_at: datetime | None = None) -> ReportDocument:
        return cls(
            objective=outcome.plan.objective,
            report=outcome.report,
            verification=outcome.verification,
            ledger=outcome.ledger,
            plan=outcome.plan,
            review_reasons=list(outcome.review_reasons),
            job_id=str(outcome.job_id),
            cost_usd=None if outcome.cost_usd is None else float(outcome.cost_usd),
            generated_at=generated_at or datetime.now(UTC),
        )

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> ReportDocument:
        """Load `to_record` output, or a record with the same keys, such as readme_example's.

        A record without review reasons gets them recomputed from its report, verification, and ledger.
        """
        missing = [key for key in ("report", "verification", "ledger") if record.get(key) is None]
        if missing:
            raise ValueError(f"record has no {', '.join(missing)}; a report needs the run's report, verification, and ledger")
        plan = ResearchPlan.model_validate(record["plan"]) if record.get("plan") else None
        objective = record.get("objective") or record.get("question") or (plan.objective if plan else "")
        report = FinalReport.model_validate(record["report"])
        verification = VerificationReport.model_validate(record["verification"])
        ledger = EvidenceLedger.from_json(record["ledger"])
        reasons = record.get("review_reasons")
        if reasons is None:
            from .async_orchestrator import review_reasons

            reasons = review_reasons(report, verification, ledger)
        generated = record.get("generated_at") or record.get("finished_at")
        return cls(
            objective=str(objective),
            report=report,
            verification=verification,
            ledger=ledger,
            plan=plan,
            review_reasons=list(reasons),
            job_id=None if record.get("job_id") is None else str(record["job_id"]),
            cost_usd=None if record.get("cost_usd") is None else float(record["cost_usd"]),
            generated_at=datetime.fromisoformat(generated) if generated else datetime.now(UTC),
        )

    def to_record(self) -> dict[str, Any]:
        """A JSON-ready record that `from_record` renders the same way again."""
        return {
            "objective": self.objective,
            "job_id": self.job_id,
            "cost_usd": self.cost_usd,
            "generated_at": self.generated_at.isoformat(),
            "review_reasons": self.review_reasons,
            "plan": None if self.plan is None else self.plan.model_dump(mode="json"),
            "report": self.report.model_dump(mode="json"),
            "verification": self.verification.model_dump(mode="json"),
            "ledger": self.ledger.to_json(),
            "sources": self.ledger.source_table(),
        }

    @cached_property
    def sources(self) -> list[SourceEntry]:
        """Every ledger source in ID order, with how the report uses it."""
        cited = {source_id for text in (self.report.answer, *self.report.caveats,
                                        *(claim.statement for claim in self.report.claims))
                 for source_id in inline_source_ids(text)}
        claim_sources = self.ledger.claim_source_ids()
        behind = {source_id for claim_id in self.report.claim_ids_used for source_id in claim_sources.get(claim_id, ())}
        evidence_ids = self.ledger.evidence_source_ids()
        used_by: dict[str, list[str]] = {}
        unobserved: set[str] = set()
        for claim in self.ledger.claims():
            for index, item in enumerate(claim.evidence):
                source_id = evidence_ids[(claim.id, index)]
                if claim.id not in used_by.setdefault(source_id, []):
                    used_by[source_id].append(claim.id)
                if item.source_check == "not_found":
                    unobserved.add(source_id)
        return [
            SourceEntry(
                id=row["id"],
                row=row,
                group=source_group(row),
                cited=row["id"] in cited,
                behind_report=row["id"] in behind,
                claim_ids=tuple(used_by.get(row["id"], ())),
                not_observed=row["id"] in unobserved,
            )
            for row in self.ledger.source_table()
        ]

    @cached_property
    def source_ids(self) -> frozenset[str]:
        return frozenset(entry.id for entry in self.sources)

    def grouped_sources(self) -> list[tuple[SourceGroup, list[SourceEntry]]]:
        return [(group, entries) for group in GROUP_TITLES
                if (entries := [entry for entry in self.sources if entry.group == group])]

    def question_ids(self) -> list[str]:
        """Ledger question IDs in plan order, then any the plan does not list."""
        planned = [question.id for question in self.plan.questions] if self.plan else []
        return [qid for qid in planned if qid in self.ledger.results] + sorted(
            (qid for qid in self.ledger.results if qid not in planned), key=_natural_key)

    def question_text(self, question_id: str) -> str:
        if self.plan:
            for question in self.plan.questions:
                if question.id == question_id:
                    return question.question
        results = self.ledger.for_question(question_id)
        return results[0].question if results else ""

    def check_for(self, claim: ReportClaim) -> ClaimCheck | None:
        """The verifier's check of a report statement: same statement, else the same cited claims."""
        for check in self.verification.checks:
            if check.statement.strip() == claim.statement.strip():
                return check
        for check in self.verification.checks:
            if claim.claim_ids and set(check.claim_ids) == set(claim.claim_ids):
                return check
        return None

    def status_lines(self) -> list[tuple[str, str]]:
        """(label, text) lines summarizing verification, evidence, and review."""
        checks = self.verification.checks
        supported = sum(check.supported for check in checks)
        major = sum(check.severity == "major" for check in checks)
        if checks:
            verification = f"{supported} of {len(checks)} statements supported"
            if major:
                verification += f"; {major} rated major"
        else:
            verification = "no statements were checked"
        breakdown = ", ".join(f"{len(entries)} {_GROUP_COUNTS[group]}{'s' * (group == 'attachment' and len(entries) != 1)}"
                              for group, entries in self.grouped_sources())
        claims = self.ledger.claim_count()
        questions = len(self.ledger.results)
        evidence = (f"{claims} claim{'s' * (claims != 1)} from {len(self.sources)} "
                    f"source{'s' * (len(self.sources) != 1)}{f' ({breakdown})' if breakdown else ''} "
                    f"across {questions} question{'s' * (questions != 1)}")
        review = "; ".join(self.review_reasons) if self.review_reasons else "no check flagged a problem"
        return [("Verification", verification), ("Evidence", evidence), ("Review", review)]

    def metadata(self) -> list[str]:
        parts = [self.generated_at.strftime("%Y-%m-%d %H:%M UTC")]
        if self.job_id:
            parts.append(f"job {self.job_id}")
        if self.cost_usd is not None:
            parts.append(f"cost ${self.cost_usd:.2f}")
        return parts


def source_group(row: Mapping[str, Any]) -> SourceGroup:
    """Attachments, then scholarly works (a DOI, an arXiv ID, a paper, or a scholarly status), then the rest."""
    if row.get("attachment_id"):
        return "attachment"
    if (row.get("doi") or row.get("arxiv_id") or row.get("source_type") == "paper"
            or row.get("publication_status") in _SCHOLARLY_STATUSES):
        return "scholarly"
    return "web"


def _natural_key(text: str) -> list[Any]:
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", text)]


def _year(row: Mapping[str, Any]) -> str | None:
    match = re.search(r"\b(1[5-9]\d\d|20\d\d|21\d\d)\b", str(row.get("published_at") or ""))
    return match.group(1) if match else None


def _host(url: str) -> str:
    host = urlsplit(url).hostname or ""
    return host.removeprefix("www.")


def _describe_source(row: Mapping[str, Any]) -> list[str]:
    """Publication details after the title: status, date, and location for attachments."""
    details = []
    if label := _STATUS_LABELS.get(str(row.get("publication_status"))):
        details.append(label)
    if published := row.get("published_at"):
        details.append(str(published))
    if row.get("attachment_id") and row.get("locator"):
        details.append(str(row["locator"]))
    return details


def _repeats_identifier(row: Mapping[str, Any]) -> bool:
    """Whether the source's URL is just its DOI or arXiv link, which the entry already shows."""
    url = str(row.get("url") or "").lower().rstrip("/")
    links = []
    if row.get("doi"):
        links += [f"https://doi.org/{row['doi']}", f"https://dx.doi.org/{row['doi']}"]
    if row.get("arxiv_id"):
        links += [f"https://arxiv.org/{kind}/{row['arxiv_id']}" for kind in ("abs", "pdf")]
    return any(url in {link.lower(), link.lower() + ".pdf"} for link in links)


def _check_label(check: ClaimCheck | None) -> str:
    if check is None:
        return "not checked"
    return ("supported" if check.supported else "unsupported") + ("" if check.severity == "none" else f", {check.severity}")


def _evidence_marks(item: Any) -> list[str]:
    marks = []
    if not item.supports:
        marks.append("contradicts")
    if item.quote_check == "verified":
        marks.append("quote verified")
    elif item.quote_check == "not_found":
        marks.append("quote not found")
    if item.source_check == "not_found":
        marks.append("source not observed")
    return marks


# --- Markdown and HTML ----------------------------------------------------------------------


def render_markdown(doc: ReportDocument) -> str:
    lines = ["# Research report", "", f"**Objective:** {doc.objective}", "", f"_{' · '.join(doc.metadata())}_", ""]
    lines += [f"> **{label}:** {text}  " for label, text in doc.status_lines()]
    lines += ["", "## Findings", "", _shift_headings(tidy_answer(doc.report.answer.strip()), 2), ""]
    if doc.report.claims:
        lines += ["## Key statements", ""]
        claim_sources = doc.ledger.claim_source_ids()
        for number, claim in enumerate(doc.report.claims, 1):
            details = [_check_label(doc.check_for(claim))]
            if claim.claim_ids:
                details.append("evidence " + ", ".join(f"`{claim_id}`" for claim_id in claim.claim_ids))
            sources = sorted({s for claim_id in claim.claim_ids for s in claim_sources.get(claim_id, ())}, key=_natural_key)
            if sources:
                details.append(f"sources [{', '.join(sources)}]")
            lines.append(f"{number}. {claim.statement} — _{'; '.join(details)}_")
        lines.append("")
    if doc.report.caveats:
        lines += ["## Caveats", "", *(f"- {caveat}" for caveat in doc.report.caveats), ""]
    lines += ["## Sources", ""]
    if not doc.sources:
        lines += ["The ledger holds no sources.", ""]
    for group, entries in doc.grouped_sources():
        lines += [f"### {GROUP_TITLES[group]}", ""]
        lines += [_markdown_source(entry) for entry in entries]
        lines.append("")
    lines += _markdown_appendices(doc)
    return "\n".join(lines).rstrip() + "\n"


def _markdown_source(entry: SourceEntry) -> str:
    row = entry.row
    parts = [f"*{_md_inline(entry.title)}*"]
    parts += _describe_source(row)
    if row.get("doi"):
        parts.append(f"doi:[{row['doi']}](https://doi.org/{row['doi']})")
    if row.get("arxiv_id"):
        parts.append(f"arXiv:[{row['arxiv_id']}](https://arxiv.org/abs/{row['arxiv_id']})")
    if row.get("attachment_id"):
        parts.append(f"attachment `{row['attachment_id']}`")
    elif row.get("url") and not _repeats_identifier(row):
        parts.append(f"<{row['url']}>")
    notes = [entry.usage]
    if entry.claim_ids:
        notes.append("claims " + ", ".join(f"`{claim_id}`" for claim_id in entry.claim_ids))
    if entry.not_observed:
        notes.append("**source not observed**")
    if entry.row.get("is_retracted"):
        notes.append("**retracted**")
    return f"- **[{entry.id}]** {'. '.join(parts)}. _{'; '.join(notes)}_"


def _markdown_appendices(doc: ReportDocument) -> list[str]:
    lines: list[str] = []
    if doc.plan:
        lines += ["## Appendix A. Research plan", "", f"**Objective:** {doc.plan.objective}", ""]
        for question in doc.plan.questions:
            lines.append(f"- `{question.id}` {question.question} _({'; '.join(_question_flags(question))})_")
        if doc.plan.stop_conditions:
            lines += ["", "**Stop conditions:**", "", *(f"- {condition}" for condition in doc.plan.stop_conditions)]
        lines.append("")
    evidence_ids = doc.ledger.evidence_source_ids()
    lines += ["## Appendix B. Evidence ledger", ""]
    for question_id in doc.question_ids():
        lines += [f"### {question_id}: {_md_inline(doc.question_text(question_id))}", ""]
        results = doc.ledger.for_question(question_id)
        for number, result in enumerate(results, 1):
            if len(results) > 1:
                lines += [f"#### Research pass {number}", ""]
            lines += [f"**Conclusion** (confidence {result.confidence:.2f}): {result.conclusion}", ""]
            for claim in result.claims:
                lines.append(f"- **`{claim.id}`** ({claim.confidence:.2f}) {claim.statement}")
                for index, item in enumerate(claim.evidence):
                    text = f"“{item.quote}”" if item.quote else item.excerpt
                    marks = _evidence_marks(item)
                    locator = f" ({item.source.locator})" if item.source.locator and not item.source.attachment_id else ""
                    lines.append(f"  - [{evidence_ids[(claim.id, index)]}]{locator} {text}"
                                 + (f" _({', '.join(marks)})_" if marks else ""))
            if result.contradictions:
                lines += ["", "**Contradictions:**", ""]
                for item in result.contradictions:
                    ids = ", ".join(f"`{claim_id}`" for claim_id in item.claim_ids)
                    lines.append(f"- {item.description}" + (f" ({ids})" if ids else ""))
            if result.unresolved_questions:
                lines += ["", "**Unresolved:**", "", *(f"- {question}" for question in result.unresolved_questions)]
            lines.append("")
    lines += ["## Appendix C. Verification", ""]
    if not doc.verification.checks:
        lines += ["The verifier checked no statements.", ""]
    for check in doc.verification.checks:
        ids = ", ".join(f"`{claim_id}`" for claim_id in check.claim_ids)
        lines.append(f"- **{_check_label(check)}** {check.statement}" + (f" ({ids})" if ids else "")
                     + f"  \n  {check.explanation}")
    if doc.verification.followups:
        lines += ["", "**Follow-ups requested:**", ""]
        for gap in doc.verification.followups:
            lines.append(f"- `{gap.question_id}` {gap.reason.replace('_', ' ')}, severity {gap.severity}: {gap.followup}")
    return lines


def _question_flags(question: Any) -> list[str]:
    flags = [f"priority {question.priority}", f"{question.expected_difficulty} difficulty"]
    if question.requires_primary_sources:
        flags.append("primary sources required")
    if question.requires_multimodal:
        flags.append("multimodal")
    return flags


def _md_inline(text: str) -> str:
    return re.sub(r"([*_`\[\]])", r"\\\1", text)


def _shift_headings(markdown: str, by: int) -> str:
    """Push the report's own headings below the document's, leaving fenced code alone."""
    lines, fenced = [], False
    for line in markdown.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
        elif not fenced and re.match(r"#{1,6}\s", line):
            line = "#" * min(6, len(line) - len(line.lstrip("#")) + by) + line.lstrip("#")
        lines.append(line)
    return "\n".join(lines)


_HTML_STYLE = """
:root { color-scheme: light dark; --fg: #1f2328; --muted: #656d76; --bg: #ffffff; --rule: #d0d7de; --accent: #0969da; }
@media (prefers-color-scheme: dark) { :root { --fg: #e6edf3; --muted: #8d96a0; --bg: #0d1117; --rule: #30363d; --accent: #4493f8; } }
body { font: 16px/1.6 Georgia, "Times New Roman", serif; color: var(--fg); background: var(--bg);
       max-width: 46rem; margin: 2rem auto; padding: 0 1rem; }
h1, h2, h3, h4 { font-family: system-ui, sans-serif; line-height: 1.25; }
h2 { border-bottom: 1px solid var(--rule); padding-bottom: .3rem; margin-top: 2.2rem; }
a { color: var(--accent); } code { font-size: .88em; }
blockquote { margin: 1rem 0; padding: .5rem 1rem; border-left: 4px solid var(--accent); color: var(--fg); }
em { color: var(--muted); } li { margin: .2rem 0; } :target { background: color-mix(in srgb, var(--accent) 18%, transparent); }
table { border-collapse: collapse; } th, td { border: 1px solid var(--rule); padding: .3rem .6rem; }
"""


def render_html(doc: ReportDocument) -> str:
    """The Markdown rendering as one standalone page; model-written HTML is shown as text, not run."""
    body = MarkdownIt("commonmark", {"html": False}).enable("table").render(render_markdown(doc))
    body = re.sub(r"<li><strong>\[(s\d+)\]</strong>", r'<li id="\1"><strong>[\1]</strong>', body)
    known = doc.source_ids

    def link(match: re.Match[str]) -> str:
        ids = re.findall(r"s\d+", match.group(1))
        return "[" + ", ".join(f'<a href="#{i}">{i}</a>' if i in known else i for i in ids) + "]"

    body = re.sub(r"\[(s\d+(?:\s*[,;]\s*s\d+)*)\](?!</strong>)", link, body)
    return (
        "<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        f"<title>{html.escape(_truncate(doc.objective, 90))}</title>\n<style>{_HTML_STYLE}</style>\n"
        f"</head>\n<body>\n{body}</body>\n</html>\n"
    )


# A bullet whose text is cells separated by " | ", and the "(Columns: a | b | c)" line that names them.
_PIPE_ROW = re.compile(r"^\s*[-*+]\s+(.*\S)\s*$")
_COLUMNS_LINE = re.compile(r"^\s*\(?\s*columns?\s*:\s*(.+?)\s*\)?\s*$", re.IGNORECASE)


def _cells(text: str) -> list[str]:
    return [cell.strip() for cell in text.split(" | ")]


_RULE_LINE = re.compile(r"^\s*(=|-){3,}\s*$")
# A short line of capitals, digits, and joining punctuation: "INDONESIA", "SRI LANKA", "SECTION 2".
_CAPS_LINE = re.compile(r"^[A-Z][A-Z0-9 &'/,.()-]{1,58}[A-Z0-9)]$")


def plain_headings_as_markdown(markdown: str) -> str:
    """`markdown` with the headings models write as plain text made into Markdown headings.

    A line between two rules of `=` or `-` ("=====", "SECTION 1 - PROFILES", "=====") becomes a
    top-level heading, and a line of capitals standing alone after a blank line ("INDONESIA") a
    second-level one. The fifth pilot's report marked all of its sections and countries this way.
    """
    lines = markdown.split("\n")
    out: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if (_RULE_LINE.match(line) and index + 2 < len(lines) and lines[index + 1].strip()
                and not _RULE_LINE.match(lines[index + 1]) and _RULE_LINE.match(lines[index + 2])):
            out += ["# " + lines[index + 1].strip(), ""]
            index += 3
            continue
        if _CAPS_LINE.match(line.strip()) and (not out or not out[-1].strip()) and any(c.isalpha() for c in line):
            out += ["## " + line.strip(), ""]
            index += 1
            continue
        out.append(line)
        index += 1
    return "\n".join(out)


def tidy_answer(markdown: str) -> str:
    """A model's answer with its plain-text headings and bullet tables made into Markdown ones."""
    return pipe_rows_as_tables(plain_headings_as_markdown(markdown))


def pipe_rows_as_tables(markdown: str) -> str:
    """`markdown` with each run of bullets written as table rows turned into a Markdown table.

    Models sometimes write a table as bullets of " | "-separated cells under a "(Columns: ...)" line,
    as the fifth pilot's report did for both of its tables. A run of two or more such bullets with the
    same three or more cells becomes a table, headed by the columns line when its count matches.
    """
    lines = markdown.split("\n")
    out: list[str] = []
    index = 0
    while index < len(lines):
        run: list[list[str]] = []
        end = index
        while end < len(lines) and (match := _PIPE_ROW.match(lines[end])) and len(cells := _cells(match.group(1))) >= 3 \
                and (not run or len(cells) == len(run[0])):
            run.append(cells)
            end += 1
        if len(run) < 2:
            out.append(lines[index])
            index += 1
            continue
        width = len(run[0])
        header = [""] * width
        previous = next((i for i in range(len(out) - 1, -1, -1) if out[i].strip()), None)
        if previous is not None and (columns := _COLUMNS_LINE.match(out[previous])):
            named = [cell.strip() for cell in columns.group(1).split("|")]
            if len(named) == width:
                header = named
                del out[previous:]
        if out and out[-1].strip():
            out.append("")
        escape = lambda cell: cell.replace("|", r"\|")
        out += ["| " + " | ".join(map(escape, header)) + " |", "|" + "---|" * width,
                *("| " + " | ".join(map(escape, row)) + " |" for row in run), ""]
        index = end
    return "\n".join(out)


# The request phrasing an objective opens with, which a title leaves out: "I need a detailed report on X",
# "I am researching X", "Please compile an overview of X".
_REQUEST_OPENING = re.compile(
    r"^(?:(?:i|we)\s+(?:need|want|would like|'d like|am|are|'m|'re)\s+(?:researching\s+|to\s+(?:know|understand)\s+)?"
    r"|please\s+|can you\s+|could you\s+)?(?:(?:write|compile|prepare|produce|provide|create|give me|research)\s+)?"
    r"(?:(?:a|an|the)\s+)?(?:(?:detailed|comprehensive|thorough|short|brief|full|in-depth)\s+)*"
    r"(?:(?:report|overview|analysis|review|study|summary)\s+(?:on|of|about|into|covering)\s+)?",
    re.IGNORECASE)
_TITLE_CHARS = 110


def report_title(doc: ReportDocument) -> str:
    """The report's title: the synthesizer's when it gave one, else the objective's opening sentence,
    without its request phrasing and cut before any list of particulars."""
    given = getattr(doc.report, "title", None)
    if isinstance(given, str) and given.strip():
        return " ".join(given.split())
    first = " ".join(doc.objective.strip().split("\n\n", 1)[0].split())
    sentence = re.split(r"(?<=[.?!])\s", first, maxsplit=1)[0]
    subject = _REQUEST_OPENING.sub("", sentence, count=1) or sentence
    subject = re.split(r"\s*(?::|;|,\s+(?:including|such as|namely|for example)\b|\()", subject, maxsplit=1)[0]
    subject = subject.rstrip(".").strip() or first
    subject = subject[0].upper() + subject[1:]
    return _truncate(subject, _TITLE_CHARS)


def _truncate(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


# --- LaTeX ----------------------------------------------------------------------------------

_LATEX_SPECIALS = {
    "\\": r"\textbackslash{}", "{": r"\{", "}": r"\}", "$": r"\$", "&": r"\&", "#": r"\#",
    "%": r"\%", "_": r"\_", "^": r"\textasciicircum{}", "~": r"\textasciitilde{}",
    "<": r"\textless{}", ">": r"\textgreater{}", "|": r"\textbar{}", '"': r"\textquotedbl{}",
}
_LATEX_SPECIAL = re.compile("[" + re.escape("".join(_LATEX_SPECIALS)) + "]")
# Characters pdfLaTeX needs spelled out. XeLaTeX and LuaLaTeX print the characters themselves.
_UNICODE_LATEX = {
    "\u2018": "`", "\u2019": "'", "\u201c": "``", "\u201d": "''", "\u201e": ",,", "\u2013": "--", "\u2014": "---",
    "\u2026": r"\ldots{}", "\u2022": r"\textbullet{}", "\u00b7": r"\textperiodcentered{}", "\u2032": r"\ensuremath{'}",
    "\u2033": r"\ensuremath{''}", "\u2212": r"\ensuremath{-}", "\u2264": r"\ensuremath{\leq}",
    "\u2265": r"\ensuremath{\geq}", "\u2260": r"\ensuremath{\neq}", "\u2248": r"\ensuremath{\approx}",
    "\u223c": r"\ensuremath{\sim}", "\u2192": r"\ensuremath{\rightarrow}", "\u2190": r"\ensuremath{\leftarrow}",
    "\u2194": r"\ensuremath{\leftrightarrow}", "\u21d2": r"\ensuremath{\Rightarrow}", "\u2191": r"\ensuremath{\uparrow}",
    "\u2193": r"\ensuremath{\downarrow}", "\u221e": r"\ensuremath{\infty}", "\u221a": r"\ensuremath{\surd}",
    "\u2211": r"\ensuremath{\sum}", "\u220f": r"\ensuremath{\prod}", "\u2208": r"\ensuremath{\in}",
    "\u2229": r"\ensuremath{\cap}", "\u222a": r"\ensuremath{\cup}", "\u2202": r"\ensuremath{\partial}",
    "\u2206": r"\ensuremath{\Delta}", "\u2713": r"\ensuremath{\surd}", "\u2714": r"\ensuremath{\surd}",
    "\u2717": r"\ensuremath{\times}", "\u2718": r"\ensuremath{\times}", "\u2020": r"\dag{}", "\u2021": r"\ddag{}",
    "\u20ac": r"\texteuro{}", "\u2122": r"\texttrademark{}", "\u2030": r"\textperthousand{}",
    "\u2009": r"\,", "\u202f": r"\,", "\u2002": r"\enspace{}", "\u2003": r"\quad{}", "\u200b": "",
    "\u2011": "-", "\u2010": "-", "\u00a0": "~", "\ufb01": "fi", "\ufb02": "fl",
}
_GREEK = {
    "ALPHA": "alpha", "BETA": "beta", "GAMMA": "gamma", "DELTA": "delta", "EPSILON": "epsilon", "ZETA": "zeta",
    "ETA": "eta", "THETA": "theta", "IOTA": "iota", "KAPPA": "kappa", "LAMDA": "lambda", "MU": "mu", "NU": "nu",
    "XI": "xi", "OMICRON": "o", "PI": "pi", "RHO": "rho", "SIGMA": "sigma", "FINAL SIGMA": "varsigma",
    "TAU": "tau", "UPSILON": "upsilon", "PHI": "phi", "CHI": "chi", "PSI": "psi", "OMEGA": "omega",
}
_GREEK_CAPITALS = {"Gamma", "Delta", "Theta", "Lambda", "Xi", "Pi", "Sigma", "Upsilon", "Phi", "Psi", "Omega"}
_GREEK_LATIN = {"ALPHA": "A", "BETA": "B", "EPSILON": "E", "ZETA": "Z", "ETA": "H", "IOTA": "I", "KAPPA": "K",
                "MU": "M", "NU": "N", "OMICRON": "O", "RHO": "P", "TAU": "T", "CHI": "X"}


def _unicode_latex(char: str) -> str:
    """What pdfLaTeX prints for a character beyond Latin-1: a command, or its code point."""
    if char in _UNICODE_LATEX:
        return _UNICODE_LATEX[char]
    name = unicodedata.name(char, "")
    if match := re.fullmatch(r"GREEK (SMALL|CAPITAL) LETTER ([A-Z ]+)", name):
        letter = match.group(2)
        if match.group(1) == "SMALL" and letter in _GREEK:
            return rf"\ensuremath{{\{_GREEK[letter]}}}"
        capital = _GREEK.get(letter, "").capitalize()
        if capital in _GREEK_CAPITALS:
            return rf"\ensuremath{{\{capital}}}"
        if letter in _GREEK_LATIN:
            return _GREEK_LATIN[letter]
    decomposed = unicodedata.normalize("NFKD", char).encode("ascii", "ignore").decode()
    if decomposed.strip():
        return latex_escape(decomposed)
    return rf"\textsf{{\footnotesize[U+{ord(char):04X}]}}"


def latex_escape(text: str) -> str:
    """`text` with LaTeX's special characters escaped; other characters are left as they are."""
    return _LATEX_SPECIAL.sub(lambda match: _LATEX_SPECIALS[match.group()], text)


def _unicode_declarations(tex: str) -> tuple[str, str]:
    """Preamble lines for the characters beyond Latin Extended-A that `tex` uses: pdfLaTeX's, then XeLaTeX's.

    pdfLaTeX gets a definition for each. XeLaTeX and LuaLaTeX print characters from the font,
    which may lack math symbols and Greek, so those alone are spelled out for them too.
    """
    chars = sorted({char for char in tex if ord(char) > 0x17F})
    pdftex = "\n".join(rf"  \DeclareUnicodeCharacter{{{ord(char):04X}}}{{{_unicode_latex(char)}}}" for char in chars)
    unicode_tex = "\n".join(rf"    \newunicodechar{{{char}}}{{{latex}}}" for char in chars
                             if (latex := _unicode_latex(char)).startswith(r"\ensuremath"))
    return pdftex, unicode_tex


# Scripts Latin Modern lacks, which reports quote from sources such as Thai or Chinese government pages:
# their characters, the fonts to print them in under XeLaTeX or LuaLaTeX (the first installed), and the
# locale XeTeX breaks their lines by, since Thai and Chinese put no spaces between words. pdfLaTeX has no
# fonts for them and prints each character's code point.
_SCRIPTS = {
    "thai": ("\u0e00-\u0e7f", ("Noto Serif Thai", "Noto Sans Thai", "FreeSerif", "Loma"), "th"),
    "cjk": ("\u3000-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uff00-\uffef",
            ("Noto Serif CJK SC", "Noto Sans CJK SC", "Noto Serif CJK JP", "Noto Sans CJK JP"), "zh"),
}
# A run of one script's characters, with the spaces between them; punctuation such as "." stays in the
# main font, since script fonts often lack it.
_SCRIPT_RUNS = [(name, re.compile(f"[{chars}]+(?: +[{chars}]+)*")) for name, (chars, _, _) in _SCRIPTS.items()]


def _script_fonts() -> str:
    lines = []
    for name, (_, fonts, locale) in _SCRIPTS.items():
        # The first installed font, or none, in which case the text prints in the main font.
        choice = rf"\let\rlfont@{name}\relax"
        for font in reversed(fonts):
            choice = rf"\IfFontExistsTF{{{font}}}{{\newfontfamily\rlfont@{name}{{{font}}}}}{{{choice}}}"
        lines.append("  " + choice)
        lines.append(rf"  \expandafter\def\csname rlscript@{name}\endcsname#1{{{{\rlfont@{name}"
                     rf'\ifXeTeX\XeTeXlinebreaklocale "{locale}"\relax\fi #1}}}}')
    return "\n".join([r"  \makeatletter", *lines, r"  \makeatother"])


_SCRIPT_FONTS = _script_fonts()


def _mark_scripts(tex: str) -> str:
    """`tex` with each run of a script Latin Modern lacks set in \\rlscript, outside verbatim blocks."""
    parts = re.split(r"(\\begin\{verbatim\}.*?\\end\{verbatim\})", tex, flags=re.DOTALL)
    for index in range(0, len(parts), 2):
        for name, run in _SCRIPT_RUNS:
            parts[index] = run.sub(lambda match, name=name: rf"\rlscript{{{name}}}{{{match.group()}}}", parts[index])
    return "".join(parts)


def needs_unicode_engine(tex: str) -> bool:
    """Whether `tex` sets text in a script only XeLaTeX or LuaLaTeX can print."""
    return r"\rlscript{" in tex.split("\\begin{document}", 1)[-1]


def _href_target(url: str) -> str:
    """A URL safe to put in \\href's first argument: odd characters percent-encoded, % and # escaped."""
    encoded = quote(url, safe="/:?&=+,;@!()*[]-._%#")
    return encoded.replace("\\", "%5C").replace("%", r"\%").replace("#", r"\#")


def _url_text(url: str) -> str:
    """A URL as printable text, with line breaks allowed after its separators."""
    escaped = latex_escape(url)
    # Before each escaped %, too: a percent-encoded path, such as a Thai page's, has no other break.
    escaped = escaped.replace(r"\%", r"\allowbreak{}\%")
    return re.sub(r"(/|\.|-|\\_|\?|\\&|=|\\#)", r"\1\\allowbreak{}", escaped)


def _latex_url(url: str) -> str:
    return rf"\href{{{_href_target(url)}}}{{\texttt{{{_url_text(url)}}}}}"


def _label(kind: str, identifier: str) -> str:
    """A LaTeX label for a claim or source ID; characters labels cannot hold become their code."""
    return f"{kind}:" + re.sub(r"[^A-Za-z0-9.\-/]", lambda match: f"+{ord(match.group()):x}+", identifier)


def _claim_ref(claim_id: str, known: Iterable[str]) -> str:
    text = rf"\texttt{{{latex_escape(claim_id)}}}"
    return rf"\hyperref[{_label('claim', claim_id)}]{{{text}}}" if claim_id in known else text


# About how many characters a line holds at each table size, for fitting a column's longest word.
_LINE_CHARS = {r"\footnotesize": 105, r"\small": 92, "": 82}


def _column_shares(cells: list[list[str]], width: int, size: str) -> list[float]:
    """Each column's share of a table's width, by the text it holds.

    By the square root of a column's longest cell, so a column of long prose gets more room without
    starving one of short codes, and never less than its longest word needs, so a country name
    is not split across lines or run into the next column.
    """
    columns = [[row[i] for row in cells if i < len(row)] for i in range(width)]
    weights = [max((len(text) for text in column), default=1) ** 0.5 for column in columns]
    shares = [weight / sum(weights) for weight in weights]
    words = [max((len(word) for text in column for word in text.split()), default=1) for column in columns]
    shares = [max(share, (word + 1) / _LINE_CHARS[size]) for share, word in zip(shares, words, strict=True)]
    return [share / sum(shares) for share in shares]


class _MarkdownToLatex:
    """The CommonMark (plus tables) that models write, as LaTeX; raw HTML is printed as text."""

    _HEADINGS = (r"\subsection*", r"\subsubsection*", r"\paragraph*", r"\paragraph*", r"\paragraph*", r"\paragraph*")

    def __init__(self, cite: Callable[[list[str]], str]) -> None:
        self._cite = cite
        self._parser = MarkdownIt("commonmark", {"html": False}).enable("table")

    def block(self, markdown: str) -> str:
        protected = rewrite_inline_citations(markdown, lambda ids: f"{_CITE_OPEN}{','.join(ids)}{_CITE_CLOSE}")
        return self._blocks(self._parser.parse(protected)).strip()

    def inline(self, markdown: str) -> str:
        """One line of Markdown, without paragraph breaks."""
        return re.sub(r"\n{2,}", " ", self.block(markdown))

    def _blocks(self, tokens: Sequence[Token]) -> str:
        out: list[str] = []
        index, list_depth, enumerate_depth = 0, 0, 0
        while index < len(tokens):
            token = tokens[index]
            kind = token.type
            if kind == "table_open":
                end = next(i for i in range(index, len(tokens)) if tokens[i].type == "table_close")
                out.append(self._table(tokens[index:end + 1]))
                index = end + 1
                continue
            if kind == "heading_open":
                out.append(self._HEADINGS[int(token.tag[1]) - 1] + "{")
            elif kind == "heading_close":
                out.append("}\n\n")
            elif kind == "paragraph_close":
                out.append("\n" if token.hidden else "\n\n")
            elif kind == "inline":
                out.append(self._inline(token.children or []))
            elif kind in {"bullet_list_open", "ordered_list_open"}:
                list_depth += 1
                env = "itemize" if kind == "bullet_list_open" else "enumerate"
                out.append(rf"\begin{{{env}}}" + "\n" if list_depth <= 4 else "")
                if env == "enumerate":
                    enumerate_depth += 1
                    # A list that starts past 1: models number items across the bullets between them
                    # ("3. Taspen", bullets, "4. ASABRI"), which CommonMark parses as a list per number.
                    start = int(token.attrGet("start") or 1)
                    if start != 1 and list_depth <= 4 and enumerate_depth <= 4:
                        counter = "enum" + ("i", "ii", "iii", "iv")[enumerate_depth - 1]
                        out.append(rf"\setcounter{{{counter}}}{{{start - 1}}}" + "\n")
            elif kind in {"bullet_list_close", "ordered_list_close"}:
                env = "itemize" if kind == "bullet_list_close" else "enumerate"
                out.append(rf"\end{{{env}}}" + "\n\n" if list_depth <= 4 else "")
                list_depth -= 1
                enumerate_depth -= env == "enumerate"
            elif kind == "list_item_open":
                out.append(r"\item " if list_depth <= 4 else r"\par\textbullet{} ")
            elif kind == "list_item_close":
                out.append("\n")
            elif kind == "blockquote_open":
                out.append("\\begin{quote}\n")
            elif kind == "blockquote_close":
                out.append("\\end{quote}\n\n")
            elif kind in {"fence", "code_block"}:
                code = token.content.replace(r"\end{verbatim}", r"\end {verbatim}")
                out.append("\\begin{verbatim}\n" + code.rstrip("\n") + "\n\\end{verbatim}\n\n")
            elif kind == "hr":
                out.append("\\par\\noindent\\rule{\\linewidth}{0.4pt}\n\n")
            elif kind == "html_block":
                out.append(self._text(token.content) + "\n\n")
            index += 1
        return "".join(out)

    def _table(self, tokens: Sequence[Token]) -> str:
        rows: list[list[str]] = []
        texts: list[list[str]] = []
        header_rows = 0
        in_head = False
        for token in tokens:
            if token.type == "thead_open":
                in_head = True
            elif token.type == "thead_close":
                in_head = False
            elif token.type == "tr_open":
                rows.append([])
                texts.append([])
                header_rows += in_head
            elif token.type == "inline":
                rows[-1].append(self._inline(token.children or [], links=False, breakable=True))
                texts[-1].append(_CITE_PLACEHOLDER.sub("[s0]", token.content))
        width = max((len(row) for row in rows), default=1)
        # Smaller type for wide tables, which otherwise wrap every cell to a word a line.
        size = r"\footnotesize" if width >= 6 else r"\small" if width >= 4 else ""
        shares = _column_shares(texts, width, size)
        columns = "".join(rf">{{\raggedright\arraybackslash}}p{{{share:.3f}\dimexpr\linewidth-{2 * width}\tabcolsep\relax}}"
                          for share in shares)
        lines = [*([rf"{{{size}"] if size else []), rf"\begin{{longtable}}{{@{{}}{columns}@{{}}}}", r"\toprule"]
        for number, row in enumerate(rows):
            cells = row + [""] * (width - len(row))
            cells = [rf"\textbf{{{cell}}}" if number < header_rows and cell else cell for cell in cells]
            lines.append(" & ".join(cells) + r" \\")
            if number == header_rows - 1:
                lines += [r"\midrule", r"\endhead"]
        lines += [r"\bottomrule", r"\end{longtable}", *(["}"] if size else []), "", ""]
        return "\n".join(lines)

    def _inline(self, tokens: Sequence[Token], *, links: bool = True, breakable: bool = False) -> str:
        """Inline tokens as LaTeX; `breakable` lets text break after a slash, as narrow table cells need."""
        out: list[str] = []
        open_links: list[bool] = []
        for token in tokens:
            kind = token.type
            if kind == "text":
                out.append(self._text(token.content, breakable=breakable))
            elif kind == "softbreak":
                out.append("\n")
            elif kind == "hardbreak":
                out.append("\\newline\n")
            elif kind == "code_inline":
                out.append(rf"\texttt{{{self._text(token.content)}}}")
            elif kind == "strong_open":
                out.append(r"\textbf{")
            elif kind == "em_open":
                out.append(r"\emph{")
            elif kind in {"strong_close", "em_close"}:
                out.append("}")
            elif kind == "link_open":
                href = str(token.attrs.get("href", ""))
                linked = links and urlsplit(href).scheme.lower() in _SAFE_LINK_SCHEMES
                open_links.append(linked)
                if linked:
                    out.append(rf"\href{{{_href_target(href)}}}{{")
            elif kind == "link_close":
                if open_links.pop():
                    out.append("}")
            elif kind == "image":
                alt = "".join(child.content for child in token.children or [])
                out.append(rf"[image: {self._text(alt)}]" if alt else "[image]")
            elif kind == "html_inline":
                out.append(self._text(token.content))
        return "".join(out)

    def _text(self, text: str, *, breakable: bool = False) -> str:
        def escape(plain: str) -> str:
            escaped = latex_escape(plain)
            return escaped.replace("/", r"/\allowbreak{}") if breakable else escaped

        pieces: list[str] = []
        last = 0
        for match in _CITE_PLACEHOLDER.finditer(text):
            pieces.append(escape(text[last:match.start()]))
            pieces.append(self._cite(match.group(1).split(",")))
            last = match.end()
        pieces.append(escape(text[last:]))
        return "".join(pieces)


_LATEX_PREAMBLE = r"""\documentclass[11pt,a4paper]{article}
\usepackage{iftex}
\ifPDFTeX
  \usepackage[T1]{fontenc}
  \usepackage[utf8]{inputenc}
  \usepackage{textcomp}
  \IfFileExists{lmodern.sty}{\usepackage{lmodern}}{}
%(pdftex_chars)s
  \newcommand{\rlscript}[2]{#2}
\else
  \usepackage{fontspec}
  \IfFileExists{newunicodechar.sty}{\usepackage{newunicodechar}
%(unicode_chars)s
  }{}
  \newcommand{\rlscript}[2]{\csname rlscript@#1\endcsname{#2}}
%(script_fonts)s
\fi
\usepackage[margin=2.5cm]{geometry}
\usepackage[table]{xcolor}
\usepackage{array,booktabs,longtable}
\IfFileExists{microtype.sty}{\usepackage[expansion=false]{microtype}}{}
\IfFileExists{enumitem.sty}{\usepackage{enumitem}\setlist{itemsep=2pt,topsep=4pt}}{}
\usepackage[hyphens]{url}
\usepackage{hyperref}
\IfFileExists{lastpage.sty}{\usepackage{lastpage}\newcommand{\rlpageof}{ of \pageref*{LastPage}}}{\newcommand{\rlpageof}{}}
\definecolor{rlaccent}{HTML}{1D4ED8}
\definecolor{rlok}{HTML}{15803D}
\definecolor{rlwarn}{HTML}{B45309}
\definecolor{rlbad}{HTML}{B91C1C}
\definecolor{rlmuted}{HTML}{57606A}
\hypersetup{colorlinks=true,linkcolor=rlaccent,citecolor=rlaccent,urlcolor=rlaccent,
  pdftitle={%(title)s},pdfcreator={research-loop}}
\setlength{\parindent}{0pt}
\setlength{\parskip}{0.55em}
\setlength{\LTpre}{0.5em}
\setlength{\LTpost}{0.5em}
\urlstyle{same}
\newcommand{\rlbadge}[2]{{\setlength{\fboxsep}{1.5pt}\colorbox{#1!12}{\textcolor{#1}{\footnotesize\sffamily #2}}}}
\newcommand{\rlnote}[1]{{\small\color{rlmuted}#1}}
\newcommand{\rlshorttitle}{%(short_title)s}
\IfFileExists{titlesec.sty}{\usepackage{titlesec}
  \titleformat{\section}{\Large\bfseries\color{rlaccent}}{\thesection}{0.8em}{}[{\color{rlaccent!35}\titlerule}]
  \titleformat{\subsection}{\large\bfseries}{\thesubsection}{0.6em}{}
  \titlespacing*{\section}{0pt}{1.6em}{0.9em}}{}
\IfFileExists{fancyhdr.sty}{\usepackage{fancyhdr}
  \pagestyle{fancy}\fancyhf{}
  \fancyhead[L]{\small\color{rlmuted}\rlshorttitle}
  \fancyhead[R]{\small\color{rlmuted}Research report}
  \fancyfoot[C]{\small\color{rlmuted}\thepage\rlpageof}
  \renewcommand{\headrulewidth}{0.4pt}
  \setlength{\headheight}{14pt}}{}
\setcounter{tocdepth}{1}
\newenvironment{rlsources}{\begin{list}{}{\setlength{\leftmargin}{3.6em}\setlength{\labelwidth}{3.2em}%%
  \setlength{\labelsep}{0.4em}\setlength{\itemsep}{0.5em}\setlength{\parsep}{0.2em}\setlength{\topsep}{0.3em}}}%%
  {\end{list}}
"""


def render_latex(doc: ReportDocument) -> str:
    """A standalone LaTeX document; pdfLaTeX, XeLaTeX, LuaLaTeX, and Tectonic all compile it."""
    known = doc.source_ids

    def cite(ids: list[str]) -> str:
        # Braced brackets, so a citation opening an \item is not read as the item's label.
        return "{[}" + ", ".join(rf"\hyperref[{_label('src', i)}]{{{i}}}" if i in known else i for i in ids) + "{]}"

    md = _MarkdownToLatex(cite)
    claim_ids = doc.ledger.claim_ids()
    body: list[str] = [_latex_title(doc, md)]
    body += [r"\section{Findings}", md.block(tidy_answer(doc.report.answer)) or r"\rlnote{The report has no answer.}", ""]
    body += _latex_disputed(doc, md, claim_ids)
    if doc.report.claims:
        claim_sources = doc.ledger.claim_source_ids()
        body += [r"\section{Key statements}", r"\begin{enumerate}"]
        for claim in doc.report.claims:
            check = doc.check_for(claim)
            color = "rlmuted" if check is None else "rlok" if check.supported and check.severity == "none" else (
                "rlwarn" if check.supported or check.severity != "major" else "rlbad")
            details = []
            if claim.claim_ids:
                details.append("evidence " + ", ".join(_claim_ref(claim_id, claim_ids) for claim_id in claim.claim_ids))
            sources = sorted({s for claim_id in claim.claim_ids for s in claim_sources.get(claim_id, ())}, key=_natural_key)
            if sources:
                details.append("sources " + cite(sources))
            body.append(rf"\item {md.inline(claim.statement)} \rlbadge{{{color}}}{{{_check_label(check)}}}"
                        + (rf"\newline\rlnote{{{'; '.join(details)}}}" if details else ""))
        body += [r"\end{enumerate}", ""]
    if doc.report.caveats:
        body += [r"\section{Caveats}", r"\begin{itemize}"]
        body += [rf"\item {md.inline(caveat)}" for caveat in doc.report.caveats]
        body += [r"\end{itemize}", ""]
    body += _latex_sources(doc, claim_ids)
    body += [r"\appendix", *_latex_plan(doc, md), *_latex_ledger(doc, md, cite, claim_ids),
             *_latex_verification(doc, md, claim_ids)]
    text = _mark_scripts("\n".join(body))
    pdftex_chars, unicode_chars = _unicode_declarations(text)
    preamble = _LATEX_PREAMBLE % {
        "pdftex_chars": pdftex_chars,
        "unicode_chars": unicode_chars,
        "title": latex_escape(report_title(doc)),
        "short_title": _mark_scripts(latex_escape(_truncate(report_title(doc), 70))),
        "script_fonts": _SCRIPT_FONTS,
    }
    return preamble + "\n\\begin{document}\n\n" + text + "\n\\end{document}\n"


# How much of the objective the title shows. A benchmark objective can run to pages, with its task
# text, formatting, and blocked-source constraints; the plan appendix prints it whole.
_TITLE_OBJECTIVE_CHARS = 400


def _latex_title(doc: ReportDocument, md: _MarkdownToLatex) -> str:
    """The title page: the title, the objective's opening, a verification scorecard, and the run's details."""
    first = doc.objective.strip().split("\n\n", 1)[0]
    shown = _truncate(first, _TITLE_OBJECTIVE_CHARS)
    more = shown != " ".join(doc.objective.split())
    where = "the research plan in the appendix" if doc.plan else "the run record"
    details = [("Generated", doc.generated_at.strftime("%d %B %Y, %H:%M UTC"))]
    if doc.job_id:
        details.append(("Job", rf"\texttt{{{latex_escape(doc.job_id)}}}"))
    if doc.cost_usd is not None:
        details.append(("Cost", latex_escape(f"${doc.cost_usd:.2f}")))
    return "\n".join([
        r"\begin{titlepage}",
        r"\noindent{\color{rlaccent}\rule{\linewidth}{2pt}}\par\vspace{1.4em}",
        r"\noindent{\small\sffamily\bfseries\color{rlaccent}RESEARCH REPORT}\par\vspace{0.9em}",
        rf"\noindent{{\huge\bfseries\raggedright {md.inline(report_title(doc))}\par}}\vspace{{1.4em}}",
        rf"\noindent{{\color{{rlmuted}}{md.inline(shown)}\par}}",
        *([rf"\noindent\rlnote{{The full objective is in {where}.}}\par"] if more else []),
        r"\vfill",
        _latex_scorecard(doc),
        r"\vspace{1.6em}",
        r"\noindent{\small\begin{tabular}{@{}l@{\hspace{1.2em}}l@{}}",
        *(rf"\textcolor{{rlmuted}}{{{label}}} & {value} \\" for label, value in details),
        r"\end{tabular}}\par",
        r"\vspace{0.8em}\noindent{\color{rlaccent}\rule{\linewidth}{0.6pt}}",
        r"\end{titlepage}",
        r"\tableofcontents",
        r"\clearpage",
        "",
    ])


def _latex_scorecard(doc: ReportDocument) -> str:
    """Verification and evidence as four figures, then what needs review."""
    checks = doc.verification.checks
    supported = sum(check.supported for check in checks)
    major = sum(check.severity == "major" for check in checks)
    share = supported / len(checks) if checks else 0.0
    support_color = "rlmuted" if not checks else "rlok" if share >= 0.9 else "rlwarn" if share >= 0.7 else "rlbad"
    figures = [
        (f"{supported}/{len(checks)}" if checks else "--", support_color, "statements the verifier supported"),
        (str(major), "rlbad" if major else "rlok", "rated major"),
        (str(len(doc.sources)), "rlaccent", "sources in the ledger"),
        (str(len(doc.ledger.results)), "rlaccent", "research questions"),
    ]
    cells = [rf"{{\LARGE\bfseries\color{{{color}}}{latex_escape(value)}}}\newline{{\footnotesize\color{{rlmuted}}{label}}}"
             for value, color, label in figures]
    lines = [r"\noindent\begin{tabular}{@{}*{4}{>{\raggedright\arraybackslash}p{0.225\linewidth}}@{}}",
             " & ".join(cells) + r" \\", r"\end{tabular}\par\vspace{1em}"]
    color = "rlok" if not doc.review_reasons else "rlbad" if any(not check.supported for check in checks) else "rlwarn"
    lines.append(rf"\noindent\fcolorbox{{{color}}}{{{color}!5}}{{\parbox{{\dimexpr\linewidth-2\fboxsep-2\fboxrule\relax}}{{\small%")
    if doc.review_reasons:
        lines += [r"\textbf{Needs review}", r"\begin{itemize}"]
        lines += [rf"\item {latex_escape(reason)}" for reason in doc.review_reasons]
        lines.append(r"\end{itemize}")
    else:
        lines.append(r"\textbf{No check flagged a problem.}")
    lines.append("}}")
    return "\n".join(lines)


def _latex_disputed(doc: ReportDocument, md: _MarkdownToLatex, claim_ids: set[str]) -> list[str]:
    """The statements the verifier rated major, with its reasons, placed before the evidence behind them."""
    major = [check for check in doc.verification.checks if check.severity == "major"]
    if not major:
        return []
    lines = [r"\section{Statements the verifier disputed}",
             (rf"The verifier rated {len(major)} of the report's statements a major problem: unsupported, wrong, or "
              r"misleading as written. Read the findings above with these in mind."), r"\begin{enumerate}"]
    for check in major:
        refs = ", ".join(_claim_ref(claim_id, claim_ids) for claim_id in check.claim_ids)
        lines.append(rf"\item {md.inline(check.statement)}" + (rf" \rlnote{{({refs})}}" if refs else "")
                     + rf"\newline{{\small\color{{rlbad}}{md.inline(check.explanation)}}}")
    return [*lines, r"\end{enumerate}", ""]


def _latex_sources(doc: ReportDocument, claim_ids: set[str]) -> list[str]:
    lines = [r"\section{Sources}",
             (r"Every source in the evidence ledger, under the ID the report cites it by. "
              r"Each entry notes whether the report cites it and which ledger claims rest on it."), ""]
    if not doc.sources:
        return [*lines, r"\rlnote{The ledger holds no sources.}", ""]
    for group, entries in doc.grouped_sources():
        lines += [rf"\subsection{{{GROUP_TITLES[group]}}}", r"\begin{rlsources}"]
        lines += [_latex_source(entry, claim_ids) for entry in entries]
        lines += [r"\end{rlsources}", ""]
    return lines


def _latex_source(entry: SourceEntry, claim_ids: set[str]) -> str:
    row = entry.row
    title = latex_escape(entry.title)
    parts = [rf"\textit{{{title}}}" if entry.group == "scholarly" else title]
    if entry.group == "web" and row.get("url") and (host := _host(str(row["url"]))):
        parts.append(latex_escape(host))
    parts += [latex_escape(detail) for detail in _describe_source(row)]
    if doi := row.get("doi"):
        parts.append(rf"DOI~\href{{{_href_target('https://doi.org/' + str(doi))}}}{{\texttt{{{_url_text(str(doi))}}}}}")
    if arxiv := row.get("arxiv_id"):
        parts.append(rf"arXiv~\href{{{_href_target('https://arxiv.org/abs/' + str(arxiv))}}}{{\texttt{{{latex_escape(str(arxiv))}}}}}")
    if row.get("attachment_id"):
        parts.append(rf"attachment \texttt{{{latex_escape(str(row['attachment_id']))}}}")
    elif row.get("url") and not _repeats_identifier(row):
        parts.append(_latex_url(str(row["url"])))
    badges = []
    if row.get("is_retracted"):
        badges.append(r"\rlbadge{rlbad}{retracted}")
    if entry.not_observed:
        badges.append(r"\rlbadge{rlwarn}{source not observed}")
    note = entry.usage
    if entry.claim_ids:
        note += "; claims " + ", ".join(_claim_ref(claim_id, claim_ids) for claim_id in entry.claim_ids)
    color = "rlaccent" if entry.cited else "rlmuted"
    return (rf"\item[\textcolor{{{color}}}{{[{entry.id}]}}]\phantomsection\label{{{_label('src', entry.id)}}}"
            + ". ".join(parts) + "." + (" " + " ".join(badges) if badges else "")
            + rf"\newline\rlnote{{{note}.}}")


def _latex_plan(doc: ReportDocument, md: _MarkdownToLatex) -> list[str]:
    if not doc.plan:
        return []
    lines = [r"\section{Research plan}", r"\textbf{Objective:}", "", md.block(doc.objective), ""]
    if doc.plan.questions:
        lines.append(r"\begin{itemize}")
        for question in doc.plan.questions:
            lines.append(rf"\item \texttt{{{latex_escape(question.id)}}} {md.inline(question.question)}"
                         rf" \rlnote{{({latex_escape('; '.join(_question_flags(question)))})}}")
        lines.append(r"\end{itemize}")
    if doc.plan.stop_conditions:
        lines += [r"\textbf{Stop conditions:}", r"\begin{itemize}"]
        lines += [rf"\item {md.inline(condition)}" for condition in doc.plan.stop_conditions]
        lines.append(r"\end{itemize}")
    return [*lines, ""]


def _latex_ledger(doc: ReportDocument, md: _MarkdownToLatex, cite: Callable[[list[str]], str],
                  claim_ids: set[str]) -> list[str]:
    evidence_ids = doc.ledger.evidence_source_ids()
    lines = [r"\section{Evidence ledger}",
             (r"Each research pass on a question, with its claims and the evidence behind them. "
              r"A quote is \emph{verified} when it appears in what the research tools returned; "
              r"a source is \emph{not observed} when no tool returned it."), ""]
    if not doc.ledger.results:
        return [*lines, r"\rlnote{The ledger is empty.}", ""]
    for question_id in doc.question_ids():
        lines.append(rf"\subsection{{\texttt{{{latex_escape(question_id)}}}: {md.inline(doc.question_text(question_id))}}}")
        results = doc.ledger.for_question(question_id)
        for number, result in enumerate(results, 1):
            if len(results) > 1:
                lines.append(rf"\subsubsection*{{Research pass {number}}}")
            lines.append(rf"\textbf{{Conclusion}} \rlnote{{(confidence {result.confidence:.2f})}}: {md.inline(result.conclusion)}")
            lines.append("")
            for claim in result.claims:
                lines.append(rf"\noindent\phantomsection\label{{{_label('claim', claim.id)}}}"
                             rf"\textbf{{\texttt{{{latex_escape(claim.id)}}}}} {md.inline(claim.statement)}"
                             rf" \rlnote{{(confidence {claim.confidence:.2f})}}")
                if claim.evidence:
                    lines.append(r"\begin{itemize}")
                    for index, item in enumerate(claim.evidence):
                        marks = [rf"\rlbadge{{{'rlok' if mark == 'quote verified' else 'rlwarn'}}}{{{mark}}}"
                                 for mark in _evidence_marks(item)]
                        text = (rf"``{latex_escape(item.quote)}''" if item.quote else latex_escape(item.excerpt))
                        locator = item.source.locator
                        where = rf" \rlnote{{({latex_escape(locator)})}}" if locator else ""
                        lines.append(rf"\item {cite([evidence_ids[(claim.id, index)]])}{where} {{\small {text}}}"
                                     + (" " + " ".join(marks) if marks else ""))
                    lines.append(r"\end{itemize}")
                lines.append("")
            if result.contradictions:
                lines += [r"\textbf{Contradictions:}", r"\begin{itemize}"]
                for item in result.contradictions:
                    refs = ", ".join(_claim_ref(claim_id, claim_ids) for claim_id in item.claim_ids)
                    lines.append(rf"\item {md.inline(item.description)}" + (rf" \rlnote{{({refs})}}" if refs else ""))
                lines.append(r"\end{itemize}")
            if result.unresolved_questions:
                lines += [r"\textbf{Unresolved:}", r"\begin{itemize}"]
                lines += [rf"\item {md.inline(question)}" for question in result.unresolved_questions]
                lines.append(r"\end{itemize}")
            lines.append("")
    return lines


def _latex_verification(doc: ReportDocument, md: _MarkdownToLatex, claim_ids: set[str]) -> list[str]:
    lines = [r"\section{Verification}"]
    if not doc.verification.checks:
        lines.append(r"\rlnote{The verifier checked no statements.}")
    else:
        lines.append(r"\begin{enumerate}")
        for check in doc.verification.checks:
            color = "rlok" if check.supported and check.severity == "none" else (
                "rlbad" if check.severity == "major" or not check.supported else "rlwarn")
            refs = ", ".join(_claim_ref(claim_id, claim_ids) for claim_id in check.claim_ids)
            lines.append(rf"\item \rlbadge{{{color}}}{{{_check_label(check)}}} {md.inline(check.statement)}"
                         + (rf" \rlnote{{({refs})}}" if refs else "")
                         + rf"\newline{{\small {md.inline(check.explanation)}}}")
        lines.append(r"\end{enumerate}")
    if doc.verification.followups:
        lines += [r"\textbf{Follow-ups requested:}", r"\begin{itemize}"]
        for gap in doc.verification.followups:
            lines.append(rf"\item \texttt{{{latex_escape(gap.question_id)}}} "
                         rf"{latex_escape(gap.reason.replace('_', ' '))}, severity {gap.severity}: {md.inline(gap.followup)}")
        lines.append(r"\end{itemize}")
    return [*lines, ""]


# --- BibTeX ---------------------------------------------------------------------------------


def render_bibtex(doc: ReportDocument) -> str:
    """Every ledger source as a BibTeX entry keyed by its source ID, for a reference manager."""
    entries = []
    for entry in doc.sources:
        row = entry.row
        fields: list[tuple[str, str]] = [("title", "{" + latex_escape(entry.title) + "}")]
        if year := _year(row):
            fields.append(("year", year))
        if row.get("doi"):
            fields.append(("doi", str(row["doi"])))
        if row.get("arxiv_id"):
            fields += [("eprint", str(row["arxiv_id"])), ("archiveprefix", "arXiv")]
        if row.get("url"):
            fields += [("url", str(row["url"])), ("howpublished", rf"\url{{{row['url']}}}")]
        notes = [_STATUS_LABELS[status]] if (status := row.get("publication_status")) in _STATUS_LABELS else []
        if row.get("attachment_id"):
            notes.append(f"Attachment {row['attachment_id']}" + (f", {row['locator']}" if row.get("locator") else ""))
        if row.get("is_retracted"):
            notes.append("Retracted")
        if notes:
            fields.append(("note", latex_escape(". ".join(notes))))
        fields.append(("keywords", entry.group))
        body = ",\n".join(f"  {name} = {{{value}}}" for name, value in fields)
        entries.append(f"@misc{{{entry.id},\n{body}\n}}\n")
    return "\n".join(entries)


# --- output ---------------------------------------------------------------------------------


_LATEX_BUILD_FILES = (".aux", ".log", ".out", ".fls", ".fdb_latexmk", ".xdv", ".toc")


class LatexError(RuntimeError):
    """A LaTeX engine is missing, or it could not compile the document."""


def find_engine(preferred: str | None = None) -> str | None:
    """The LaTeX engine to compile with: `preferred` if given and installed, else the first installed."""
    if preferred:
        if preferred not in ENGINES:
            raise LatexError(f"unknown LaTeX engine {preferred!r}; choose one of {', '.join(ENGINES)}")
        return preferred if shutil.which(preferred) else None
    return next((engine for engine in ENGINES if shutil.which(engine)), None)


def compile_pdf(tex_path: Path, *, engine: str | None = None, timeout: float = 180.0) -> Path:
    """Compile `tex_path` beside itself and return the PDF; shell escape stays off.

    Without an `engine`, a document with Thai or CJK text goes to XeLaTeX (or LuaLaTeX, then Tectonic)
    when one is installed, since pdfLaTeX prints those characters as code points.
    """
    chosen = find_engine(engine)
    if engine is None and needs_unicode_engine(tex_path.read_text(encoding="utf-8")):
        chosen = next((name for name in _UNICODE_ENGINES if shutil.which(name)), chosen)
    if chosen is None:
        wanted = engine or " or ".join(ENGINES)
        raise LatexError(f"no LaTeX engine found ({wanted}); install TeX Live or Tectonic, or compile {tex_path.name} yourself")
    name, workdir = tex_path.name, tex_path.parent
    flags = ["-interaction=nonstopmode", "-halt-on-error", "-no-shell-escape"]
    if chosen == "latexmk":
        runs = [["latexmk", "-pdf", "-quiet", *flags, name]]
    elif chosen == "tectonic":
        runs = [["tectonic", "--keep-logs", name]]
    else:
        # Twice: the second run resolves the cross-references the first one wrote.
        runs = [[chosen, *flags, name]] * 2
    for command in runs:
        try:
            # pdflatex wraps its output at a fixed width in bytes, which can split a UTF-8 character.
            done = subprocess.run(command, cwd=workdir, capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=timeout, stdin=subprocess.DEVNULL, check=False)
        except subprocess.TimeoutExpired as exc:
            raise LatexError(f"{chosen} did not finish within {timeout:.0f}s") from exc
        if done.returncode != 0:
            log = tex_path.with_suffix(".log")
            detail = log.read_text(encoding="utf-8", errors="replace") if log.exists() else done.stdout + done.stderr
            errors = [line for line in detail.splitlines() if line.startswith("!") or ":error:" in line.lower()]
            summary = "\n".join(errors[:5]) or "\n".join(detail.splitlines()[-15:])
            raise LatexError(f"{chosen} failed on {name}; see {log}:\n{summary}")
    pdf = tex_path.with_suffix(".pdf")
    if not pdf.exists():
        raise LatexError(f"{chosen} finished but wrote no {pdf.name}")
    # A failed compile keeps these for debugging; a good one leaves only the .tex and the PDF.
    for suffix in _LATEX_BUILD_FILES:
        tex_path.with_suffix(suffix).unlink(missing_ok=True)
    return pdf


def write_report(
    doc: ReportDocument,
    out_dir: Path | str,
    formats: Iterable[str] = ("pdf", "md"),
    *,
    stem: str = "report",
    engine: str | None = None,
) -> dict[str, Path]:
    """Write `doc` in each format to `out_dir/<stem>.<ext>`; a PDF also leaves its .tex source there."""
    wanted = list(dict.fromkeys(formats))
    if unknown := [name for name in wanted if name not in FORMATS]:
        raise ValueError(f"unknown report format(s) {unknown}; choose from {', '.join(FORMATS)}")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    renderers: dict[str, Callable[[ReportDocument], str]] = {
        "tex": render_latex, "md": render_markdown, "html": render_html, "bib": render_bibtex,
        "json": lambda d: json.dumps(d.to_record(), indent=2, ensure_ascii=False) + "\n",
    }
    written: dict[str, Path] = {}
    for name in wanted:
        if name == "pdf":
            continue
        path = out / f"{stem}{_SUFFIX[name]}"
        path.write_text(renderers[name](doc), encoding="utf-8")
        written[name] = path
    if "pdf" in wanted:
        tex = written.get("tex") or out / f"{stem}.tex"
        if "tex" not in written:
            tex.write_text(render_latex(doc), encoding="utf-8")
            written["tex"] = tex
        written["pdf"] = compile_pdf(tex, engine=engine)
    return written


# --- command line ---------------------------------------------------------------------------


async def _load_job(dsn: str, job_id: UUID) -> dict[str, Any]:
    import psycopg

    async with await psycopg.AsyncConnection.connect(dsn) as conn:
        cursor = await conn.execute(
            "select objective, status, plan, final_report, verification, evidence_ledger, review_reasons, finished_at,"
            # What the job's calls were billed; null when any call had no price.
            " (select case when bool_and(t.usage ? 'cost' and t.usage->>'cost' is not null)"
            "  then sum((t.usage->>'cost')::numeric) end from research_tasks t where t.job_id = j.id and t.usage is not null)"
            " from research_jobs j where id = %s", (job_id,))
        row = await cursor.fetchone()
    if row is None:
        raise LookupError(f"no research job {job_id}")
    objective, status, plan, report, verification, ledger, reasons, finished, cost = row
    if status != "succeeded":
        raise LookupError(f"research job {job_id} is {status}; only a finished job has a report to render")
    return {"objective": objective, "plan": plan, "report": report, "verification": verification, "ledger": ledger,
            "review_reasons": reasons, "job_id": str(job_id), "generated_at": finished.isoformat() if finished else None,
            "cost_usd": None if cost is None else float(cost)}


def _parse_formats(value: str) -> list[str]:
    names = [name.strip().lower() for name in value.split(",") if name.strip()]
    names = list(FORMATS) if names == ["all"] else names
    if unknown := [name for name in names if name not in FORMATS]:
        raise argparse.ArgumentTypeError(f"unknown format(s) {', '.join(unknown)}; choose from all, {', '.join(FORMATS)}")
    return names


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="research-report",
        description="Render a finished research run as PDF (via LaTeX), LaTeX, Markdown, HTML, BibTeX, or JSON",
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("record", nargs="?", type=Path, help="A saved run record (JSON with report, verification, ledger)")
    source.add_argument("--job-id", type=UUID, help="Render a job stored in Postgres at DATABASE_URL")
    parser.add_argument("-o", "--output-dir", type=Path, help="Where to write (default: beside the record, or ./report)")
    parser.add_argument("-f", "--format", type=_parse_formats, default=["pdf", "md"],
                        help=f"Comma-separated formats, or all: {', '.join(FORMATS)} (default: pdf,md)")
    parser.add_argument("--stem", help="File name without extension (default: the record's, or job-<ID>)")
    parser.add_argument("--engine", choices=ENGINES, help="LaTeX engine for PDF (default: the first installed)")
    args = parser.parse_args(argv)

    if args.job_id:
        from .settings import ResearchSettings

        settings = ResearchSettings.from_env()
        if not settings.database_dsn:
            parser.error("--job-id needs DATABASE_URL; see docs/setup.md#postgres")
        try:
            record = asyncio.run(_load_job(settings.database_dsn, args.job_id))
        except LookupError as exc:
            parser.error(str(exc))
        out_dir = args.output_dir or Path("report")
        stem = args.stem or f"job-{args.job_id}"
    else:
        try:
            record = json.loads(args.record.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            parser.error(f"cannot read {args.record}: {exc}")
        out_dir = args.output_dir or args.record.parent
        stem = args.stem or args.record.stem
    try:
        doc = ReportDocument.from_record(record)
    except ValueError as exc:
        message = str(exc)
        if record.get("ledger") is None and record.get("report") is not None:
            # readme_example records written before it saved the ledger; Postgres kept it if persisted.
            if record.get("persisted") and record.get("job_id"):
                message += f"; the job was persisted, so try: research-report --job-id {record['job_id']}"
            else:
                message += "; records from before readme_example saved the ledger can't be rendered, so rerun it"
        parser.error(message)
    if args.record and "json" in args.format and (Path(out_dir) / f"{stem}.json").resolve() == args.record.resolve():
        if set(args.format) != set(FORMATS):
            parser.error("the json output would overwrite the record; pass --stem or --output-dir")
        args.format.remove("json")  # `all`: the record is already there
    try:
        written = write_report(doc, out_dir, args.format, stem=stem, engine=args.engine)
    except LatexError as exc:
        # The .tex source is already written; say where, and how the PDF failed.
        print(f"PDF not written: {exc}")
        tex = Path(out_dir) / f"{stem}.tex"
        if tex.exists():
            print(f"tex: {tex}")
        raise SystemExit(1) from exc
    for name, path in written.items():
        print(f"{name}: {path}")


if __name__ == "__main__":
    main()

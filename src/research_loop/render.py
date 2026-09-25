"""A run as a Markdown report: the answer, how well it is supported, and every source it cites.

`render_markdown` takes a run record, the dictionary `ScoutRun.to_record` returns and `research show`
rebuilds from a stored run. Sources keep the [sN] IDs the report cites them by, and each says how much of
it the research read.
"""
from __future__ import annotations

import re
from typing import Any

from .evidence import EvidenceLedger, inline_source_ids

_ACCESS = {"full_text": "read in full", "abstract": "abstract read", "metadata": "record only",
           "snippet": "search snippet only"}


def render_markdown(record: dict[str, Any]) -> str:
    report = record.get("report")
    checks = record.get("checks") or {}
    ledger = EvidenceLedger.from_json(record.get("ledger") or {})
    sources = {row["id"]: row for row in ledger.source_table()}
    lines = [f"# {_title(record)}", "", f"Question: {record['question']}", "", _status_line(record, sources), ""]
    if reasons := checks.get("review_reasons"):
        lines += ["## Needs review", "", *(f"- {reason}" for reason in reasons), ""]
    if report:
        if report.get("executive_summary"):
            lines += ["## Summary", "", report["executive_summary"], ""]
        lines += ["## Answer", "", _shift_headings(tidy_answer(report["answer"]), 1), ""]
        if report.get("caveats"):
            lines += ["## Caveats", "", *(f"- {caveat}" for caveat in report["caveats"]), ""]
        weak = [s for s in checks.get("statements", []) if s["support"] != "read"]
        if weak:
            lines += ["## Statements resting on thin evidence", "",
                      ("These statements rest only on search snippets, records without an abstract, or quotes and "
                       "sources the research tools did not return, or on no evidence at all."), ""]
            lines += [f"- {s['statement']} ({'no supporting evidence' if s['support'] == 'unsupported' else 'not read'})"
                      for s in weak]
            lines.append("")
    else:
        lines += _claims_only(ledger)
    if missing := checks.get("not_established"):
        lines += ["## Could not establish", "", *(f"- {item}" for item in missing), ""]
    cited = _cited_ids(report, ledger)
    if cited:
        lines += ["## Sources", ""]
        lines += [_source_line(sources[source_id]) for source_id in sorted(cited, key=lambda s: int(s[1:]))
                  if source_id in sources]
        lines.append("")
    if unreached := checks.get("unreached"):
        lines += ["## Sources that could not be read", ""]
        lines += [f"- {item['target']}: {item['reason']}" for item in unreached]
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _title(record: dict[str, Any]) -> str:
    report = record.get("report") or {}
    title = " ".join(str(report.get("title") or "").split())
    return title or " ".join(record["question"].split())[:110]


def _status_line(record: dict[str, Any], sources: dict[str, dict[str, Any]]) -> str:
    parts = [f"Scout run `{record['run_id']}`", str(record["status"])]
    if (cost := record.get("cost_usd")) is not None:
        parts.append(f"${float(cost):.2f}")
    if (seconds := record.get("seconds")) is not None:
        parts.append(f"{float(seconds) / 60:.1f} minutes")
    read = sum(row.get("access") in ("full_text", "abstract") for row in sources.values())
    parts.append(f"{len(sources)} sources, {read} read as full text or abstract")
    if record.get("trace_id"):
        parts.append(f"trace `{record['trace_id']}`")
    return " · ".join(parts)


def _cited_ids(report: dict[str, Any] | None, ledger: EvidenceLedger) -> set[str]:
    if report:
        texts = [report.get("executive_summary") or "", report["answer"], *(report.get("caveats") or [])]
        return {source_id for text in texts for source_id in inline_source_ids(text)}
    return {source_id for ids in ledger.claim_source_ids().values() for source_id in ids}


def _source_line(row: dict[str, Any]) -> str:
    where = row.get("url") or (f"https://doi.org/{row['doi']}" if row.get("doi") else f"arXiv:{row.get('arxiv_id')}")
    details = [_ACCESS.get(row.get("access", ""), "not returned by any tool")]
    if row.get("publication_status") not in (None, "unknown"):
        details.append(row["publication_status"].replace("_", " "))
    if row.get("is_retracted"):
        details.append("retracted")
    return f"- [{row['id']}] {row.get('title', '')}. {where} ({', '.join(details)})"


def _claims_only(ledger: EvidenceLedger) -> list[str]:
    """The research's claims by question, for a run whose synthesis did not finish."""
    lines = ["## Claims found", "", "The synthesis did not finish, so these are the research's claims as found.", ""]
    sources = ledger.claim_source_ids()
    for result in ledger.all():
        lines += [f"### {result.question_id}: {result.question}", "", result.conclusion, ""]
        for claim in result.claims:
            ids = sorted(sources.get(claim.id, ()), key=lambda s: int(s[1:]))
            lines.append(f"- {claim.statement}" + (f" [{', '.join(ids)}]" if ids else "") + f" ({claim.id})")
        lines.append("")
    return lines


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


# Markdown that models write, made into the Markdown they meant. Each fix comes from a settings-study report.

# A bullet whose text is cells separated by " | ", and the "(Columns: a | b | c)" line that names them.
_PIPE_ROW = re.compile(r"^\s*[-*+]\s+(.*\S)\s*$")
_COLUMNS_LINE = re.compile(r"^\s*\(?\s*columns?\s*:\s*(.+?)\s*\)?\s*$", re.IGNORECASE)
_RULE_LINE = re.compile(r"^\s*(=|-){3,}\s*$")
# A heading written on one line between runs of "=": "=== SECTION 1. PROFILES ===".
_FENCED_HEADING = re.compile(r"^\s*={2,}\s*(\S.*?\S)\s*={2,}\s*$")
# A short line of capitals, digits, and joining punctuation: "INDONESIA", "SRI LANKA", "SECTION 2".
_CAPS_LINE = re.compile(r"^[A-Z][A-Z0-9 &'/,.()-]{1,58}[A-Z0-9)]$")
_NUMBERED_ITEM = re.compile(r"^(\d{1,3}[.)]\s+)\S")
_BULLET = re.compile(r"^[-*+]\s+\S")
_ITEM_HEADING_CHARS = 120


def tidy_answer(markdown: str) -> str:
    """A model's answer with its plain-text headings, numbered items' bullets, and bullet tables made Markdown."""
    return pipe_rows_as_tables(bullets_under_numbered_items(plain_headings_as_markdown(markdown)))


def plain_headings_as_markdown(markdown: str) -> str:
    """Headings written as plain text made into Markdown headings: a line between two rules of `=` or `-`, or
    between runs of `=` on one line, becomes a top-level heading, and a line of capitals standing alone after
    a blank line a second-level one."""
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
        if match := _FENCED_HEADING.match(line):
            out += ["# " + match.group(1), ""]
        elif _CAPS_LINE.match(line.strip()) and (not out or not out[-1].strip()) and any(c.isalpha() for c in line):
            out += ["## " + line.strip(), ""]
        else:
            out.append(line)
        index += 1
    return "\n".join(out)


def bullets_under_numbered_items(markdown: str) -> str:
    """Bullets that directly follow a numbered item, indented under it.

    Models write "1. Item" and then its "- detail" bullets at the left margin, which Markdown reads as a
    new list, so every number starts a list of its own. An item that gets bullets is a heading for them,
    so its text is set in bold when it is short and has no emphasis of its own.
    """
    out: list[str] = []
    indent, item = "", -1
    for line in markdown.split("\n"):
        if match := _NUMBERED_ITEM.match(line):
            indent, item = " " * len(match.group(1)), len(out)
        elif indent and (_BULLET.match(line) or (line[:1].isspace() and line.strip())):
            if item >= 0 and _BULLET.match(line):
                marker, text = out[item][:len(indent)], out[item][len(indent):].strip()
                if len(text) <= _ITEM_HEADING_CHARS and "*" not in text and "[" not in text:
                    out[item] = f"{marker}**{text}**"
                item = -1
            line = indent + line
        else:
            indent, item = "", -1
        out.append(line)
    return "\n".join(out)


def pipe_rows_as_tables(markdown: str) -> str:
    """Each run of two or more bullets written as " | "-separated rows of three or more cells made a table,
    headed by a preceding "(Columns: ...)" line when its count matches."""
    lines = markdown.split("\n")
    out: list[str] = []
    index = 0
    while index < len(lines):
        run: list[list[str]] = []
        end = index
        while end < len(lines) and (match := _PIPE_ROW.match(lines[end])) \
                and len(cells := [cell.strip() for cell in match.group(1).split(" | ")]) >= 3 \
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

        def row(cells: list[str]) -> str:
            return "| " + " | ".join(cell.replace("|", r"\|") for cell in cells) + " |"

        out += [row(header), "|" + "---|" * width, *(row(cells) for cells in run), ""]
        index = end
    return "\n".join(out)

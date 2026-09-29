"""Split the text a research tool returned into passages: the units a scout cites and a reader is pointed to.

A passage is a span of a text, given by character offsets, so the text itself is stored once and never edited
(docs/passage-evidence-plan.md). Every non-whitespace character of a text belongs to exactly one passage, in
order. Passages follow the text's own structure where it has one:
- blocks are separated by blank lines; a text with none, such as trafilatura's one paragraph a line or JSON's
  one value a line, has a block for each line; a PDF's hard-wrapped lines are joined back into paragraphs;
- consecutive table rows (`|` lines) form table passages of up to `TABLE_CHARS`, and a later part of a long
  table keeps a reference to the table's header row, which is shown before it;
- consecutive list items are grouped, up to `TARGET_CHARS`;
- a block shorter than `MIN_CHARS`, such as a heading, joins the block after it;
- a block longer than `TARGET_CHARS` is cut at sentence ends, or where there are none, at a space; Europe PMC's
  XML text has no line breaks at all.

The splitter is a pure function with a version (`SPLITTER_VERSION`); a passage's ID is a digest of its text's
hash and its offsets, so the same span always has the same ID whatever version found it.
"""
from __future__ import annotations

import bisect
import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

SPLITTER_VERSION = 1
# The most a text passage holds: small enough that a citation points a reader near the fact, since Claude
# cites whole passages (citations.py).
TARGET_CHARS = 800
# A block shorter than this joins the next one: a heading, a caption, a short list item.
MIN_CHARS = 80
# The most a table passage holds; a longer table is split between rows.
TABLE_CHARS = 2400
# Characters that mark passages in what a model is shown (`display`); a page's own are replaced, so a page
# cannot forge a marker.
MARK_OPEN, MARK_CLOSE = "⟦", "⟧"

Kind = Literal["text", "table", "list"]


@dataclass(frozen=True)
class Passage:
    """Characters `start` to `end` of a text, the `ordinal`th passage in it (from 0)."""

    ordinal: int
    start: int
    end: int
    kind: Kind = "text"
    # The page the passage starts on (from 1), when the text's page starts are known.
    page: int | None = None
    # A table's header row, as offsets, for a passage that continues a table past its first part.
    header: tuple[int, int] | None = None


def passage_id(text_sha256: str, start: int, end: int) -> str:
    """The stable ID of characters `start` to `end` of the text whose SHA-256 is `text_sha256`."""
    return hashlib.sha256(f"{text_sha256}:{start}:{end}".encode()).hexdigest()[:16]


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


def display(text: str, passage: Passage) -> str:
    """A passage's text as a model or reader is shown it: its table's header row first, when it continues a
    table, and the passage markers replaced, so text a page supplies cannot pose as a marker."""
    body = text[passage.start:passage.end]
    if passage.header:
        body = text[passage.header[0]:passage.header[1]] + "\n" + body
    return body.replace(MARK_OPEN, "[").replace(MARK_CLOSE, "]")


# --- Blocks --------------------------------------------------------------------------------------------------

_BLANK = re.compile(r"\n[ \t\r\f\v]*\n\s*")
_LIST_ITEM = re.compile(r"(?:[-*•·▪◦]|\d{1,3}[.)]|\(?[a-z]\)|\[\d{1,3}\])\s")
# Words before a full stop that do not end a sentence.
_ABBREVIATIONS = frozenset({
    "al", "e.g", "i.e", "cf", "fig", "figs", "eq", "eqs", "ref", "refs", "no", "nos", "vol", "vols", "pp", "p",
    "ch", "sec", "dr", "mr", "mrs", "ms", "prof", "st", "vs", "etc", "approx", "inc", "ltd", "co", "corp", "jan",
    "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec", "u.s", "u.k", "e.u", "ph.d"})
_SENTENCE_END = re.compile(r"[.!?][\"'”’)\]]*(?=\s+[\"'“‘(\[]?[A-Z0-9])")


@dataclass
class _Block:
    start: int
    end: int
    kind: Kind


def _trim(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def _lines(text: str, start: int, end: int) -> list[tuple[int, int]]:
    """The non-blank lines of `text[start:end]`, trimmed, as offsets."""
    lines, position = [], start
    while position < end:
        stop = text.find("\n", position, end)
        stop = end if stop < 0 else stop
        a, b = _trim(text, position, stop)
        if a < b:
            lines.append((a, b))
        position = stop + 1
    return lines


def _is_pdf(extraction: str) -> bool:
    return extraction.startswith("pypdf")


_CLOSES = re.compile(r"[.!?:][\"'”’)\]]*$")


def _pdf_paragraphs(text: str, lines: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """A PDF's hard-wrapped lines joined into paragraphs. Lines are compared with their neighbours, not the whole
    text, since a two-column page's lines are half the width of a full-page line. A paragraph ends after a line
    that closes a sentence and is clearly shorter than the lines around it, or after a short line, such as a
    heading, followed by a much longer line that starts with a capital; never after a hyphen or a comma."""
    paragraphs, first = [], lines[0][0] if lines else 0
    for index, (a, b) in enumerate(lines):
        if index == len(lines) - 1:
            paragraphs.append((first, b))
            break
        line = text[a:b]
        following = lines[index + 1]
        width = max(b - a, following[1] - following[0],
                    lines[index - 1][1] - lines[index - 1][0] if index else 0)
        ends = (not line.endswith(("-", ",", ";"))
                and ((_CLOSES.search(line) and b - a < 0.8 * width)
                     or (b - a < 0.5 * (following[1] - following[0]) and text[following[0]].isupper())))
        if ends:
            paragraphs.append((first, b))
            first = following[0]
    return paragraphs


def _kind(text: str, start: int) -> Kind:
    if text.startswith("|", start):
        return "table"
    return "list" if _LIST_ITEM.match(text, start) else "text"


def _blocks(text: str, extraction: str, page_starts: Sequence[int] = ()) -> list[_Block]:
    """The text's structural blocks, in order, each trimmed of surrounding whitespace. A page start also ends a
    section, so no passage spans two pages."""
    sections, position = [], 0
    for match in _BLANK.finditer(text):
        sections.append((position, match.start()))
        position = match.end()
    sections.append((position, len(text)))
    for page_start in page_starts:
        sections = [piece for start, end in sections
                    for piece in (((start, page_start), (page_start, end)) if start < page_start < end
                                  else ((start, end),))]
    section_lines = [lines for start, end in sections if (lines := _lines(text, start, end))]
    pdf = _is_pdf(extraction)
    # A text with no blank lines, or few for its lines, has one paragraph a line (trafilatura, JSON); in one
    # with blank lines between paragraphs (Markdown), the lines of a section are one paragraph.
    per_line = not pdf and (len(section_lines) <= 1 or sum(map(len, section_lines)) > 4 * len(section_lines))
    blocks: list[_Block] = []
    for lines in section_lines:
        # Table rows and list items are their own lines in every format.
        runs: list[list[tuple[int, int]]] = []
        for line in lines:
            kind = _kind(text, line[0])
            if runs and kind == _kind(text, runs[-1][0][0]) and kind != "list":
                runs[-1].append(line)
            else:
                runs.append([line])
        for run in runs:
            kind = _kind(text, run[0][0])
            if kind == "table":
                blocks.append(_Block(run[0][0], run[-1][1], "table"))
            elif kind == "list" or per_line:
                blocks.extend(_Block(a, b, kind) for a, b in run)
            elif pdf:
                blocks.extend(_Block(a, b, "text") for a, b in _pdf_paragraphs(text, run))
            else:
                blocks.append(_Block(run[0][0], run[-1][1], "text"))
    return blocks


# --- Passages ------------------------------------------------------------------------------------------------

def _sentence_cuts(text: str, start: int, end: int) -> list[int]:
    """Offsets inside `text[start:end]` just after a sentence's closing punctuation."""
    cuts = []
    for match in _SENTENCE_END.finditer(text, start, end):
        word = re.search(r"([\w.]+)[.!?]?$", text[max(start, match.start() - 12):match.start() + 1])
        before = (word.group(1) if word else "").lower().rstrip(".")
        if text[match.start()] == "." and (before in _ABBREVIATIONS or (len(before) == 1 and before.isalpha())):
            continue  # "et al.", "e.g.", "J. Smith"
        cuts.append(match.end())
    return cuts


def _cut_long(text: str, start: int, end: int, limit: int) -> list[tuple[int, int]]:
    """`text[start:end]` in pieces of at most `limit` characters: whole sentences where they fit, and otherwise
    cut at the last space before the limit, or at the limit itself."""
    pieces: list[tuple[int, int]] = []
    cuts = _sentence_cuts(text, start, end)
    position = start
    while end - position > limit:
        fitting = [cut for cut in cuts if position < cut <= position + limit]
        if fitting:
            stop = fitting[-1]
        else:
            space = text.rfind(" ", position + limit // 2, position + limit)
            stop = space if space > position else position + limit
        a, b = _trim(text, position, stop)
        if a < b:
            pieces.append((a, b))
        position = stop
    a, b = _trim(text, position, end)
    if a < b:
        pieces.append((a, b))
    return pieces


def _table_parts(text: str, block: _Block) -> list[tuple[int, int, tuple[int, int] | None]]:
    """A table in parts of at most TABLE_CHARS, split between rows; each later part names the header row."""
    rows = _lines(text, block.start, block.end)
    header = rows[0]
    parts: list[tuple[int, int, tuple[int, int] | None]] = []
    first = rows[0][0]
    for index, (a, b) in enumerate(rows):
        if b - first > TABLE_CHARS and a > first:
            previous_end = rows[index - 1][1]
            parts.append((first, previous_end, header if parts else None))
            first = a
    parts.append((first, rows[-1][1], header if parts else None))
    # A single row longer than a table passage is cut like text, keeping the header reference.
    return [(x, y, h) for start, end, h in parts for x, y in _cut_long(text, start, end, TABLE_CHARS)]


def split(text: str, extraction: str = "", page_starts: Sequence[int] = ()) -> list[Passage]:
    """The passages of `text`, extracted by `extraction` (such as `trafilatura` or `pypdf`), whose pages start
    at `page_starts` when known."""
    spans: list[tuple[int, int, Kind, tuple[int, int] | None]] = []
    starts = sorted(page_starts)

    def same_page(start: int, end: int) -> bool:
        return not any(start < page_start < end for page_start in starts)

    # Consecutive list items form one list block, up to TARGET_CHARS.
    grouped: list[_Block] = []
    for block in _blocks(text, extraction, starts):
        previous = grouped[-1] if grouped else None
        if (previous and block.kind == "list" and previous.kind == "list"
                and block.end - previous.start <= TARGET_CHARS and same_page(previous.start, block.end)):
            previous.end = block.end
        else:
            grouped.append(_Block(block.start, block.end, block.kind))
    # Short blocks wait, packed together up to TARGET_CHARS, and join the next long block.
    pending: _Block | None = None

    def flush() -> None:
        nonlocal pending
        if pending is not None:
            spans.append((pending.start, pending.end, pending.kind, None))
            pending = None

    for block in grouped:
        if block.kind == "table":
            flush()
            spans.extend((a, b, "table", header) for a, b, header in _table_parts(text, block))
            continue
        if pending is not None and not same_page(pending.start, block.end):
            flush()
        if block.end - block.start < MIN_CHARS:
            if pending is not None and block.end - pending.start <= TARGET_CHARS:
                pending = _Block(pending.start, block.end, "list" if pending.kind == block.kind == "list" else "text")
            else:
                flush()
                pending = block
            continue
        if pending is not None:
            # A heading or other short block takes the kind of the block it introduces.
            block = _Block(pending.start, block.end, block.kind)
            pending = None
        spans.extend((a, b, block.kind, None) for a, b in _cut_long(text, block.start, block.end, TARGET_CHARS))
    flush()
    return [Passage(ordinal, a, b, kind,
                    bisect.bisect_right(starts, a) if starts else None, header)
            for ordinal, (a, b, kind, header) in enumerate(spans)]

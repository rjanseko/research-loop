"""The passage splitter: its invariants under generated text, and its rules on hand-written examples of each
format research tools return (docs/passage-evidence-plan.md, build step 1)."""
from __future__ import annotations

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from research_loop.passages import (
    MARK_CLOSE,
    MARK_OPEN,
    MIN_CHARS,
    TABLE_CHARS,
    TARGET_CHARS,
    Passage,
    display,
    passage_id,
    split,
    text_sha256,
)

EXTRACTIONS = ("trafilatura", "pypdf", "pypdf-first-pages", "exa-contents", "firecrawl-markdown", "europepmc-xml",
               "json", "")


def _broken(text: str, passages: list[Passage]) -> list[str]:
    """The invariants `passages` break, checked without the splitter's own helpers."""
    problems, position = [], 0
    for index, passage in enumerate(passages):
        if passage.ordinal != index:
            problems.append(f"ordinal {passage.ordinal} at {index}")
        if not position <= passage.start < passage.end <= len(text):
            problems.append(f"{passage.start}-{passage.end} out of order or bounds")
            continue
        body = text[passage.start:passage.end]
        if body != body.strip():
            problems.append(f"{index} is not trimmed")
        limit = TABLE_CHARS if passage.kind == "table" else TARGET_CHARS + MIN_CHARS
        if len(body) > limit:
            problems.append(f"{index} is {len(body)} characters")
        position = passage.end
    inside = [False] * len(text)
    for passage in passages:
        for i in range(passage.start, passage.end):
            inside[i] = True
    if any(not inside[i] and not char.isspace() for i, char in enumerate(text)):
        problems.append("a non-whitespace character is in no passage")
    return problems


pieces = st.one_of(
    st.text(alphabet=st.characters(codec="utf-8", exclude_categories=("Cs",)), max_size=60),
    st.sampled_from(["\n", "\n\n", " ", ". ", "et al. ", "e.g. ", "| a | b |\n", "- item\n", "1. Heading\n",
                     "algo-\n", MARK_OPEN + "r7.3" + MARK_CLOSE, "Fig. 3 shows ", "x" * 900, "word " * 60]))


@settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.lists(pieces, max_size=40).map("".join), st.sampled_from(EXTRACTIONS))
def test_every_character_is_in_exactly_one_passage_in_order(text: str, extraction: str) -> None:
    passages = split(text, extraction)
    assert _broken(text, passages) == []
    assert split(text, extraction) == passages  # deterministic


@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.lists(pieces, max_size=30).map("".join), st.lists(st.integers(0, 2000), max_size=4))
def test_no_passage_crosses_a_page_start(text: str, starts: list[int]) -> None:
    starts = sorted({0, *(start for start in starts if start < len(text))})
    passages = split(text, "pypdf", starts)
    assert _broken(text, passages) == []
    for passage in passages:
        assert not any(passage.start < start < passage.end for start in starts)
        assert starts[passage.page - 1] <= passage.start


def _texts(text: str, passages: list[Passage]) -> list[str]:
    return [text[p.start:p.end] for p in passages]


def test_a_pdfs_wrapped_lines_join_into_paragraphs_across_column_widths() -> None:
    text = ("1. Introduction\n"
            "In this perspective article, we review the \n"
            "current state of data-driven materials \n"
            "science. Data-driven invokes asso-\n"
            "ciations with big data and open data.\n"
            "Materials science is a well established discipline that combines chemistry, physics, and \n"
            "engineering research, and its scientists dream of designing new materials from scratch.\n")
    passages = split(text, "pypdf")
    # The heading joins its paragraph; a hyphen, a narrow column, and a sentence ending mid-line do not end one;
    # a short line that closes a sentence does.
    assert _texts(text, passages) == [
        text[:text.index("Materials science")].strip(), text[text.index("Materials science"):].strip()]
    assert _broken(text, passages) == []


def test_trafilatura_lines_are_paragraphs_and_a_heading_joins_the_next() -> None:
    first = "The Materials Project holds computed properties of more than 150,000 inorganic compounds. " * 2
    second = "OQMD holds DFT formation energies for about a million materials, computed the same way. " * 2
    text = f"Databases\n{first.strip()}\n{second.strip()}"
    passages = split(text, "trafilatura")
    assert _texts(text, passages) == [f"Databases\n{first.strip()}", second.strip()]
    assert [p.kind for p in passages] == ["text", "text"]


def test_markdown_paragraphs_keep_their_wrapped_lines_together() -> None:
    paragraph = "A paragraph that a reader\nwrapped over lines, " * 3 + "and ends here."
    text = f"{paragraph}\n\n{paragraph}"
    assert _texts(text, split(text, "firecrawl-markdown")) == [paragraph, paragraph]


def test_short_lines_pack_together_up_to_the_target() -> None:
    lines = [f'  "field_{n}": "value {n}",' for n in range(80)]
    text = "\n".join(lines)
    passages = split(text, "json")
    assert len(passages) < 10 and all(p.end - p.start <= TARGET_CHARS for p in passages)
    assert _broken(text, passages) == []


def test_a_long_table_is_split_between_rows_and_later_parts_show_its_header() -> None:
    header = "| Database | Entries | Description |"
    rows = [f"| db{n} | {n * 1000} | {'computed properties of materials ' * 2}|" for n in range(60)]
    text = "Table 1\n" + "\n".join([header, *rows]) + "\nAfter the table."
    passages = split(text, "trafilatura")
    tables = [p for p in passages if p.kind == "table"]
    assert len(tables) > 1 and all(p.end - p.start <= TABLE_CHARS for p in tables)
    assert tables[0].header is None and all(text[p.header[0]:p.header[1]] == header for p in tables[1:])
    assert display(text, tables[1]).startswith(header + "\n| db")
    # Every row is in one part, and none is cut.
    assert all(text[p.start:p.end].startswith("| ") and text[p.start:p.end].endswith("|") for p in tables)


def test_a_text_without_line_breaks_is_cut_at_sentences_not_abbreviations() -> None:
    sentence = ("Wang et al. showed that inverse design, e.g. with GANs as in Fig. 3, finds candidates J. Smith "
                "had missed in the U.S. dataset. ")
    text = sentence * 12
    passages = split(text, "europepmc-xml")
    assert len(passages) > 1 and all(p.end - p.start <= TARGET_CHARS for p in passages)
    for passage in passages:
        body = text[passage.start:passage.end]
        assert body.startswith("Wang et al.") and body.endswith("dataset."), body[:40]


def test_a_sentence_longer_than_the_target_is_cut_at_a_space() -> None:
    text = "word " * 400
    passages = split(text.strip(), "")
    assert all(p.end - p.start <= TARGET_CHARS for p in passages)
    assert all(text[p.start:p.end].endswith("word") for p in passages)


def test_markers_a_page_prints_cannot_pose_as_markers() -> None:
    text = f"A page that prints {MARK_OPEN}r7.3{MARK_CLOSE} in its own text, hoping to be cited as something else."
    (passage,) = split(text, "trafilatura")
    shown = display(text, passage)
    assert MARK_OPEN not in shown and MARK_CLOSE not in shown and "[r7.3]" in shown


def test_passage_ids_depend_only_on_the_text_and_the_offsets() -> None:
    digest = text_sha256("some text")
    assert passage_id(digest, 0, 4) == passage_id(digest, 0, 4)
    assert len({passage_id(digest, 0, 4), passage_id(digest, 0, 5), passage_id(text_sha256("other"), 0, 4)}) == 3


def test_a_passage_knows_its_page_when_page_starts_are_given() -> None:
    page = "A full paragraph of text that fills most of a line in the document, and says one thing.\n"
    # A paragraph can run on to the next page, and pypdf's pages are joined with a blank line or not at all.
    text = page * 3 + page * 3
    starts = [0, len(page) * 3]
    passages = split(text, "pypdf", starts)
    assert {p.page for p in passages if p.start < starts[1]} == {1}
    assert {p.page for p in passages if p.start >= starts[1]} == {2}
    assert all(p.page is None for p in split(text, "pypdf"))

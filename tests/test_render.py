"""render.py: a finished run as LaTeX and PDF, Markdown, HTML, BibTeX, and JSON."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import UTC, datetime

import pytest

from research_loop import render
from research_loop.ledger import EvidenceLedger
from research_loop.policy import get_policy
from research_loop.render import (
    ReportDocument,
    render_bibtex,
    render_html,
    render_latex,
    render_markdown,
)
from research_loop.repository import InMemoryResearchRepository
from research_loop.schemas import (
    Claim,
    ClaimCheck,
    Contradiction,
    Evidence,
    FinalReport,
    Gap,
    ReportClaim,
    ResearchPlan,
    ResearchQuestion,
    ResearchResult,
    SourceRef,
    VerificationReport,
)
from research_loop.synthetic import SyntheticResearchLoop

_WHEN = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
_PAPER = SourceRef(url="https://arxiv.org/abs/2310.06770", arxiv_id="2310.06770", source_type="paper",
                   title="SWE-bench: Can Language Models Resolve Real-World GitHub Issues?",
                   publication_status="preprint", published_at="2023-10-10")
_JOURNAL = SourceRef(url="https://doi.org/10.1000/xyz_1%20a", doi="10.1000/xyz_1", title="Benchmarks & their discontents",
                     publication_status="journal", published_at="2025")
_VENDOR = SourceRef(url="https://vendor.example/blog/launch?ref=a&b=%7E#scores", title="Model launch post",
                    source_type="official", publication_status="vendor_technical_report")
_FILE = SourceRef(attachment_id="att-1", locator="page 3", title="figures.xlsx", source_type="attachment")


def _document() -> ReportDocument:
    plan = ResearchPlan(objective="Is SWE-bench Verified trustworthy? 50% of $X & #1_rank", questions=[
        ResearchQuestion(id="q1", question="How was it built?", priority=5, requires_primary_sources=True),
        ResearchQuestion(id="q2", question="Do vendor scores hold up?", expected_difficulty="high"),
    ])
    ledger = EvidenceLedger()
    ledger.add(ResearchResult(question_id="q1", question="How was it built?", conclusion="From GitHub issues.",
                              confidence=0.9, claims=[
        Claim(id="c1", statement="Tasks pair issues with their fixing PRs.", confidence=0.9, evidence=[
            Evidence(source=_PAPER, excerpt="Pairs issues and PRs.", quote="Each task pairs an issue",
                     confidence=0.9, quote_check="verified", source_check="observed"),
            Evidence(source=_JOURNAL, excerpt="Discusses benchmark validity ≥ 2024 — α and Ω.", confidence=0.7),
        ]),
    ]))
    ledger.add(ResearchResult(question_id="q2", question="Do vendor scores hold up?", conclusion="Not clearly.",
                              confidence=0.55, unresolved_questions=["No matched independent run"], claims=[
        Claim(id="c1", statement="Vendor scores run ahead.", confidence=0.7, evidence=[
            Evidence(source=_VENDOR, excerpt="Vendor reports more.", quote="best model we have tested",
                     confidence=0.7, quote_check="not_found", source_check="not_found"),
        ]),
        Claim(id="c2", statement="The attachment disagrees.", confidence=0.6, evidence=[
            Evidence(source=_FILE, excerpt="Lower score in the table.", confidence=0.6, supports=False),
        ]),
    ], contradictions=[Contradiction(description="Vendor and file disagree", claim_ids=["c1", "c2"])]))
    # A second pass on q2 renumbers its c1 to q2/c1~2.
    ledger.add(ResearchResult(question_id="q2", question="Do vendor scores hold up?", conclusion="Still unclear.",
                              confidence=0.6, claims=[
        Claim(id="c1", statement="An independent run scores lower.", confidence=0.6, evidence=[
            Evidence(source=_PAPER, excerpt="Independent run.", confidence=0.6),
        ]),
    ]))
    answer = (
        "# Summary\n\nSWE-bench pairs issues with fixes [s1]. Vendor claims **exceed** independent runs "
        "[s3, s1] — see [the paper](https://arxiv.org/abs/2310.06770) and a missing [s99].\n\n"
        "- Costs 100% & rising_fast ~ 3 ≥ 2 ≈ 2.1 “quoted” ✓ 中\n- Nested:\n  1. one\n  2. two `a_b{c}`\n\n"
        "| Model | Score |\n|---|---|\n| A & B | 50% [s2] |\n\n```\nraw \\end{verbatim} {code}\n```\n\n"
        "<script>alert(1)</script>\n"
    )
    report = FinalReport(answer=answer, caveats=["Vendor numbers are unverified [s3]."], claims=[
        ReportClaim(statement="Tasks pair issues with their fixing PRs.", claim_ids=["q1/c1"]),
        ReportClaim(statement="Vendor scores run ahead.", claim_ids=["q2/c1"]),
        ReportClaim(statement="An unchecked statement.", claim_ids=["q2/c1~2"]),
    ])
    verification = VerificationReport(needs_research=True, checks=[
        ClaimCheck(statement="Tasks pair issues with their fixing PRs.", claim_ids=["q1/c1"], supported=True,
                   severity="none", explanation="Quote verified."),
        ClaimCheck(statement="Vendor scores run ahead.", claim_ids=["q2/c1"], supported=False, severity="major",
                   explanation="Quote not found."),
    ], followups=[Gap(question_id="q2", reason="missing_evidence", severity=4, followup="Find an independent run.")])
    return ReportDocument(objective=plan.objective, report=report, verification=verification, ledger=ledger,
                          plan=plan, review_reasons=["1 of 2 verifier checks is unsupported"], job_id="job-1",
                          cost_usd=0.4212, generated_at=_WHEN)


def test_sources_are_grouped_and_marked_with_how_the_report_uses_them() -> None:
    doc = _document()
    by_id = {entry.id: entry for entry in doc.sources}
    assert [(e.id, e.group) for e in doc.sources] == [
        ("s1", "scholarly"), ("s2", "scholarly"), ("s3", "web"), ("s4", "attachment")]
    assert by_id["s1"].cited and by_id["s3"].cited and by_id["s2"].cited  # s2 only in the answer's table
    assert by_id["s1"].claim_ids == ("q1/c1", "q2/c1~2")
    assert by_id["s3"].not_observed and not by_id["s1"].not_observed
    assert not by_id["s4"].cited and not by_id["s4"].behind_report  # contradicting evidence is not support
    assert by_id["s4"].usage == "Gathered, not cited"


def test_markdown_holds_the_report_every_source_and_the_ledger() -> None:
    text = render_markdown(_document())
    assert text.startswith("# Is SWE-bench Verified trustworthy?\n")
    assert "### Summary" in text  # the report's own heading sits below the document's
    for heading in ("## Findings", "## Key statements", "## Caveats", "### Scholarly literature",
                    "### Web and other sources", "### Attachments", "## Appendix A. Research plan",
                    "## Appendix B. Evidence ledger", "#### Research pass 2", "## Appendix C. Verification"):
        assert heading in text
    assert "**[s4]**" in text and "doi:[10.1000/xyz_1](https://doi.org/10.1000/xyz_1)" in text
    assert "<https://arxiv.org/abs/2310.06770>" not in text  # the arXiv link already names it
    assert "_(quote not found, source not observed)_" in text and "_(contradicts)_" in text
    assert "unsupported, major" in text and "not checked" in text
    assert "> **Review:** 1 of 2 verifier checks is unsupported" in text


def test_html_links_citations_and_shows_model_html_as_text() -> None:
    page = render_html(_document())
    assert '<li id="s1"><strong>[s1]</strong>' in page
    assert '[<a href="#s3">s3</a>, <a href="#s1">s1</a>]' in page
    assert "[s99]" in page and 'href="#s99"' not in page  # an ID the ledger lacks stays plain text
    assert "<script>" not in page and "&lt;script&gt;" in page


def test_latex_escapes_text_and_links_citations_claims_and_sources() -> None:
    tex = render_latex(_document())
    assert tex.startswith(r"\documentclass") and tex.rstrip().endswith(r"\end{document}")
    assert r"50\% of \$X \& \#1\_rank" in tex
    assert r"{[}\hyperref[src:s3]{s3}, \hyperref[src:s1]{s1}{]}" in tex
    assert "{[}s99{]}" in tex and "src:s99" not in tex
    for source_id in ("s1", "s2", "s3", "s4"):
        assert rf"\label{{src:{source_id}}}" in tex
    # Claim IDs with '~' become labels LaTeX accepts, and references use the same label.
    assert r"\label{claim:q2/c1+7e+2}" in tex and r"\hyperref[claim:q2/c1+7e+2]" in tex
    assert r"\href{https://vendor.example/blog/launch?ref=a&b=\%7E\#scores}" in tex
    assert r"\DeclareUnicodeCharacter{2265}{\ensuremath{\geq}}" in tex
    assert r"\DeclareUnicodeCharacter{03B1}{\ensuremath{\alpha}}" in tex
    assert r"\DeclareUnicodeCharacter{4E2D}{\textsf{\footnotesize[U+4E2D]}}" in tex
    # XeLaTeX and LuaLaTeX print characters from the font; math symbols and Greek are spelled out for them.
    assert r"\newunicodechar{≥}{\ensuremath{\geq}}" in tex and r"\newunicodechar{中}" not in tex
    assert r"\begin{longtable}" in tex and r"\end {verbatim}" in tex
    assert r"\textless{}script\textgreater{}" in tex
    assert tex.count(r"\begin{") == tex.count(r"\end{")


def test_latex_braces_balance_outside_verbatim() -> None:
    tex = render_latex(_document())
    outside = re.sub(r"\\begin\{verbatim\}.*?\\end\{verbatim\}", "", tex, flags=re.DOTALL)
    unescaped = re.sub(r"\\[{}]", "", outside.replace("\\\\", ""))
    assert unescaped.count("{") == unescaped.count("}")


def test_bibtex_keys_every_source_by_its_id() -> None:
    bib = render_bibtex(_document())
    assert re.findall(r"@misc\{(s\d+),", bib) == ["s1", "s2", "s3", "s4"]
    assert "eprint = {2310.06770}" in bib and "archiveprefix = {arXiv}" in bib
    assert "year = {2023}" in bib and "doi = {10.1000/xyz_1}" in bib
    assert r"title = {{Benchmarks \& their discontents}}" in bib
    assert "note = {Attachment att-1, page 3}" in bib and "keywords = {attachment}" in bib


def test_a_record_renders_the_same_document_again() -> None:
    doc = _document()
    record = json.loads(json.dumps(doc.to_record()))
    again = ReportDocument.from_record(record)
    assert render_latex(again) == render_latex(doc)
    assert render_markdown(again) == render_markdown(doc)


def test_a_record_without_review_reasons_gets_them_recomputed() -> None:
    record = _document().to_record()
    del record["review_reasons"], record["objective"]
    record["question"] = "Asked as a question"
    doc = ReportDocument.from_record(record)
    assert doc.objective == "Asked as a question"
    assert "the verifier still asked for more research" in doc.review_reasons


def test_a_record_needs_the_report_verification_and_ledger() -> None:
    with pytest.raises(ValueError, match="ledger"):
        ReportDocument.from_record({"report": {"answer": "x"}, "verification": {}})


async def test_a_synthetic_run_writes_every_format_but_pdf(tmp_path) -> None:
    outcome = await SyntheticResearchLoop(get_policy("synthetic"), repository=InMemoryResearchRepository()).run("Synthetic objective")
    doc = ReportDocument.from_outcome(outcome)
    written = render.write_report(doc, tmp_path, ["tex", "md", "html", "bib", "json"], stem="run")
    assert sorted(written) == ["bib", "html", "json", "md", "tex"]
    assert written["md"] == tmp_path / "run.md"
    assert "Synthetic source" in written["md"].read_text(encoding="utf-8")
    assert json.loads(written["json"].read_text(encoding="utf-8"))["job_id"] == str(outcome.job_id)


def test_unknown_formats_and_missing_engines_are_refused(tmp_path, monkeypatch) -> None:
    with pytest.raises(ValueError, match="docx"):
        render.write_report(_document(), tmp_path, ["docx"])
    monkeypatch.setattr(render.shutil, "which", lambda name: None)
    with pytest.raises(render.LatexError, match="no LaTeX engine"):
        render.write_report(_document(), tmp_path, ["pdf"])
    assert (tmp_path / "report.tex").exists()  # the source is left to compile elsewhere


def test_cli_renders_a_saved_record(tmp_path, capsys) -> None:
    record = tmp_path / "run.json"
    record.write_text(json.dumps(_document().to_record()), encoding="utf-8")
    render.main([str(record), "-f", "md,bib", "-o", str(tmp_path / "out")])
    out = capsys.readouterr().out
    assert f"md: {tmp_path / 'out' / 'run.md'}" in out and (tmp_path / "out" / "run.bib").exists()


def test_cli_will_not_overwrite_the_record_it_renders(tmp_path) -> None:
    record = tmp_path / "run.json"
    record.write_text(json.dumps(_document().to_record()), encoding="utf-8")
    with pytest.raises(SystemExit) as exit_info:
        render.main([str(record), "-f", "json"])
    assert exit_info.value.code == 2
    render.main([str(record), "-f", "json", "--stem", "copy"])
    before = record.read_text(encoding="utf-8")
    render.main([str(record), "-f", "all"] if render.find_engine() else [str(record), "-f", "md,html,tex,bib"])
    assert record.read_text(encoding="utf-8") == before and (tmp_path / "run.md").exists()
    assert json.loads((tmp_path / "copy.json").read_text(encoding="utf-8"))["objective"] == _document().objective


def test_cli_refuses_a_record_without_a_ledger(tmp_path, capsys) -> None:
    record = tmp_path / "run.json"
    record.write_text(json.dumps({"report": {"answer": "x"}, "verification": {}}), encoding="utf-8")
    with pytest.raises(SystemExit) as exit_info:
        render.main([str(record)])
    assert exit_info.value.code == 2
    assert "rerun it" in capsys.readouterr().err
    job_id = "00000000-0000-0000-0000-000000000001"
    record.write_text(json.dumps({"report": {"answer": "x"}, "verification": {}, "persisted": True,
                                  "job_id": job_id}), encoding="utf-8")
    with pytest.raises(SystemExit):
        render.main([str(record)])
    assert f"research-report --job-id {job_id}" in capsys.readouterr().err


@pytest.mark.skipif(render.find_engine() is None, reason="no LaTeX engine installed")
def test_the_latex_compiles_to_a_pdf(tmp_path) -> None:
    written = render.write_report(_document(), tmp_path, ["pdf"])
    assert written["pdf"].read_bytes().startswith(b"%PDF")



def test_engine_output_that_is_not_utf8_does_not_stop_the_build(tmp_path, monkeypatch) -> None:
    # pdflatex wraps output at a byte width, which can split a UTF-8 character, as in a 72-page report.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    engine = bin_dir / "pdflatex"
    engine.write_text("#!/bin/sh\nprintf 'Overfull \\326\\n'\nfor a; do f=$a; done\n"
                      "printf '%%PDF-1.5\\n' > \"${f%.tex}.pdf\"\n")
    engine.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    tex = tmp_path / "doc.tex"
    tex.write_text("x", encoding="utf-8")
    assert render.compile_pdf(tex, engine="pdflatex").read_bytes().startswith(b"%PDF")


def test_a_numbered_list_split_by_bullets_keeps_its_numbers() -> None:
    to_latex = render._MarkdownToLatex(lambda ids: "")
    tex = to_latex.block("3. Taspen\n- Type: DB\n\n4. ASABRI\n- Type: DB\n\n1. Back to one\n\n"
                         "- outer\n\n  2. nested second")
    assert tex.count(r"\setcounter{enumi}{2}") == 1 and tex.count(r"\setcounter{enumi}{3}") == 1
    assert r"\setcounter{enumi}{0}" not in tex
    assert tex.count(r"\setcounter{enumi}{1}") == 1  # the nested list is the first enumerate in its itemize


def test_a_long_objective_is_cut_on_the_title_page_and_printed_whole_in_the_plan() -> None:
    from dataclasses import replace as replace_field

    tail = "BENCHMARK CONSTRAINT: do not open https://example.org/blocked"
    doc = replace_field(_document(), objective="Compare **pension** schemes. " + "Detail. " * 80 + "\n\n" + tail)
    tex = render_latex(doc)
    title = tex[tex.index(r"\begin{titlepage}"):tex.index(r"\end{titlepage}")]
    assert r"\textbf{pension}" in title and "**" not in title
    assert tail.split(":")[0] not in title and "The full objective is in the research plan" in title
    plan = tex[tex.index(r"\section{Research plan}"):]
    assert "BENCHMARK CONSTRAINT" in plan


def test_thai_and_cjk_runs_get_a_script_font_and_leave_punctuation_in_the_main_one() -> None:
    from dataclasses import replace as replace_field

    doc = replace_field(_document(), report=_document().report.model_copy(
        update={"answer": "Law “กฎกระทรวง และจำนวน” พ.ศ. ๒๕๕๘ and 养老金.\n\n    กฎ in code"}))
    tex = render_latex(doc)
    assert r"“\rlscript{thai}{กฎกระทรวง และจำนวน}”" in tex
    assert r"\rlscript{thai}{พ}.\rlscript{thai}{ศ}." in tex and r"\rlscript{cjk}{养老金}." in tex
    assert "\\begin{verbatim}\nกฎ in code" in tex  # verbatim text is left as it is
    assert r"\IfFontExistsTF{Noto Serif Thai}" in tex and render.needs_unicode_engine(tex)
    assert not render.needs_unicode_engine("\\newcommand{\\rlscript}[2]{#2}\n\\begin{document}plain")


def test_a_document_with_thai_goes_to_xelatex_when_no_engine_is_named(tmp_path, monkeypatch) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("pdflatex", "xelatex"):
        engine = bin_dir / name
        engine.write_text(f"#!/bin/sh\nfor a; do f=$a; done\nprintf '%%PDF {name}' > \"${{f%.tex}}.pdf\"\n")
        engine.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    thai, latin = tmp_path / "thai.tex", tmp_path / "latin.tex"
    thai.write_text("\\begin{document}\\rlscript{thai}{กฎ}", encoding="utf-8")
    latin.write_text("\\begin{document}plain", encoding="utf-8")
    assert render.compile_pdf(thai).read_text() == "%PDF xelatex"
    assert render.compile_pdf(latin).read_text() == "%PDF pdflatex"
    assert render.compile_pdf(thai, engine="pdflatex").read_text() == "%PDF pdflatex"


@pytest.mark.skipif(shutil.which("xelatex") is None or shutil.which("fc-list") is None
                    or "Noto Serif Thai" not in subprocess.run(["fc-list", ":lang=th", "family"], capture_output=True,
                                                               text=True, check=False).stdout,
                    reason="needs xelatex and Noto Serif Thai")
def test_thai_prints_in_its_font_under_xelatex(tmp_path) -> None:
    from dataclasses import replace as replace_field

    doc = replace_field(_document(), report=_document().report.model_copy(
        update={"answer": "กฎกระทรวง พ.ศ. ๒๕๕๘"}))
    pdf = render.write_report(doc, tmp_path, ["pdf"])["pdf"]
    fonts = subprocess.run(["pdffonts", str(pdf)], capture_output=True, text=True, check=False).stdout
    assert "NotoSerifThai" in fonts


def test_a_percent_encoded_url_can_break_between_its_escapes() -> None:
    assert render._url_text("https://x.th/%E0%B8%81") .endswith(r"\allowbreak{}\%E0\allowbreak{}\%B8\allowbreak{}\%81")


def test_bullets_written_as_table_rows_become_a_table() -> None:
    markdown = ("TABLE 1: Formulas\n(Columns: Country | Plan | Accrual)\n\n- Indonesia | JP | 1% [s1]\n"
                "- Malaysia | KWAP | 2% (1/600 a month)\n\nNote:\n- Indonesia | severance\n- a | b | c")
    table = render.pipe_rows_as_tables(markdown)
    assert "| Country | Plan | Accrual |\n|---|---|---|\n| Indonesia | JP | 1% [s1] |" in table
    assert "(Columns:" not in table
    assert "- Indonesia | severance\n- a | b | c" in table  # two cells, then a run of one: left as bullets
    tex = render._MarkdownToLatex(lambda ids: "").block(table)
    assert r"\begin{longtable}" in tex and r"\textbf{Country}" in tex


def test_pipe_rows_without_a_columns_line_get_an_empty_header() -> None:
    table = render.pipe_rows_as_tables("- a | b | c\n- d | e | f")
    assert table.startswith("|  |  |  |\n|---|---|---|\n| a | b | c |")


def test_table_columns_fit_their_longest_word_and_wide_tables_use_smaller_type() -> None:
    cells = [["Country", "Salary base"], ["Philippines", "Average adjusted earnings over the whole career " * 3]]
    shares = render._column_shares(cells, 2, "")
    assert abs(sum(shares) - 1) < 1e-9 and shares[0] >= 12 / 82 and shares[1] > shares[0]
    wide = "| " + " | ".join(f"c{i}" for i in range(6)) + " |\n|" + "---|" * 6 + "\n| " + " | ".join("x/y" for _ in range(6)) + " |"
    tex = render._MarkdownToLatex(lambda ids: "").block(wide)
    assert tex.startswith(r"{\footnotesize") and r"x/\allowbreak{}y" in tex


def test_the_title_page_has_a_title_a_scorecard_and_the_disputed_statements_follow_the_findings() -> None:
    from dataclasses import replace as replace_field

    doc = replace_field(_document(), objective="I need a detailed report on pension gaps in Asia, including: Indonesia.")
    tex = render_latex(doc)
    page = tex[tex.index(r"\begin{titlepage}"):tex.index(r"\end{titlepage}")]
    assert r"\huge\bfseries\raggedright Pension gaps in Asia" in page
    checks = doc.verification.checks
    assert f"{sum(c.supported for c in checks)}/{len(checks)}" in page and "rated major" in page
    assert r"\tableofcontents" in tex and r"\newcommand{\rlshorttitle}{Pension gaps in Asia}" in tex
    if any(c.severity == "major" for c in checks):
        assert tex.index(r"\section{Findings}") < tex.index(r"\section{Statements the verifier disputed}") \
            < tex.index(r"\section{Key statements}")


def test_plain_text_headings_become_markdown_headings() -> None:
    text = ("Scope note.\n\n=====\nSECTION 1 - PROFILES\n=====\n\nINDONESIA\n1. JP\n- Type: DB\n\n"
            "A sentence with CAPS inside.\nNOT A HEADING, it follows text")
    tidy = render.plain_headings_as_markdown(text)
    assert "# SECTION 1 - PROFILES" in tidy and "## INDONESIA\n\n1. JP" in tidy and "=====" not in tidy
    assert "## NOT A HEADING" not in tidy


def test_bullets_right_after_a_numbered_item_nest_under_it() -> None:
    text = "1. Jaminan Pensiun (JP)\n- Type: DB\n- Coverage: all\n\n2. JHT\n- Type: DC\n\n- a later list\n10. Ten\n- detail"
    tidy = render.bullets_under_numbered_items(text)
    assert "1. **Jaminan Pensiun (JP)**\n   - Type: DB\n   - Coverage: all\n\n2. **JHT**\n   - Type: DC" in tidy
    assert "\n\n- a later list" in tidy and "10. **Ten**\n    - detail" in tidy
    tex = render._MarkdownToLatex(lambda ids: "").block(tidy)
    assert tex.count(r"\begin{enumerate}") == 2 and tex.index(r"\begin{itemize}") < tex.index(r"\item \textbf{JHT}")


def test_nested_bullets_under_a_numbered_item_keep_their_levels() -> None:
    text = "=== SECTION 1. PROFILES ===\n\n1. INDONESIA\n- JHT: MDC.\n  - Coverage: employees.\n- JP: MDB.\n  - Rate: 3%.\n\n2. MALAYSIA\n- EPF"
    tidy = render.tidy_answer(text)
    assert tidy.startswith("# SECTION 1. PROFILES")
    assert "1. **INDONESIA**\n   - JHT: MDC.\n     - Coverage: employees.\n   - JP: MDB.\n     - Rate: 3%." in tidy
    assert "2. **MALAYSIA**\n   - EPF" in tidy


def test_a_reports_own_title_and_summary_lead_the_document() -> None:
    from dataclasses import replace as replace_field

    base = _document()
    doc = replace_field(base, report=base.report.model_copy(
        update={"title": "Pension Gaps in Seven Asian Economies", "executive_summary": "Coverage is thin [s1]."}))
    tex = render_latex(doc)
    assert r"\huge\bfseries\raggedright Pension Gaps in Seven Asian Economies" in tex
    assert tex.index(r"\section*{Summary}") < tex.index(r"\section{Findings}") and "Coverage is thin" in tex
    text = render_markdown(doc)
    assert text.startswith("# Pension Gaps in Seven Asian Economies\n") and "## Summary\n\nCoverage is thin [s1]." in text

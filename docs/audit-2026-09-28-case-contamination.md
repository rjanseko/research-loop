# Audit: does any case's content reach a run? (28 September 2026)

The user stopped all paid calls on 28 September 2026 and asked for an audit. The trigger was the claim fix (scout-v13): it was designed from drb2-task8's rubric, and a study was about to measure it on drb2-task8. This audit asks whether any frozen case's content reaches the models that do the research, and which results that affects. It made no model calls.

## What a run receives

A case run (`research scout --case`, `cli.py`) gives its models three things from the case:
- its question (`objective`);
- its blocked addresses;
- its blocked titles.

The case identity (ID, rubric version, hash) is recorded in the run's configuration and never sent to a model.

Only these read the rubric, and all of them run after a run has finished:
- grading (`evals.py`, `diagnose.py`, `quality.py`);
- the study report (`study.py`);
- the expected-member lists of the two development cases in `coverage.py`, which only count what a finished run found.

**Clean.**

## What the models see

The audit compared every frozen case's question and rubric with all model-visible text: the instructions of all four roles, the budget notes, the output schemas, the tool descriptions, and the validator messages. It looked for shared three-word phrases and for shared long or hyphenated terms.

| # | Finding | Since | Severity |
|---|---|---|---|
| 1 | The scouts' output schema gave `open_items` the example "Inorganic Crystal Structure Database (ICSD)". That is one of drb2-task8's expected databases and three of its rubric points (listed, described, correct URL). | scout-v6, 26 September (`fe4dfa0`) | High for drb2-task8 |
| 2 | The planner's example was "the principle, algorithms, advantages, and disadvantages of each approach" and "Exploration-based: disadvantages", which are drb2-task8's structure and one of its category names. | scout-v13, 28 September (`4439313`) | High for drb2-task8 |
| 3 | The scouts' examples asked for "a database's name, what it holds, and its official address", which paraphrases drb2-task8's database fields. They also asked for a category's "principle, typical methods, strengths, and weaknesses". The phrase check cannot catch a paraphrase; this was found by reading. | scout-v13 | Medium for drb2-task8 |
| 4 | The synthesizer is told to "label a vendor's claims about its own products as vendor claims", which matches a point of st07. st07 and the other short cases were written in-house with the first design. | scout-v1 | Low: general source practice, but st07 is not independent of the design |

No text shares distinctive wording with any held-out case (drb2-task82, task59, task78) or with the four newer development cases (task98-plus, task75, task15, task21).

## How development used the cases

The design was improved by reading development-case rubrics, which is what development cases are for:
- listing a set before confirming it (v2 and v3);
- coverage items (v6);
- the claim fix (v13), from the drb2-task8 audit of 28 September.

The consequence is that drb2-task8 and drb2-task68-plus measure how well Scout fits cases it was tuned on, not how it generalizes. Only held-out cases can show that.

The held-out cases are untouched. No run, report grade, or diagnosis exists for any of them, or for the four newer development cases. Runs exist only for st04, st05, st07, drb2-task8, and drb2-task68-plus.

The blocked titles reach scouts and the gap analyzer (since v12), which tells them the title of the article a case was written from. DeepResearch Bench II blocks the same way, by listing the source in the prompt, and our code also refuses the article by address and title. This is by design, and it is recorded here.

## Which results are affected

- **Comparisons within a study stay valid** as comparisons. Every arm ran with the same prompts. This covers the trimming, search-engine, Serper, DeepSeek, and Sol-regrade results of 28 September.
- **Their absolute drb2-task8 scores are suspect**, as are gains on drb2-task8 between versions from v6 on. Report grades show the ICSD points met in 1 of 2 grades before v6 and in 67 to 100% after. The samples are too small to separate this from other changes.
- **No study measured the v13 planner on drb2-task8.** All of them rescouted stored v10 plans. Three planner-only calls on 28 September ($0.04) did use it; they showed the category items but prove nothing about generalization. The planned `fresh-plans-task8` study was dropped before any paid run.

## A second finding: stopped runs left marked as running

When a study was stopped, the runner's `subprocess.run` killed its child command with SIGKILL, so the child could not record its run as cancelled. Two runs stayed marked `running`:
- `6e1d421b`, from the first search study;
- `e994230a`, from the DeepSeek study.

The runner also printed "Interrupted; the run is recorded as cancelled", which described no run of its own. It now sends the child SIGTERM, which the child handles by recording the cancellation, and allows it a minute before killing it (`study.run_child`). The two stale rows can be closed with `research db reconcile`, which needs the user's approval since it writes to the database.

## Fixes (scout-v16)

- **The examples are replaced** with ones from unrelated domains: 'Protein Data Bank (PDB)', "Solar: risks", "a standard's name, what it covers, and where it is published", and a category's "how it works, what it is used for, and what it costs".
- **`tests/test_case_contamination.py`** fails if model-visible text shares a distinctive three-word phrase, or a long or hyphenated term, with any frozen case's question or rubric. It fails on the pre-v16 text with the phrases of findings 1 and 2, and passes on v16. Its allowed phrases are listed with reasons. It cannot catch a paraphrase such as finding 3, so a change to an example still needs reading.
- **The study runner forwards SIGTERM** to its child command, with a test that fails under SIGKILL.

Tests (369, with the Postgres tests), lint, and 200 fuzz runs are clean.

## Pass criteria

The audit passes when:
- the v16 fixes are merged;
- the stale runs are closed;
- the study log and `docs/lessons.md` mark results since v6 as described above;
- any measurement of the claim fix uses held-out cases, with drb2-task8 reported only as a development case.

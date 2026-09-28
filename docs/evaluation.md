# How Scout's quality is measured

This page describes how Scout's reports are evaluated and how a change to Scout is decided on. The results of every study are in [study-log.md](study-log.md), and what the first design learned is in [lessons.md](lessons.md).

A run is judged in three separate ways, and none of them stands in for another. Whether it did its work is its status. Whether its statements trace to evidence the research actually read is decided by code. Whether the report is correct, complete, and useful is decided by model judges and, for anything that decides a change, by a person.

## The study cases

The frozen cases are in `src/research_loop/study_cases.jsonl`. A case is never edited once it has been graded; a correction is a new rubric version.

- **Short cases.** st01 to st05 are a direct fact, a two-hop fact, a figure from one primary source, a false premise (st04), and a table of facts from three papers (st05). They test whether Scout answers precisely and cheaply, and they catch regressions: a drop from full marks is easy to see.
- **Broad cases.** st06 and st07 are broad research questions. st07, whether SWE-bench Verified is still a trustworthy measure, is kept as a contested-topic diagnostic and a bridge to the first design's grades; both are retired as selection cases.
- **Development cases from DeepResearch Bench II.** `drb2-task8` and `drb2-task68-plus` are long expert tasks, used to tune changes. Four more were frozen on 27 September 2026 with `role: development`, so that tuning no longer rests on two cases: `drb2-task98-plus`, `drb2-task75`, `drb2-task15`, and `drb2-task21` (see below).
- **Held-out cases.** `drb2-task82`, `drb2-task59`, and `drb2-task78` are run only to confirm a change before adopting it, never to tune one.

### How the DeepResearch Bench II cases were frozen

Two English, CC BY 4.0 cases from [DeepResearch Bench II](https://github.com/imlrz/DeepResearch-Bench-II)
are now frozen as `drb2-task8` and `drb2-task68-plus` in `study_cases.jsonl`, while st04 and st07 remain diagnostics:

| Official ID | Domain | Expert rubric points | What it tests |
|---|---|---:|---|
| `task8` (idx 16) | Materials inverse design | 52 | Synthesis of three method families, their limits, and source-backed database comparison. |
| `task68+` (idx 46) | Cloud auto-scaling | 54 | Reactive versus proactive methods and evidence for five practical challenges. |

Selection was based on the official English tasks' topic and scope, before any Scout output on these
cases was read. The dataset snapshot was `imlrz/DeepResearch-Bench-II` commit
`b38f360603db9531b102aef8c166cedb8509b6f6` (download SHA-256
`263aaabb8c279fb16cbe7c9499afe82d657a8ab3ccfb07ace084387e367d921a`). Each case now preserves the exact `content.task`, expert rubric, blocked source URLs, license, official ID and index, and dataset revision. The case content digests are `840c63bd8195a546bbd3ee4bee15ba24aae4fee7e34f06b7461651d641ad4367` and `2ef645b6ab3c877e82eaca77463f873fceaebe3d4f274f53dc4552f5a3208500`. The Scout case command sends only the task to research agents; the rubric and blocked expert report remain out of research context. Before fetch version 12 the scholarly tools did not apply the blocked list, and five early drb2-task8 ledgers cite the blocked report by its DOI (study log, 27 September 2026, architectural audit). Blocked sources are now matched by address and by the DOI or arXiv ID a blocked address names, in every tool and in the scouts' evidence. A copy that carries neither is not recognized, and the blocked lists of drb2-task68-plus, drb2-task82, and drb2-task78 name no DOI. Grading requires the saved run to match the frozen case and has a separate enforced pre-dispatch ceiling.

Four more development cases were frozen on 27 September 2026 from the same snapshot, with `scripts/import_drb2.py --role development`, before any Scout run on them. They were chosen from the English, CC BY 4.0 tasks by these criteria:
- each tests a different kind of research, in a domain the other cases do not cover;
- each rubric has 39 to 75 points, near the existing cases' 44 to 62;
- each can be answered from public English sources;
- each blocked list names the expert report's address.

Tasks over 100 points, and tasks that depend on Chinese-language, company-specific, or single-author sources, were passed over.

| Official ID | Domain | Expert rubric points | What it tests |
|---|---|---:|---|
| `task98+` (idx 130) | Sugar substitutes, before March 2019 | 75 | A categorized comparison table filled in field by field, and three mechanisms of harm. |
| `task75` (idx 62) | Salt substitutes, trials to March 2022 | 39 | Finding every trial and extracting its numbers: baseline blood pressure, follow-up, and composition. |
| `task15` (idx 36) | Quantum technology standards, to the end of 2022 | 59 | A complete set across five standards bodies, down to document numbers. |
| `task21` (idx 52) | Generative AI in education, to May 2023 | 55 | A broad qualitative synthesis, anchored by dated facts. |

The blocked lists of task75, task15, and task21 name the expert report's own DOI, so every copy that carries it is blocked. task98+'s list names only a CABI record's DOI, not the paper's own (10.3390/nu11030644). So a copy of that paper outside its listed addresses, such as MDPI, PubMed, and PMC, is not recognized.

These are stress cases: a bounded Scout run may return partial coverage of a rubric drawn from a long
expert report. Report task coverage, unresolved sections, cost, and deadline behavior separately from
overall reader quality. The existing seven-country pension case (`task2+`, 72 points) is an extreme
stress diagnostic if the first two cases show the workflow can finish useful research.

## Three views of a run

Use three separate views of each scheduled run:

1. **Operational outcome.** Record completion, elapsed time, settled cost, and the reason work stopped. Include failed and partial runs in every comparison.
2. **Deterministic integrity.** Report unknown claim IDs, detached citations, source observation, quote matching, unsupported statements, and the share of evidence read beyond snippets. These checks measure traceability and access. They do not certify that a claim follows from a source.
3. **Research quality.** Evaluate the full report and its evidence against the user's question. Keep overall usefulness and factual reliability visible as separate results.

For research quality, use a structured, high-level evaluator with anchored judgments on these dimensions:

| Dimension | Question for the evaluator |
|---|---|
| Direct answer | Does the report give a clear, defensible answer to the user's actual question? |
| Coverage and synthesis | Does it address the important parts and reconcile material agreement or conflict? |
| Evidence use | Are decisive claims tied to appropriate sources, with primary and secondary evidence distinguished? |
| Calibration | Does the strength of the conclusion match the strength and limits of the evidence? |
| Reader utility | Could the intended reader use the answer for the stated decision or understanding? |

Use four anchored levels per dimension: 0 = absent or misleading, 1 = major gaps, 2 = useful with material reservations, 3 = strong. Require a short reason that points to a passage in the report or evidence. On a direct fact case, judge directness, correctness, attribution, and unnecessary effort; coverage and synthesis are more informative for broad cases. Preserve the dimension results; an average would conceal a critical factual error. A blind paired preference between reports on the same case can be the primary comparative judgment, with `A`, `B`, `tie`, or `neither` and a reason. Randomize report order and mask model and arm names.

Audit factual reliability separately. For each report, select the direct answer and a small set of decision-critical claims, including claims that change the recommendation or quantify an effect. Check each against an independently reviewed source packet, recording `supported`, `contradicted`, or `unresolved`, the cited passage, and the correction needed. Also record important omissions. The evaluator should not infer truth solely from source titles or from the report's own ledger. Human review resolves claims that would decide a model change. A report with a fabricated premise, a contradicted decisive claim, or an unresolved citation defect cannot win on style or breadth.

## What code checks

Every run is checked by code, with no model involved; the README's "Evidence and what the checks mean" section describes the marks in full.

- **Quotes.** A quote is `verified` only in the text of the source it cites, `misattributed` when it appears only in another source's text, and `not_found` otherwise (evidence version 6). A copy of the same work counts as that work: a Wayback copy, an ar5iv rendering, a publisher page carrying the DOI.
- **Statement support.** A report statement is `read` when read evidence behind it carries a verified quote, `paraphrase` when it rests only on a scout's summary of a page it read, `shallow` when it rests on a snippet, metadata, or an unverified quote, and `unsupported` when nothing supports it (evidence version 7). A run also counts its short quotes, verified quotes with under a quarter of their claim's words.
- **Answer support.** The answer is `supported` only when every statement is `read`, every research question returned evidence, and every coverage item is addressed with cited claims; otherwise it is `weak` or `unsupported`.

These checks establish traceability, not truth. A verified quote shows the source contains those words, not that they carry the claim. That is what the support audit is for.

## The support audit

`research audit` gives a model from a vendor the run did not use each report statement with the verified quotes behind it, and the record the research tools returned for each quoted source: its address, and for a scholarly record its title, authors, date, and venue. It answers `supported`, `partial` when the statement adds something the quotes do not state, or `unsupported`; a statement with no verified quote is marked `no_quote` without a call. Nothing a model wrote about a source is sent (audit version 2). A study spec with `audit = true` audits every run, and its summary shows the counts.

The first audit of all stored reports found that about one in five quoted statements says more than its quotes, usually because the scout's claim already went beyond its quote, and that claims that a source leaves something out recur and can never be established by a quote. A model's verdicts are not ground truth: check a sample by hand before relying on them.

## The rubric judge

`research grade` scores a report against a case's rubric with one verdict per rubric point (judge version 2, the first design's judge, ported unchanged so that old and new grades compare). It measures coverage of what an expert report contains, not overall quality. It was stable at high effort in the settings study, varying by at most one point in 72 on repeat gradings of one report.

The judge itself matters more than that stability suggests. Regraded by `zai:glm-5.3` with the same prompt, the stored reports agreed on 92.6% of rubric points, but GLM credited 27 points that `gpt-6-sol` did not, and on the DeepResearch Bench II cases the judge alone moved a score by up to 8 points, as much as the run-to-run variation a study tries to see past. In the disputes checked by hand, `gpt-6-sol` was too strict about equivalent wording. So a decision that rests on a few rubric points uses both judges and has their disagreements checked by hand.

### What a rubric score misses

`study_cases.jsonl` contains three short-answer cases, a false-premise case, a fixed table of facts, and two broad research questions. The two broad cases, st06 and st07, are retired as selection cases. The current `research grade` command handles only rubric cases. Its judge decides whether each listed point appears in the report and produces the fraction met. It reads the answer, key statements, caveats, and source names, but no source passages. That makes it a coverage check for predefined facts, not an assessment of whether the conclusion is useful or the cited material supports it.

The saved Scout runs show the gap. `runs/st07-high` completed a broad question with a direct answer, but its checks flagged one unsupported statement. `runs/st07` returned no evidence before the research deadline. A study must count both outcomes. Neither a high rubric score nor a completed status alone establishes that a reader can rely on a report.

## The quality judge and the human reference marks

`research assess` judges a report's overall quality on five dimensions, and specific facts against independently reviewed source summaries in `src/research_loop/quality_packets.jsonl`, which cover st04 and st07. It is calibrated against these human marks; its first paid calibration is in the study log.

These marks calibrate a model evaluator; they are not automatic grades or a selection decision. The source packet in `quality_packets.jsonl` was checked against the official [ILSVRC task list](https://www.image-net.org/challenges/LSVRC/2016/index.php) and [results](https://image-net.org/challenges/LSVRC/2016/results), [OpenAI's Verified introduction](https://openai.com/index/introducing-swe-bench-verified/) and [later reappraisal](https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/), and [METR's maintainer review](https://metr.org/notes/2026-03-10-many-swe-bench-passing-prs-would-not-be-merged-into-main/). Packet passages are paraphrases, not report excerpts. They were reviewed on 25 September 2026; changing one creates a new packet version.

| Saved run | Overall reference (0–3) | Decisive fact marks | Human review note |
|---|---:|---|---|
| `95f9ed4d-f2ec-4269-b8a5-a874890e4018` (st04 Flash high) | 2 | Premise correct; video task correct | Gives the direct answer and official task list. It spends substantial space on alternate video subtasks. Its NUIST Task 3a detail was unverified by its own retrieved quote, although the independent official results packet confirms the value. |
| `c6d1848a-cc4b-4734-ac37-510d2bf6fb6c` (st04 Luna high) | 3 | Premise correct; video task correct | Gives the direct answer quickly, identifies the relevant task, and cites the official results. Its optional NUIST Task 3a detail matches the independent results page. |
| `f1558521-b72f-445d-9cea-be9025b11c7a` (st07 Flash high) | 2 | Origin correct; test limitations substantially correct; contamination/deprecation correct as an OpenAI claim; maintainer gap omitted | Direct, broad synthesis with several caveats. The run could not read OpenAI's 2026 page and relied on snippets and secondary reports for a decisive point; the packet independently confirms OpenAI's published position. One report statement has no supporting ledger evidence. The METR maintainer-review result is absent. Quantitative preprint findings need separate source audits before trusting them. |
| `e344f2fd-7938-4609-b3a7-119363400628` (st07 Luna high) | 1 | Origin correct; test limitations partly covered; contamination/deprecation omitted; maintainer gap omitted | The report is clear about its limits and does not invent a contamination conclusion. Its q2 scout failed on a provider 429, so it cannot answer a central part of the question. Its current quality mark describes this partial report, not Luna's inherent ability on st07. |

The evaluator should keep overall quality and factual results separate. For st07 it should flag the missing contamination evidence and maintainer gap; for st04 it should not penalize the true NUIST detail merely because the run's own quote check failed. A decisive disagreement with these marks gets human review before model selection.

## Question types

Use a mixed portfolio of short fact questions, structured comparisons, and broad research questions. The short cases test whether Scout can answer efficiently and precisely; the broad cases test whether it can synthesize evidence into a useful judgment. Each case should state a reader, the decision or understanding the report should support, a time boundary, and any source restrictions. Keep the natural user question short. Store this context alongside it for evaluation, rather than appending a long checklist to the model's prompt.

Start with a small set spanning different kinds of research:

| Kind | Candidate question | What it probes |
|---|---|---|
| Direct fact | At which conference and in which year was *Attention Is All You Need* published? | Correctness, directness, and the cost of finding a simple fact. |
| Primary-source extraction | How many developers annotated SWE-bench Verified samples according to its announcement? | Precise extraction and attribution. |
| Structured comparison | What do the original papers report for the size, training tokens, and context length of GPT-3, Chinchilla, and Llama 2? | Coverage of requested fields and accurate source attribution. |
| Contested assessment | Is SWE-bench Verified still a trustworthy measure of coding-agent progress? | Weighing conflicting evidence and drawing a qualified conclusion. |
| Literature synthesis | Under what conditions does chain-of-thought prompting help language models below about 10 billion parameters? | Separating methods, populations, and publication status. |
| False premise | Which team won the ILSVRC 2016 video captioning track? | Detecting the premise and answering directly. |

These are versions of the repository's existing st01, st03, st05, st07, st06, and st04 questions, respectively. Keep their existing IDs and rubrics for historical diagnostics. Give any revised wording or newly selected broad case a new ID. Before a paid study, review scope and source availability, then freeze wording, evaluation context, date, source packet, and evaluator version. Include each question type in selection, and report results within each type so several easy fact cases cannot mask a weak broad report.

## How to compare configurations

Pair runs by the same frozen question and evaluation context. Change one configuration variable at a time; for synthesis comparisons, use the same stored ledger, and for scout-effort comparisons, use the same stored plan. Score the report and the research outcome. A failed run remains in the denominator and has no report-quality score, rather than disappearing from the sample.

Show results case by case and by question type: paired preference, dimension judgments, factual defects, completion, cost, and elapsed time. Do not average fact-case scores and broad-case scores into one quality number. Repeat a close or inconsistent pair before adoption. Treat a single run as screening. A configuration is promising when it preserves factual reliability and citation integrity, improves or ties on reader utility across the tested types, and offers a meaningful cost, latency, or completion benefit. Otherwise mark the comparison undecided. Set exact adoption margins after calibrating the new evaluator on saved reports, before paid comparison runs.

First establish human reference judgments on the saved st04 and st07 reports: mark direct answers, decisive claims, material omissions, and overall usefulness. This can be done without model calls. Then implement the versioned high-level judge and its storage, with offline scripted-model tests. Calibrating the real judge against those human marks is a paid step with its own cap and approval, before configuration comparisons. Keep the historical version-2 point rubric as a separate diagnostic; do not rewrite its old grades.

### How a paid comparison is set up

These rules come from the studies so far; AGENTS.md requires them.

- **Fix the decision rule first.** Write it in the study spec's header before any paid run: what must not regress, what must improve, and by how much, and what added cost is acceptable. A result that does not meet it is recorded as undecided or negative.
- **Make sure the sample can decide.** Run-to-run variation on the frozen cases is several rubric points, so one run per arm is a screen, not a result. Prefer measures that vary less than rubric points: failed fetches, sources read in full, audit verdicts, time, and cost.
- **Change one thing.** Compare rescouts on one stored plan to compare scouts (`research rescout`), and syntheses of one stored ledger to compare synthesizers (`research synthesize`). Alternate the arm order between replicates, and let the arms share the study's cache.
- **Check it for free, then for cents.** Run the spec `--dry`, then `--cheap`, before paying; both must report no invariant violations.
- **Estimate from the most expensive comparable run,** set a hard cap per run and a ceiling for the study, and get approval before the paid run. Leave the ceiling some room above the worst case by the estimates: the runner lowers each run's cap to what remains of the ceiling, so a tight ceiling gives the last runs smaller caps than the first, and a cut-short run would count against whichever arm ran last. Run one Luna study at a time, because the rate limit is per account.
- **Report every case separately,** with failed and partial runs kept in the denominator, and record the runs, costs, and outcome in the study log.

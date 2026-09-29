# How Scout's quality is measured

This page describes how Scout's reports are evaluated and how a change to Scout is decided on. The results of every study are indexed in [study-log.md](study-log.md), which links to archived entries, and what the first design learned is in [lessons.md](lessons.md).

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
- **Uncited sentences.** The answer's sentences of six words or more that carry no inline citation. Headings, bold heading lines, table rules, and a table's header row are not counted; a table's other rows are, since they state findings. From 29 September the header row and bold heading lines are left out; the Opus 5.5 resynthesis of st07 counted one of each as uncited (study log, "Uncited sentences re-scored on synthesis v8"). With Claude's citations, most uncited sentences are framing or conclusions, so the count is a diagnostic, not a measure of support.
- **Answer support.** The answer is `supported` only when every statement is `read`, every research question returned evidence, and every coverage item is addressed with cited claims; otherwise it is `weak` or `unsupported`.

These checks establish traceability, not truth. A verified quote shows the source contains those words, not that they carry the claim. That is what the support audit is for.

## The support audit

`research audit` gives the configured auditor model each report statement with the verified quotes behind it, and the record the research tools returned for each quoted source: its address, and for a scholarly record its title, authors, date, and venue. It answers `supported`, `partial` when the statement adds something the quotes do not state, or `unsupported`; a statement with no verified quote is marked `no_quote` without a call. Nothing a model wrote about a source is sent (audit version 2). A study spec with `audit = true` audits every run, and its summary shows the counts.

The first audit of all stored reports found that about one in five quoted statements says more than its quotes, usually because the scout's claim already went beyond its quote, and that claims that a source leaves something out recur and can never be established by a quote. A model's verdicts are not ground truth: check a sample by hand before relying on them.

## The rubric judge

`research grade` scores a report against a case's rubric with one verdict per rubric point (judge version 2, the first design's judge, ported unchanged so that old and new grades compare). It measures coverage of what an expert report contains, not overall quality. It was stable at high effort in the settings study, varying by at most one point in 72 on repeat gradings of one report.

The judge itself matters more than that stability suggests. Regraded by `zai:glm-5.3` with the same prompt, the stored reports agreed on 92.6% of rubric points, but GLM credited 27 points that `gpt-6-sol` did not, and on the DeepResearch Bench II cases the judge alone moved a score by up to 8 points, as much as the run-to-run variation a study tries to see past. In the disputes checked by hand, `gpt-6-sol` was too strict about equivalent wording. So a decision that rests on a few rubric points uses both judges and has their disagreements checked by hand.

### Where the points were lost: `research diagnose`

A rubric score says how many points a report met, not why it missed the others, and a change can move it for reasons that have nothing to do with the research. `research diagnose` (diagnose.py, diagnose version 1) is the step after grading that answers both. It grades three views of each run with one judge, `zai:glm-5.3@high` by default:
- **the report**, exactly as a report grade does, stored in `grades` like any other;
- **the claims**: every claim statement, with the titles and addresses of its sources;
- **the research**: everything the synthesizer was shown, including quotes, excerpts, conclusions, open items, and `unresolved`.

The two earlier views are stored in `stage_grades`, apart from report grades, so no report score takes one in. Each point the report missed is put at the earliest view that met it:
- *claimed, not reported*: the synthesis dropped a claim;
- *seen, not claimed*: the synthesizer was shown it, but only in a quote or an open item, and no claim stated it;
- *not found*: nothing the research returned met it.

A point the report met that neither earlier view did is counted apart, as judge noise or the synthesizer writing from its own knowledge. A change that works should move points out of a named stage. Watching that stage varies less than the total score.

The same step reads only stored grades for four checks of the score itself:
- how often the two judges disagree about the same reports, and on which points;
- missed URL points where the report gives an address on the same site, which is judge literalness;
- points that no stored grade of a case has met in any view, which may be out of reach until a person confirms them;
- for a two-arm study, the smallest difference in mean score its replicates can detect at 80% power.

A stored grade by the same judge, judge version, and rubric version is reused, so a run is only paid for once, and `--free` makes no calls at all. A study spec with `diagnose = true` diagnoses each run after its grade and audit, then appends the whole study's diagnosis to its summary. Dry and cheap studies use their fake and cheap models for it. The step was built after the audit of 28 September 2026 found, by hand, that drb2-task8's missed points were mostly seen by the synthesizer but never claimed (study log).

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

The study runner snapshots model IDs, efforts, and model-call limits before its first arm. An arm’s environment and explicit `audit_model` or `diagnose_model` setting can then change its model. Check the saved run configuration and recorded judge identity before comparing grades across arms; use the same judge unless the judge itself is the variable under study.

Show results case by case and by question type: paired preference, dimension judgments, factual defects, completion, cost, and elapsed time. Do not average fact-case scores and broad-case scores into one quality number. Repeat a close or inconsistent pair before adoption. Treat a single run as screening. A configuration is promising when it preserves factual reliability and citation integrity, improves or ties on reader utility across the tested types, and offers a meaningful cost, latency, or completion benefit. Otherwise mark the comparison undecided. Set exact adoption margins after calibrating the new evaluator on saved reports, before paid comparison runs.

First establish human reference judgments on the saved st04 and st07 reports: mark direct answers, decisive claims, material omissions, and overall usefulness. This can be done without model calls. Then implement the versioned high-level judge and its storage, with offline scripted-model tests. Calibrating the real judge against those human marks is a paid step with its own cap and approval, before configuration comparisons. Keep the historical version-2 point rubric as a separate diagnostic; do not rewrite its old grades.

### How a paid comparison is set up

These rules come from the studies so far; AGENTS.md requires them.

- **Fix the decision rule first.** Write it in the study spec's header before any paid run: what must not regress, what must improve, and by how much, and what added cost is acceptable. A result that does not meet it is recorded as undecided or negative.
- **Make sure the sample can decide.** Run-to-run variation on the frozen cases is several rubric points, so one run per arm is a screen, not a result. Prefer measures that vary less than rubric points: failed fetches, sources read in full, audit verdicts, time, and cost.
- **Change one thing.** Compare rescouts on one stored plan to compare scouts (`research rescout`), and syntheses of one stored ledger to compare synthesizers (`research synthesize`). Rotate the arm order between replicates and targets, as the study runner does, and let the arms share the study's cache.
- **Check it for free, then for cents if anything is new.** Run the spec `--dry` before paying; it must report no invariant violations. Run `--cheap` too when an arm uses something that has not had a real run on the current code (a new engine, reader, model, or provider, or changed scout, fetch, or study code); otherwise it repeats what earlier real runs showed.
- **Estimate from the most expensive comparable run,** set a hard cap per run and a ceiling for the study, and get approval before the paid run. Leave the ceiling some room above the worst case by the estimates: the runner lowers each run's cap to what remains of the ceiling, so a tight ceiling gives the last runs smaller caps than the first, and a cut-short run would count against whichever arm ran last. Run at most two Luna studies at once, because the rate limit is per account and each study process paces itself.
- **Report every case separately,** with failed and partial runs kept in the denominator, and record the runs, costs, and outcome in the study log.

## Serper, Brave, and DeepSeek integration study

The 28 September 2026 integrations add two web-search choices (`serper` and `brave`) and two
DeepSeek scout candidates (`deepseek-flash` and `deepseek-v4-pro`). Evaluate search and models in
separate studies so each comparison changes one setting. The frozen development cases are st04
(false premise), st05 (structured extraction), st07 (contested synthesis), and drb2-task8 (broad
research). Force standard depth in every arm. Keep the current planner, synthesizer, reading
fallback, prompts, judge, and auditor fixed. The search comparison uses Luna scouts; the model
comparison uses DuckDuckGo. The study runner snapshots all model settings and call limits; verify
each saved run's configuration and prompt fingerprint before comparing it.

### Redesign, 28 September 2026

Two cheaper checks replace parts of the screens below. A paired search replay (`scripts/search_replay.py`)
answered the search screen's availability question: on 40 replayed queries DuckDuckGo found nothing for 18,
Serper and Brave for 4 each. The DeepSeek comparison is now a rescout study,
[`deepseek-rescout-task8.toml`](../studies/deepseek-rescout-task8.toml): Luna, DeepSeek Flash, and DeepSeek
V4 Pro each rescout the same three stored drb2-task8 plans, and a rescout's claims and research are graded
with `diagnose`, since a rescout writes no report. Only the scout model changes, with no planner or
synthesizer variation. The spec's header holds its decision rule. Run the trimming study
([`trim-history-rescout-task8.toml`](../studies/trim-history-rescout-task8.toml)) first, because DeepSeek's
cost depends far more on the prompt cache hit rate than Luna's, and report each arm's hit rate.

The search comparison is now a rescout study too, [`search-rescout-task8.toml`](../studies/search-rescout-task8.toml):
DuckDuckGo, Serper, Brave, and Exa, each alone, rescout the same three plans with Luna@xhigh scouts and trimming
off, graded with `diagnose`. It pins trimming instead of waiting for the trimming study, and it replaces
the search screen's full runs. Both rescout studies run every scout at xhigh, the highest effort.

### Screen and confirmation

The runnable screens are [`search-integrations-screen.toml`](../studies/search-integrations-screen.toml)
and [`deepseek-integrations-screen.toml`](../studies/deepseek-integrations-screen.toml). Each runs
one replicate of each arm on all four cases, with the same rubric judge and support auditor. A
screen identifies broken integrations and large directional changes. Its scores are **provisional**:
one run per arm cannot select a default. The spec headers fix their advancement rules before any
paid call. A failed or partial run stays in the denominator. Do not select a winner from an average
that combines the four cases.

Before either paid screen, run its `research study run SPEC --dry` and `--cheap` checks and require
no invariant violations. The cheap mode replaces every model, so it checks the search engines but
does not prove DeepSeek itself can answer a tool call. Smoke-check both DeepSeek model IDs before its screen: set `RESEARCH_MODELS__SCOUT` to each
candidate in separate `research doctor --smoke` invocations under the small-step cap. Log these paid checks and
their run IDs and costs. The current `.env` has the Serper, Brave, and DeepSeek keys but its provider allowlist omits
DeepSeek. The DeepSeek spec explicitly allows the four providers it needs in every arm; use the
same allowlist during its smoke checks. Do not copy key values into a spec or log. The local Postgres `research_loop` stores real study runs; the dry
check uses `research_dry`.

Advance a screen candidate only under its spec's rule. Confirm any advancing candidate against
its baseline with **three replicates per case** and the same four cases, fixed depth, judge, auditor,
and version. The runner rotates the arm order on each replicate and target. Create a new confirmation spec
with its own study label and ceiling estimated from the screen's most expensive comparable run,
then run that exact spec's dry and cheap checks. A DuckDuckGo-then-candidate chain is a separate
search setting and needs its own comparison after the direct-engine result; it must not be credited
to the direct-engine arm. Search caches are namespaced by engine, while page reads can be shared
within a study.

Run **at most two** Luna studies at once (AGENTS.md). The study runner
executes its runs sequentially. Standard depth plans at most four research questions, so a single
run can have up to four simultaneous scouts; scouts on Luna share that run's token pacer. Its
configured rate is 2,000,000 tokens per minute and it adopts a limit OpenAI reports. Separate
study processes have separate pacers, so a third concurrent Luna study
can exceed the account limit even though each process looks safe alone. After each paid screen,
check `research breakdown` for long model spans, 429s, and request timeouts, and Logfire
for explicit pacing waits. Keep affected runs in
the reported denominator; investigate these stops before treating a quality difference as an
integration effect.

### Scorecard and confirmation decision rules

Report each case and replicate, including completion, elapsed time, settled cost, search calls
with results versus empty/error outputs, full pages read and failed fetches, verified and
misattributed quotes, coverage, answer support, rubric points, and support-audit verdicts.
Inspect stored tool messages for search outcomes; the study summary does not yet count empty
searches. Record model tokens, provider 429s, and budget/time stops for the DeepSeek comparison.
Review decisive claims against independent sources when a grade or audit would decide adoption.
Use the same two rubric judges on every confirmation report; the study runner uses the configured
judge first, and a second, separately capped regrade is an explicit follow-up.

| Candidate | Confirmation rule against its baseline |
|---|---|
| Serper or Brave as sole search engine | At least a 25% lower empty/error share among web-search calls across all cases; at least as many full pages read in three of four cases; no extra failed run or blocked-source exposure; supported-audit share no more than 5 percentage points lower; no case loses more than one rubric point on st04/st05 or eight on st07/task8 under either judge. Median added cost must be at most $0.30 per run and median time at most 20% longer. |
| DeepSeek Flash or V4 Pro as scout | No extra failed or partial run, blocked-source exposure, or decisive factual defect; supported-audit share no more than 5 points lower; no case loses more than one rubric point on st04/st05 or eight on st07/task8 under either judge. It must either gain at least eight points on drb2-task8 under both judges with a median cost premium of at most $0.50 per run, or tie case-level quality (no mean loss greater than two points on any case) while cutting median cost by 25% or median time by 20%. |

The eight-point broad-case threshold reflects the variation seen even with three replicates in
the existing drb2-task8 study. Treat a close or conflicting result as **undecided**, not as a
small measured improvement. A candidate meeting its rule is eligible for a held-out confirmation
before changing a default. A candidate that improves reliability or evidence but misses a price
or latency limit can be rated useful for opt-in use without changing the default. Report source
integrity, research quality, completion, cost, and time separately; do not compress them into a
single score.

Historical standard-depth runs in local Postgres cost at most $0.368 on drb2-task8, $0.113 on
st05, and $0.206 on st07. The search screen estimates $0.60 per run plus $0.10 for grading and
audit, with a $9.00 study ceiling; the DeepSeek screen estimates the $0.75 standard-depth run
limit plus $0.10 for evaluation, with an $11.00 ceiling. These are planning ceilings, not spend
approval. Request approval with the hard cap before either paid screen or confirmation.

Historical standard-depth runs took 191–254 seconds on st05, 349 seconds on st07 (one run),
and 454–514 seconds on drb2-task8. Stored st04 runs used quick depth and took 65–70 seconds,
so its standard-depth time is an extrapolation. In the previous six-run study, the sequential
grade-and-audit interval between runs was 100–147 seconds. For twelve serial runs, allow about
1–2 hours for the search screen and 1.5–3 hours for the DeepSeek screen; DeepSeek latency has
not been measured here. Both screens together may take roughly 3–5 hours, including evaluation.
These are wall-time estimates, not deadline guarantees; individual standard runs have a 12-minute
whole-run deadline.

Afterward, `research breakdown` can compare planner, scout, and synthesizer time, token use,
cost, 429s, and stop reasons for every stored run. A separately capped `research diagnose` can
grade the saved research, claims, and final report without rerunning acquisition, attributing
missed rubric points to evidence not found, evidence seen but not claimed, and claims not
reported. That analysis is useful for locating a weak stage, but the search screen alone cannot
identify a causal planner or synthesizer effect. For controlled role comparisons, use `rescout`
on a fixed stored plan to compare scouts and `synthesize` on a fixed ledger to compare
synthesizers. A validator that judges existing records can be checked on these saved runs;
a gap-analysis change that sends scouts back to research needs new paired runs. The standard-depth
screens do not invoke the existing gap-analysis follow-up.

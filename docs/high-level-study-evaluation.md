# Evaluating research questions at the level users ask them

This is a proposal for the next Scout study. It changes the choice of study questions and the way reports are assessed. It does not change Scout's workflow or authorize model calls. The existing rubric judge remains useful for comparing with the first design's historical grades.

## What the current setup measures

`study_cases.jsonl` contains three short-answer cases, a false-premise case, a fixed table of facts, and two broad research questions. The two broad cases, st06 and st07, are retired as selection cases. The current `research grade` command handles only rubric cases. Its judge decides whether each listed point appears in the report and produces the fraction met. It reads the answer, key statements, caveats, and source names, but no source passages. That makes it a coverage check for predefined facts, not an assessment of whether the conclusion is useful or the cited material supports it.

The saved Scout runs show the gap. `runs/st07-high` completed a broad question with a direct answer, but its checks flagged one unsupported statement. `runs/st07` returned no evidence before the research deadline. A study must count both outcomes. Neither a high rubric score nor a completed status alone establishes that a reader can rely on a report.

## Study questions

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

## Evaluation record

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

## How to compare configurations

Pair runs by the same frozen question and evaluation context. Change one configuration variable at a time; for synthesis comparisons, use the same stored ledger, and for scout-effort comparisons, use the same stored plan. Score the report and the research outcome. A failed run remains in the denominator and has no report-quality score, rather than disappearing from the sample.

Show results case by case and by question type: paired preference, dimension judgments, factual defects, completion, cost, and elapsed time. Do not average fact-case scores and broad-case scores into one quality number. Repeat a close or inconsistent pair before adoption. Treat a single run as screening. A configuration is promising when it preserves factual reliability and citation integrity, improves or ties on reader utility across the tested types, and offers a meaningful cost, latency, or completion benefit. Otherwise mark the comparison undecided. Set exact adoption margins after calibrating the new evaluator on saved reports, before paid comparison runs.

First establish human reference judgments on the saved st04 and st07 reports: mark direct answers, decisive claims, material omissions, and overall usefulness. This can be done without model calls. Then implement the versioned high-level judge and its storage, with offline scripted-model tests. Calibrating the real judge against those human marks is a paid step with its own cap and approval, before configuration comparisons. Keep the historical version-2 point rubric as a separate diagnostic; do not rewrite its old grades.

## Recent Scout evidence and the next study gate

Use runs after the Scout database migration applied on 25 September 2026 at 19:24 UTC. The older workflow had different prompts and context and is not a direct comparison. The current live screen has one repetition per cell:

| Case | Flash high | Luna high | Interpretation |
|---|---|---|---|
| st04, false premise | Complete in 123 s, $0.0536, 4/4 rubric points | Complete in 56 s, $0.0291, 4/4 | Luna is promising on this short case. |
| st05, structured facts | Complete in 199 s, $0.1315, 11/11 | Complete in 80 s, $0.0778, 11/11 | Luna is promising on this medium case. |
| st07, contested assessment | Complete in 300 s, $0.2627, 6/9 | Partial in 130 s, $0.1542, 2/9 | Luna's q2 scout received a timed 429; the grade is not a clean quality comparison. |

Run costs exclude separate judge calls. On the completed st04 and st05 pairs, Opus synthesis accounts for roughly 67–80% of run cost. This makes fixed-ledger synthesis a larger measured cost lever than another scout-effort screen, provided research quality can be assessed.

The first approved st07 Luna retry used a shared 429 pause but did not finish. After a successful $0.003876 planner call, the process exited with code 139 while four scouts were active; the kernel reported a fault in the native `etree` extension. The database has no settled scout costs for that attempt, so actual provider billing is unknown. Its run and four scout rows were marked abandoned. A four-worker offline HTML extraction stress check passed, leaving the exact triggering path unresolved; both page extraction and web search use `lxml`. Do not count this attempt as a quality result.

The immediate reliability gate is to capture a Python fault trace on the next authorized live attempt and isolate the native parser failure. In parallel, use existing reports to build the versioned high-level evaluator and independent decisive-claim audit. Once that evaluation is calibrated, compare synthesizers on identical stored ledgers. Only then use another broad live run to decide whether Luna's short-case advantage extends to research synthesis.

## Fixed-plan scout comparison on drb2-task8

On 25 September 2026 the scouts were compared on one fixed plan, so that planning and synthesis could not account for the difference. `research rescout` (commit e424ad2) took the four-question plan of drb2-task8 baseline replicate 4 (`b61f1b55`) and researched it again without synthesis. Each rescout had the production research window of 270 seconds and was recorded in study `drb2-task8-scouts`. Arms alternated so that neither benefited more from the fetch cache. The eight rescouts cost $0.32 in total.

| Arm | Cap | Questions with claims | Evidence | Verified quotes | Evidence from sources the tools did not return | Time | Cost |
|---|---|---|---|---|---|---|---|
| Luna high, r1 | $0.50 | 3/4 | 35 | 29/31 | 1 | 135 s | $0.044 |
| Luna high, r2 | $0.50 | 2/4 | 22 | 20/22 | 0 | 108 s | $0.031 |
| Luna high, r3 | $3.00 | 3/4 | 40 | 37/39 | 0 | 124 s | $0.042 |
| Luna high, r4 | $3.00 | 3/4 | 25 | 24/24 | 0 | 137 s | $0.035 |
| Flash max, r1 | $0.50 | 1/4 | 11 | 10/11 | 0 | 270 s | $0.041 |
| Flash max, r2 | $0.50 | 0/4 | 0 | 0/0 | 0 | 270 s | $0.027 |
| Flash high, r1 | $3.00 | 3/4 | 44 | 23/26 | 11 | 217 s | $0.045 |
| Flash high, r2 | $3.00 | 3/4 | 41 | 32/33 | 20 | 221 s | $0.058 |

Flash at max effort does not fit the window. Seven of its eight scouts were still running at the deadline after two to five requests each. Nearly all of that time was model time; the tools ran for about ten seconds per scout. This repeats the earlier screen's finding.

None of Luna's missing questions reflects its research. At the $0.50 cap all three were refused by the budget guard. `byte-reserve-v3` reserved about $0.09 to $0.11 for each Luna request that cost about $0.003, and the reservations of four parallel scouts filled the cap. With the $3 cap, one question failed on an OpenAI 429 without a retry time. Another was lost when a single response asked for 165 distinct web searches; PydanticAI refused the whole batch against the tool-call limit of 40, so that scout ended with nothing.

Flash at high effort finished every scout inside the window, but it used about 80% of it. Each run had one question for which Flash returned a conclusion and no claims, because it could not open the review article it judged decisive and put that under unresolved instead. Its ledgers held more evidence than Luna's, but 11 and 20 of those items cite sources its own tools did not return, against at most one for Luna. They also read fewer pages in full.

On this plan the two usable arms answered the same number of questions. Luna did so in about 60% of the time, with fewer unobserved sources. Its failures came from the budget guard, provider rate limits, and one unbounded tool batch, all of which code can address. Luna remains the default scout. Grading ledgers from both arms would not change this without a coverage difference to explain, so no synthesis or grading was run.

Two fixes follow. The budget guard should reserve closer to the actual price of cheap models, so that tight study caps stop refusing requests. A scout response with more tool calls than its remaining limit should lose the excess calls rather than the whole question.

Both fixes were then made. Budget policy `usage-anchor-v4` bounds a request from the billed tokens of the reply before it. Replayed over the study's 189 scout requests, it never fell below the billed input, and its median bound fell from 11.4 to 2.5 times the billed input. A scout turn that asks for more tool calls than its limit leaves now runs the calls that fit, and the next request tells the model how many were dropped.

## Listing the set before confirming it (scout-v2)

The graded runs on both development cases stopped short of the expected set, and their stored ledgers showed why. On every drb2-task8 run the database scout named the four or five computed-data databases its first search returned, then spent the rest of its budget confirming each one on its official site; replicate 4 made 28 searches, nearly all of them `site:` lookups of names it already had. Its own `unresolved` list said the list was not exhaustive, while it reported a confidence of 0.97. The drb2-task68-plus methods scout did the same with the cloud providers' scaling documentation. Commit 53fd75d (workflows `scout-v2`, `scout-followup-v2`, `scout-research-v2`) tells a scout to find a survey or overview that lists the set and its categories before confirming members, to list what it did not cover, and to let its confidence reflect coverage. It tells the gap analysis that a partial set is a material gap.

On 26 September 2026 study `set-coverage-screen` compared the two scout prompts on fixed plans: drb2-task8's `b61f1b55` and drb2-task68-plus's `88b6017b`, two Luna rescouts per arm and case, in ABBA order, each with a $3 cap. The v1 arm ran from a worktree at e79a120. `scripts/ledger_coverage.py` counts the rubric's expected members that a claim or conclusion names (found) and those that appear only under `unresolved` (named).

| Case and arm | Run | Found | Named only | Cost |
|---|---|---|---|---|
| task8 v1, r1 | `00f08fb2` | 3/7 | 0 | $0.059 |
| task8 v1, r2 | `d7ee85a9` | 3/7 | 0 | $0.048 |
| task8 v2, r1 | `df9e8e3c` | 2/7 | 1 (NOMAD) | $0.054 |
| task8 v2, r2 | `a28ae597` | 3/7 | 2 (ICSD, CSD) | $0.053 |
| task68-plus v1, r1 | `9cfebd57` | 2/6 | 0 | $0.055 |
| task68-plus v1, r2 | `a4621804` | 4/6 | 0 | $0.046 |
| task68-plus v2, r1 | `9057aaf7` | 5/6 | 0 | $0.052 |
| task68-plus v2, r2 | `d496e94c` | failed | | $0.049 |

The eight rescouts cost $0.42. On drb2-task68-plus the v2 scout found fuzzy logic and machine learning, which no earlier run had, but with one usable v2 run that is a direction, not a result. On drb2-task8 the v2 scouts did what the prompt asked: both searched for reviews first and read a 2020 guide to materials databases that names many more of them. They did not establish those members, because the planned question required each database to be verified against its official website and the productive-call budget ran out first; they listed them under `unresolved` instead. ICSD and CSD appear there, the first time any run has reached them. Quote verification and cut-offs did not change between arms. Under the study's decision rule the screen is undecided; v2 stays, since it cost nothing in quality, and the next change raises the research budget that now limits it.

The failed run exposed a bug. pypdf extracted a math italic letter in a VLDB paper as two surrogate code points, the scout's next request could not be encoded as UTF-8, and that error was not a recognized call failure, so it failed the whole run after its other three scouts had finished. Commit 586bd21 makes every tool result valid Unicode (`FETCH_VERSION` 8) and cuts off only the question whose scout hits an unexpected error, with a note that names it as a bug.

# Study log

Every study, paid run, and offline re-scoring since the Scout refactor, in date order, with its run IDs, costs, result, and decision. How quality is measured, and how a comparison is designed so that it can decide, is in [evaluation.md](evaluation.md). What the first design learned is in [lessons.md](lessons.md).

Run IDs are the first eight characters of the run's UUID unless given in full. Costs are what providers charged, as recorded in Postgres. Each paid step was approved in advance with an estimate and a hard cap.

| Date | Entry | Spend | Outcome |
|---|---|---|---|
| 09-25 | [Scout's first live screen](#2026-09-25-scouts-first-live-screen-flash-and-luna-scouts-on-st04-st05-and-st07) | about $0.71 | Luna promising on the short cases; st07's Luna run lost a question to a 429, and a retry crashed in a native parser |
| 09-25 | [First paid calibration of the quality judge](#2026-09-25-first-paid-calibration-of-the-quality-judge) | $0.10 | The judge was within one level of the human marks; not yet a selection rule |
| 09-25 | [First drb2-task8 baseline, and the budget guard's corrections](#2026-09-25-first-drb2-task8-baseline-and-the-budget-guards-corrections) | about $0.16 | Three runs stopped by the guard or a credential; guard fixed from byte-reserve-v1 to v3 |
| 09-25 | [Baseline retries, the first graded DRB-II reports, and follow-up runs](#2026-09-25-baseline-retries-the-first-graded-drb-ii-reports-and-follow-up-runs) | about $1.20 | drb2-task8 20 to 24 of 52, drb2-task68-plus 14 of 54; research stops short of the expected set |
| 09-25 | [Fixed-plan scout comparison](#2026-09-25-fixed-plan-scout-comparison-on-drb2-task8-luna-against-flash) | $0.32 | Luna stays the default scout; Flash at max does not fit the window |
| 09-26 | [Listing the set before confirming it (scout-v2)](#2026-09-26-listing-the-set-before-confirming-it-scout-v2) | $0.42 | Undecided; v2 kept |
| 09-26 | [A larger research budget (scout-v3)](#2026-09-26-a-larger-research-budget-scout-v3) | $0.30 | Broader lists at about 40% more research cost; v3 kept |
| 09-26 | [Quote attribution (evidence version 6)](#2026-09-26-quote-attribution-evidence-version-6) | free | Misattributed quotes fell from 6.2% to 3.0% once copies of the same work count as it |
| 09-27 | [Live check of scout-v6](#2026-09-27-live-check-of-scout-v6-study-smoke-v6-task8) | $0.93 | Deep run 31 of 52, the best so far; four defects fixed in scout-v7 |
| 09-27 | [The bug-finding harness](#2026-09-27-the-bug-finding-harness-and-its-first-cheap-check) | $0.08 | Five bugs found while it was built; the cheap check found sentences in open items |
| 09-27 | [Statements that rest on a summary (evidence version 7)](#2026-09-27-statements-that-rest-on-a-summary-evidence-version-7) | free | 18 of 208 report statements rested only on a scout's summary |
| 09-27 | [Checks of scout-v9](#2026-09-27-checks-of-scout-v9-on-st04-st05-and-st07) | $0.57 | No short-case regression; st07 lost its decisive question to a TLS error |
| 09-27 | [Support audit (audit version 1)](#2026-09-27-support-audit-of-every-stored-report-audit-version-1) | $0.26 | About one in five quoted statements says more than its quotes |
| 09-27 | [A second rubric judge](#2026-09-27-a-second-rubric-judge) | $0.27 | 92.6% agreement; the judge moves DRB-II scores by up to 8 points |
| 09-27 | [Exa search checks](#2026-09-27-exa-search-dry-and-cheap-checks) | $0.02 | Exa search works end to end, and records its cost exactly |
| 09-27 | [Exa against DuckDuckGo: comparison checks](#2026-09-27-exa-against-duckduckgo-dry-and-cheap-checks-of-the-comparison-specs) | $0.39 | Exa's uncapped highlights flood the scouts' context; the comparison is held |
| 09-27 | [Fetch bake-off](#2026-09-27-fetch-bake-off-on-the-pages-our-fetcher-failed-on) | $0.10 and free-tier credits | A chain of our fetcher, open access, Exa, and Firecrawl reads 139 of 160 failed pages |
| 09-27 | [Reading fallback: dry and cheap checks](#2026-09-27-reading-fallback-dry-and-cheap-checks) | $0.17 | A screen: with the fallback, failed page fetches fell from 12 to 0 on st07 and from 10 to 1 on drb2-task8 |
| 09-27 | [Architectural audit: blocked sources and the study ceiling](#2026-09-27-architectural-audit-blocked-sources-and-the-study-ceiling) | free | Five early drb2-task8 ledgers cite the blocked expert report; the study ceiling was not a hard cap; a request's reservation missed long-context output prices. All three fixed |
| 09-27 | [Reading fallback: cheap checks after the audit fixes](#2026-09-27-reading-fallback-cheap-checks-after-the-audit-fixes) | $0.16 | Clean on the new code once the keys were in `.env`; the runner now checks every arm first. Ceilings raised |
| 09-27 | [Deep example runs: throughput, trimmed history, and a second scout model](#2026-09-27-deep-example-runs-throughput-trimmed-history-and-a-second-scout-model) | $1.59 | Both real deep runs were partial; Luna's token rate, not the deadline, limited them. Longer deep limits, history trimming, and a second scout model were built but not measured |
| 09-28 | [Deep against standard on drb2-task8 (scout-v10, followup-v11)](#2026-09-28-deep-against-standard-on-drb2-task8-scout-v10-followup-v11) | $3.59 | Undecided: deep scored 5.0 points more under Sol and 4.7 under GLM, short of the 5-point rule under both; better supported, 1.75 times the cost |
| 09-28 | [Where drb2-task8's rubric points are lost](#2026-09-28-where-drb2-task8s-rubric-points-are-lost) | free | Points are lost before synthesis: scouts claim narrow, paper-level findings from few reviews, and coverage counts a dimension met by any one claim. About 6 points are out of reach, and URL literalness explains much of the gap between the judges |
| 09-28 | [The first free diagnosis of the deep-vs-standard study](#2026-09-28-the-first-free-diagnosis-of-the-deep-vs-standard-study) | free | With three runs per arm the study could detect only about 8-point differences, so its 5-point rule could not decide; 7 of 52 points never met |

## 2026-09-25 Scout's first live screen: Flash and Luna scouts on st04, st05, and st07

These are the first runs after the Scout database migration, applied on 25 September 2026 at 19:24 UTC; earlier runs used the first design's prompts and context, so they are not a direct comparison. The current live screen has one repetition per cell:

| Case | Flash high | Luna high | Interpretation |
|---|---|---|---|
| st04, false premise | Complete in 123 s, $0.0536, 4/4 rubric points | Complete in 56 s, $0.0291, 4/4 | Luna is promising on this short case. |
| st05, structured facts | Complete in 199 s, $0.1315, 11/11 | Complete in 80 s, $0.0778, 11/11 | Luna is promising on this medium case. |
| st07, contested assessment | Complete in 300 s, $0.2627, 6/9 | Partial in 130 s, $0.1542, 2/9 | Luna's q2 scout received a timed 429; the grade is not a clean quality comparison. |

Run costs exclude separate judge calls. On the completed st04 and st05 pairs, Opus synthesis accounts for roughly 67–80% of run cost. This makes fixed-ledger synthesis a larger measured cost lever than another scout-effort screen, provided research quality can be assessed.

The first approved st07 Luna retry used a shared 429 pause but did not finish. After a successful $0.003876 planner call, the process exited with code 139 while four scouts were active; the kernel reported a fault in the native `etree` extension. The database has no settled scout costs for that attempt, so actual provider billing is unknown. Its run and four scout rows were marked abandoned. A four-worker offline HTML extraction stress check passed, leaving the exact triggering path unresolved; both page extraction and web search use `lxml`. Do not count this attempt as a quality result.

The immediate reliability gate is to capture a Python fault trace on the next authorized live attempt and isolate the native parser failure. In parallel, use existing reports to build the versioned high-level evaluator and independent decisive-claim audit. Once that evaluation is calibrated, compare synthesizers on identical stored ledgers. Only then use another broad live run to decide whether Luna's short-case advantage extends to research synthesis.

## 2026-09-25 First paid calibration of the quality judge

The user approved two saved-report assessments with separate pre-dispatch ceilings. Both calls used
`openai:gpt-6-sol`, evaluator v1, and the reviewed packet v1. The judgments and transcripts are in
Postgres `quality_assessments`; no new Scout research was run.

| Report | Assessment ID | Human / judge overall | Factual result | Actual / reserved / ceiling |
|---|---|---:|---|---:|
| st04 Flash high | `31742d33-2546-48d6-a564-de87e6163d2d` | 2 / 3 | Both targets correct; the two official-result claims checked were supported | $0.025422 / $0.383184 / $0.75 |
| st07 Flash high | `8d3582ef-7a9d-48f1-ab23-647b1bce0f64` | 2 / 2 | Three targets correct; METR maintainer-review finding omitted | $0.070892 / $1.458256 / $3.00 |

The st04 one-level disagreement is about reader utility, not the factual answer. The judge considered
the report's extra task tables and cautious, unverified NUIST detail harmless; the human reference
marked that detour as a material reservation for a simple false-premise question. The saved Luna st04
report answers the same question more directly and cheaply. Do not tune the judge to this one example
or treat v1 overall scores as a selection rule yet. Check the other saved st04 and st07 reports with
separately approved calls or a blind human pair review, then freeze any evaluator revision.

The st07 judgment matches the human overall mark and identifies the missing maintainer evidence. It
also marks several numerical claims `unresolved` because packet v1 contains only three source summaries;
that is a packet-coverage limit, not proof those claims are false. Both assessments used one request.
Their combined actual charge was $0.096314, below the combined $3.75 ceiling.

## 2026-09-25 First drb2-task8 baseline, and the budget guard's corrections

The user approved one `drb2-task8` baseline Scout run with a $3.00 pre-dispatch ceiling and the
ordinary $0.75 soft budget. Run `53478812-e5d0-4b89-82cc-031184cfe24f` took 103.7 seconds and
charged $0.043367815. It reserved $2.4916688 under `byte-reserve-v1`. Two of four scouts returned
claims, with 11 distinct sources, 10 read as full text or abstracts, and 26 of 28 quotes verified.
The exploration and optimization scouts were refused before later requests because the four-times-byte
reservation estimated more than one million input tokens, despite their recorded model usage being
about 90,000 and 111,000 input tokens. Synthesis was not dispatched: its $1.1928 reservation would
have exceeded the $3.00 ceiling. This run has no final report or rubric grade, so it is a budget-guard
diagnostic, not a quality score for Scout or Luna. The `unpriced call` review flag was also misleading:
the refused synthesis sent no request.

The guard now uses `byte-reserve-v2`: two times serialized request bytes plus 16,000 fixed input
tokens, with the same output caps and atomic pre-dispatch ceiling. The doubled byte count remains a
conservative allowance for message framing and serialization. It stops treating zero-request budget
refusals as unpriced calls. An offline regression checks a 300,000-byte history that v1 would reject
as above the million-token range. The next paid step is one separately approved baseline retry of the
same frozen case under the same $3.00 ceiling. Inspect its coverage and final report before paying to
grade it or comparing gap follow-up. No follow-up or grading call has yet been made on these cases.

## 2026-09-25 Baseline retries, the first graded DRB-II reports, and follow-up runs

The user approved one retry of the same frozen case under `byte-reserve-v2`. Run
`d106c122-a47b-422a-9ab1-48def7b6e700` took 181 seconds and charged $0.05925755. All four scouts
returned claims, with 62 of 71 quotes verified and 66 items read as full text. Synthesis was again not
dispatched: the scouts had charged about $0.05 but still held $1.8206 of reservations, and the
synthesizer's $1.1972 reservation would have crossed the $3.00 ceiling. The run is partial with no
report, so it is a second guard diagnostic rather than a quality result. Its `unpriced call` flag was
also still wrong: PydanticAI counts a request before the guard refuses it, so v2's zero-request check
never applied to a real refusal.

The guard now uses `byte-reserve-v3`. It still reserves the same conservative maximum before dispatch,
then replaces the reservation with the priced charge when the response returns. A failed request, or
one whose usage cannot be priced, keeps its full reservation. A refusal that was a call's only request
is no longer reported as an unpriced call. Offline tests cover settling, a kept reservation after
failure, streamed responses, and sequential requests that v2 would have refused. The next paid step
is again one separately approved retry of `drb2-task8` under the $3.00 ceiling.

The approved `byte-reserve-v3` retry, run `e16d7c7b-3ea9-491e-ad69-c01ae3e71ae0`, took 3.1 minutes and
charged about $0.06. All four scouts returned claims from 19 sources, 18 read as full text or
abstracts, and the guard dispatched synthesis with room to spare. Anthropic rejected that request with
HTTP 400 because the configured API key is not scoped to a workspace. The run is partial with no
report. It confirms the settled reservations, but it is a credential failure, not a quality result.

After the key was replaced, replicate 4, run `b61f1b55-ef3e-43c8-9e34-510df86b9061`, completed in 2.6
minutes for $0.22, of which Opus synthesis was $0.17. All four questions returned evidence, and 38 of
42 quotes were verified. The version-2 judge gave it 20 of 52 points (0.385) for $0.043, recorded as
grade `214fb3be-136f-4cd7-89d7-f902ee470660`. Presentation met 3 of 3, analysis 5 of 12, and
information recall 12 of 37.

Most of the loss is in the database list, which met 4 of its 21 points. The report lists five
computed-data platforms (Materials Project, AFLOW, OQMD, JARVIS, NOMAD). The rubric expects the
experimental and specialist databases ICSD, the Cambridge Structural Database, the ASM Alloy Center, and
DDSE, each with a description and URL. The method section names most expected algorithms but does not
explain how reinforcement learning, GANs, genetic algorithms, Bayesian optimization, or topology
optimization work in inverse design. Its advantages and disadvantages are source-specific rather than
the general trade-offs the rubric names. At least one verdict looks too strict: the report lists NOMAD
with its official URL, but the judge marked "Novel materials discovery" as absent because the report
does not spell out the name. That is at most three points and does not change the picture.

This is the first Scout score on a DRB-II case, so there is no earlier score to compare it with. The
missing databases are the kind of material gap that `--follow-up` is meant to find, which makes a
follow-up run on this case a direct test of that mode.

Two follow-up runs were then graded with the same judge. The first, `f157f38f-58b2-4ba2-9e08-fb42e4aac001`,
cost $0.28. Its database scout was lost to an OpenAI token-rate 429, and the gap analysis spent the deep
dive on that question. The deep dive found ICSD, COD, and Materials Cloud. It scored 24 of 52 (0.462),
grade `e4c4247c-be2c-4ced-9bfa-6c09e67d74bf`. After scout pacing (commit bc749a7), the second attempt,
`fbe4997b-e6aa-447b-b697-b6ff0e7971b0`, failed on an unwrapped TLS error from one scout after spending
$0.047; commit 819d03c makes such an error end only its own call. The third, `302403c4-4cf5-4d35-94e6-c263c70c0663`,
cost $0.27 and had no rate-limit errors. All four scouts returned claims, and the gap analysis
chose to confirm NOMAD. It scored 21 of 52 (0.404), grade `6451fddf-6390-4c5d-91ac-5daf17b0d080`.

| Run | Points | ICSD points | Other differences from replicate 4 |
|---|---|---|---|
| Replicate 4, no follow-up | 20 | 0 of 3 | |
| Follow-up 1 | 24 | 3 of 3 | +3 method points; −2 database URLs, −1 presentation |
| Follow-up 3 | 21 | 0 of 3 | +2 NOMAD points; −1 analysis point |

The follow-up gained most when its deep dive recovered a named database that the rubric wanted. Otherwise,
the gain was about the size of the run-to-run variation, which moves method and URL points either way. With
one baseline run and two follow-up runs, this cannot separate the mode's effect from that variation. On
this case the gap analyzer treats the database question as complete once it holds a few computed-data
platforms. It does not look for the experimental and specialist databases the expert report lists. Those
account for 12 of the 21 database points, and no run found them.

The first `drb2-task68-plus` baseline, run `88b6017b-f889-485c-ad21-8347d91c73e6`, completed in 4.8
minutes for $0.23. All four questions returned evidence, 37 of 42 quotes were verified, and pacing
kept it free of rate-limit errors. The same judge gave it 14 of 54 points (0.259), recorded as grade
`8ba2bd7c-bdf2-4ac9-8eed-75db6301e532`. Presentation met 5 of 7, analysis 2 of 9, and information
recall 7 of 38.

The report follows the requested structure, with the reactive and proactive sections and each named
challenge as a heading, but its taxonomy stops short. It covers threshold rules and general machine
learning. It mentions queuing theory and reinforcement learning only in passing, and it omits fuzzy
logic and time-series methods. Twenty-three of the 38 recall points ask for specific named papers,
which suggests the expert report follows one survey closely. Open-web research is unlikely to land on
those exact papers, so this case caps Scout's score lower than drb2-task8 does. The shared pattern is
the one drb2-task8 showed: the research finds some members of the expected set and stops short of the
full set, and the gap analysis cannot see what is missing without a checklist.

## 2026-09-25 Fixed-plan scout comparison on drb2-task8: Luna against Flash

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

## 2026-09-26 Listing the set before confirming it (scout-v2)

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

## 2026-09-26 A larger research budget (scout-v3)

The set-coverage screen showed that scouts now find an overview of a requested set but run out of productive tool calls before establishing its members. Commit 5738676 (`scout-v3`) raised a scout's budget from 12 requests, 16 productive calls, and 12 misses to 20, 32, and 16; the research window from 270 to 480 seconds and the run deadline from 360 to 720; told the planner to ask for a whole set rather than per-member verification; hid a result's self-rated confidence from the gap analysis and synthesis; and added the budget notes to the prompt fingerprint.

On 26 September 2026 study `scout-v3-screen` researched the same two stored plans again: two v3 rescouts per case, and one v2 rescout on drb2-task68-plus to replace the run that had crashed, from a worktree at 59c7c03. Every run used the study's own reused cache (`.cache/studies/scout-v3-screen`) and a $3 cap. The five rescouts cost $0.30.

| Case and arm | Run | Found | Named only | Time | Cost |
|---|---|---|---|---|---|
| task68-plus v3, r1 | `1aaf2430` | 6/6 | 0 | 477 s | $0.067 |
| task68-plus v2, r2 | `db2521db` | 6/6 | 0 | 206 s | $0.044 |
| task68-plus v3, r2 | `fddd3bcb` | 6/6 | 0 | 430 s | $0.076 |
| task8 v3, r1 | `259537d3` | 3/7 | 2 (ICSD, CSD) | 416 s | $0.063 |
| task8 v3, r2 | `6337ed01` | 3/7 | 2 (ICSD, CSD) | 299 s | $0.052 |

With the larger budget most scouts returned on their own rather than on a limit, and a v3 rescout cost $0.05 to $0.08 against about $0.05 before. On drb2-task68-plus every run in this study found all six method families, the v2 run included. That run followed the first v3 run and was served 8 of its 27 page lookups from the study cache, so this case no longer separates the arms; the earlier screen's v2 run found five. On drb2-task8 the database answer grew from five databases to eight or nine, now including AFLOW, JARVIS, Materials Cloud, the Crystallography Open Database, and an organic materials database, and both runs named ICSD and CSD under `unresolved` as members of the 2020 overview they did not check. The rubric's experimental and specialist databases are still not established: the scouts spend their reading on the computed-data databases first, several of whose official sites refuse the fetcher (Materials Project returns 403).

The larger budget gives broader lists at about 40% more research cost and longer runs, with no loss of quote verification. What remains is the gap between members a scout names and members it establishes, which is what the parallel deep dives of `scout-followup-v4` target: a gap analysis that sees ICSD and CSD under `unresolved` can send a deep dive to each. The next screen is a deep run on drb2-task8, graded.

## 2026-09-26 Quote attribution (evidence version 6)

An outside review of the data export found that the quote check did not establish attribution. Evidence versions 5 and earlier marked a quote `verified` when its words appeared anywhere in what a scout's tools returned, and checked separately whether the cited source had been returned, so a quote from one page could vouch for another. `scripts/rescore_quotes.py` re-checks every stored scout and deep-dive call from its recorded messages, with no model call. Over the 117 successful calls stored on 26 September 2026 (1,234 quotes, 1,153 stored as verified), matching each quote only within its cited source's text turned 71 verified quotes (6.2%) into mismatches, the same count the review reported.

Most of those were the same work under another address. Evidence version 6 counts a Wayback Machine copy as the page it archived, an ar5iv rendering as its arXiv paper, a publisher page whose address contains a DOI (Springer, APS, ACM, Wiley) or a Nature article page as that DOI, and a fetched document's first window as the DOI printed in its opening 3,000 characters. With those identities, 35 of the 1,153 (3.0%) remain `misattributed`. Fourteen are PDFs on a publisher's file server that never print their DOI where the check can see it, and are probably the cited paper. The rest are different documents: a preprint quoted as its published article, a paper quoted as a different paper from the same journal, documentation quoted as the paper it describes, and ScienceDirect pages cited by DOI, whose addresses carry an internal ID. A misattributed quote no longer makes a statement `read`, and the run lists it for review. Stored runs keep their version-5 marks; only new runs use the new check.

## 2026-09-27 Live check of scout-v6 (study smoke-v6-task8)

On 27 September 2026 one standard and one deep run on drb2-task8 checked scout-v6 (quote attribution, answer support, coverage items, parallel deep dives) with real models before any replicated study. The study runner crashed after the first run's grade: its pattern for the grade line kept the sentence's full stop in the cost, and a broad `except ValueError` reported that as a refusal (fixed in the next commit). The deep run was then run on its own under the same study name.

| Arm | Run | Status, answer | Rubric | Cost | Time | Expected databases found | Quotes verified / misattributed / not found |
|---|---|---|---|---|---|---|---|
| standard | `10bb8779` | complete, weak | 22/52 (grade `11abf371`) | $0.37 + $0.04 grade | 514 s | 4/7 (ICSD now among them) | 64 / 10 / 3 |
| deep | `56c4b4dd` | complete, weak | 31/52 | $0.46 + grade | 663 s | 5/7 (the first run to establish CSD) | 56 / 0 / 3 |

The planner wrote sensible coverage items, including "experimental crystal-structure or measured-property databases", the category every earlier run missed, and the deep run's gap analysis sent all three deep dives after open database items (CSD, SuperCon, NIMS MatNavi). The deep run's 31 of 52 is the highest score on this case so far, but it is one run per arm, so it is a direction for the replicated study, not a result. The check spent $0.93.

It exposed four defects. All ten of the standard run's misattributed quotes are one case: the publisher's page refused the fetcher, the scout read the engrXiv preprint, and cited the published article's DOI. The planner named its dimension items d1 to d4, the IDs code also gave open items, so open items showed as covered (fixed in 5cf86fc). Open-item IDs were numbered in question order, so a deep dive's results under an earlier question renumber later items and a dive's tag can point at the wrong one. And a deep dive can establish its target, as the deep run did for CSD, without tagging it, so the item stays open. These are the motivation for the bug-finding harness described below, and their fixes go into scout-v7.

## 2026-09-27 The bug-finding harness and its first cheap check

The paid runs of 26 and 27 September 2026 kept finding bugs instead of measuring research, so a harness now looks for that class of bug for free before a study pays (README, "Finding bugs before paying"). While it was being built it found:

- Open-item IDs changed meaning when a deep dive added results under an earlier question, found by `research fuzz` on three seeds and shrunk by a property test to one open item. IDs now come from the item's name (72996e7).
- A database behind on migrations ended every command in a traceback (a dry study).
- The study budget guard priced models itself and refused every call of the harness's models (a dry study).
- The study runner treated an unread grade line as a failed grade, so a parser bug could hide.
- A Wayback copy of a URL ending in `/.` keyed differently from the original, since trailing punctuation was trimmed twice (a property test).

To check that the harness finds real bugs, each earlier bug was reintroduced on a scratch copy and the harness run against it. It caught the grade-line parser (property test and dry study), colliding open-item IDs (fuzz), unstable open-item IDs (fuzz and property test), lone-surrogate PDF text (fuzz), an unexpected scout error failing the run (fuzz), a quote verified against another source's text (fuzz and property test), and the blanket `ValueError` catch together with the parser bug (dry study). Two gaps closed on the way: the surrogate bug was missed until the fuzz model encoded each request as a provider client does and every world held a surrogate PDF, and the unstable-ID bug was missed by one seed batch until fuzzed deep dives named new items under earlier questions. On the final code, 500 fuzz runs and a three-seed dry study of `studies/deep-vs-standard-task8.toml` keep every invariant.

The first cheap check (`research study run studies/deep-vs-standard-task8.toml --cheap`, study `deep-vs-standard-task8-cheap`, 27 September 2026) ran one standard and one deep run on drb2-task8 with every role on `openai:gpt-6-luna@low`: runs `77f51d9e` ($0.03, 2.0 minutes) and `0d6feefb` ($0.05, 5.2 minutes), $0.08 in all with no invariant violations and no tracebacks. Their grades come from the cheap judge and are not comparable with real ones. They did show a design problem: scouts put caveats and whole sentences in `open_items` ("No uncovered categories required by the stated early-2024 scope; ..."), each became a coverage item, and the reports neither addressed nor listed 10 to 13 of them, so every answer read as weak.

## 2026-09-27 Statements that rest on a summary (evidence version 7)

A review of the scout-v6 smoke runs on 27 September 2026 found that answer support measured whether a statement's source was read, not whether code had checked any of its words. A statement counted as `read` when a scout had fetched its source, even if the evidence was only the scout's own summary, with no quote. Nothing checks that such a summary matches its source. Evidence version 7 calls such statements `paraphrase`. That makes the answer `weak`, lists the statements for review, and adds a count of short quotes, meaning verified quotes under a quarter of their claim's words. The study summary gains a statement column: quoted, summary only, and thin. Quote checks themselves are unchanged. The fetch record of an unreached page now keeps the detail of a bare `ValueError`, so the size cap, an empty extraction, and an unread type are told apart.

The 16 stored reports were re-scored from their ledgers with no model call. Of 208 report statements, 178 rest on a verified quote, 18 only on a summary, 11 on thin evidence, and 1 on none. The deep smoke run `56c4b4dd` holds 11 of the 18 summary-only statements: 11 of its 30 statements, and it has 8 short quotes. Its two method-survey scouts quoted almost nothing (1 of 18 and 0 of 16 evidence items quoted), while its other scouts and deep dives quoted nearly everything. The scout prompt asks for a quote only "when a claim rests on specific wording", so conceptual summaries of reviews go unquoted. No stored run that was `supported` becomes `weak` under version 7, because every scout-v6 run was already weak on coverage. Among the older runs, `f1558521` (st07, Flash high) has 3 summary-only statements of 12.

The fetch failures recorded as `ValueError` in stored scout messages were mostly MDPI pages (118 empty extractions) and PDFs over the 5 MB cap (21 on arXiv and 8 on Nature). MDPI's CDN now refuses the fetcher and a browser user agent alike with a 403, so those pages need another copy of the paper. The size cap could be raised for PDFs.

## 2026-09-27 Checks of scout-v9 on st04, st05, and st07

Two study specs check scout-v9: `studies/v9-short-check.toml` (st04 and st05, two runs each) and `studies/v9-st07-check.toml` (one diagnostic run of st07). Both ran `--dry` with no invariant violations, over three seeds for the first, and then `--cheap`, with every role on `openai:gpt-6-luna@low`. The cheap judge's grades are not comparable with real ones.

| Study | Run | Status, answer | Quotes verified / misattributed / not found | Statements quoted / summary only / thin | Cost with grade |
|---|---|---|---|---|---|
| v9-short-check-cheap (st04) | `3f5ed912` | complete, weak | 1 / 0 / 1 | 1 / 0 / 1 | $0.0062 |
| v9-st07-check-cheap (st07) | `f7c7e94e` | complete, supported | 14 / 0 / 0 | 6 / 0 / 0 | $0.0115 |

Together they cost $0.018 and showed no invariant violations. OpenAI's two SWE-bench Verified pages refused the fetcher with 403. 
The paid runs followed on the same day, with the default models and grading by the version-2 judge. They cost $0.55 in total, grades included, with no tracebacks or invariant violations.

| Case | Run | Status, answer | Rubric | Quotes verified / misattributed / not found (short) | Statements quoted / summary only / thin | Cost | Time | scout-v1 Luna for comparison |
|---|---|---|---|---|---|---|---|---|
| st04 r1 | `f94412fc` | complete, supported | 4/4 | 2 / 0 / 0 | 2 / 0 / 0 | $0.045 | 65 s | 4/4, $0.029, 56 s |
| st04 r2 | `804ab265` | complete, weak | 4/4 | 3 / 0 / 0 | 2 / 0 / 0 | $0.050 | 70 s | |
| st05 r1 | `72131d69` | complete, supported | 11/11 | 11 / 0 / 0 | 9 / 0 / 0 | $0.109 | 254 s | 11/11, $0.078, 80 s |
| st05 r2 | `61cc0645` | complete, supported | 10/11 | 14 / 0 / 1 | 9 / 0 / 0 | $0.121 | 191 s | |
| st07 r1 | `7a7fc5b5` | partial, weak | 2/9 | 31 / 0 / 3 (1) | 12 / 0 / 0 | $0.218 | 349 s | 2/9 partial, $0.154, 130 s |

The short cases did not regress on their rubrics. The one lost point is st05's Chinchilla item. The rubric wants the report to say that the paper gives no context length; the report said this could not be established, because the scout's quote for the absence was not found in the source. That is a cautious answer, not a wrong one. st04 replicate 2 is `weak` only because the planner listed three video subtasks as coverage items that research did not establish.

No report statement in the five runs rests on a summary alone: all 34 carry a verified quote, against 11 of 30 summary-only statements in the v6 deep run. That is what the scout-v9 prompt asked for. Whether each quote supports its claim is still unchecked.

The short cases now cost 50 to 70 percent more and take longer than under scout-v1: st05 took 191 to 254 seconds against 80. That follows from the scout-v3 budgets rather than from quoting, but it is a real cost to quick questions.

st07 lost the question that decides it. The q2 scout, which researched contamination and flawed tests, read 8 pages in 6 requests, then its next model request failed with `SSLError: SSLV3_ALERT_BAD_RECORD_MAC`, a transient TLS fault. A network error ends a call without a retry, so the question returned nothing, and the report again omits the contamination evidence and the maintainer review, as scout-v1 Luna's did after a 429. Seven DuckDuckGo searches also timed out. The grade therefore measures that loss, not scout-v9.

## 2026-09-27 Support audit of every stored report (audit version 1)

`research audit` (commit a7fdda7) asked `zai:glm-5.3` at high effort, a vendor no run uses, whether the verified quotes behind each report statement say what the statement says. All 23 stored reports were audited in one batch under a $2.00 cap, and the batch cost $0.26. The verdicts are in the `support_audits` table.

| Runs | Statements | Supported | Partial | Unsupported | No verified quote |
|---|---|---|---|---|---|
| scout-v1, production models (12 reports) | 132 | 66 | 53 | 4 | 9 |
| scout-v6, production models (2) | 60 | 29 | 18 | 0 | 13 |
| scout-v9, production models (5) | 34 | 29 | 5 | 0 | 0 |
| cheap checks, Luna low (4) | 24 | 10 | 12 | 1 | 1 |

A hand check of all 5 `unsupported` verdicts and a random 12 of the 76 `partial` verdicts in production runs found the following:

- **Unsupported verdicts.** All 5 are right. Four are statements that go beyond their quotes; the fifth is a report describing its own scope.
- **Partial verdicts, real overreach (6 of 12).** The statement adds something the quotes do not state: that the Chinchilla paper gives no context length, that OpenHands with Claude 3.7 Sonnet was the system behind a figure, "since 2022", two challenges missing from the quote, that the dataset is static, and that saturation is not established.
- **Partial verdicts, audit artefacts (4 of 12).** They come from sending only each source's title: author names, dates, and URLs that came from a source's record look unsupported.
- **Partial verdicts, other (2 of 12).** One statement had already said its figure rested on an unverified quote, and one is minor framing.

Overreach enters at both steps. Usually the scout's claim already says more than its quote, drawing on the rest of a page it read or on its own inference; claims that something is absent from a source, such as Chinchilla's context length, are a recurring case that a quote can never establish. Once, the synthesizer added a specific system and model name that neither the claim nor the quote contains.

Taking the sample's rate, roughly one in five quoted statements in production runs says more than its quotes. The scout-v9 reports did best (29 of 34 supported), but they are the three easiest cases, so this is not yet a comparison of versions. Audit version 2 should send each source's URL, date, and authors, so that record details stop counting against a statement.

## 2026-09-27 A second rubric judge

Every report with a `gpt-6-sol` high-effort grade (18 reports) was graded again by `zai:glm-5.3` at high effort, with the same version-2 judge prompt (`RESEARCH_MODELS__JUDGE=zai:glm-5.3@high research grade`, $0.30 cap per grade). The 18 grades cost $0.27, and all of them are in `grades` under their judge model.

| Case | Reports | Sol points | GLM points | Points where the judges disagree |
|---|---|---|---|---|
| st04 | 5 | 19/20 | 19/20 | 0 |
| st05 | 4 | 43/44 | 43/44 | 0 |
| st07 | 3 | 10/27 | 13/27 | 3 |
| drb2-task8 | 5 | 118/260 | 131/260 | 15 |
| drb2-task68-plus | 1 | 14/54 | 22/54 | 10 |

The judges agree on 375 of 405 points (92.6%). Of the 30 disagreements, GLM credits 27 points that Sol does not, and Sol credits 3 that GLM does not. On the short cases they agree exactly. On the DRB-II cases GLM scores each report 1 to 8 points higher. Both judges rank the drb2-task8 reports almost identically: the deep run `56c4b4dd` is first under both, and only two reports two points apart swap places.

A hand check of the three st07 disagreements on `7a7fc5b5` favours GLM on two. The rubric point "human annotators screened instances for underspecified issues and unfair or overly specific tests" is stated in the report in other words ("a clear problem statement, a correct test patch, and solvability"). The point "documents contamination or memorization concerns" is met by a quoted SWE-rebench finding that scores "might be inflated due to contamination issues". This matches the earlier NOMAD verdict: Sol does not always credit equivalent wording.

The choice of judge therefore moves a DRB-II score by up to 8 points, as much as the run-to-run variation the studies try to see past, while leaving short cases and the order of reports mostly unchanged. A decision that rests on a few rubric points should use both judges and have their disagreements checked by hand.

## 2026-09-27 Exa search: dry and cheap checks

`RESEARCH_SEARCH_ENGINE=exa` (commit 05d5c69) was checked with `studies/exa-search-check.toml`, which has a DuckDuckGo arm and an Exa arm on st04. The dry check passed, as did a three-seed dry study of both short cases with the Exa arm. The cheap check (every role on `gpt-6-luna@low`) cost $0.02, with no invariant violations:

| Arm | Run | Status, answer | Searches | Search cost | Run cost | Time | Cheap judge |
|---|---|---|---|---|---|---|---|
| DuckDuckGo | `37a0a63d` | complete, weak | 6 (3 cached as results) | free | $0.006 | 45 s | 3/4 |
| Exa | `3aa8dde9` | complete, supported | 2 | $0.014 | $0.017 | 20 s | 4/4 |

Exa charged the listed $0.007 a search, and the run recorded exactly that as `search_usd`. Its first search returned ten results, the first of them the official ILSVRC 2016 results page with a highlight from its results table. On this cheap run the searches cost more than the models; with the production models they would be a smaller share. One run per arm on the easiest case says nothing yet about quality. A paid comparison needs more cases, replicates, and a decision rule set before it runs.

## 2026-09-27 Exa against DuckDuckGo: dry and cheap checks of the comparison specs

The comparison is split into three specs (`studies/exa-vs-duckduckgo-{short,st07,task8}.toml`), so each can have a ceiling fitted to its runs, and their shared header fixes the decision rule. All three passed `--dry --seeds 2`. The cheap checks, with every role on `gpt-6-luna@low`, cost $0.39 in all, with no invariant violations. A cheap run's hard cap is now the $0.25 cheap ceiling (commit after 7dd3ad1), because paid searches are not cheap; the task8 Exa run reached it and had one question refused, as designed.

| Case | Arm | Run | Status, answer | Search cost | Run cost | Time |
|---|---|---|---|---|---|---|
| st04 | DuckDuckGo | `e47e39a3` | complete, supported | | $0.002 | 17 s |
| st04 | Exa | `ee0c71d8` | complete, supported | $0.007 | $0.010 | 16 s |
| st07 | DuckDuckGo | `43a40c62` | complete, supported | | $0.014 | 56 s |
| st07 | Exa | `6a16f441` | complete, supported | $0.077 | $0.115 | 277 s |
| drb2-task8 (deep) | DuckDuckGo | `bbe0b73a` | complete, weak | | $0.045 | 303 s |
| drb2-task8 (deep) | Exa | `6b504ddd` | partial, weak (cheap cap) | $0.154 | $0.198 | 335 s |

Exa's own requests are quick: 8 to 15 seconds of tool time per scout, as with DuckDuckGo. Its highlights are not snippets, though. The median result carried 3,900 to 6,400 characters against DuckDuckGo's 210 to 230, and one search returned 49,000 to 61,000 characters against about 2,500. Every later request of a scout resends them, so the Exa scouts on st07 sent 119,000 to 336,000 input tokens each against 44,000 to 63,000. That made them wait under Luna's token rate limit and cost about 2.7 times as much in model calls. Exa's text also arrives labeled `snippet`, so evidence resting on it counts as shallow unless the scout fetches the page. A paid comparison with uncapped highlights would mostly measure this context growth.

## 2026-09-27 Fetch bake-off on the pages our fetcher failed on

`scripts/fetch_bakeoff.py` retried the 160 distinct URLs our fetcher failed on in production runs, with no model calls. The free candidates together read 83 of the 160. Jina Reader, used without a key, read 52, including MDPI 20 of 26 and OpenAI 5 of 5, but hit a challenge page on all 26 RSC pages. The open-access route, through OpenAlex and Europe PMC, read 38 and recovered the most cited quotes (39 of 79). Our own fetcher now reads 19, all 11 arXiv PDFs among them, since the 25 MB cap. RSC, ScienceDirect, OQMD, De Gruyter, and Materials Project stay mostly unread. The paid candidates ran the same day. Exa `/contents` cost $0.097; Tavily used 38 of its free credits, and Firecrawl 134, with basic proxies only (no stealth or residential proxies).

| Candidate | Pages read | Cited quotes recovered | Median seconds | Cost |
|---|---|---|---|---|
| Firecrawl scrape | 117 of 160 (73%) | 65 of 79 | 3.5 | 134 credits (1 a page) |
| Exa `/contents` | 86 (54%) | 57 | 0.7 | $0.097 |
| Jina Reader | 52 (32%) | 25 | 6.3 | free |
| Open access | 38 (24%) | 39 | 0.5 | free |
| Tavily Extract | 35 (22%) | 42 | 0.5 | 38 credits |
| Our fetcher, again | 19 (12%) | 31 | 0.3 | free |

By host, Firecrawl read MDPI 23 of 26, RSC 14 of 26, ScienceDirect 13 of 13, ACS 5 of 5, and ACM 4 of 5. Exa read DOI redirects 9 of 12, De Gruyter 3 of 3, and all of OpenAI and arXiv. Nothing read Materials Project, and little read OQMD. Three of Firecrawl's thirteen ScienceDirect reads were 1,600 to 4,500 characters long and are probably abstract pages; the rest ran 17,000 to 31,000.

Layered in order, the free steps first, the chain reads more than any single service:

| Chain | Pages read | Cited quotes recovered |
|---|---|---|
| Our fetcher, then open access | 46 | 41 |
| ... then Exa `/contents` | 97 | 63 |
| ... then Firecrawl | 125 | 67 |
| ... then Exa, then Firecrawl | 139 | 68 |

Adding Jina before the paid services reads one page more but recovers fewer quotes, since its conversion changes wording. The chain of our fetcher, open access, Exa `/contents`, and Firecrawl is the candidate for the reading fallback. A success here means text that is long enough, free of challenge markers, and carries the page's title; a paid comparison of whole runs has to show that it improves reports.

## 2026-09-27 Reading fallback: dry and cheap checks

The reading fallback (commit cde6465, `RESEARCH_READ_FALLBACK=oa,exa,firecrawl`) is compared with our fetcher alone in three specs, `studies/reading-fallback-{short,st07,task8}.toml`, whose shared header fixes the decision rule. All three passed `--dry --seeds 2`. The cheap checks, with every role on `gpt-6-luna@low` against the real web, Exa, and Firecrawl, cost $0.17 in all, with no invariant violations or tracebacks. They ran under the standing budget for small paid steps. One run per arm with cheap models is a screen: its grades come from the cheap judge and are not comparable with real ones, and it cannot change a default. The paid comparison is on hold at the user's request.

| Case | Arm | Run | Status, answer | Pages failed | Read by the fallback | Paid reads | Run cost | Time |
|---|---|---|---|---|---|---|---|---|
| st04 | own | `06c13142` | complete, supported | 0 | | | $0.006 | 29 s |
| st04 | fallback | `c82353ad` | complete, weak | 0 | none needed | | $0.002 | 21 s |
| st07 | own | `321ffb47` | complete, supported | 12 | | | $0.028 | 220 s |
| st07 | fallback | `3bfedf80` | complete, supported | 0 | 3 (Exa) | $0.002 | $0.018 | 99 s |
| drb2-task8 (deep) | own | `7cf6aaa5` | complete, weak | 10 | | | $0.036 | 259 s |
| drb2-task8 (deep) | fallback | `ca416113` | complete, weak | 1 | 7 (Exa 4, Europe PMC 2, Firecrawl 1) | $0.014 | $0.072 | 339 s |

The fallback did what it is for: on the two cases where our fetcher failed pages, it read them, with 12 and 10 failures falling to 0 and 1, for $0.002 and $0.014 of paid reads. On st04 our fetcher read everything, so the fallback never ran. Whether reading those pages makes reports better is the paid comparison's question.

## 2026-09-27 Architectural audit: blocked sources and the study ceiling

An outside review by gpt-6-astra of commit 1f9233a is kept as [architectural-audit-2026-09-27.md](architectural-audit-2026-09-27.md). It lists eleven findings, F01 to F11, each with an offline counterexample. All 15 of its counterexamples still reproduced on 41f7600. An addendum by the same reviewer, [architectural-audit-addendum-2026-09-27.md](architectural-audit-addendum-2026-09-27.md), adds C01 to C04, and all four of its counterexamples reproduced on this branch. This entry covers the findings fixed so far: F05, F06, and C01. The others are still open.

**Blocked expert reports reached scouts through paths the block list did not cover (F06).** The web search and fetch tools matched a case's blocked list by address only. `scholar_search` and `scholar_get` did not apply it at all, and the scouts' evidence check looked only at a source's URL. A free scan of the stored runs found three paths:
- **Scholarly records.** drb2-task8's blocked review (DOI 10.32604/cmc.2025.060109) was returned by a scholarly tool five times, in scout-research-v1 and v3 runs. drb2-task68-plus's blocked review was returned by `scholar_search` 42 times in scout-v1 and scout-research-v1 to v3 runs. Scouts then searched for it by its title, but no ledger cites it.
- **A copy at another address.** The drb2-task8 review was read in full five times from `cdn.techscience.cn`, whose address names neither a blocked entry nor the DOI. The last time was in scout-research-v3.
- **A copy whose address carries the DOI.** `sciopen.com/article/10.32604/cmc.2025.060109` appeared ten times in web search results, four of them in a scout-followup-v9 run.

Five ledgers cite the drb2-task8 review by its DOI:
- 9d2ac887, 5901c846, and fb9d1fce: scout-research-v1, study drb2-task8-scouts;
- f157f38f: scout-followup-v1, study drb2-pilot;
- 6337ed01: scout-research-v3, study scout-v3-screen.

No ledger at scout-v6 or later cites it. So the drb2-task8 coverage figures of the fixed-plan scout comparison (25 September) and the scout-v3 screen (26 September) may be slightly inflated. This scan did not measure by how much. The blocked reports of drb2-task82, drb2-task59, and drb2-task78 were not found in any tool result.

What changed, in fetch version 12:
- A blocked source is matched by its address, and also by the DOI or arXiv ID that a blocked address names.
- A fetched document whose first page prints a blocked DOI is refused, including a copy already in a study's cache.
- The scholarly tools refuse a blocked lookup and leave blocked records out.
- A scout's evidence that cites a blocked work by DOI alone is refused.

Replayed over the stored tool results of the 37 runs with a blocked list, the new policy catches all 47 scholarly records, the five full reads from the CDN, and the ten sciopen results. A copy that carries neither the address nor the DOI, anywhere in its first page, is still not recognized. The blocked lists of drb2-task68-plus, drb2-task82, and drb2-task78 name no DOI, so for them only the addresses protect. The 42 drb2-task68-plus records are caught because their landing page is the blocked MDPI address.

**The study ceiling was checked against estimates (F05).** The runner started a run whenever spend so far plus the run's estimates fit under the ceiling, and then gave the run its full `cap_usd`. A run that cost more than its estimate could therefore take a study past its ceiling. The audit's case was two $0.75 runs under a $1.00 ceiling. A grade or audit that failed, and a run that wrote no record, counted as free. Now each run, grade, and audit gets a hard cap no larger than what remains of the ceiling, rounded down to the cent, and a step whose cost cannot be read counts its whole cap.

**A request's reservation missed long-context output prices (C01).** The budget guard priced a request's output as if its input were short. GPT-6 Luna and Sol, the default scout, planner, and judge, charge 1.5 times as much for output once the input passes 272,000 tokens, and so do Gemini Pro models. So a long request could settle above its reservation, and a run could pass `--max-usd`. No stored request has reached that tier; the largest had 108,723 input tokens. The reservation is now never less than the price of one request at both the input and output bounds (`usage-anchor-v6`). Below the tier it is unchanged.

**Checks, all free.** 100 fuzz runs and the full offline suite passed. A two-seed dry run of `studies/reading-fallback-task8.toml` on the new code (runs 6c07eb9b, c490646f, 6fb53ecb, and 3f2e2126) had no invariant violations. One of its fake audits failed, and the summary counted that audit's whole $0.30 cap as spent. The dry check now also flags a scholarly record from a blocked source. The reading-fallback specs need their cheap checks rerun before the paid comparison, because the code they run has changed.

## 2026-09-27 Reading fallback: cheap checks after the audit fixes

The audit fixes changed the code the reading-fallback specs run: fetch version 12, budget policy `usage-anchor-v6`, and the study runner's hard caps. So the three specs needed new dry and cheap checks. Each passed `--dry --seeds 2` with no invariant violations. Their ceilings equalled their worst case by the estimates, which would let the runner cap the last runs at their own estimates, so they were raised by about a quarter:
- short: $2.80 to $3.50;
- st07: $2.20 to $2.80;
- drb2-task8: $3.20 to $4.00.

The first cheap attempt failed. Its fallback arm refused to start, because `EXA_API_KEY` and `FIRECRAWL_API_KEY` were missing from `.env`; the earlier cheap checks had them only in the shell that ran them. By then the own arm had already run on short and st07, as runs `f56754c6` and `ae40efa3` with their cheap grades and audits. The drb2-task8 check was stopped during its own arm's run, `7b7d351c`, which stays marked `running` in the database. The attempt cost about $0.04. The runner now checks every arm's configuration, with its environment and code, before the first run, and refuses the study if an arm could not start.

With the keys in `.env`, the second attempt ran all three cheap checks, with every role on `gpt-6-luna@low`. It cost $0.12, with no invariant violations or tracebacks, so the two attempts cost $0.16 in all. As before, one run per arm with cheap models is a screen: its grades come from the cheap judge and cannot change a default.

| Case | Arm | Run | Status, answer | Pages failed | Read by the fallback | Paid reads | Run cost | Time |
|---|---|---|---|---|---|---|---|---|
| st04 | own | `491ac2fa` | complete, supported | 0 | | | $0.005 | 28 s |
| st04 | fallback | `87e05cab` | complete, weak | 0 | none needed | | $0.002 | 12 s |
| st07 | own | `bf4cfc67` | complete, supported | 11 | | | $0.017 | 167 s |
| st07 | fallback | `79581927` | complete, supported | 0 | 5 (Exa) | none | $0.019 | 57 s |
| drb2-task8 (deep) | own | `89e44ef9` | complete, weak | 6 | | | $0.033 | 165 s |
| drb2-task8 (deep) | fallback | `b1381c69` | complete, weak | 2 | 6 (Exa 5, Europe PMC 1) | $0.009 | $0.039 | 192 s |

The pattern matches the first cheap checks. The fallback arm read the pages our fetcher could not: on st07, 11 failures fell to 0; on drb2-task8, 6 fell to 2, a 404 and a site's home page. st07's fallback paid for nothing, because every page it read came from the study's reading cache (11 hits, no misses), written by the first cheap check. The paid study shares one cache between its arms in the same way.

## 2026-09-27 Deep example runs: throughput, trimmed history, and a second scout model

The aim was one worked example for the README: a deep run on a new question, with its report audited and checked by hand. The question was "What is the current clinical evidence that GLP-1 receptor agonists reduce alcohol consumption or alcohol use disorder, and which trials are still underway?", with the note "Keep randomized trials, observational studies, and animal work distinct, and give effect sizes and the populations studied." It is not a benchmark case and has no rubric, so no run on it is graded. Every run used `--depth deep` and the reading fallback (`oa,exa,firecrawl`). The planned budget was about $0.95, with a $2.50 hard cap on the deep run. No example was published, because neither real run finished.

| Run | Kind | Version | Scouts | Status | Scouts that returned | Coverage established | Quotes verified | Scout tokens a minute | Cost | Time |
|---|---|---|---|---|---|---|---|---|---|---|
| `9e5d710d` | cheap | followup-v9 | Luna@low | complete | 4 of 4 | 7 of 9 | 65 of 71 | about 158,000 | $0.114 | 7.9 min |
| `d8c8198e` | real | followup-v9 | Luna@high | partial | 1 of 4 | 8 of 14 | 46 of 53 | about 111,000 | $0.469 | 12.1 min |
| `39964fd5` | cheap | followup-v10 | Luna@low | complete | 4 of 4 | 7 of 8 | 54 of 55 | about 119,000 | $0.062 | 6.5 min |
| `2c66e8bd` | real | followup-v10 | Luna@xhigh | partial | 1 of 7 | 8 of 20 | 40 of 47 | about 123,000 | $0.603 | 26.9 min |
| `6d29289d` | cheap | followup-v11 | Luna@low and GLM-5.3-flash@low | complete | 4 of 4 | 7 of 14 | 46 of 67 | | $0.163 | 10.7 min |
| `96037bcf` | cheap | followup-v11, size-based trimming | Luna@low and GLM-5.3-flash@low | complete | 4 of 4 | 8 of 12 | 36 of 53 | | $0.172 | 9.6 min |

The real runs used the Sol@high planner and gap analyzer and the Opus 5.5@medium synthesizer. The cheap runs put every role on Luna@low. A smoke test of `openai:gpt-6-luna@xhigh` cost $0.004. In all, the day cost $1.59: $1.07 for the two real runs and $0.52 for the cheap checks and the smoke test. Scout tokens a minute are the scouts' billed input divided by the longest scout's time, so they are approximate.

**The first real run (`d8c8198e`)** planned four questions. Three of its scouts were cancelled at the 480-second research deadline. They had spent $0.010 to $0.012 of their $0.175 shares, and a scout cut off at the deadline keeps no claims. So the animal-evidence question returned nothing, and the observational question had nothing until a deep dive. Opus synthesis cost $0.27 of the run's $0.47. The run also exposed two defects:
- `web_fetch` refused JSON, so three ClinicalTrials.gov API queries failed as an unsupported content type;
- `RESEARCH_LIMITS__DEEP__MAX_QUESTIONS` on its own replaced the whole deep tier, which turned the gap follow-up off.

Both were fixed with unit tests in `scout-followup-v10` (commit 2252ec4). That commit also gave deep runs 20 minutes of research, 32 minutes in all, 48 productive calls a scout, and deep dives of 8 minutes with a scout's full loop budget. The cheap check `39964fd5` confirmed JSON reads from ClinicalTrials.gov.

**The second real run (`2c66e8bd`)** used the longer limits with Luna@xhigh scouts, at the user's request. The planner chose seven questions. At 20 minutes, five scouts were cancelled at the deadline, one failed when a single request passed the 120-second timeout, and one finished. Each scout had managed only six to nine requests. So the deadline was never the constraint. Both real runs sent about the same number of tokens a minute through Luna's limit of 200,000, whether four scouts had 8 minutes or seven had 20. Fetched page text was 2.6 of the 4.0 million characters in the scouts' tool results (63%), and every request resends it. The log's `h2 connection driver error: connection reset` lines come from `primp`, the DuckDuckGo client, and did no harm.

**What changed after it** (commits 6c9043c and effea01):
- **Trimming (`scout-v10`).** A scout's request keeps the newest 48,000 characters of page text, and always the pages of its latest two responses. Older pages become a stub asking the scout to fetch again before quoting; the re-read is free. The stored history, which the quote check reads, keeps every page.
- **A second scout model (`scout-followup-v11`).** `RESEARCH_MODELS__SCOUT_ALT` gives every other scout and deep dive of a deep run a second model with its own pacer, and deep runs get $3.00. Dry and cheap studies now replace the second model too; before that fix, a cheap check could have reached real GLM-5.3@xhigh scouts.

The first version of trimming stubbed every page two responses after it was read. In the cheap check `6d29289d`, scouts then got 11 to 18 requests each, against 4 to 9 before. But Luna's questions verified only 18 of 27 quotes: the scouts that lost most never re-read a page, and one had only four requests, where trimming saved little. With trimming based on size (`96037bcf`), Luna's questions verified 24 of 25. The GLM-5.3-flash@low questions verified 28 of 40 and then 12 of 28. That stand-in says nothing about GLM-5.3@xhigh, the second model these changes are meant for.

**Other fixes.** A study check stopped with SIGTERM earlier on 27 September had left run `7b7d351c` and one of its scouts marked `running`. `research db reconcile --older-than 60 --apply` marked them failed (Abandoned). The CLI now stops on SIGTERM as it does on Ctrl-C, so a stopped run records itself as cancelled (3788d3d). A subprocess test checks this, and fails without the fix.

**What it showed, and what it did not.** Deep runs on Luna are limited by the account's token rate, not by their deadline, their money, or the scouts' effort. More time or more questions spread the same throughput more thinly. The cheap checks show that trimming and the second model run end to end. They cannot show that either improves reports: they run cheap stand-in models, finish because low effort is fast, and are one run each. None of today's changes has been graded. A replicated, graded comparison on a frozen case, with its decision rule written first, should decide whether the deep changes stay. Everything is on PR #39.

## 2026-09-28 Deep against standard on drb2-task8 (scout-v10, followup-v11)

`studies/deep-vs-standard-task8.toml` asked whether a deep run is a large improvement over a standard one on the current code. No DeepResearch Bench II case had had a production run since scout-v6, when the smoke check `smoke-v6-task8` ran one of each. Its decision rule, written before any paid run, required all of these:
- a mean rubric score at least 5 of 52 points above standard's under both judges;
- a supported-audit share no more than 5 points below standard's, and no failed deep run;
- deep at $1.00 a run or less.

The spec's dry check (two seeds) and cheap check (runs `0cd21cbc` and `7efab7f1`, $0.08) were clean. The study ran six runs in alternating arm order, each graded by `gpt-6-sol@high` and audited by `zai:glm-5.3@high`. It cost $3.28 of its $5.00 ceiling. The six reports were then regraded by `zai:glm-5.3@high` for $0.23. The first attempt at that, capped at $0.10 a grade, was refused before dispatch, because the guard's reservation for one grade is about $0.20.

| Arm | Rep | Run | Sol | GLM | Quotes verified | Audit supported / partial / unsupported / no quote | Coverage | Cost | Time |
|---|---|---|---|---|---|---|---|---|---|
| standard | 1 | `27701a2f` | 24 | 26 | 56 of 60 | 24 / 4 / 0 / 0 | 4/7 | $0.421 | 464 s |
| deep | 1 | `3dfd1f92` | 26 | 36 | 90 of 106 | 28 / 3 / 0 / 0 | 5/7 | $0.730 | 1,150 s |
| deep | 2 | `fdd47ad4` | 24 | 26 | 101 of 109 | 21 / 5 / 0 / 0 | 5/7 | $0.687 | 964 s |
| standard | 2 | `8f753940` | 16 | 24 | 58 of 59 | 18 / 7 / 0 / 0 | 3/7 | $0.350 | 496 s |
| standard | 3 | `aea52be0` | 17 | 26 | 62 of 69 | 12 / 5 / 1 / 0 | 4/7 | $0.422 | 454 s |
| deep | 3 | `307f5e4d` | 22 | 28 | 80 of 86 | 39 / 2 / 0 / 1 | 5/7 | $0.669 | 1,152 s |

| Arm | Sol mean | GLM mean | Audit supported | Mean cost | Mean time |
|---|---|---|---|---|---|
| standard | 19.0 | 25.3 | 54 of 71 statements (76%) | $0.40 | 7.9 min |
| deep | 24.0 | 30.0 | 88 of 99 statements (89%) | $0.70 | 18.0 min |

**Decision: undecided.** Deep scored 5.0 points more under Sol and 4.7 under GLM. The rule needs 5 under both, so the score condition is not met, and no disagreement was checked by hand because none could change that. The other two conditions held. Deep's reports rested on quotes more often, with 89% of statements supported against 76%. Every deep run completed, and deep averaged $0.70.

Deep was ahead under both judges in every pairing but one: GLM's fdd47ad4 at 26 against standard's 24 and 26. Standard's Sol scores spread from 16 to 24, so three replicates cannot place a difference this size reliably. Deep costs 1.75 times as much and takes 2.3 times as long.

**Against v6.** Judged the same way, the v6 smoke runs scored 22 (Sol) and 27 (GLM) for standard, and 31 and 33 for deep. Standard's are inside today's ranges. The single v6 deep run is above all three deep runs under Sol, and inside the range under GLM. One run per arm cannot show whether v7 to v11 changed scores.

The GLP-1 example waits on this. With deep neither adopted nor rejected, the next step is either more replicates or a held-out case, with a rule written first.

## 2026-09-28 Where drb2-task8's rubric points are lost

This was a free audit of the six study runs above: 12 grades, each run graded by both judges. It uses the stored grades, ledgers, plans, and reports, with no model calls. Its aim was to find what would raise rubric scores before designing the next test.

**Which points fail.** Of 52 points, 17 were met in 9 or more of the 12 grades, 10 in 5 to 8, 14 in 1 to 4, and 11 in none. The failing points fall into three groups:
- **Category-level method statements, about 14 points.** Examples: RL, MCTS, and PSO as exploration-based algorithms; each category's advantages and disadvantages, such as "high computational cost, slow convergence"; and what GANs, RL, and topology optimization each do.
- **Database members that sources named but that were never established, about 4 points.** CSD, and NOMAD's URL.
- **Items probably found only in the blocked expert report, about 6 points.** The Dynamic Database of Solid-State Electrolytes, OQMD's "1.2 million structures", and ASM's description and URL. No ledger mentioned the first two. These are a ceiling.

**What the audit ruled out.**
- *The judge's view.* `evals.reader_text` gives the judge the whole answer, key statements, caveats, and cited sources, leaving out only the executive summary, with no truncation.
- *Synthesis dropping claims.* The reports cite 90 to 100% of their ledgers' claims.
- *The plan.* Every run split the task into its natural four questions: one per method category, and one for databases. Its coverage items name the categories and dimensions, such as algorithms, advantages, and disadvantages for each category.

**Where the points go.** They are lost before synthesis, in what scouts turn into claims.
- "Computational cost" appears in five of six ledgers, but mostly only in a quote's evidence text: the claim built on it is narrower than its source.
- CSD appears in open items and `unresolved`. Sources named it, and no claim established it.
- For the exploration-based question, each run's claims cite only 2 to 6 sources. Most are single-method papers, such as "Deep Reinforcement Learning for Inverse Inorganic Materials Design". At most two are reviews, and some reviews were read only as abstracts. The scout prompt already asks for surveys first.
- Coverage treats a dimension such as "main disadvantages of each strategy" as one item, met by any claim. So it can read as covered while two of the three categories have no disadvantage at all.

**The judges.** They disagreed on 45 of 312 verdicts (14%), and GLM gave the credit in 41 of them. The disputes cluster on database descriptions and URLs. Reports gave `https://oqmd.org/`, `https://materialsproject.org/`, and `https://nomad-lab.eu/nomad-lab/index.html`, while the rubric lists `https://www.oqmd.org/`, `https://next-gen.materialsproject.org/`, and `https://nomad-lab.eu/nomad`. Sol does not credit equivalent addresses. That is measurement noise, not research quality. Any change to it would need a new judge version.

**Another case.** drb2-task68-plus, cloud auto-scaling, has only two graded runs, both scout-v1. On it, 31 of 54 points were met in at most a third of grades. They have the same shape: techniques under each category ("under proactive methods, identify reinforcement learning"), category-level explanations, and about 13 points that require citing specific papers, which is largely a ceiling there.

**What follows.** Three changes should reach the failing points without reaching into the blocked source:
- coverage items that cross each category with each requested dimension;
- scouts that read at least one review in full for a category question, and state its category-level characterizations as quoted claims;
- scouts that give every set member a source names its own claim, or mark it not established.

They should be tested on more than one development case at standard depth, with the decision rule written first. If they work, earlier model comparisons judged under the old claim behavior should be looked at again.

## 2026-09-28 The first free diagnosis of the deep-vs-standard study

`research diagnose` (diagnose version 1, PR #41) was first run with `--free` on the six runs of the deep-vs-standard study. With `--free` it makes no calls, so only the report grades already stored were read, and the stage columns stay ungraded until the claims and research views are graded (about $0.50 to $0.80, held for now). Its checks of the score itself reproduced the hand audit:
- **Judges.** Sol and GLM differ on 45 of 312 verdicts (14%). GLM alone credits 41 of them, and the most disputed points are database descriptions and URLs.
- **Equivalent URLs.** 5 missed URL points name a site the report gives at another address.
- **Never met.** 7 of 52 points were never met in any of the 32 stored grades of drb2-task8: DDSE (3 points), ASM Alloy Center (3), and model-based design's advantages (1).

**The lesson.** With three runs per arm, the smallest difference the study could detect at 80% power is about 7.8 points under Sol and 8.8 under GLM. So its 5-point rule could not have been met reliably, and the study was bound to come out undecided. It would have taken about 12 runs per arm on this one case to see 4 points.

The next study should rest its decision on measures that vary less, such as the count of points seen but never claimed, and on more than one development case, with its detectable difference computed before it is paid for.

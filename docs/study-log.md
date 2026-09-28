# Study log

Every study, paid run, and offline re-scoring since the Scout refactor is indexed here, in date order, with its run IDs, costs, result, and decision. Entries through 27 September 2026 are in the [dated archive](archive/study-log-2026-09-25-to-27.md); entries from 28 September onward follow this index. How quality is measured, and how a comparison is designed so that it can decide, is in [evaluation.md](evaluation.md). What the first design learned is in [lessons.md](lessons.md).

Run IDs are the first eight characters of the run's UUID unless given in full. Costs are what providers charged, as recorded in Postgres. Each paid step was approved in advance with an estimate and a hard cap.

| Date | Entry | Spend | Outcome |
|---|---|---|---|
| 09-25 | [Scout's first live screen](archive/study-log-2026-09-25-to-27.md#2026-09-25-scouts-first-live-screen-flash-and-luna-scouts-on-st04-st05-and-st07) | about $0.71 | Luna promising on the short cases; st07's Luna run lost a question to a 429, and a retry crashed in a native parser |
| 09-25 | [First paid calibration of the quality judge](archive/study-log-2026-09-25-to-27.md#2026-09-25-first-paid-calibration-of-the-quality-judge) | $0.10 | The judge was within one level of the human marks; not yet a selection rule |
| 09-25 | [First drb2-task8 baseline, and the budget guard's corrections](archive/study-log-2026-09-25-to-27.md#2026-09-25-first-drb2-task8-baseline-and-the-budget-guards-corrections) | about $0.16 | Three runs stopped by the guard or a credential; guard fixed from byte-reserve-v1 to v3 |
| 09-25 | [Baseline retries, the first graded DRB-II reports, and follow-up runs](archive/study-log-2026-09-25-to-27.md#2026-09-25-baseline-retries-the-first-graded-drb-ii-reports-and-follow-up-runs) | about $1.20 | drb2-task8 20 to 24 of 52, drb2-task68-plus 14 of 54; research stops short of the expected set |
| 09-25 | [Fixed-plan scout comparison](archive/study-log-2026-09-25-to-27.md#2026-09-25-fixed-plan-scout-comparison-on-drb2-task8-luna-against-flash) | $0.32 | Luna stays the default scout; Flash at max does not fit the window |
| 09-26 | [Listing the set before confirming it (scout-v2)](archive/study-log-2026-09-25-to-27.md#2026-09-26-listing-the-set-before-confirming-it-scout-v2) | $0.42 | Undecided; v2 kept |
| 09-26 | [A larger research budget (scout-v3)](archive/study-log-2026-09-25-to-27.md#2026-09-26-a-larger-research-budget-scout-v3) | $0.30 | Broader lists at about 40% more research cost; v3 kept |
| 09-26 | [Quote attribution (evidence version 6)](archive/study-log-2026-09-25-to-27.md#2026-09-26-quote-attribution-evidence-version-6) | free | Misattributed quotes fell from 6.2% to 3.0% once copies of the same work count as it |
| 09-27 | [Live check of scout-v6](archive/study-log-2026-09-25-to-27.md#2026-09-27-live-check-of-scout-v6-study-smoke-v6-task8) | $0.93 | Deep run 31 of 52, the best so far; four defects fixed in scout-v7 |
| 09-27 | [The bug-finding harness](archive/study-log-2026-09-25-to-27.md#2026-09-27-the-bug-finding-harness-and-its-first-cheap-check) | $0.08 | Five bugs found while it was built; the cheap check found sentences in open items |
| 09-27 | [Statements that rest on a summary (evidence version 7)](archive/study-log-2026-09-25-to-27.md#2026-09-27-statements-that-rest-on-a-summary-evidence-version-7) | free | 18 of 208 report statements rested only on a scout's summary |
| 09-27 | [Checks of scout-v9](archive/study-log-2026-09-25-to-27.md#2026-09-27-checks-of-scout-v9-on-st04-st05-and-st07) | $0.57 | No short-case regression; st07 lost its decisive question to a TLS error |
| 09-27 | [Support audit (audit version 1)](archive/study-log-2026-09-25-to-27.md#2026-09-27-support-audit-of-every-stored-report-audit-version-1) | $0.26 | About one in five quoted statements says more than its quotes |
| 09-27 | [A second rubric judge](archive/study-log-2026-09-25-to-27.md#2026-09-27-a-second-rubric-judge) | $0.27 | 92.6% agreement; the judge moves DRB-II scores by up to 8 points |
| 09-27 | [Exa search checks](archive/study-log-2026-09-25-to-27.md#2026-09-27-exa-search-dry-and-cheap-checks) | $0.02 | Exa search works end to end, and records its cost exactly |
| 09-27 | [Exa against DuckDuckGo: comparison checks](archive/study-log-2026-09-25-to-27.md#2026-09-27-exa-against-duckduckgo-dry-and-cheap-checks-of-the-comparison-specs) | $0.39 | Exa's uncapped highlights flood the scouts' context; the comparison is held |
| 09-27 | [Fetch bake-off](archive/study-log-2026-09-25-to-27.md#2026-09-27-fetch-bake-off-on-the-pages-our-fetcher-failed-on) | $0.10 and free-tier credits | A chain of our fetcher, open access, Exa, and Firecrawl reads 139 of 160 failed pages |
| 09-27 | [Reading fallback: dry and cheap checks](archive/study-log-2026-09-25-to-27.md#2026-09-27-reading-fallback-dry-and-cheap-checks) | $0.17 | A screen: with the fallback, failed page fetches fell from 12 to 0 on st07 and from 10 to 1 on drb2-task8 |
| 09-27 | [Architectural audit: blocked sources and the study ceiling](archive/study-log-2026-09-25-to-27.md#2026-09-27-architectural-audit-blocked-sources-and-the-study-ceiling) | free | Five early drb2-task8 ledgers cite the blocked expert report; the study ceiling was not a hard cap; a request's reservation missed long-context output prices. All three fixed |
| 09-27 | [Reading fallback: cheap checks after the audit fixes](archive/study-log-2026-09-25-to-27.md#2026-09-27-reading-fallback-cheap-checks-after-the-audit-fixes) | $0.16 | Clean on the new code once the keys were in `.env`; the runner now checks every arm first. Ceilings raised |
| 09-27 | [Deep example runs: throughput, trimmed history, and a second scout model](archive/study-log-2026-09-25-to-27.md#2026-09-27-deep-example-runs-throughput-trimmed-history-and-a-second-scout-model) | $1.59 | Both real deep runs were partial; Luna's token rate, not the deadline, limited them. Longer deep limits, history trimming, and a second scout model were built but not measured |
| 09-28 | [Deep against standard on drb2-task8 (scout-v10, followup-v11)](#2026-09-28-deep-against-standard-on-drb2-task8-scout-v10-followup-v11) | $3.59 | Undecided: deep scored 5.0 points more under Sol and 4.7 under GLM, short of the 5-point rule under both; better supported, 1.75 times the cost |
| 09-28 | [Where drb2-task8's rubric points are lost](#2026-09-28-where-drb2-task8s-rubric-points-are-lost) | free | Points are lost before synthesis: scouts claim narrow, paper-level findings from few reviews, and coverage counts a dimension met by any one claim. About 6 points are out of reach, and URL literalness explains much of the gap between the judges |
| 09-28 | [The first free diagnosis of the deep-vs-standard study](#2026-09-28-the-first-free-diagnosis-of-the-deep-vs-standard-study) | free | With three runs per arm the study could detect only about 8-point differences, so its 5-point rule could not decide; 7 of 52 points never met |
| 09-28 | [Hybrid search and capped Exa highlights: checks](#2026-09-28-hybrid-search-and-capped-exa-highlights-checks) | $0.12 | Capped highlights come to about 6,000 characters a search; in a cheap run Exa answered all 10 searches DuckDuckGo could not, and no blocked source reached a scout |
| 09-28 | [Blocked works known by title](#2026-09-28-blocked-works-known-by-title) | $0.12 | 36 of 49 stored DRB-II case runs were shown their case's blocked title; every task68-plus run saw the expert report's abstract. Now blocked by title (fetch version 15) |
| 09-28 | [Luna's rate limit is ten times what the pacer assumed](#2026-09-28-lunas-rate-limit-is-ten-times-what-the-pacer-assumed) | under $0.01 | 2,000,000 tokens a minute, not 200,000: the deep runs' throughput ceiling was our own pacer |

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

## 2026-09-28 Hybrid search and capped Exa highlights: checks

A free look at the stored searches came first. DuckDuckGo is reached through `ddgs`, which scrapes whichever of several search sites it picks. Of 2,636 production searches, 34% returned nothing and 104 more timed out. Results were almost always none or about seven, and plain queries came back empty about as often as those with quotes or `site:`. Four of six queries that had come back empty returned results when tried again. So the empty searches are the scraper's failures, not the scouts' queries.

Fetch version 14 adds a hybrid engine, which sends a query to Exa only when DuckDuckGo finds nothing or fails. It also caps Exa's highlights at 600 characters a result, and makes the reading fallback on by default. Scout-v11 drops old search snippets from a scout's view past 16,000 characters.

**Checks, $0.12.**
- *One Exa search*, $0.007. Ten results carried 583 to 600 characters each, 5,955 in all. Uncapped, the median had been 3,900 to 6,400 a result. Two of the top three results were copies of drb2-task8's blocked expert report. That search ran without a case, and the case's block list catches both addresses.
- *A cheap run* on drb2-task8 at standard depth, with every role on Luna@low: `10e3fce3`, complete, $0.115, 152 s. Of 23 DuckDuckGo searches, 10 found nothing. Exa answered all 10, for $0.07 of the run's $0.088 in paid searches and reads, and the reading fallback read 4 pages through Exa. The blocked report appeared only in the scouts' block list, never in a tool result.

A cheap run is a pass or fail check, not a measure of quality. Whether hybrid search raises rubric scores, and where points are lost, is left to a study with the diagnosis on.

## 2026-09-28 Blocked works known by title

The architectural audit left one known gap. A copy of a blocked work could reach a scout if its address carried neither a blocked address nor the work's DOI. Exa search makes such copies more likely to turn up. So a blocked work is now also known by its title, which every frozen case already records as `blocked_title`. Fetch version 15 leaves out or refuses:
- search results whose title or snippet carries the title;
- scholarly records with that title;
- fetched documents whose first 3,000 characters print it.

Scout-v12 refuses a scout's evidence that cites the work by title. It also shows scouts and the gap analyzer the blocked titles beside the blocked addresses. A page that only cites the work further down is still read. Titles shorter than four words are never matched. `--block-title` blocks a title for ordinary runs.

**A free scan of the stored runs.** 36 of the 49 stored DRB-II case runs had been shown their case's blocked title:
- **drb2-task68-plus, every stored run (v1 to v3).** Scouts were shown the blocked review's OpenAlex record with its 1,080-character abstract. The case blocks `mdpi.com/1424-8220/24/17/5551`, an address without the DOI 10.3390/s24175551, so neither the address nor the DOI check recognized the record. Its stored scores, such as the 14 of 54 baseline, were earned with the expert report's abstract in view.
- **drb2-task8 before fetch version 12.** Search results at the blocked addresses carried abstract text in their snippets, and six v1 to v3 ledgers cite the review by title. That partly overlaps the audit's F06 finding.
- **drb2-task8 at scout-v10.** Two runs of the deep-vs-standard study, `27701a2f` and `aea52be0`, were shown a Semantic Scholar page for the review. Its snippet gave only the title and authors, with no content, so their grades are unaffected in substance.

Title blocking catches every case above. Scores from before fetch version 12 on drb2-task8, and every stored score on drb2-task68-plus, should be read with this exposure in mind.

**Check, $0.12.** A cheap hybrid run on drb2-task8 at scout-v12, with every role on Luna@low: `a44bd2e3`, complete, $0.119, 180 s. It recorded the case's blocked title and made 13 Exa searches, and the same scan found no blocked title in any of its tool results.

## 2026-09-28 Luna's rate limit is ten times what the pacer assumed

One small Luna request read OpenAI's rate-limit headers. It cost under a cent. `gpt-6-luna` allows **2,000,000 tokens and 5,000 requests a minute**. The pacer's default, `RESEARCH_TOKENS_PER_MINUTE={"openai:gpt-6-luna": 200000}`, dates from 25 September, when that was the tier. So every run since the tier rose has held its scouts to about a tenth of the real limit.

The throughput ceiling of about 115,000 tokens a minute, which cut off the scouts of both deep GLP-1 runs (`d8c8198e`, `2c66e8bd`), came from our pacer, not from OpenAI. Trimming still saves tokens and money. The second scout model's purpose, a second provider's rate limit, matters much less than it seemed. The deep-vs-standard study also ran under the throttle.

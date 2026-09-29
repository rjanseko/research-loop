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
| 09-28 | [Serper, Brave, and DeepSeek study design and dry checks](#2026-09-28-serper-brave-and-deepseek-study-design-and-dry-checks) | free | Two 12-run offline checks had no invariant violations; paid screens remain unrun |
| 09-28 | [Search replay, DeepSeek smoke, and the screens' cheap checks](#2026-09-28-search-replay-deepseek-smoke-and-the-screens-cheap-checks) | $0.29 | On 40 replayed scout queries DuckDuckGo found nothing for 18, Serper and Brave for 4 each; both DeepSeek scouts call tools after the Flash profile fix |
| 09-28 | [What scouts see and skip, and MDPI's bot wall (fetch version 17)](#2026-09-28-what-scouts-see-and-skip-and-mdpis-bot-wall-fetch-version-17) | free | Scouts fetched 1 of 545 scholarly works shown and mostly the top web results; 29% of fetches failed. MDPI challenges our fetcher, so its articles now find their DOI and a free copy first: 3 of 11 recovered |
| 09-28 | [Rescout studies graded on claims; the DeepSeek and trimming specs' checks](#2026-09-28-rescout-studies-graded-on-claims-the-deepseek-and-trimming-specs-checks) | $0.53 | Both specs' dry and cheap checks clean, before and after server-error retries (scout-429-v5) and CORE (fetch version 18) |
| 09-28 | [A search-engine rescout study, xhigh scouts, and fewer cheap checks](#2026-09-28-a-search-engine-rescout-study-xhigh-scouts-and-fewer-cheap-checks) | $2.32 | Stopped after 6 of 12 rescouts: 5 lost questions to 120-second request timeouts, so it says nothing about the engines |
| 09-28 | [Trimming off, and limits as safety nets (scout-v14)](#2026-09-28-trimming-off-and-limits-as-safety-nets-scout-v14) | $1.49 | Untrimmed met every condition of the rule; trimming is off by default, and the time, call, and request limits were raised |
| 09-28 | [Search engines on three drb2-task8 plans (scout-v14)](#2026-09-28-search-engines-on-three-drb2-task8-plans-scout-v14) | $4.99 | No engine advanced under the rule: Serper was disqualified by one partial unrelated to search and Exa by quote share; Serper is rerun on scout-v15 |
| 09-28 | [Sol regrades ten search-study rescouts](#2026-09-28-sol-regrades-ten-search-study-rescouts) | $1.58 | Sol gives 3 to 9 fewer claimed points than GLM (mean 21.6 against 27.7) but ranks the arms the same way |
| 09-28 | [Case contamination audit (scout-v16)](#2026-09-28-case-contamination-audit-scout-v16) | $0.04 | drb2-task8's wording was in model-visible examples since v6 and v13; its absolute scores since v6 are suspect, comparisons within a study stand; held-out cases untouched |
| 09-28 | [Scouts pay for their own searches (scout-v15)](#2026-09-28-scouts-pay-for-their-own-searches-scout-v15) | under $0.001 | A scout's share counts its paid searches and reads; productive calls become a loop guard at 128; replies may be 48,000 tokens; budgets loosened where the bottleneck check found them close |
| 09-29 | [First live synthesis with Claude's citations (synthesis v5, test branch)](#2026-09-29-first-live-synthesis-with-claudes-citations-synthesis-v5-test-branch) | $0.03 | The citation path works end to end on the real API: a complete, supported st07 report with 21 citations, all mapped to their claims. A first attempt was refused before dispatch because the passages were stored twice; fixed |
| 09-29 | [Smoke check of the synthesizer's fallback (synthesis v7, test branch)](#2026-09-29-smoke-check-of-the-synthesizers-fallback-synthesis-v7-test-branch) | $0.011 | Anthropic accepts Opus 5@medium as Opus 5.5's server-side fallback: the synthesizer's smoke call carried it and was answered |
| 09-29 | [Opus 5.5 resynthesis of st07 on synthesis v7 (test branch)](#2026-09-29-opus-55-resynthesis-of-st07-on-synthesis-v7-test-branch) | $0.13 | Complete and supported; all 34 citations matched their passages. 18 of 45 answer sentences uncited, but 6 are counting artifacts and 7 framing; 5 are facts left uncited, mostly lead-ins |

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

## 2026-09-28 Serper, Brave, and DeepSeek study design and dry checks

The protocol and confirmation rules are in [evaluation.md](evaluation.md#serper-brave-and-deepseek-integration-study). The two one-replicate screen specs compare DuckDuckGo/Serper/Brave and Luna/DeepSeek Flash/DeepSeek V4 Pro separately on four frozen development cases. Each spec parsed and fit its ceiling ($8.40 estimated under $9.00 for search, $10.20 under $11.00 for DeepSeek). Both `research study run SPEC --dry` checks finished with **no invariant violations**. After making the scout model and the absence of a second scout explicit in the specs, both final specs were dry-checked again, also with no invariant violations. Dry runs used fake models, the offline world, and `research_dry`; their synthetic charges in the generated summaries are not provider charges. Actual spend: **$0**. A fake fault caused one DeepSeek-screen grade to fail; it was accounted for under the dry study's ceiling.

Run IDs in case order st04, st05, st07, drb2-task8, with arms in spec order:

| Dry study | st04 | st05 | st07 | drb2-task8 |
|---|---|---|---|---|
| Search: DuckDuckGo / Serper / Brave | `a81091e7` / `a95c5932` / `fcdeb838` | `283befb7` / `89f2efe1` / `b7050f54` | `3e3964db` / `3d219886` / `0f0ec82a` | `a8e187d3` / `75837dd3` / `fadd4390` |
| Models: Luna / Flash / V4 Pro | `2c8a80af` / `b43eb65a` / `eded7367` | `bf9bd084` / `de6a71cd` / `57d4effb` | `1d9cc567` / `cecb49a3` / `fc674233` | `9ba4d688` / `eb64de62` / `88d5ca76` |


The final-spec reruns, in the same case and arm order, produced these IDs:

| Final dry study | st04 | st05 | st07 | drb2-task8 |
|---|---|---|---|---|
| Search: DuckDuckGo / Serper / Brave | `2e6b256b` / `c5dfc7ba` / `a1416c0b` | `710d6e42` / `1411ce96` / `f55a177f` | `056744ec` / `f9f93705` / `44e4acee` | `1162c967` / `501d6a9b` / `6707247f` |
| Models: Luna / Flash / V4 Pro | `4d53cd5b` / `a4aae00d` / `48d1be8f` | `67006356` / `4a4676e1` / `97b09ce4` | `e3c72ab2` / `69f8d273` / `6c9ea237` | `fd8da612` / `965265a9` / `f7c780e0` |

These fake outcomes do not rate the providers. Before either paid screen, the same spec still needs a clean `--cheap` check; the DeepSeek model IDs also need a live smoke check. A paid screen needs approval under AGENTS.md and its hard study ceiling.

## 2026-09-28 Search replay, DeepSeek smoke, and the screens' cheap checks

The two integration screens above would have cost up to $20 together and could not change a default. Before spending that, three cheaper checks. Total spend: **$0.29**.

**Cheap checks, $0.051.** Both specs ran with `--cheap`, which replaces every model with Luna@low and keeps the real search engines. They stopped after st04, so each covered one case per arm: search screen `e39bc0f7` (DuckDuckGo, $0.003), `887f7cc0` (Serper, $0.010), `18e3adc3` (Brave, $0.029); DeepSeek screen `cc2be58f`, `f23e7455`, `f4d9819c` (all Luna@low, about $0.003 each). All completed. The cheap mode does not call DeepSeek, so the second three show nothing about it.

**DeepSeek smoke, $0.0005.** DeepSeek rejects `tool_choice=required` with thinking on, and PydanticAI did not know `deepseek-flash` as a thinking model, so Flash failed every structured call with HTTP 400. With the profile fix in `models.py`, `deepseek:deepseek-flash@high` and `deepseek:deepseek-v4-pro@high` each answered the smoke tool call (3.1 s and 3.3 s). Earlier smoke checks the same day are not recorded here; their output was not kept.

**Search replay, $0.24.** `scripts/search_replay.py` sampled 40 of the 1,264 distinct queries production scouts sent to DuckDuckGo, 20 whose stored search had found nothing and 20 that had found results, and sent each to DuckDuckGo, Serper, and Brave at once through the run's own `WebSearch` (seed 20260928; per-query results in `benchmark_outputs/search-replay/replay-20260928-114836.json`).

| Stored DuckDuckGo outcome | Queries | DuckDuckGo found / empty / failed | Serper | Brave |
|---|---|---|---|---|
| found nothing | 20 | 11 / 9 / 0 | 16 / 4 / 0 | 18 / 2 / 0 |
| found results | 20 | 11 / 9 / 0 | 20 / 0 / 0 | 18 / 2 / 0 |

DuckDuckGo came back empty as often on queries that had found results before as on those that had not, so its empties are the scraper's failures, not hard queries. Serper found results for 15 of DuckDuckGo's 18 empties and Brave for 16; both came back empty on only 2 queries. Found searches returned a median of 10 results on Serper and Brave, 7 on DuckDuckGo. Median search time was 0.5 s on Brave, 1.1 s on Serper, and 1.6 s on DuckDuckGo.

This meets the search screen's availability condition (a lower empty/error share) by a wide margin, on paired queries rather than one run per arm, so the full search screen is not needed for availability. It says nothing about report quality, and no default changes. Serper is a fifth of Brave's price per search ($0.001 against $0.005) for about the same availability.

**Code found along the way.** Rescout and resynthesis accepted source runs from scout-v1 to v8 and the current version only, so every run from v9 to v12 was refused as a source; the list is now derived from the current versions. The study summary now counts searches found, empty, and failed, and pages read and failed, per run (`study.tool_counts`).

## 2026-09-28 What scouts see and skip, and MDPI's bot wall (fetch version 17)

A free check of the stored drb2-task8 runs, asking whether a source ranker would help: do scouts leave good sources unread among what their searches return? It read the tool messages of runs `aea52be0`, `8f753940`, `27701a2f`, `0cd21cbc` (scout-v10), `10e3fce3` (v11), and `a44bd2e3` (v12), 18 scout calls in all.

| What scouts were shown | Distinct results | Fetched |
|---|---|---|
| Scholarly search works | 545 | 1 |
| Web search results | 988 | 118 |

Of 177 fetches, 119 were of a result the scouts had been shown and 52 failed. Scouts fetched the first web result 44 times and the fifth or lower 29 times. Most review-titled results they left unread were off the topic: scholarly search returns highly cited works such as AlphaFold, fairness surveys, and EEG reviews. A few on-topic reviews were shown and not read, such as "Deep Generative Models in Engineering Design: A Review". Reordering results would therefore gain little on this case; the earlier audit places its lost points in claims. Scholarly search's off-topic works cost context, not reads.

**The failed fetches.** 21 were mdpi.com, all "empty extraction", and 14 were pubs.rsc.org 403s. Across every stored run, all MDPI fetches by our own fetcher failed, in every version: mdpi.com is behind Akamai Bot Manager, which refuses our User-Agent with a 403 and gives the browser retry a JavaScript challenge page. Since scout-v11 the Exa reader has read them, paid. We do not try to pass the challenge. An MDPI address carries no DOI, so the free open-access reader never ran for one. Of the 11 MDPI articles stored runs failed to read, OpenAlex gives a DOI for 10 from the address's ISSN, volume, issue, and article number, and 3 of those are in Europe PMC.

**Change (fetch version 17).** The open-access reader looks up an MDPI address's DOI in OpenAlex, and after OpenAlex's best location it tries PDFs at up to three of its other locations, such as repository copies. Run live on the 11 articles, it read 3 from Europe PMC; one repository copy (Middlesex eprints) did not respond. The other 7 still go to Exa. CORE, which aggregates repository full text, is not used and might cover some of them.

## 2026-09-28 Rescout studies graded on claims; the DeepSeek and trimming specs' checks

The DeepSeek comparison was redesigned to rescout fixed plans (see "Serper, Brave, and DeepSeek study design" above), but a rescout writes no report, so it could not be graded. Now `research diagnose` accepts a fixed-plan rescout and grades only its claims and research, and a rescout study with `diagnose = true` compares arms on the rubric points their claims met. Three more runner changes came with it:
- a dry rescout or synthesis study copies its source runs into the dry database first; before this, it could not run at all;
- the arm order rotates on every target as well as every replicate, so with three arms on three plans each arm goes first once against the shared cache;
- the cheap mode's run estimate is $0.05, not $0.06, so three arms and their diagnoses plan under its $0.25 ceiling. Cheap runs have cost $0.003 to $0.03.

**Trimming.** Stored Luna@high scouts read 67 to 74% of their input from OpenAI's prompt cache before history trimming (scout-v6, v9, research-v3) and 43 to 49% after it (v10, followup-v11). `RESEARCH_TRIM_HISTORY` (default on) now turns it off, and each run records it. See [notes.md](notes.md).

**Checks.**
- `studies/deepseek-rescout-task8.toml` and `studies/trim-history-rescout-task8.toml` ran `--dry` with no invariant violations.
- The DeepSeek spec's cheap check (`eab6ad79`, `5a0ad3d8`, `cc20d317`, source `aea52be0`) failed: every Luna@low scout request and every diagnosis grade got HTTP 500 from OpenAI. It cost under $0.01.
- `research doctor --smoke` right after returned HTTP 500 for both `gpt-6-sol@high` and `gpt-6-luna@low`, while `claude-opus-5-5@medium` answered ($0.0038). This was an OpenAI outage, not our code. Both specs need a clean cheap check before any paid run.
- Once OpenAI answered the smoke check again (Sol $0.0006, Luna under $0.0001), both cheap checks ran with no invariant violations: trimming study `bc15967a` (trimmed, complete) and `d6e3e191` (untrimmed, partial), $0.11; DeepSeek study `e874d730`, `557604ff`, `d9f0d0b8` (all Luna@low in cheap mode, partial), $0.15. Of the five scouts that failed, three got a leftover HTTP 500 and two hit the cheap mode's run cap. The cheap scores say nothing about quality.

**A single provider 500 loses a scout's question.** The scout wrapper retries only timed rate limits (`rate_limit.py`), so one HTTP 500 ends that research question for the run. A paid study during a flaky hour would count those losses against whichever arm met them. A bounded retry of 5xx responses, with a `RATE_LIMIT_POLICY_VERSION` bump, would prevent it.

**Server-error retries and CORE.** With the user's approval, scouts now send a request again twice after an HTTP 500, 502, 503, or 504, pausing 2 then 8 seconds (`scout-429-v5`). The open-access reader asks CORE last for its full text of the DOI (fetch version 18). CORE allows 100 requests a day without a key, so requests are spaced 6.5 seconds apart, and none is sent after a 429 until CORE's reset time. The dry world sends injected 500s again at once; with the real pauses the fixed fuzz sweep took 30 s instead of 12 s. Both specs' dry checks, then their cheap checks, ran clean on this code: trimming study `7a637fe8` (trimmed, complete) and `63026306` (untrimmed, partial), $0.13; DeepSeek study `dd80a830`, `82ea3f46`, `80945be6`, $0.13. The three partials each hit the cheap mode's run cap.

## 2026-09-28 A search-engine rescout study, xhigh scouts, and fewer cheap checks

`studies/search-rescout-task8.toml` compares DuckDuckGo, Serper, Brave, and Exa, each alone, as rescouts of the three stored drb2-task8 plans the trimming and DeepSeek studies use. Only the engine changes. Scouts are Luna@xhigh, and trimming is pinned off in every arm, both at the user's request, so the study does not wait on the trimming study's result. It has its own study label and so its own search and page cache. Its decision rule is in the spec header. The ceiling is $9.50, with a $1.50 cap per rescout and $8.40 planned by the estimates. The user approved it.

At the same request, every arm of `deepseek-rescout-task8.toml` now runs at xhigh too (Luna@xhigh, DeepSeek Flash@xhigh, and V4 Pro@xhigh, which DeepSeek receives as `max`). Its per-rescout cap rose to $2.50 and its ceiling to $10.50.

**Dry checks.** Both specs ran `--dry` after these changes, with 12 and 9 rescouts and no invariant violations. Their failed and partial runs came from the dry world's injected faults.

**Cheap check skipped.** The first cheap attempt was refused before any spend, because four arms plan to $0.32 against the fixed $0.25 cheap ceiling. With the user's agreement, it was skipped: each arm's path had already had a real run on the current code. Serper and Brave ran in `887f7cc0` and `18e3adc3`, Exa in the hybrid-search cheap check, and untrimmed DuckDuckGo with Luna in the trimming spec's cheap check after scout-429-v5 and fetch version 18. The rule in AGENTS.md, the README, and evaluation.md now asks for a cheap check only when an arm uses something without a real run on the current code. Cheap and smoke checks had cost about $0.85 that day, mostly repeated confirmations.

The study was started detached alongside the trimming study, two Luna processes as AGENTS.md allows.

**Stopped after 6 of 12 rescouts, $2.32** ($1.80 of rescouts and $0.52 of diagnoses). Five of the six ended partial, in every arm but DuckDuckGo's single run, because scout requests hit the 120-second request timeout ("Request timed out"). Without trimming, Luna@xhigh requests carried 200,000 to 365,000 input tokens, and the requests that wrote a result took longest. Runs: DuckDuckGo `710c5d4e` (complete, $0.127); Serper `0a85fb23` and `721c4455`, Brave `7b254786` and `357910a3`, Exa `7bdcebca` (all partial); Exa `6e1d421b` was cancelled when the study was stopped. The partials say nothing about the engines, and the study is not scored. One Exa rescout spent $0.45 on searching and reading (63 searches) against $0.08 on its scouts. The study was rerun under scout-v14 as `search-rescout-task8-v14`.

## 2026-09-28 Trimming off, and limits as safety nets (scout-v14)

**The trimming study.** `studies/trim-history-rescout-task8.toml` rescouted three drb2-task8 plans with Luna@high scouts, trimmed and untrimmed, and diagnosed each with GLM. It cost $1.49 of its $3.00 ceiling ($0.82 of rescouts and $0.67 of diagnoses).

| Plan | Trimmed | Untrimmed |
|---|---|---|
| `aea52be0` | `38687e43` partial (a scout passed its $0.075 share), $0.344, 23 points claimed | `513bdff1` complete, $0.223, 23 |
| `8f753940` | `644101a7` partial (a request timed out), $0.200, 17 | `1bb386d1` complete, $0.212, 31 |
| `27701a2f` | `02ae1629` complete, $0.268, 33 | `b1151f7b` complete, $0.240, 30 |

Costs include the diagnosis. Scout input read from the prompt cache was 35.5% trimmed and 59.0% untrimmed (`run_calls` usage). Verified quotes were 165 of 184 (89.7%) trimmed and 200 of 228 (87.7%) untrimmed. Mean points claimed were 24.3 and 28.0.

**Decision: trimming off.** Untrimmed met every condition of the rule. It had no failed, partial, or limit-stopped rescout that trimmed did not; a larger cache share; a lower median cost ($0.223 against $0.268); a verified-quote share 2.0 points lower, within 5; and more points claimed. Three plans per arm cannot show the difference in points is real, and the rule did not ask it to.

**The limits.** Of 107 real-run scouts from 27 to 28 September:
- 65 returned on their own;
- 17 stopped on their 32 productive calls;
- 11 stopped at or near the research deadline, and 10 on a request timeout, the last two losing their claims;
- 1 stopped on its dollar share.

The slowest tenth of standard scouts took 340 seconds of 480 and spent $0.037 of a $0.075 share. The slowest tenth of deep scouts took 1,174 of 1,200 seconds. One standard scout used 19 of its 20 requests. At the user's request, the limits became safety nets that a normal run should not reach (scout-v14, followup-v15, research-v14):

| Limit | Before | Now |
|---|---|---|
| Scout request timeout | 120 s | 600 s (`scout_request_timeout_seconds`) |
| Standard research window and run deadline | 480 s and 720 s | 900 s and 1,320 s |
| Deep research window and deadlines | 1,200 s and 1,920 s | 1,800 s and 2,520 s |
| Scout requests | 20 | 30 |
| Productive calls, standard and deep | 32 and 48 | 48 and 64 |
| Standard envelope | $0.75 ($0.075 a scout) | $1.25 ($0.20 a scout) |

A timed-out request is still not sent again: at 600 seconds a second attempt would rarely fit, and the research deadline bounds time. Tests, lint, and 200 fuzz runs are clean. The user asked that the first case of the rerun search study be checked for failures before the rest runs.

## 2026-09-28 Scouts pay for their own searches (scout-v15)

A scout's dollar share bounded only its model requests. Paid searches and page reads were charged to the run and seen only by a hard cap, so the productive-call limit was the one per-scout bound on them: one Exa rescout spent $0.45 on searches and reads against $0.08 on its model. At the user's request, scout-v15 (followup-v16, research-v15) changes three things:
- **Paid spend by scout.** Each scout call gets its own spend key, set on every tool call it makes (`reading.charged_question`), so `ExternalSpend.by_question` holds what each call's paid searches and page reads cost.
- **Money as the budget.** The scout's loop budget withdraws its tools, with a new note ("Your research budget is spent"), once its model cost plus that spend would leave less than two requests like its average (`LoopBudget.out_of_money`). A scout that stops this way is recorded as "returned after its dollar share was spent". The note is part of the prompt fingerprint.
- **Wider caps.** Productive calls became a guard against loops rather than a budget: 128 a scout and a deep dive, 192 a deep scout. A scout's reply under a hard cap may be 48,000 tokens, up from 24,000; the largest had used 22,313.

Smoke calls with a 48,000-token cap were answered by `openai:gpt-6-luna@xhigh`, `deepseek:deepseek-flash@xhigh`, and `deepseek:deepseek-v4-pro@xhigh`, for under $0.001 in all. Tests (369, with the Postgres tests), lint, and 200 fuzz runs are clean, with new tests for spend attributed across two concurrent scout calls and for the money note. The change was built in a separate worktree while `search-rescout-task8-v14` ran on scout-v14, and it is merged after that study, so the study runs on one version.

**Where budgets bind.** `scripts/budget_bottlenecks.py` reads the stored runs, makes no model calls, and reports what stopped each call and how much of each limit it used. Over the 35 real runs and 188 calls since 27 September:
- **Scout dollar shares did not bind.** Scouts used a median of 15 to 28% of their share, and at most 52% at the 90th percentile. One scout of about 140 reached its share. Deep dives used 6 to 15%.
- **The synthesizer's share nearly bound on deep runs.** It used 88 to 93% of its $0.40 on scout-followup-v11, and a synthesis cut off by its share writes no report.
- **Paid searching is a scout's largest cost with a paid engine.** On the first v14 plan, search and reading were $0.26 of Brave's $0.36 run and $0.40 of Exa's $0.58, or $0.06 and $0.10 a scout. Under scout-v15 this counts against a $0.20 share, beside $0.02 to $0.05 of model.
- **What bound was calls, time, and output checks.**
  - Productive calls stopped 17 to 25% of standard scouts on every version.
  - The research deadline cut off most scouts of the v9 and v10 deep runs.
  - Request timeouts cut off 9 of 52 v13 rescout scouts.
  - On v14, one Luna@xhigh scout left out a required field twice and lost its question (`43e5c141`, Serper).
- **No real run reached a hard cap.** The study-budget refusals since 27 September were all in cheap checks.

So scout-v15 also loosened the budgets that are close, or that paid searching will make close:

| Budget | Before | Now |
|---|---|---|
| Standard envelope (scout share at four questions) | $1.25 ($0.20) | $1.75 ($0.275) |
| Synthesizer's share | $0.40 | $0.60 |
| Follow-up envelope, standard and deep | $2.00 and $3.00 | $2.50 and $4.00 (deep scouts $0.275 each) |
| Deep dive share | $0.25 | $0.35 |
| Scout output retries | 1 | 2 |

These are soft shares, and a run pays only for what it uses. The envelopes change what a run may spend, not what it typically spends: v14 standard rescouts used a median of 21% of theirs. A new test checks that a scout whose result fails its checks twice gets a third try; it fails with one retry.

**The token limit.** Plan 3's DuckDuckGo rescout (`0acd61da`) lost a question to the 2,000,000-token limit. With trimming off, its scout resent its whole history on each of 20 requests. It billed 2,034,129 tokens for $0.040, 93% of them read from the cache, and it was cut off with its claims. scout-v15 raises `scout_tokens` to 8,000,000, because the dollar share already bounds what tokens cost. Nearing either limit now withdraws the scout's tools with the budget note, instead of cutting it off. The next request is estimated as twice the average so far, since each request resends the history, and two such requests must still fit. A new test covers the token case.

## 2026-09-28 Search engines on three drb2-task8 plans (scout-v14)

`studies/search-rescout-task8.toml`, labelled `search-rescout-task8-v14`, rescouted three stored drb2-task8 plans once per arm. The arms were DuckDuckGo, Serper, Brave, and Exa, each alone, with Luna@xhigh scouts and trimming off on scout-v14. GLM@high diagnosed every run. Its dry check was clean. In place of a cheap check, the user asked for the first plan's four rescouts to be checked for failures before the rest ran: all 16 scouts returned. It cost $4.99 of its $9.50 ceiling, diagnoses included.

| Plan | DuckDuckGo | Serper | Brave | Exa |
|---|---|---|---|---|
| `aea52be0` | `281aa7e2` complete, 26 | `6a76c7bb` complete, 25 | `6fa930d6` complete, 29 | `3c6697be` complete, 30 |
| `8f753940` | `9f116926` complete, 24 | `43e5c141` partial, 13 | `9b822c2d` complete, 23 | `ed4872c8` complete, 26 |
| `27701a2f` | `0acd61da` partial, 24 | `b1de9f70` complete, 30 | `7060df48` complete, 32 | `0ba7d7b8` complete, 32 |

Each cell gives the run, its status, and the rubric points its claims met, of 52.

| Arm | Empty or failed searches | Verified quotes | Mean points claimed | Median rescout cost (search and reading) |
|---|---|---|---|---|
| DuckDuckGo | 62 of 160 (39%) | 221 of 240 (92.1%) | 24.7 | $0.159 ($0.03) |
| Serper | 9 of 215 (4%) | 210 of 231 (90.9%) | 22.7 | $0.221 ($0.08) |
| Brave | 44 of 163 (27%) | 226 of 255 (88.6%) | 28.0 | $0.388 ($0.26) |
| Exa | 1 of 138 (1%) | 232 of 267 (86.9%) | 29.3 | $0.462 ($0.33) |

**Decision: no engine advances, and DuckDuckGo stays the default.**
- **Serper is disqualified.** It had a partial rescout (`43e5c141`) where DuckDuckGo had none on the same plan. The cause was not the engine: one scout's result left out a required field twice. Without that disqualification, Serper met every other condition: 4% empty against 39%, points within 2 of DuckDuckGo's (22.7 against 24.7, on the line), and $0.06 more.
- **Exa is disqualified.** Its verified-quote share was 5.2 points below DuckDuckGo's, past the 5-point limit. It also cost $0.30 more a rescout, over the $0.15 allowed, though it found the most.
- **Brave does not advance.** Its empty share (27%) is not half of DuckDuckGo's, and it cost $0.23 more.
- No point difference is larger than the study can detect: the diagnosis gives 5 to 14 points by arm.
- Two fetches of a blocked source were refused, and no blocked source reached a scout.

Both partial runs came from limits that scout-v15 changes. Serper's came from output checks, which now get two retries. DuckDuckGo's (`0acd61da`) came from the 2,000,000-token limit, which is now 8,000,000 and ends with a note rather than a cut-off. DuckDuckGo's plan-3 numbers include that lost question. With the user's approval, Serper is rerun against DuckDuckGo on scout-v15 (`studies/serper-rescout-task8.toml`).

**Trimming removed.** At the user's request, the history-trimming code (`history.py`, `RESEARCH_TRIM_HISTORY`) was removed, after the trimming study turned it off by default. Runs at the defaults send the same requests as before, so no workflow version changes. The prompt fingerprint changes, because it no longer includes the trimming notes. Re-reading a page window a scout has already read still uses no loop budget, and its test moved to the budget-notes tests. The trimming spec is kept as the record, marked historical, and the other specs no longer set the removed variable.

## 2026-09-28 Sol regrades ten search-study rescouts

The v14 search study was diagnosed by GLM@high alone. To see whether a second judge would change what the rescout studies show, `gpt-6-sol@high` diagnosed ten of its complete rescouts: `research diagnose RUN --model openai:gpt-6-sol@high`, one run at a time, each under a $1.80 cap. The first attempt, under a $0.40 cap, was refused before dispatch, because the guard reserves $0.54 for Sol's claims view and $1.24 for its research view. The ten runs cost $1.58, or $0.16 a run against GLM's $0.12. The user asked for a handful rather than all 33 rescouts of the day's studies.

| Run | Arm | Claims, GLM | Claims, Sol | Research, GLM | Research, Sol |
|---|---|---|---|---|---|
| `281aa7e2` | DuckDuckGo | 26 | 18 | 26 | 24 |
| `9f116926` | DuckDuckGo | 24 | 19 | 22 | 21 |
| `6a76c7bb` | Serper | 25 | 17 | 26 | 21 |
| `b1de9f70` | Serper | 30 | 26 | 32 | 27 |
| `6fa930d6` | Brave | 29 | 21 | 25 | 21 |
| `9b822c2d` | Brave | 23 | 20 | 26 | 21 |
| `7060df48` | Brave | 32 | 27 | 30 | 27 |
| `3c6697be` | Exa | 30 | 23 | 32 | 24 |
| `ed4872c8` | Exa | 26 | 18 | 30 | 24 |
| `0ba7d7b8` | Exa | 32 | 27 | 33 | 25 |

Sol gave fewer claimed points on every run, 3 to 9 fewer and 6.1 fewer on average (21.6 against 27.7). That matches the report grades, where GLM credited more and Sol read URLs and wording literally. The two judges order the runs much the same: both put `7060df48` and `0ba7d7b8` at the top. On the same runs, both put DuckDuckGo lowest: 18.5 under Sol and 25.0 under GLM, with the other engines 3 to 4 points above it under either judge. A second judge would not have changed the search study's decision.

**Decision.** GLM stays the single judge for screens. Sol, or both judges, is used where a decision rests on a few points, such as a confirmation. Which judge is right on a disputed point is a hand check, and none was done here.

## 2026-09-28 Case contamination audit (scout-v16)

The user stopped all paid calls and asked for an audit, after the claim fix, designed from drb2-task8's rubric, was about to be measured on drb2-task8. The DeepSeek study was stopped after 5 of its 9 rescouts ($2.48 of rescouts; `e994230a` was cut off). The Serper rerun had finished ($1.87). Neither is written up yet. The full audit is [audit-2026-09-28-case-contamination.md](audit-2026-09-28-case-contamination.md).

- **What a run receives is clean.** It gets the case's question, blocked addresses, and blocked titles, and no rubric.
- **The models' examples carried drb2-task8's wording.** An expected database, ICSD, was in the scouts' output schema since scout-v6, and the case's category names and database fields were in the planner's and scouts' examples since scout-v13. So drb2-task8's absolute scores since v6 are suspect, while comparisons within a study stand.
- **The held-out cases are untouched.** They have no runs, grades, or expected sets, and share no wording with the prompts.
- **scout-v16** replaces the examples, adds a test that keeps every frozen case's wording out of model-visible text, and makes a stopped study stop its current run with SIGTERM, so the run records itself as cancelled.
- **The planner calls.** The three planner-only calls earlier the same day cost $0.04 and used the v13 planner, whose example came from the case.

## 2026-09-29 First live synthesis with Claude's citations (synthesis v5, test branch)

A functional check of the citations test branch (`claude/citations-synthesis`, scout-v17 and synthesis v5), approved by the user with a $0.25 hard cap while other paid work stays paused. It resynthesized the stored ledger of st07 run `321ffb47` (scout-v9, 21 claims from 14 sources) with `anthropic:claude-haiku-4-5-20251001@low`. It is a check that the path works, not a quality comparison, and nothing was graded.

- **The first attempt, `8e4bfa1c`, cost nothing.** The budget guard refused it before dispatch: it would have reserved $0.2919 against the $0.25 cap. Each passage was stored twice in the prompt message, as the text of its `TextContent` and in its metadata, and the guard bounds a first request at two tokens per byte of the serialized messages. The passages are now kept once, in the metadata, and a test checks it. Even so, the bound was about 100,000 input tokens with a 32,000-token output cap, so this check set `RESEARCH_LIMITS__SYNTHESIS_MAX_OUTPUT_TOKENS=16000`.
- **The second, `54315592`, cost $0.0261 and took 24 seconds.** It was complete, with answer support `supported`. It used 12,222 input tokens, 12,203 of them written to the prompt cache, and 2,175 output tokens, 455 of them thinking.
- **Claude followed the format.** It wrote all five tagged sections and cited inside them. Its reply came as 41 text blocks, 20 of them with 21 citations, and every citation mapped to its source and to the claims behind the cited blocks. The report lists 20 cited statements that use 17 of the 21 claims, all at support level `read`. There were no citation problems. 6 of the 14 answer sentences the check counts were uncited, mostly framing and concluding sentences.
- **Two costs to watch.** PydanticAI's automatic prompt caching wrote the whole prompt to the cache at 1.25 times the input price, which pays only when a retry follows. And the guard's bound for a first request is about eight times the tokens this one used, so a synthesis at the default 32,000-token output cap needs a cap above $0.25 even on Haiku.

## 2026-09-29 Smoke check of the synthesizer's fallback (synthesis v7, test branch)

`research doctor --smoke` on commit `a3a95bf` (scout-v19, synthesis v7), which the user asked for while other paid work stays paused. It is a check that the configuration is accepted, not a quality measurement. Each call was capped at $0.05, so $0.20 at most.

- **The synthesizer's call carried the fallback and was answered.** `anthropic:claude-opus-5-5@medium` was sent with `fallbacks: [{"model": "claude-opus-5", "output_config": {"effort": "medium"}}]` under the `server-side-fallback-2026-07-01` beta, and answered with a tool call for $0.0053. Anthropic rejects a request up front when a named fallback is not one of the requested model's permitted targets, so Opus 5 is a permitted fallback for Opus 5.5. No refusal occurred, so the handoff itself, its pricing, and the report built across it have been tested only offline.
- **The other models answered too:** `openai:gpt-6-sol@high` for $0.0006, `openai:gpt-6-luna@high` for under $0.0001, and `anthropic:claude-opus-5@medium`, the fallback on its own, for $0.0054. Total about $0.011.

## 2026-09-29 Opus 5.5 resynthesis of st07 on synthesis v7 (test branch)

A functional check of synthesis v7 on its real synthesizer, approved by the user with a $2.60 hard cap: the budget guard reserves a declined reply at the full output cap and the fallback's full reply, about $2.50, though the synthesis was expected to cost $0.10 to $0.20. It resynthesized the stored ledger of st07 run `321ffb47` (scout-v9, 21 claims from 14 sources) with `anthropic:claude-opus-5-5@medium` on commit `1e1931f`. Nothing was graded.

- **Run `6e811f5f` cost $0.1309 and took 30 seconds.** It was complete, with answer support `supported`. It used 14,387 input tokens, none cached, and 3,669 output tokens, 243 of them thinking. There was no refusal, so no fallback, and no notes.
- **Every citation matched.** The reply came as 69 text blocks, 34 of them with one citation each, and all 34 matched their passages on source, search-result position, block range, and cited text. The report lists 34 cited statements that use 19 of the 21 claims.
- **18 of 45 answer sentences were uncited, and most of that is not missing evidence.** Six are counting artifacts: four sit inside a cited block that spans several sentences, which code marks only after its last word, and two are a bold heading and a table header row. Seven are framing and conclusions, such as how the report reads "still" and what the benchmark can and cannot support. Five are facts left uncited, mostly lead-ins whose specifics are cited in the next block ("Sampling settings also changed between releases."), and one plain fact ("a curated subset of 12 Python repositories"). The v5 check's 6 of 14 on Haiku is not comparable: a different model and a report a third as long.
- **Claude often quotes its passages nearly verbatim**, such as the harness's description of fail-to-pass tests, so parts of the answer read as quotation.

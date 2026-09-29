# Takeaways

> **Read with the 29 September corrections.** This summary was written before two findings of the same day. First, snippets of drb2-task8's blocked expert report reached scouts in most runs of every drb2-task8 study it draws on, in every arm, and two rescouts cited it ([study log](study-log.md#2026-09-29-the-blocked-review-reached-scouts-under-shortened-titles-fetch-version-19)). So the Serper rerun fails its blocked-source condition, and Serper does not advance, contrary to the reading below. Second, the Serper rerun and the DeepSeek study are now written up in the study log. The overall conclusions (retrieval loses most points, no engine or model has been shown better, and the data rests on one contaminated case) stand, and more strongly.

## Data

### What the data shows so far (29 September 2026)

This note pulls together what Scout's studies from 25 to 29 September 2026 support and what they do not. It covers search engines, page reading, scout models, depth, the judges, synthesis, and the limits on all of it. The sources are [docs/study-log.md](study-log.md), its [archive](archive/study-log-2026-09-25-to-27.md), the [contamination audit](audit-2026-09-28-case-contamination.md), and the stored outputs of two studies that have not yet been written up: `runs/serper-rescout-task8/summary.md`, and the DeepSeek study's stored diagnoses, read with `research diagnose --free` at no cost. The recorded spend behind it is about $27.

The short version:
- DuckDuckGo's weakness is availability, not relevance. It returns nothing for about a third to two fifths of searches because its scraper fails, and every paid engine fixes most of that.
- Serper is the best value among the engines tested. It is nearly as available as Exa, costs a fifth of Brave per search, and scored no worse than DuckDuckGo. By my reading of its rule, its rerun meets every condition to advance to a confirmation.
- No engine, scout model, or depth has yet been shown to produce better reports. Every difference measured is smaller than what the studies could detect.
- Most missing rubric points never appear in anything the scouts read, so finding and reading the right sources is the weakest step. Turning sources into claims loses a few points more, and synthesis loses little.
- Almost all of the evidence comes from one development case, drb2-task8, whose absolute scores since scout-v6 are suspect because its wording leaked into the prompts.

#### Search engines

Four engines have been compared: DuckDuckGo (free, through the `ddgs` scraper), Serper ($0.001 a search), Brave ($0.005 a search), and Exa (about $0.007 a search, plus its highlights).

##### Availability

This is the one result that is clear. DuckDuckGo fails often, and the failures come from the scraper rather than the queries.

| Measure | DuckDuckGo | Serper | Brave | Exa |
|---|---|---|---|---|
| Production searches, all runs to 28 September | 34% empty, plus 104 timeouts in 2,636 | | | |
| Replay of 40 stored queries | 18 empty | 4 empty | 4 empty | |
| Rescout study, scout-v14 (empty or failed) | 62 of 160 (39%) | 9 of 215 (4%) | 44 of 163 (27%) | 1 of 138 (1%) |
| Serper rerun, scout-v15 (empty or failed) | 91 of 217 (42%) | 18 of 255 (7%) | | |
| Median search time (replay) | 1.6 s | 1.1 s | 0.5 s | |

In the replay, DuckDuckGo came back empty as often on queries that had found results before as on those that had not, and four of six empty queries returned results when simply tried again. Serper and Brave found results for 15 and 16 of DuckDuckGo's 18 empty queries. A found search returned a median of 10 results on Serper and Brave, and 7 on DuckDuckGo.

Brave is the odd one. It was empty on only 4 of 40 replayed queries, but on 27% of the scouts' live queries in the study, all empty and none failed. The cause has not been looked at. The scouts' queries often carry quotes and `site:`, which Brave may treat more strictly.

##### Research quality

The quality measure for these studies is the number of drb2-task8's 52 rubric points that a rescout's claims met, graded by `zai:glm-5.3@high`. A rescout writes no report, so this is a score of the research, not of a report.

| Study | DuckDuckGo | Serper | Brave | Exa |
|---|---|---|---|---|
| scout-v14, three plans, one run each | 24.7 | 22.7 (one partial run unrelated to search) | 28.0 | 29.3 |
| Same runs regraded by `gpt-6-sol@high` (two or three per arm) | 18.5 | 21.5 | 22.7 | 22.7 |
| scout-v15 rerun, three plans, one run each | 26.7 (25, 27, 28) | 28.0 (30, 23, 31) | | |

Exa and Brave found the most in the v14 study, and DuckDuckGo was lowest under Sol, by 3 to 4 points. None of these differences is larger than the study could detect, which was 5 to 14 points by arm in v14 and about 7.5 points in the rerun. So the table is a direction at most. It does not show that any engine produces better research than DuckDuckGo.

##### Quote reliability and cost

| Study | Measure | DuckDuckGo | Serper | Brave | Exa |
|---|---|---|---|---|---|
| scout-v14 | Verified quotes | 92.1% | 90.9% | 88.6% | 86.9% |
| scout-v14 | Median rescout cost (of which search and reading) | $0.159 ($0.03) | $0.221 ($0.08) | $0.388 ($0.26) | $0.462 ($0.33) |
| scout-v15 rerun | Verified quotes | 90.7% | 90.8% | | |
| scout-v15 rerun | Median cost with its diagnosis | $0.251 | $0.379 | | |

The engines that surface the most text verify fewer quotes. Exa's verified-quote share was 5.2 points below DuckDuckGo's, which disqualified it under its rule. Uncapped, Exa's highlights carried 3,900 to 6,400 characters a result against DuckDuckGo's 210 to 230, and every later request of a scout resends them, so they inflated input tokens, time, and model cost. They are now capped at 600 characters a result. With a paid engine, searching and reading become a scout's largest cost: one v13 Exa rescout spent $0.45 on them against $0.08 on its model.

##### Which engine is best

Serper is the best candidate, on value rather than on proven quality:
- it cut empty and failed searches from about 40% to 4 to 7% in two studies;
- its verified-quote share matched DuckDuckGo's;
- its points claimed were within the noise of DuckDuckGo's, once below and once above;
- it cost about $0.06 to $0.13 more per rescout, the least of the paid engines.

**The Serper rerun's rule.** `studies/serper-rescout-task8.toml` fixed its rule before it ran. By my reading of the stored summary, Serper meets every condition:
- no partial or failed run;
- its empty share (7%) is under half of DuckDuckGo's (42%);
- its verified quotes are 0.1 points higher;
- its points claimed are 1.3 points higher;
- its median cost is about $0.13 higher, against a $0.15 limit.

The cost condition is close, and the summary's costs include each run's diagnosis, which the rule does not mention. I could not check from the summary that no blocked source reached a scout. This is not a written-up result: the study still needs its study-log entry, and then its four-case confirmation.

Exa found the most, failed the least, and read pages best, but it verifies fewer quotes and costs about three times DuckDuckGo per rescout. Brave costs almost as much as Exa and is emptier on live queries than Serper. The hybrid engine, DuckDuckGo with Exa only when DuckDuckGo finds nothing, answered all 10 of DuckDuckGo's empty searches in one cheap run. It has never been compared with Serper alone, and a chain such as DuckDuckGo then Serper would need its own comparison.

#### Reading pages

Reading, not searching, is where most evidence is lost.
- 29 to 38% of page fetches fail, mostly with 403s and challenge pages from the publishers research needs: MDPI (every fetch in every version failed, behind Akamai's bot wall), RSC, ScienceDirect, and OpenAI.
- Scouts fetched 1 of 545 scholarly works they were shown and mostly the top web results. Most unread scholarly results were off the topic, so better ranking would gain little on drb2-task8.

The fetch bake-off retried the 160 pages our fetcher had failed on:

| Reader | Pages read | Cited quotes recovered | Cost |
|---|---|---|---|
| Firecrawl | 117 (73%) | 65 of 79 | 1 credit a page |
| Exa `/contents` | 86 (54%) | 57 | $0.097 for all |
| Jina Reader | 52 (32%) | 25 | free |
| Open access (OpenAlex, Europe PMC) | 38 (24%) | 39 | free |
| Tavily Extract | 35 (22%) | 42 | credits |
| Our fetcher, again | 19 (12%) | 31 | free |
| Chain: ours, open access, Exa, Firecrawl | 139 (87%) | 68 | |

The chain became the reading fallback, on by default. In cheap checks it cut failed fetches from 12 to 0 on st07 and from 10 to 1 on drb2-task8, for a cent or two of paid reads. Whether reading those pages makes reports better has not been measured. Materials Project and OQMD stayed mostly unreadable by everything.

#### Scout models

**Luna remains the default, and nothing has beaten it.**
- On one fixed plan, `gpt-6-luna@high` answered as many questions as `zai:glm-5.3-flash@high` in about 60% of the time. Flash's ledgers cited 11 to 20 sources its own tools had not returned, against at most one for Luna.
- `zai:glm-5.3-flash@max` did not fit the research window.

**DeepSeek.** The DeepSeek study was stopped by the audit after 5 of its 9 rescouts, and it has not been written up. Its stored diagnoses (GLM@high, points claimed of 52) are:

| Plan | Luna@xhigh | DeepSeek Flash@xhigh | DeepSeek V4 Pro@xhigh |
|---|---|---|---|
| `aea52be0` | 30, $0.159, 5.9 min | 33, $0.356, 7.4 min | 28, $0.806, 6.8 min |
| `8f753940` | cut off by the stop | 27, $0.357, 7.4 min | 28, $0.798, 8.0 min |

On the one plan with all three, Flash scored 3 points more than Luna at 2.2 times the cost and 25% more time. V4 Pro scored 2 fewer at 5 times the cost. The rule advances a DeepSeek arm only if it is cheaper or faster at about Luna's score, or at least 8 points better. Neither arm looks likely to meet that, but with one comparable plan it is undecided.

#### Depth, history trimming, and limits

**Deep against standard: undecided.** Three runs per arm on drb2-task8 gave deep 5.0 more points under Sol and 4.7 more under GLM (24.0 against 19.0, and 30.0 against 25.3). Its statements were better supported, 89% against 76%. It cost 1.75 times as much ($0.70 against $0.40) and took 2.3 times as long. The rule needed 5 points under both judges, but three runs per arm could detect only about 8 points, so the study could not have decided.

**History trimming is off.** Untrimmed scouts met every condition of the rule. They read 59% of their input from the prompt cache against 35.5%, cost less, and claimed more points (28.0 against 24.3). The trimming code has since been removed.

**What limited runs was our own settings, more than money.**
- The pacer held Luna to 200,000 tokens a minute when OpenAI allowed 2,000,000. Every earlier deep run, and the deep-against-standard study, ran under that throttle.
- Across 188 calls, scouts used a median of 15 to 28% of their dollar shares, and no real run reached a hard cap.
- Productive-call limits, deadlines, 120-second request timeouts, and output checks cut scouts off.
- The synthesizer's share nearly bound on deep runs, at 88 to 93%.

Most of these limits were loosened in scout-v14 and v15.

#### Where rubric points are lost

The free audit of six drb2-task8 reports on 28 September, and the diagnoses of 23 rescouts since, place the losses at different steps. Together they show where most points go.
- **Most missing points were never found.** The diagnosis sorts each of a rescout's 52 points by the earliest step that met it. Across the 23 rescouts of the search, Serper, and DeepSeek studies, 17 to 28 points a run were not found in any of the research, or 35 in one partial run. Only 0 to 4 a run were seen in the research but never claimed. With about 7 points out of reach (below), roughly 10 to 20 reachable points a run were never found.
- **Claims lose a few more.** Scouts make narrow, paper-level claims from few reviews. "Computational cost" appears in five of six ledgers, but only inside quotes, never as a claim. This is real, but on the diagnoses it accounts for a few points a run, not most of the gap.
- **Synthesis drops little.** Reports cite 90 to 100% of their ledgers' claims.
- **About 7 of 52 points were never met in 141 stored grades.** Examples are the Dynamic Database of Solid-State Electrolytes and the ASM Alloy Center. Many probably appear only in the blocked expert report, so the practical ceiling is about 45.
- **Some points are missed whatever the engine or model.** In the Serper and DeepSeek studies, no run found topology optimization's role in any of its research, and no DeepSeek-study run found Monte Carlo Tree Search. That suggests the plans or the searches never looked for them.
- **Coverage counts a dimension as met by any one claim.** So a question can read as covered while two of its three categories have no disadvantage stated.

The claim fix (scout-v13) was built from the report audit, and it has not been measured. Since most points are never found, a change to searching and reading, such as reading a review for each category question, may matter more. The diagnoses were graded by GLM alone, and how much of the research the judge is shown bounds what it can call "seen".

#### Judges

The two rubric judges rank reports the same way, but they do not score them the same.
- **Report grades.** `gpt-6-sol@high` and `zai:glm-5.3@high` agree on 92.6% of points. On the short cases they agree exactly. On the DRB-II cases GLM scores a report 1 to 8 points higher, and gives nearly all of the disputed credit.
- **Rescout diagnoses.** On ten rescouts, Sol gave 3 to 9 fewer claimed points, 6.1 fewer on average.
- **Much of the gap is URL literalness.** Sol does not credit `oqmd.org` for `www.oqmd.org`. Hand checks on st07 favoured GLM on equivalent wording.

No disputed point on drb2-task8 has been checked by hand, so which judge is closer to right there is not known.

#### Support and synthesis

**Support.**
- The support audit found that about one in five quoted statements says more than its quotes. The overreach usually enters at the scout's claim, and once at the synthesizer.
- "Verified" means a quote string was found in a source. The 29 September architectural review shows that quote matching can change a number's meaning, and that the `supported` label is a traceability heuristic, not a verdict on the report.

**Claude citations synthesis** (test branch, synthesis v8). It works end to end:
- all 34 citations of an Opus 5.5@medium resynthesis of st07 matched their passages;
- 12 of 47 answer sentences are uncited, 7 of them framing and 5 of them facts;
- it cost $0.13.

Nothing on this branch has been graded, so there is no evidence yet that it improves reports.

#### Limits on the data

These limit every conclusion above.

**One case, and a contaminated one.**
- Every search, model, trimming, and depth comparison ran on drb2-task8 alone, mostly by rescouting the same three stored scout-v10 plans. Those plans were made by one planner, `gpt-6-sol@high`, at standard depth.
- drb2-task8 is a development case the design was tuned on. From scout-v6 its wording, including one expected database, was in the prompts, so its absolute scores since v6 are suspect. Comparisons within one study still stand, because every arm saw the same prompts.
- Every stored drb2-task8-plus score was earned with the blocked expert report's abstract in view.
- The short cases (st04, st05, st07) were written in-house, and st07 matches one synthesizer instruction.
- The three held-out cases and the four newer development cases have never been run.

**Samples too small to decide.**
- Almost every paid comparison was one run per plan per arm, three per arm in all. At that size the smallest detectable difference is 7.5 to 14 points of 52, and no measured difference reached it.
- Deep against standard would have needed about 12 runs per arm on one case to see 4 points.
- Screens, cheap checks, and single runs cannot change a default.

**What is being scored.**
- Rescout studies score claims, not reports, and were graded by GLM alone, the more lenient judge.
- Points claimed, verified quotes, and coverage are proxies. Coverage measures labels, not whether a requirement is established.
- Cheap checks run every role on `gpt-6-luna@low` and say nothing about quality.

**A moving target.**
- The workflow went from scout-v1 to scout-v20 in five days. Limits, prompts, retries, and prices changed between studies: the Serper rerun ran on v15, and the study it reruns on v14.
- So numbers from different studies should not be pooled or compared, only numbers within one study.

**Confounds in the environment.**
- The DuckDuckGo scraper is not deterministic.
- The pacer throttled Luna tenfold until 28 September.
- An OpenAI outage returned HTTP 500s on 28 September. Before scout-429-v5, one 500 lost a scout's whole question.
- Two studies ran at once and shared rate limits, though each had its own cache.
- One search study was abandoned after 5 of 6 rescouts timed out, and says nothing about the engines.

**Unwritten and partial results.** The Serper rerun and the DeepSeek study are summarized here from their stored outputs, and are not yet in the study log. The DeepSeek study is missing Luna on two plans and a third plan entirely.

**Costs are partial views.** Rescout costs leave out planning and synthesis. Some study summaries include the diagnosis in a run's cost and some entries do not. Costs rest on local price corrections in `prices.toml`.

#### What this suggests next

1. **Write up the Serper rerun and the partial DeepSeek study** in the study log. If Serper's rule holds on a careful reading, run its four-case confirmation. It would also be worth comparing Serper with the DuckDuckGo-then-Exa hybrid, since both mainly fix availability.
2. **Stop spending on drb2-task8 alone.** Use several development cases per study, and confirm on the held-out cases.
3. **Size each study from its detectable difference before paying for it.** Prefer measures that vary less, such as the diagnosis's count of points never found, and use both judges where a decision rests on a few points.
4. **Measure changes to finding and reading sources first**, since most points are never found: better scholarly search, a review read in full for each category question, and the reading fallback on graded runs. Then measure the claim fix, which targets the smaller loss.

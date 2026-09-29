# Lessons from the first design

This page keeps what the first design of Research Loop taught, so the smaller Scout design can use it after the code that produced it is gone. That code is tagged `archive/pre-scout-2026-09`. Its database, study records, and search cache are archived outside the repository (see "Where the old work is" below). Every figure here comes from runs recorded between 23 and 25 September 2026.

## The old workflow, for comparison

The first design ran every question through a six-role graph: planner, parallel scouts, gap analyst, deep dives, synthesizer, and verifier, with up to two verification rounds.

The README question ("Is SWE-bench Verified still a trustworthy measure of coding-agent progress?") ran on it in job `bf89797d`, with results as follows:

- It cost $2.26 and took 11.8 minutes.
- The verifier supported all 15 statements it checked.
- 97.8% of quotes were found in tool output, and every cited source had been returned by a tool.
- The rubric judge (`openai:gpt-6-sol` at high effort, judge version 2) gave it 0.556: 5 of 9 points.

This is the "before" figure for the same question under Scout. The grade is in `benchmark_outputs/grades/baseline-bf89797d-st07.jsonl`.

The settings-study pilots on a deep DeepResearch Bench II task cost $1.67 to $5.03 each and took 25 to 42 minutes. Several never produced a report.

## Research loops and their limits

**Loops that cannot see their limits run into them.** In five pilots, every scout and deep dive stopped on a limit, and a separate salvage call wrote its result. Salvage took 1.6 to 2.9 minutes per call and about a third of each research phase. It sometimes failed outright, taking the loop's research with it.

With budget notes, which end each request with the requests and tool calls left, 9 of 10 loops returned their result on their own. Without them, 0 of 16 did. Withdrawing the tools on the last request forces a result from the full context instead of a summary of cut-down output.

**Count misses apart from productive calls.** In pilot 8, two-country scouts spent all 48 tool calls in 14 to 17 requests, mostly on empty searches and failed fetches. One scout had 8 of 9 searches empty and 28 of 36 fetches failed, and returned no claims. A budget of productive calls (a search with results, a fetch with text) plus a separate budget of misses stops a loop that is only failing, without starving one that is working. The study settled on 12 requests, 16 productive calls, and 12 misses per scout.

**Token limits bind before request limits at depth.** The first pilot's 400,000-token limit stopped scouts at 10 to 17 requests, while they were still finding sources they went on to cite. At shallow depth the README run's scouts had 97% of their cited sources by request 8. On a deep task, only 58% arrived by request 8 and 90% by request 20.

**Broad questions starve a scout.** A question spanning seven countries exhausted its scout at any limit tried. One subject per scout works better. How a planner splits a question varies from run to run and by model: `gpt-6-sol` usually split by entity, while Opus 5.5 paired entities. Best-of-3 planning and a landscape survey before planning did not make plans steadier.

## Sources and tools

**Retrieval is the weakest link.** In pilots 4 and 5, 44% of scout tool calls returned nothing usable:

- 37% of fetches failed with HTTP 403 or 404.
- DuckDuckGo was unavailable on 31% of searches.
- PDFs were refused until `web_fetch` learned to read them and to recognize a PDF by its first bytes.

A report built on thin evidence draws heavy verifier findings whichever model writes it.

**Quote checks need tolerant matching.** Every `not_found` quote in the p01 calibration run was text the run had actually fetched. pypdf splits words, lists get reformatted, and quoted segments appear out of order. Comparing only letters and digits after NFKC normalization and case folding, and checking each "..." segment separately, cut false negatives from 8 of 92 to 2 of 120. A quote that crosses a PDF running header is still missed.

**A source a tool returned is not a source that supports the claim.** `source_check: observed` shows only that a tool returned the source. Evidence needs to record whether the passage came from a search snippet, metadata, an abstract, or full text. The first design did not record this.

## Models and providers

**Scout on Flash.** On the same plan and recorded searches, `zai:glm-5.3-flash` scouted as well as `zai:glm-5.3` at about a tenth of the cost. Its grades were 0.12 to 0.22 against 0.21, and fewer of its quotes were missing from tool output: 4% against 7%. It is less steady on broad questions: one of three runs lost a two-country question.

**Plan on `gpt-6-sol`.** It planned at a third of Opus 5.5's cost. Opus 5.5 refused to plan a question on T-cell exhaustion as a biological risk, so planner and synthesizer routes need a fallback model that takes refusals and provider errors.

**Synthesizers.** Opus 5.5 at medium effort synthesized a large ledger in one streamed request, in about 92 seconds for $0.43. `glm-5.3-flash` at max did it for $0.03 but took 819 seconds. Z.ai does not stream an output tool call's arguments, so a long report arrives in one burst after minutes of silence, which risks the 600-second read timeout.

One verification per report varies more between runs than synthesizer prompts or models differed. The study could not rank synthesizers on single verifications.

**Stream long outputs, and set timeouts.** Pilot 8's unstreamed synthesis on `glm-5.3` at max failed after three 600-second attempts, because the OpenAI SDK that the Z.ai provider uses retries timeouts twice. Streaming makes the read timeout apply between chunks.

**Reasoning effort.** PydanticAI sends `thinking="xhigh"` to GLM-5.3 models as Z.ai's `reasoning_effort: "max"`. Opus 5.5 defaults to `medium` and thinks more at a given level than Opus 5 did. It cannot turn thinking off, and it rejects a forced `tool_choice`. PydanticAI therefore uses native JSON-schema output whenever thinking is set.

**A Z.ai 429 usually means an empty balance.** Check the balance before assuming a rate limit. One scout's 429 used to cancel its siblings and fail the whole job.

**Prices need local corrections.** genai-prices priced `glm-5.3-flash` at half its list price and had no `glm-5.3-flashx`. A cost cap is only as good as the price table, so the corrections live in `src/research_loop/prices.toml` with their sources.

## Grading

**The rubric judge is stable at high effort.** Repeat gradings of a report varied by at most 1 point in 72. At low effort, the judge missed points that reports stated plainly.

Rubric points should state one fact each and carry no incidental details. The judge must credit equivalent wording.

**A trial must be able to decide something before it spends.** The prompt trials cost about $6 and decided nothing, because single verifications vary more than the prompts differed. Estimate from the most expensive call observed, cap the script, and check that the sample can detect the difference you are looking for.

## Operations

- Record paid trials as jobs in Postgres with their transcripts. Results kept only in memory were lost when a trial crashed.
- To tell whether a long call is still receiving data, read the socket's `bytes_received`. `rchar` does not count socket reads.
- Test with an empty benchmark cache. A warm local cache hid downloads that CI refuses.

## Where the old work is

- **Code:** git tag `archive/pre-scout-2026-09`. It holds the graph, the legacy loop, long-horizon studies, benchmark adapters, the settings study and its trial scripts, attachments, and LaTeX reports.
- **Data:** `~/research-loop-archive/`, with a `pg_dump` of the `research_loop` database, one JSONL file per table, `benchmark_outputs/` with the study records, and the recorded search cache. Its `README.md` explains how to restore it.
- **The settings study's full decision log** is in `docs/settings-study.md` at that tag.

## Lessons from Scout, 25 to 27 September 2026

These come from Scout's first three days of studies; the runs behind them are in [study-log.md](study-log.md).

**Measure before adopting, and on enough runs to decide.** Scout went from version 1 to version 8 in about two and a half days, and most versions were adopted after one or two runs per arm, or none, while the studies themselves reported "a direction, not a result". Only 23 reports existed by the end, 14 of them with the production models. A change now needs a decision rule written before it is paid for, and a sample that can meet it.

**A metric can pull the work off course.** From scout-v2 on, every workflow change aimed at one problem measured on one case: rubric recall on drb2-task8's list of databases. The short and false-premise cases went unrun for six versions, and nothing measured whether a report's statements say what their sources say. Keep a small, mixed set of cases in every study, and measure the thing the project is for.

**Traceable is not the same as supported.** Code verified that each quote appeared in its cited source, and a report could still be labeled supported while a third of its statements rested on a scout's own summary, and while about one in five quoted statements said more than its quotes. Checking quotes is necessary; the support audit checks what quotes cannot.

**Context size is a cost and a clock.** Every request of a scout resends its history. Exa's uncapped highlights put about 20 times the text of a DuckDuckGo search into that history, so scouts waited under Luna's 200,000-token-a-minute limit and ran five times longer. Read time and cost from the token timeline before blaming the budget: the slow short runs were waiting on the rate limit, not using more requests.

**Reading pages is the weakest step.** 38% of distinct page fetches failed, mostly 403s and challenge pages from the publishers research needs (MDPI, RSC, ScienceDirect, OpenAI). A better search engine only surfaces more pages that cannot be read. Legitimate open-access copies and a reading fallback recover many of them.

**One transient error can decide a report.** A single TLS fault ended the scout for st07's decisive question after it had read eight pages. A scout now sends a request once more after a connection fault that is not a timeout.

**The judge is part of the measurement.** Two judges agreed on 92.6% of rubric points, but on the DeepResearch Bench II cases the choice of judge moved a score by up to 8 points. Grade close decisions with both and check their disagreements by hand.

**Fuzzing guards plumbing, not quality.** The bug-finding harness found five bugs as it was built, one of them by fuzzing, and catches every earlier bug when it is put back. It cannot see a claim that overreaches its quote or a page that cannot be read. A bug a paid run finds now gets the narrowest test that would have caught it, and fuzzing only when it comes from parts of a run interacting.

**A guarantee covers only the paths that enforce it.** Blocked sources were matched by address in web search and fetch but not in the scholarly tools, and drb2-task8's blocked expert report reached five early ledgers by its DOI. The study ceiling was compared with estimates while each run kept its full cap, so it did not bound spending. An outside audit found both by writing counterexamples against the stated guarantee rather than against the code's own rules. The fuzz model shared those rules, so it could not find them.

## Lessons from 28 September 2026

**An example in a prompt is part of the test.** The claim fix was designed from drb2-task8's rubric, and its examples were drb2-task8's own: its category names, its database fields, and, since scout-v6, one of its expected databases in the scouts' output schema. A higher drb2-task8 score could then come from the prompt echoing the case rather than from the fix. A development case may shape a fix, but not the text a model sees, and a fix is confirmed on held-out cases. A test now keeps every frozen case's wording out of model-visible text; a paraphrase still needs reading ([audit](audit-2026-09-28-case-contamination.md)).

**A limit should stop work only where it saves something.** In one day, request timeouts, the token limit, and output checks each cost a question while money and time were left. Where a limit is reached, the scout should return what it has, and the dollar share and deadline should bound the work.

**Stopping a study must stop its runs cleanly.** `subprocess.run` answers an interrupt with SIGKILL, so a stopped study's current run could not record itself and stayed marked running.


## Provenance of new Scout reports (29 September 2026)

**A valid citation pointer is not the same as support for an assertion.** A report can cite a search snippet while the same claim also has a full page from another source; grading the claim's whole evidence set made that assertion look well read. Evidence version 8 saves the exact checked tool-text span and a hash of its source snapshot, and report assertions keep the particular passage and source IDs they cite. The support check and audit follow those links. Numeric punctuation and attached units need stricter quote matching than ordinary PDF formatting. This design has offline tests but no paid study result yet; whether its labels agree with human review still needs measurement.

## Blocking and rebuilding, later on 29 September 2026

**Block what the engines show, not what the benchmark names.** The blocked title was the review's whole title. Search engines cut it short or append their site's name, and copies drop the subtitle, so the full title never appeared in the results that showed the review's abstract to scouts. The check passed its tests because the tests used the whole title. A block needs test cases taken from real stored results, and every study of a case with blocked sources needs a replay of what its scouts were shown before its results are read. Perfect blocking on the live web is not achievable, but measuring exposure is.

**A study entry's "no blocked source reached a scout" must say how it was checked.** The v14 search study counted refused fetches, and so missed snippets and two rescouts that cited copies of the blocked review.

**Checking reconstructed provenance keeps finding new cases.** Every evidence version since v4 fixed how a scout's copied quote is matched back to tool text, and the next review found more mismatches. The passage-evidence plan removes the reconstruction: code splits tool output into passages before a model sees it, and models cite their IDs.

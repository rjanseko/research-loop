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

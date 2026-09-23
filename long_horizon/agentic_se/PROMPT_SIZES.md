# Prompt sizes: where they come from and why they cause problems

Status as of 2026-09-23. The measurements come from the successful calibration pilot (question
`p01`, job `253311b6`, now archived in `benchmark_outputs/archive/long_horizon_pilot-2026-09-23/`).
They were taken after the fixes
in commits `5c62878` and `36ce94e`. The deeper research settings from `36ce94e` had not yet been
used for a paid run. Numbers marked *estimate* are projections, not measurements.

Since then, fetch version 2 added paging through long documents (option 5). Gap analysis,
synthesis, and verification now send a projection of the ledger instead of the full dump
(options 1 and 2), and those three calls are refused before the model when one validation
retry would not fit their token limit (option 3). The measurements through the rerun are what
those runs sent. The [projection](#ledger-prompt-projection) section rebuilds the rerun prompts
with the new view. Long-horizon synthesis sends a source table, report claims, caveats, unresolved
questions, contradictions, and verifier findings; the full ledger stays in the aggregated file
(option 4). The finishing and long-horizon token limits were then raised so questions deeper than p01
still fit a retry; see [Headroom](#headroom).

## Summary

Every model call in this pipeline gets a prompt that code builds from JSON. Four problems stand
out, most serious first:

1. **The old long-horizon synthesis prompt did not fit.** One pilot question produced 82,719 characters.
   All 11 questions on that prompt would have been about 910,000 characters, roughly 367,000 Opus
   tokens per request, over the `[synthesis]` limits. The current prompt (option 4) is 47,312
   characters for p01 at the rerun's depth. Eleven questions of that depth are 463,000 to 510,000
   characters, under the 600,000 character cap, and a prompt at that cap fits one citation retry
   inside the 600,000 token limit, counted at 2.5 characters per token.
2. **The pilot sent gap analysis, synthesis, and verification the whole evidence ledger.** That block
   was about 97,000 characters. Those three calls now send the projection, and each is refused before
   the model when one validation retry would not fit. On the trimmed rerun prompts, all three fit
   one retry, and synthesis and verification now fit a retry of prompts about 1.9 times that size.
   These steps still have no salvage path.
3. **Agent loops resend their whole history.** Scouts and deep dives start with small prompts
   (about 2,000 characters). Every turn resends everything so far, so tokens grow roughly with the
   square of the number of turns. A scout used about 243,000 input tokens in 12 turns. Two of the
   three scouts and the deep dive stopped on a request or tool-call limit; nothing stopped on
   dollars.
4. **Some evidence never reached the pilot prompts.** Fetches returned only the first
   12,000 characters of a document; paging has since landed. Salvage used to keep the first
   results that fit, 4,000 characters each; it now keeps every useful result within 64,000 characters
   (see [Salvage selection](#salvage-selection)). The long-horizon prompt no longer sends excerpts. openai.com
   blocks the fetcher. The pilot report lists missing primary-source details as caveats because of this.

## Background: prompts and limits in this pipeline

- **A prompt** is the JSON text built for one model call: the task, the relevant evidence, and
  the constraints. The agent's fixed instructions and the tool and output schemas are sent with it.
- **A tool loop** (scouts, deep dives) is one agent run with many requests. After each tool call,
  the next request resends the instructions, the schemas, the prompt, and every earlier call and
  result.
- **Limits per call** come from each role's `ModelRoute` in `policy.py`. The spec adjusts some
  of them in `spec.toml`. There are four:
  - `max_requests`;
  - `max_tool_calls`;
  - `total_tokens_limit`, which counts input plus output added up across all requests in the run;
  - `cost_limit`.

  PydanticAI raises `UsageLimitExceeded` when a limit is hit. Scouts and deep dives are salvaged
  when that happens; gap analysis, synthesis, and verification are not.
- **Characters versus tokens.** Tokens are what providers bill for and what the limits count.
  On the same JSON, OpenAI models measured about **3.8 characters per token** and Opus about
  **2.5**. The same prompt therefore costs roughly 1.5 times as many tokens on Opus, which runs the
  per-question synthesis and the long-horizon synthesis. These ratios come from pilot tasks (gap
  analysis 87,125 characters to 22,829 input tokens, and the verifier 127,067 to 32,950, both on
  OpenAI; synthesis 99,254 to 39,986 on Opus). They are approximate, because the input also
  includes the instructions and the output schema.

## Where prompts come from

| Role | Built in | What the prompt contains | Pilot limits (requests / tool calls / tokens / $) |
|---|---|---|---|
| Planner | `async_orchestrator.py` `_plan`, shared by graph and legacy | objective, constraints, planning guidance | 6 / 4 / 70k / $2.50 |
| Scout | `_run_scout` | one subquestion and the constraints; tool history grows each turn | 12 / 24 / 400k / $0.80 |
| Gap analysis | `async_orchestrator.py` `_analyze_gaps`, shared by graph and legacy | objective, plan, projected results (one source table; search fields kept) | 6 / 4 / 70k / $1.25 |
| Deep dive | `_run_gap` | subquestion, gap, constraints; tool history grows each turn | 20 / 40 / 180k / $3.00 during the pilot (now $1.25; route default $5) |
| Salvage | `_run_research` | the original prompt plus `gathered_evidence`: every useful tool result, cut to one shared allowance within 64,000 characters, and `gathered_counts` (`_SALVAGE_*`) | 2 requests / 80k / a quarter of the route's $ cap during the pilot ($0.20 scout, $0.75 deep dive); now half |
| Synthesis | `_synthesize` | objective, projected ledger (one source table; search fields omitted), constraints | 8 / 4 / 120k (now 180k) / $3.50 |
| Verifier | `_verify` | objective, the report, projected ledger limited to claims the report cites or a contradiction names, constraints | 8 / 8 / 100k (now 150k) / $2.50 |
| Long-horizon synthesis | `long_horizon.py` `synthesis_prompt` | one source table (title, url, date per work); for each question: report claims (statement, claim refs, lowest cited confidence, supporting and contradicting works, source type, publication status, not-found and retracted flags), caveats, unresolved questions, contradictions, verifier findings. The full ledger stays in the aggregated file | `[synthesis]`: 2 requests / 400k tokens (now 600k) / 36k output tokens / $6, prompt at most 360,000 characters (now 600,000) |

## Measured in the pilot

| Task | Prompt chars | Requests | Input tokens | Output tokens | Tokens used / limit | Cost | Ended |
|---|---:|---:|---:|---:|---|---:|---|
| Planner | 2,126 | 1 | 1,624 | 1,207 | 2.8k / 70k | $0.04 | done |
| Scout q1 | 1,627 | 12 | 243,356 | 7,391 | 251k / 400k | $0.14 | request limit, then salvaged |
| Scout q2 | 1,641 | 12 | 219,470 | 10,253 | 230k / 400k | $0.14 | request limit, then salvaged |
| Scout q3 | 1,840 | 9 | 251,038 | 16,801 | 268k / 400k | $0.19 | done |
| Scout salvage q1, q2 | 6.5–6.8k stored* | 2 | 35–38k | 9–12k | 45–51k / 80k | $0.07–0.09 | done |
| Gap analysis | 87,125 | 1 | 22,829 | 1,272 | 24.1k / 70k | $0.14 | done |
| Deep dive q1 | 2,167 | 10 | 158,600 | 2,572 | 161k / 180k | $0.66 | tool-call limit (40), then salvaged |
| Deep-dive salvage | — | 1 | 14,306 | 4,282 | 18.6k / 80k | $0.39 | done |
| Synthesis | 99,254 | 1 | 39,986 | 10,844 | 50.8k / 120k | $0.47 | done |
| Verifier | 127,067 | 1 | 32,950 | 8,669 | 41.6k / 100k | $0.34 | done |

\* Salvage prompts are stored with the replayed tool output replaced by hashes. The model saw
the full gathered evidence, which accounts for the 35–38k input tokens.

The run cost $2.66 in total. Synthesis wrote 10,844 output tokens, which would have been cut off
at Anthropic's default of 4,096. That confirms the `max_tokens` setting added to the planner and
synthesis routes in `b22fe28` was needed.

## Problem 1: the long-horizon synthesis prompt

These figures are the pilot prompt, before option 4. The current prompt is the claim list in the summary: report claims, caveats, contradictions, and verifier findings, with no report prose and no excerpts. The 48,000-token output cap in the list below is the old one; it is 36,000 now, so one retry of a 360,000-character prompt fits the 400,000 token limit.

For the pilot question the old prompt was **82,719 characters**:

| Part | Characters | Contents |
|---|---:|---|
| Long-horizon header | 1,010 | title, dates, source policy, output fields |
| Question block | 23,728 | report text 11,480; caveats 3,419; contradictions 2,842; unresolved questions 5,703 |
| Evidence | 57,910 | 45 claims and 68 evidence items: statements 18,305; excerpts (at most 300 each) 15,245; source metadata 16,957; the rest is JSON structure |

**Projection for the full study on that old prompt** *(estimate)*: 11 questions × about 82,700 characters ≈ **910,000
characters**. At Opus's ~2.5 characters per token, that is **≈367,000 input tokens per request**.
Compared with the `[synthesis]` limits as they stood then:

- **`max_prompt_chars = 360,000`.** Four questions of that prompt fit (about 328,000 characters); five do not
  (about 410,000). The character check still runs during `--synthesize --dry-run` and before any paid call.
- **`total_tokens_limit = 400,000`.** One request of about 367,000 tokens plus the old 48,000-token output
  cap went over the limit. A citation-fix retry would have resent the whole prompt.
- **`cost_limit_usd = 6.0`.** Each request of that prompt cost about $1.83 of input plus up to $1.20 of output,
  roughly $3. A retry reached the cap.
- **The model's context window** was not checked against a prompt this size.

**That projection was a floor for the old prompt.** Deeper research settings add claims. Option 4 drops the report prose, unresolved questions, and excerpts; eleven questions at the rerun's depth are about 351,000 characters, and one retry fits.

**Why the old prompt was built this way.** The synthesis output must cite claim refs, and a validator rejects any ref that does not exist. The old prompt therefore sent every claim with its evidence, and the report text beside it. The current prompt keeps the refs and the source facts used to classify a claim. Excerpts stay in `evidence_ledger.json`.

## Problem 2: the three per-question steps that carry the whole ledger

These figures are what the pilot sent. Gap analysis, synthesis, and verification now send the projection under [Ledger prompt projection](#ledger-prompt-projection), and each call is refused before the model when one retry would not fit.

In the pilot, the full ledger block was **97,291 characters**: 4 results, 45 claims, 68 evidence items, and 55 distinct
sources.

| Part | Characters | Note |
|---|---:|---|
| Source metadata | 30,530 | about **10,900** of it is null fields (`"doi": null` and similar) repeated on every evidence item |
| Claim statements | 18,305 | |
| Evidence excerpts | 17,272 | |
| Result conclusions | 7,190 | |
| Unresolved questions | 5,703 | |
| Contradictions | 3,587 | |
| Suggested follow-ups | 2,484 | of little use to synthesis and verification |
| Search queries used | 2,056 | of little use to synthesis and verification |
| JSON structure and confidences | the rest | |

Dropping null and empty fields alone removed about 11% of the block (97,291 to 86,394). That omission is part of the projection.

**The same block was sent three times.** Gap analysis got it as `results` (81,798 characters,
before the deep dive's result was added). Synthesis got it as `evidence`. The verifier got it
again, plus the 27,801-character report. The projection lists each source once, and the verifier receives only claims the report cites or a contradiction names.

**On this ledger, headroom depended on not retrying.** Evidence version 3 checks citations, so a bad claim ID causes a retry that resends the prompt and the first output. The call is now refused before the model when that retry would not fit. The estimates below are the pilot ledger, before the projection:

| Step | One attempt / limit | With one retry *(estimate)* | Deeper research (1.5–2× ledger) with one retry *(estimate)* |
|---|---|---|---|
| Synthesis (Opus) | 50.8k / 120k | ~113k (fits, just) | ~150–190k (**over**) |
| Verifier | 41.6k / 100k | ~92k | ~125–160k (**over**) |
| Gap analysis | 24.1k / 70k | ~50k | ~70–95k (**over**) |

These estimates use: total ≈ 2 × prompt tokens + 3 × output tokens. That is the first request, a
retry carrying the prompt and the first answer, and the second answer.

These three steps are not salvaged. If one goes over its limit, the whole question fails, and
by then the scouts and deep dives have already spent most of the budget (about $3–4 per question
under the new settings, *estimate*). The 1.5–2× growth is a guess: the pilot ran at the old,
shallower settings.

## Problem 3: agent loops grow with every turn

Scout and deep-dive prompts are small: 1,600–2,200 characters. Their cost comes from history. Each
request resends the instructions, the tool schemas, the prompt, and every earlier tool call and
result. Total input therefore grows roughly with the square of the number of turns.

- **Scouts:** q1 used 243,356 input tokens over 12 requests, about 20,000 per request on average.
  q3 used 251,038 over 9, about 28,000 per request.
- **Deep dive:** 158,600 input tokens over 10 requests. At GPT-6-astra's $20 per million input
  tokens, that alone is most of its $0.66.

**Average size of each tool result, which stays in the history for every later turn:**

| Tool | Avg characters per call (by task) | Note |
|---|---|---|
| `scholar_search` | 10,230 / 16,015 / 12,953 (scouts), 5,932 (deep dive) | the largest: up to 5 OpenAlex and 5 arXiv records, with abstracts |
| `scholar_fetch` | up to 12,362 | full text cut at 12,000 characters |
| `web_fetch` | 2,168–4,258 | main page text |
| `duckduckgo_search` | 613–4,442 | snippets |

**No research run stopped on tokens or dollars.** Two scouts hit the 12-request limit and the deep
dive hit its 40-tool-call limit; the third scout finished on its own after 9 requests. They spent
17–24% of their dollar caps ($0.80 per scout; $3.00 for the deep dive during the pilot). After a
limit, salvage replayed up to 48,000 characters of what the run gathered; see
[Salvage selection](#salvage-selection) for the change since.

**The search guidance was followed only partly.** The study's research notes tell scouts to
start with scholarly search. Their actual tool use:

| Task | web_fetch | scholar_search | scholar_fetch | scholar_get | duckduckgo |
|---|---:|---:|---:|---:|---:|
| Scout q1 | 12 | 2 | 3 | 3 | 3 |
| Scout q2 | 9 | 1 | 1 | 0 | 7 |
| Scout q3 | 4 | 9 | 4 | 2 | 4 |
| Deep dive q1 | 19 | 3 | 6 | 4 | 8 |

**Failed calls still take up history.** In the deep dive, these returned an error or an empty
result:

- 4 of 8 DuckDuckGo searches;
- 7 of 19 `web_fetch` calls;
- 3 of 4 `scholar_get` calls;
- 4 of 6 `scholar_fetch` calls.

Each of those still adds a call and a result to every later turn.

## Problem 4: evidence that never reaches a prompt

- **The pilot's fetches stopped at 12,000 characters, with no way to read further.** This is why the deep
  dive never reached the dataset-construction section and Table 1 of the SWE-bench paper; the pilot
  report's caveats say so directly. Fetch version 2 now pages through documents with `start` and
  `next_start` (see `docs/acquisition.md`).
- **Salvage kept 4,000 characters per tool result and 48,000 in total.** Anything longer or later
  was dropped before the wrap-up call; [Salvage selection](#salvage-selection) describes the fix.
- **The pilot's long-horizon synthesis kept 300 characters of each excerpt.** The current long-horizon prompt does not send excerpts. Per-question prompts still cut an excerpt at 300 characters when the evidence has no quote.
- **openai.com returns HTTP 403** (bot protection), so OpenAI's own posts never enter a prompt. The
  report's OpenAI figures came from secondary sources, and the verifier flagged them as major issues.

## Consequences

| Prompt | Risk | Consequence | When it bites |
|---|---|---|---|
| Long-horizon synthesis | over the character cap, or a retry over the token limit | `--synthesize` refuses (dry run) or fails before a paid call | a prompt above 360,000 characters, or limits whose output cap cannot fit one retry of that cap |
| Gap analysis, synthesis, verifier | one retry goes over the token limit | the question fails after most of its budget is spent | more likely under the deeper settings |
| Scout and deep-dive loops | history grows with the square of the turns | budgets run out early; salvage replaces the final answer | two of three scouts and the deep dive in the pilot |
| Truncation and blocking | evidence never collected | more caveats, weaker or secondary-source findings | long documents; vendor sites behind bot protection |

## Options, ranked by value against effort

Fetch paging (part of option 5), the ledger prompt projection (options 1 and 2), and the
pre-call retry check (option 3) are implemented. On the rerun's ledger, the trimmed projection
leaves room for one retry of synthesis and verification.

1. **Compact evidence serialization for the ledger-carrying prompts.** **Done.**
   - Drop null and empty fields (about −11%).
   - Leave out `search_queries_used` and `suggested_followups` for synthesis and the verifier
     (about −4.5k characters).
   - Send each source once in a table referenced by ID, instead of repeating it on each evidence
     item (68 items point to 55 sources here; the saving grows with depth).

   On the p01 rerun those two search fields were already empty, so leaving them out saved nothing
   further. The measurement is under [Ledger prompt projection](#ledger-prompt-projection).
2. **Send the verifier only what it checks:** the claims the report cites
   (`FinalReport.claim_ids_used`) rather than the whole ledger. **Done.** The rerun's report
   cited 55 of 56 claims, so this removed one claim and one source. Claims a contradiction names
   are kept even when uncited, so each contradiction the verifier sees can be judged, and a
   result with no kept claims still shows its question and conclusion, so the verifier can ask
   for follow-ups on questions the report left out. On the rerun every contradiction's claims
   were already cited, so the verifier prompt is the same size.
3. **Size the finishing steps' token limits from the prompt.** **Done** for gap analysis,
   synthesis, and verification. Before the call, input tokens are estimated from the prompt
   (2.5 characters per token for Anthropic, 3.8 for OpenAI, 2.5 for any other provider) and the
   call is refused when `2 × input + 3 × output allowance` exceeds `total_tokens_limit`. The
   allowance is 2,000, 12,000, and 9,000 tokens for those three roles, the larger calibration
   answer rounded up, and never more than the route's `max_tokens`. The synthesis and verifier
   limits were later raised to 180k and 150k tokens; see [Headroom](#headroom). The question fails with `PromptExceedsRetryBudget`, the task is stored with no
   model request, and the ledger gathered so far is kept. On the trimmed rerun prompts, all three
   finishing steps fit one retry; see [Ledger prompt projection](#ledger-prompt-projection).
4. **Slim the long-horizon synthesis prompt.** **Done.** Each question contributes its report claims
   (statement, claim refs, lowest cited confidence, supporting and contradicting work ids and counts, source type, publication status, and a not-found quote, source, or retracted flag),
   caveats, unresolved questions, contradictions, and verifier findings. One source table lists each
   cited work once, with its title, url, and date, so the synthesizer can tell a vendor's report
   from independent work and place it in the publication window. The report prose and per-claim
   excerpts stay out; the full evidence remains in the aggregated `evidence_ledger.json`. Rebuilt
   from the p01 rerun, one question is 47,312 characters, 4,682 of them the source table. Eleven
   questions of that depth are 462,812 characters if they cite the same works and 509,632 if none
   do, under the 600,000 cap. Two-stage merging is the fallback beyond that. A retry is checked on
   the actual prompt when synthesizing (`--synthesize`, including `--dry-run`): 2 × input + 3 ×
   `max_output_tokens`, at the synthesis route's characters-per-token ratio. A prompt at the
   600,000 character cap is about 588,000 tokens at 2.5 characters per token, inside the 600,000
   limit. Loading the spec does not run this check, so questions run and aggregate whatever the
   synthesis limits are.
5. **Make the agent loops cheaper.**
   - Add paging to the fetch tools, so long documents can be read in parts instead of lost.
     **Done** as fetch version 2: a `start` argument, `next_start` in results, and a per-job
     document memo.
   - Trim `scholar_search` results: shorter abstracts, fewer arXiv records.
   - Consider a history-compaction capability.

   Paging and trimming change what benchmark runs see, so they need a `docs/benchmarks.md` note
   and a new fetch version.
6. **Steer the search more strongly.** Move the key search guidance from the study's research
   notes, which reach the model as constraints, into the scout instructions in `agents.py`.

## Rerun under evidence version 3

p01 was rerun on 2026-09-23 (job `46044486`, commit `fd691fd`) with the deeper research settings,
`evidence_version` 3, and `fetch_version` 3. It took 22 minutes and cost $2.94 (the first pilot:
$2.66), and produced 56 claims, 108 evidence items, and 62 sources (45, 68, and 55 before).

| Step | First pilot: prompt / tokens used of limit | Rerun: prompt / tokens used of limit |
|---|---|---|
| Gap analysis | 87,125 chars / 24.1k of 70k | 88,187 chars / 24.0k of 70k |
| Synthesis | 99,254 chars / 50.8k of 120k | 148,387 chars / 69.7k of 120k |
| Verifier | 127,067 chars / 41.6k of 100k | 177,808 chars / 54.5k of 100k |
| Long-horizon synthesis prompt | 82,719 chars | 111,722 chars |

What changed on that rerun. It still sent the full ledger. The [projection](#ledger-prompt-projection) rebuilds these prompts: one retry of each finishing step fits, and eleven questions fit the long-horizon character cap with room for one retry.

- **Problem 2 had arrived for the unprojected ledger.** At these sizes one retry of synthesis (about 2 x 58k input + 3 x 11k
  output tokens) or of the verifier (about 2 x 46k + 3 x 9k) exceeds its token limit *(estimate)*.
  Evidence version 3 retries an output that cites an unknown claim ID, so such a retry would have
  ended the question with `UsageLimitExceeded` rather than fix the citation. Neither retried in this run.
  The pre-call check now refuses that case before the model, and the trimmed prompts fit.
- **Problem 1 was closer on the old long-horizon prompt.** At about 112,000 characters per question, three questions fit the
  360,000-character synthesis cap and four do not *(estimate)*. Option 4 brings eleven questions of the rerun's depth under that cap.
- **Research loops still end on limits.** Two of three scouts stopped at their 16-request limit,
  and both deep dives stopped on their 180k-token limit (162k and 185k input tokens), now that
  paging lets them read further; all four were salvaged. No research call stopped on dollars.
- **The new checks.** Synthesis and verification each took one request (no citation retry); every
  claim ID the report and verifier cite resolves in the ledger stored in Postgres; 84 of 92 quotes
  and all 108 cited sources were found in the run's tool output; the verifier's three follow-ups
  name planned questions. `review_reasons`: 13 of 42 verifier checks unsupported, 2 rated major,
  and the verifier still asking for research (the first pilot: 11 of 45, 5 major).

## Ledger prompt projection

Gap analysis, synthesis, and verification no longer send `model_dump` of every result. The stored
ledger is unchanged. The prompt lists each source once, as `sources` with ids `s1`, `s2`, ..., and
evidence cites `source_id`. Null and empty strings and lists are omitted. Synthesis and verification
also omit `search_queries_used` and `suggested_followups`. The verifier receives the claims the
report cites plus any claim a contradiction names. Every result stays, with its question,
conclusion, contradictions, and unresolved questions, even when none of its claims is kept.

Rebuilt from the rerun's stored prompts (job `46044486`), with the same objective, plan, report, and
constraints. No model call. Character counts use the same JSON encoding as the prompts.

| Step | Stored prompt | Nulls and empty fields omitted | Projected |
|---|---:|---:|---:|
| Gap analysis | 88,187 | 78,356 | 75,426 |
| Synthesis | 148,387 | 123,267 | 113,964 |
| Verifier | 177,808 | 152,688 | 142,376 |

The source table is the step from the middle column to the projected column. Gap analysis keeps
every claim (32 claims, 56 evidence items, 40 sources). Synthesis keeps all 56 claims, 108 evidence
items, and 62 sources. The verifier keeps 55 claims and 61 sources.

Token estimates use the ratios above: about 3.8 characters per token for gap analysis and the
verifier, and about 2.5 for synthesis. A retry is about 2 × input tokens + 3 × the output tokens
this run actually produced.

| Step | Estimated input | One retry *(estimate)* | Limit | Room for one retry |
|---|---:|---:|---:|---|
| Gap analysis | 19.8k | 43.0k (output 1,094) | 70k | yes |
| Synthesis | 45.6k | 125.0k (output 11,269) | 120k | no |
| Verifier | 37.5k | 101.7k (output 8,911) | 100k | no |

The bar for a retry under the deeper settings was about 44k input tokens for synthesis and about
37k for the verifier. That first projection missed it. One attempt still fit. Gap analysis
already had room for a retry.

The projection now also drops fields the finishing steps do not use. A `quote` replaces the
excerpt (93 of 108 evidence items had both; the paraphrase was about 15,000 characters), except
when the quote was not found in tool output: those 8 items keep their paraphrase, which adds
1,165 characters to the p01 verifier view. Other excerpts are cut at 300 characters. `supports` is omitted when it is true. Source rows leave out
`provider`, `full_text_url`, `openalex_id`, `acl_id`, and `accessed_at`; url, title, type,
publication status, doi, and arXiv id stay. Rebuilt the same way:

| Step | Trimmed prompt | One retry against the pre-call allowance | Limit | Room for one retry |
|---|---:|---:|---:|---|
| Gap analysis | 63,475 | 39.4k | 70k | yes |
| Synthesis | 94,390 | 111.5k | 120k | yes |
| Verifier | 123,055 | 91.8k | 100k | yes |

The retry column is the check the run actually makes: 2 × estimated input + 3 × the role's output
allowance (2,000 / 12,000 / 9,000), not the output this run happened to write. A question of this
size now gets past that check.

Source rows are keyed on the fields the model sees, so sources that differ only in a hidden
field share one row. Keeping the claims contradictions name and every result's question left the
p01 verifier view unchanged (91,685 characters): the report cited 55 of 56 claims, and all 21
claims named in contradictions were among them.

Long-horizon synthesis counts distinct works whose evidence supports a report claim. Citations that
share a URL, DOI, arXiv ID, or catalog ID are one work, whatever their locator, provider, fetch
time, or title. Of p01's 41 report claims, 13 now have a lower `source_count` than when every
distinct source JSON counted. Three of them fall below 2, leaving 27 claims instead of 30 with
the two sources a well-supported finding needs. One claim has contradicting evidence, which is
now reported as `contradicting_source_count` instead of adding to `source_count`.

## Headroom

Status as of 2026-09-23. The retry check above let p01 through with about 10% to spare, and eleven
p01-sized questions filled the old long-horizon cap to within 400 characters. A question refused at
synthesis or verification loses its research: the p01 rerun cost $2.94. The finishing calls are
cheap by comparison, and the token limits bound them well below their dollar caps, so the token
limits were raised and the dollar caps kept. Prices are list prices on that date, read from
OpenRouter's model catalog, without prompt caching: Opus 5 $5 / $25 and GPT-5.6 Sol $2 / $10 per
million input / output tokens. Direct provider prices can differ.

| Step | p01 prompt (characters) | Largest prompt one retry fits, before | Limit, before → now | Largest prompt one retry fits, now | Worst-case retry, now | Dollar cap |
|---|---:|---:|---|---:|---:|---:|
| Synthesis | 94,390 | 105,000 | 120k → 180k tokens | 180,000 | $1.62 | $3.50 |
| Verifier | 124,220 *(estimate)* | 138,700 | 100k → 150k tokens | 233,700 | $0.52 | $2.50 |
| Long-horizon synthesis | 462,812–509,632 for eleven | 360,000 (the character cap) | 360,000 chars and 400k tokens → 600,000 and 600k | 600,000 (the character cap) | $5.10 | $6.00 |

A retry is billed only when one happens; none of the five finishing calls on record needed one.
Checking only that one attempt fits was rejected, because a retry that then does not fit is still
billed before the run fails. The long-horizon prompt's source table, confidence, and unresolved
questions add about $0.29 per attempt for eleven questions of p01's depth. Excerpts were left out:
they would add about 32,000 characters per question, about 858,000 characters for eleven, and a
worst-case retry of about $6.13, above the $6 cap.

## Appendix: how to reproduce these numbers

All measurements are read-only. Per-task prompt size and usage:

```sql
select role, question_id, coalesce(effective_config->>'salvage', 'false') as salvage, status,
       length(prompt) as prompt_chars, usage->>'requests' as requests,
       usage->>'input_tokens' as input_tokens, usage->>'output_tokens' as output_tokens,
       usage->>'total_tokens' as total_tokens,
       effective_config->>'total_tokens_limit' as token_limit, usage->>'cost' as cost
  from research_tasks
 where job_id = '253311b6-f12b-41b9-bf8d-dc9519b64791'
 order by started_at;
```

Tool mix and result sizes (persisted fetch results are hashes plus `response_chars`):

```sql
select t.role, t.question_id, e.tool_name, count(*) as calls,
       round(avg(coalesce((e.result->>'response_chars')::int, length(e.result::text)))) as avg_result_chars
  from research_tasks t join research_tool_events e on e.task_id = t.id
 where t.job_id = '253311b6-f12b-41b9-bf8d-dc9519b64791'
   and t.role in ('scout', 'deep_dive') and e.tool_name <> 'final_result'
 group by 1, 2, 3 order by 1 desc, 2, 3;
```

The composition of the ledger-carrying prompts comes from parsing `research_tasks.prompt` for the
gap-analysis, synthesis, and verifier tasks as JSON and measuring each key's serialized length.

Long-horizon synthesis prompt, rebuilt without any model call. The archived p01 outputs predate
objective hashes, so the aggregator no longer accepts them; rerun p01 into
`benchmark_outputs/long_horizon_pilot` first, and expect a different size from a new run:

```python
from pathlib import Path
from research_loop.long_horizon import aggregate_long_horizon, load_spec, synthesis_prompt

spec = load_spec(Path("long_horizon/agentic_se/pilot.toml"))
evidence = aggregate_long_horizon(spec, Path("benchmark_outputs/long_horizon_pilot"))
print(len(synthesis_prompt(spec, evidence)))  # the archived p01 measured 82,719
```

`research-long-horizon --spec long_horizon/agentic_se/pilot.toml --output
benchmark_outputs/long_horizon_pilot --synthesize --dry-run` reports the same size against
`max_prompt_chars`.

The projection table uses the same prompt query with job `46044486-1399-42a0-9705-f0fa0cf0b35f`.
Rebuild each prompt through `EvidenceLedger.prompt_view`: `results` with `include_search=True`
for gap analysis, `evidence` for synthesis, and `evidence` limited to the report's cited claim
IDs for the verifier. Keep the stored objective, plan, report, and constraints, and compare
`json.dumps(..., ensure_ascii=False)` lengths. Do not print the prompts.

## Salvage selection

Status as of 2026-09-23. All four research runs in the p01 evidence-version-3 rerun (job `46044486`)
ran out of budget and were salvaged. Their stored tool events show what the salvage calls received:

| Run | Tool calls | Useful results | Useful characters | Old salvage kept | New salvage keeps |
|---|---:|---:|---:|---|---|
| Deep dive q3 | 44 | 31 (8 errors, 5 unanswered) | 183,291 | first 20, up to 4,000 chars each | all 31: 14 whole, 17 cut to 2,788 |
| Deep dive q1 | 39 | 25 (14 errors) | 165,917 | first 22 | all 25: 7 whole, 18 cut to 3,231 |
| Scout q3 | 25 | 22 (3 errors) | 122,922 | first 19 | all 22: 10 whole, 12 cut to 3,477 |
| Scout q1 | 29 | 27 (2 errors) | 196,735 | first 13 | all 27: 3 whole, 24 cut to 2,560 |

The old selection took results in call order until 48,000 characters were used, counting errors and
unanswered calls against the budget, so the latest results were dropped. In a deep dive those are
usually the most targeted fetches. The new selection (`_gathered_evidence` in
`async_orchestrator.py`) skips errors, unanswered calls, and repeated calls. It keeps every other
result, short ones whole and long ones cut to one shared allowance, the largest the bound allows.
Only if even 800 characters each would not fit are the oldest left out. The bound rose to 64,000
characters, which still leaves room for one validation retry within the salvage route's 80,000
tokens at 2.5 characters per token with a 6,000-token answer; a test holds it to that. The salvage
prompt gains `gathered_counts`, which is stored with it: calls skipped as errors, unanswered, or
repeated, and results kept, cut, or left out.

This changes the salvage prompt, so it is a behavior change for any run that salvages, including
long-horizon studies. The figures in the new column are computed from the stored result sizes; the
effect on evidence quality has not been measured on a paid run.

## p01 rerun under evidence version 4

Status as of 2026-09-23. p01 was rerun (job `31eba511`, $2.50) after the quote-check fix (evidence
version 4, commit `c4ee927`) and the salvage selection change (commit `1abac11`). The Anthropic
account had no credits, so the planner ran on `openai:gpt-5.6-sol` and the synthesizer on
`zai:glm-5.3` (`xai:grok-4.5` returned HTTP 403 on the synthesizer route); the verifier stayed on
`openai:gpt-5.6-sol`, so it still checked another model's report. The earlier run (job `46044486`,
evidence version 3) is archived in `benchmark_outputs/archive/long_horizon_pilot-p01-evidence-v3/`.

| | Evidence version 3 | Evidence version 4 |
|---|---|---|
| Planner / synthesizer | Opus 5 / Opus 5 | GPT-5.6 Sol / GLM-5.3 |
| Cost | $2.94 | $2.50 |
| Claims / sources | 56 / 62 | 62 / 86 |
| Quotes not found | 8 of 92 (8.7%) | 2 of 120 (1.7%) |
| Verifier checks unsupported | 13 of 42 (31%) | 8 of 32 (25%) |
| Major findings | 2, both from quotes the checker missed | 2, both about the report's content |
| Research calls salvaged | 2 scouts, 2 deep dives | 2 deep dives |
| Results given to each salvage | first 13–21 | all useful: 29 and 31 (14 errors and 15 unanswered calls skipped) |

The quote check no longer produces the false alarms that drove v3's major findings. Both v4 majors
are problems a reviewer should see: a comparison the cited excerpt does not support, and a source
dated only "2026" called post-window although the window runs to 2026-09-23. The two quotes still
not found come from arXiv papers and are unexamined; the PDF page-header case is a likely cause.

This is one run with a different planner and synthesizer, so it shows direction, not effect size:
a different synthesizer writes a different report, and the verifier's counts move with it. A rerun
on the original models would separate the fixes from the model change.


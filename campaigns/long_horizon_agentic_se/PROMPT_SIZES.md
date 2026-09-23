# Prompt sizes: where they come from and why they cause problems

Status as of 2026-09-23. The measurements come from the successful calibration pilot (question
`p01`, job `253311b6`, now archived in `benchmark_outputs/archive/long_horizon_pilot-2026-09-23/`).
They were taken after the fixes
in commits `5c62878` and `36ce94e`. The deeper research settings from `36ce94e` had not yet been
used for a paid run. Numbers marked *estimate* are projections, not measurements.

Since then, fetch version 2 added paging through long documents (option 5 below); the rest of
this document still describes current behavior. The measurements are unchanged.

## Summary

Every model call in this pipeline gets a prompt that code builds from JSON. Four problems stand
out, most serious first:

1. **The campaign synthesis prompt does not fit.** One pilot question produces 82,719 characters of
   synthesis prompt. All 11 campaign questions would produce about 910,000 characters, roughly
   367,000 Opus tokens per request. That is over the `[synthesis]` limits for prompt size, tokens,
   and cost. Synthesis will refuse to run once 5 or more questions are complete.
2. **Three per-question steps each receive the whole evidence ledger.** Gap analysis, synthesis,
   and verification each get the full ledger, about 97,000 characters in the pilot. At pilot depth
   each fits one attempt, and one retry fits only just. With the deeper research settings, a retry
   is likely to exceed the token limit *(estimate)*. These steps have no salvage path, so the
   question would fail after most of its budget was spent.
3. **Agent loops resend their whole history.** Scouts and deep dives start with small prompts
   (about 2,000 characters). Every turn resends everything so far, so tokens grow roughly with the
   square of the number of turns. A scout used about 243,000 input tokens in 12 turns. Two of the
   three scouts and the deep dive stopped on a request or tool-call limit; nothing stopped on
   dollars.
4. **Some evidence never reaches any prompt.** During the pilot, fetches returned only the first
   12,000 characters of a document (paging has since landed); salvage and campaign synthesis
   truncate further, and openai.com blocks the fetcher. The pilot report lists missing
   primary-source details as caveats because of this.

## Background: prompts and limits in this pipeline

- **A prompt** is the JSON text built for one model call: the task, the relevant evidence, and
  the constraints. The agent's fixed instructions and the tool and output schemas are sent with it.
- **A tool loop** (scouts, deep dives) is one agent run with many requests. After each tool call,
  the next request resends the instructions, the schemas, the prompt, and every earlier call and
  result.
- **Limits per call** come from each role's `ModelRoute` in `policy.py`. The campaign adjusts some
  of them in `campaign.toml`. There are four:
  - `max_requests`;
  - `max_tool_calls`;
  - `total_tokens_limit`, which counts input plus output added up across all requests in the run;
  - `cost_limit`.

  PydanticAI raises `UsageLimitExceeded` when a limit is hit. Scouts and deep dives are salvaged
  when that happens; gap analysis, synthesis, and verification are not.
- **Characters versus tokens.** Tokens are what providers bill for and what the limits count.
  On the same JSON, OpenAI models measured about **3.8 characters per token** and Opus about
  **2.5**. The same prompt therefore costs roughly 1.5 times as many tokens on Opus, which runs the
  per-question synthesis and the campaign synthesis. These ratios come from pilot tasks (gap
  analysis 87,125 characters to 22,829 input tokens, and the verifier 127,067 to 32,950, both on
  OpenAI; synthesis 99,254 to 39,986 on Opus). They are approximate, because the input also
  includes the instructions and the output schema.

## Where prompts come from

| Role | Built in | What the prompt contains | Pilot limits (requests / tool calls / tokens / $) |
|---|---|---|---|
| Planner | `async_orchestrator.py` `_plan`, shared by graph and legacy | objective, constraints, planning guidance | 6 / 4 / 70k / $2.50 |
| Scout | `_run_scout` | one subquestion and the constraints; tool history grows each turn | 12 / 24 / 400k / $0.80 |
| Gap analysis | `async_orchestrator.py` `_analyze_gaps`, shared by graph and legacy | objective, plan, **all results so far** | 6 / 4 / 70k / $1.25 |
| Deep dive | `_run_gap` | subquestion, gap, constraints; tool history grows each turn | 20 / 40 / 180k / $3.00 during the pilot (now $1.25; route default $5) |
| Salvage | `_run_research` | the original prompt plus `gathered_evidence`: tool results cut to 4,000 characters each, 48,000 in total (`_SALVAGE_*`) | 2 requests / 80k / a quarter of the route's $ cap during the pilot ($0.20 scout, $0.75 deep dive); now half |
| Synthesis | `_synthesize` | objective, **all results** (the full ledger), constraints | 8 / 4 / 120k / $3.50 |
| Verifier | `_verify` | objective, **the report and all results**, constraints | 8 / 8 / 100k / $2.50 |
| Campaign synthesis | `campaign.py` `synthesis_prompt` | for each question: report text, caveats, contradictions, unresolved questions; **every claim** with its evidence (excerpts cut to 300 characters) | `[synthesis]`: 3 requests / 400k / $6, prompt at most 360,000 characters |

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

## Problem 1: the campaign synthesis prompt

`research-campaign --synthesize` builds one prompt from every completed question. For the pilot
question it is **82,719 characters**:

| Part | Characters | Contents |
|---|---:|---|
| Campaign header | 1,010 | title, dates, source policy, output fields |
| Question block | 23,728 | report text 11,480; caveats 3,419; contradictions 2,842; unresolved questions 5,703 |
| Evidence | 57,910 | 45 claims and 68 evidence items: statements 18,305; excerpts (at most 300 each) 15,245; source metadata 16,957; the rest is JSON structure |

**Projection for the full campaign** *(estimate)*: 11 questions × about 82,700 characters ≈ **910,000
characters**. At Opus's ~2.5 characters per token, that is **≈367,000 input tokens per request**.
Compared with the limits in `campaign.toml` `[synthesis]`:

- **`max_prompt_chars = 360,000`.** Four questions fit (about 328,000 characters); five do not
  (about 410,000). This check runs during `--synthesize --dry-run` and before any paid call, so it
  fails safely and costs nothing.
- **`total_tokens_limit = 400,000`.** One request of about 367,000 tokens plus up to 48,000 output
  tokens goes over the limit. A citation-fix retry resends everything, so it has no chance.
- **`cost_limit_usd = 6.0`.** Each request costs about $1.83 of input plus up to $1.20 of output,
  roughly $3. A retry reaches the cap.
- **The model's context window** has not been checked against a prompt this size.

**This projection is a floor.** The deeper research settings (16-request scouts, two deep dives)
will add claims to every question.

**Why it is built this way.** The synthesis output must cite claim refs, and a validator rejects
any ref that does not exist. So the prompt includes every claim together with its evidence. The
per-question report text sits alongside it, and it largely restates those same claims.

## Problem 2: the three per-question steps that carry the whole ledger

Gap analysis, synthesis, and verification each receive the full evidence ledger. In the pilot,
that block was **97,291 characters**: 4 results, 45 claims, 68 evidence items, and 55 distinct
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

Dropping null and empty fields alone would remove about 11% of the block (97,291 to 86,394).

**The same block is sent three times.** Gap analysis gets it as `results` (81,798 characters,
before the deep dive's result was added). Synthesis gets it as `evidence`. The verifier gets it
again, plus the 27,801-character report.

**Headroom depends on not retrying.** Evidence version 3 adds a citation check to synthesis and verification, so a retry is now also triggered by a citation to a claim ID the ledger does not have. If a step's output fails validation, PydanticAI makes another
request that resends the prompt and the first output, and the token count covers both requests.

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
limit, salvage replays up to 48,000 characters of what the run gathered.

**The search guidance was followed only partly.** The campaign's research notes tell scouts to
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

- **Fetches stopped at 12,000 characters, with no way to read further.** This is why the deep
  dive never reached the dataset-construction section and Table 1 of the SWE-bench paper; the pilot
  report's caveats say so directly. Fetch version 2 now pages through documents with `start` and
  `next_start` (see `docs/acquisition.md`), which the pilot did not have.
- **Salvage keeps 4,000 characters per tool result and 48,000 in total.** Anything longer or later
  is dropped before the wrap-up call.
- **Campaign synthesis keeps 300 characters of each excerpt.**
- **openai.com returns HTTP 403** (bot protection), so OpenAI's own posts never enter a prompt. The
  report's OpenAI figures came from secondary sources, and the verifier flagged them as major issues.

## Consequences

| Prompt | Risk | Consequence | When it bites |
|---|---|---|---|
| Campaign synthesis | over the prompt, token, and cost limits | `--synthesize` refuses (dry run) or fails | with 5 or more completed questions at pilot depth |
| Gap analysis, synthesis, verifier | one retry goes over the token limit | the question fails after most of its budget is spent | more likely under the deeper settings |
| Scout and deep-dive loops | history grows with the square of the turns | budgets run out early; salvage replaces the final answer | two of three scouts and the deep dive in the pilot |
| Truncation and blocking | evidence never collected | more caveats, weaker or secondary-source findings | long documents; vendor sites behind bot protection |

## Options, ranked by value against effort

Only the fetch paging in option 5 has been implemented.

1. **Compact evidence serialization for the ledger-carrying prompts.**
   - Drop null and empty fields (about −11%).
   - Leave out `search_queries_used` and `suggested_followups` for synthesis and the verifier
     (about −4.5k characters).
   - Send each source once in a table referenced by ID, instead of repeating it on each evidence
     item (68 items point to 55 sources here; the saving grows with depth).
2. **Send the verifier only what it checks:** the claims the report cites
   (`FinalReport.claim_ids_used`) rather than the whole ledger.
3. **Size the finishing steps' token limits from the prompt.** Measure or estimate the prompt's
   tokens, set `total_tokens_limit` to allow one retry, and fail before the call if even one
   attempt cannot fit.
4. **Slim the campaign synthesis prompt.** Send each question's report claims (statements with
   their refs), caveats, and contradictions, instead of every ledger claim with its evidence. The
   full evidence stays in the aggregated `evidence_ledger.json` for audit. That is roughly
   25–35k characters per question, about 330k for 11 *(estimate)*: just under the current cap and
   likely over it at the deeper settings. The alternative is to merge in two stages (groups of
   questions, then a final merge). Either way, the limits should be rechecked with a dry run.
5. **Make the agent loops cheaper.**
   - Add paging to the fetch tools, so long documents can be read in parts instead of lost.
     **Done** as fetch version 2: a `start` argument, `next_start` in results, and a per-job
     document memo.
   - Trim `scholar_search` results: shorter abstracts, fewer arXiv records.
   - Consider a history-compaction capability.

   Paging and trimming change what benchmark runs see, so they need a `docs/benchmarks.md` note
   and a new fetch version.
6. **Steer the search more strongly.** Move the key search guidance from the campaign's research
   notes, which reach the model as constraints, into the scout instructions in `agents.py`.

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

Campaign synthesis prompt, rebuilt without any model call. The archived p01 outputs predate
objective hashes, so the aggregator no longer accepts them; rerun p01 into
`benchmark_outputs/long_horizon_pilot` first, and expect a different size from a new run:

```python
from pathlib import Path
from research_loop.campaign import aggregate_campaign, load_campaign, synthesis_prompt

campaign = load_campaign(Path("campaigns/long_horizon_agentic_se/pilot.toml"))
evidence = aggregate_campaign(campaign, Path("benchmark_outputs/long_horizon_pilot"))
print(len(synthesis_prompt(campaign, evidence)))  # the archived p01 measured 82,719
```

`research-campaign --spec campaigns/long_horizon_agentic_se/pilot.toml --output
benchmark_outputs/long_horizon_pilot --synthesize --dry-run` reports the same size against
`max_prompt_chars`.

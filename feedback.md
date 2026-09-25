# Briefing for designing the first Scout study

You are designing the first study of Research Loop's Scout workflow. The study's purpose is to let us compute what runs cost and choose configurations: models per role, reasoning effort, budgets, and depth. This file is everything we know that bears on that design, as of 25 September 2026. It covers:

- the system as it is now;
- the prices;
- every measurement from the previous design;
- the questions and graders available;
- the methods that worked and the mistakes to avoid;
- our constraints.

Design the study; do not run it. Anything that spends money needs our approval first (see "Constraints").

## 1. What Scout is

Research Loop answers a research question with a report in which every statement cites ledger claim IDs, and inline citations such as [s3] name sources. The current workflow, Scout (`src/research_loop/scout.py`, workflow version `scout-v1`), has three steps:

1. **Plan.** A planner splits the question into 1 to `max_questions` research questions, which are renumbered q1, q2, and so on in plan order. If planning fails or takes more than 90 seconds, the whole question is researched as one.
2. **Scout.** Each question gets its own scout call, all running at once up to `parallel_scouts`. A scout uses four tools, each result labeled with its access level:
   - `web_search` (DuckDuckGo): snippets;
   - `fetch`: full text of a page or PDF, in windows of 12,000 characters;
   - `scholar_search` (OpenAlex and arXiv): each work at abstract or metadata level;
   - `scholar_get` (lookup by DOI, OpenAlex ID, or arXiv ID): the same.

   It returns a `ResearchResult`: claims, each with evidence items (source, excerpt, optional exact quote).
3. **Synthesize.** A synthesizer writes a `FinalReport` from the evidence ledger: title, summary, a Markdown answer, and statements with claim IDs. The request is streamed.

**What code checks, and the model cannot set:**

- **Quote check.** Every quote is matched against what the tools returned, comparing letters and digits only and allowing "..." segments in any order. The result, `quote_check` verified or not_found, also records `quote_access`: the most complete kind of text the quote was found in.
- **Source check.** Every cited source is matched against the items the tools returned (by URL, DOI, or arXiv ID). The result, `source_check`, also records `source_access`: snippet, metadata, abstract, or full_text.
- **Support per statement.** Each report statement gets a support level: `read`, when some supporting evidence came from an abstract or full text and any quote was verified; `shallow`, when support is only snippets, metadata, or unverified quotes; `unsupported`, when nothing supports it.
- **Citation problems.** An unknown claim ID gets the synthesizer one retry. An inline [sN] citation with no listed claim behind it is dropped, and a caveat says so.

**Run status:**

| Status | When |
|---|---|
| `complete` | A report exists, every question has claims, and there are no citation problems |
| `partial` | There is some output, but a question went unanswered or the synthesis failed |
| `failed` | No claims at all |
| `cancelled` | The run was interrupted |

**What happens when something runs out:**

- A scout that hits a limit, fails, or is still running at the research deadline returns no claims. It keeps its searches, the pages it read, and the sources it could not reach, and its question is listed under "Could not establish".
- A failed synthesis returns the claims without a written answer.
- There is no salvage call; the previous design had one.

**Budget notes.** A scout is told on every request how many requests, productive calls, and misses it has left. Its tools are withdrawn on its last request, or when either call budget is spent. A productive call is a search with results, a fetch with text, or a scholarly call that returned works. A miss is an empty search, an HTTP error, a blocked URL, and the like. This design came from the previous study (section 5.5).

### Configuration

The study will vary these settings. Each is an environment variable or `.env` entry, and in Python each is a field on `Settings`.

| Setting | Default | Meaning |
|---|---|---|
| `RESEARCH_MODELS__PLANNER` | `openai:gpt-6-sol` | Planner model |
| `RESEARCH_MODELS__SCOUT` | `zai:glm-5.3-flash` | Scout model |
| `RESEARCH_MODELS__SYNTHESIZER` | `anthropic:claude-opus-5-5` | Synthesizer model |
| `RESEARCH_MODELS__FALLBACK` | `openai:gpt-6-sol` | Takes a planner or synthesizer call after a refusal or provider error (PydanticAI `FallbackModel`); not used when it equals the primary |
| `RESEARCH_LIMITS__COST_USD` | 0.75 | Total cap per run |
| `RESEARCH_LIMITS__PLANNER_USD` | 0.05 | Planner's share |
| `RESEARCH_LIMITS__SYNTHESIS_USD` | 0.40 | Synthesizer's share, sized so an Opus report can take one retry |
| (derived) | (0.75 − 0.05 − 0.40) ÷ questions | Each scout's share: $0.075 with four questions, $0.30 with one |
| `RESEARCH_LIMITS__MAX_QUESTIONS` | 4 | Upper bound on planned questions (the schema allows up to 8) |
| `RESEARCH_LIMITS__PARALLEL_SCOUTS` | 4 | Scouts running at once |
| `RESEARCH_LIMITS__SCOUT_REQUESTS` | 12 | Model requests per scout |
| `RESEARCH_LIMITS__SCOUT_PRODUCTIVE_CALLS` | 16 | Productive tool calls per scout |
| `RESEARCH_LIMITS__SCOUT_MISSES` | 12 | Missed tool calls per scout |
| `RESEARCH_LIMITS__SCOUT_TOKENS` | 400,000 | Total tokens per scout loop (input, including cached, plus output, summed over requests) |
| `RESEARCH_LIMITS__SYNTHESIS_TOKENS` | 200,000 | Total tokens for synthesis, including one retry |
| `RESEARCH_LIMITS__SYNTHESIS_MAX_OUTPUT_TOKENS` | 32,000 | Output cap per synthesis request |
| `RESEARCH_LIMITS__DEADLINE_SECONDS` | 360 | Whole run |
| `RESEARCH_LIMITS__RESEARCH_SECONDS` | 270 | Scouts still running after this are cut off |
| `RESEARCH_LIMITS__REQUEST_TIMEOUT_SECONDS` | 120 | One model request, or the gap between streamed chunks |
| `RESEARCH_CACHE_MODE` | `live` | Search, page, and scholarly cache mode (below) |
| `RESEARCH_CACHE_DIR` | `.cache/research-loop` | Cache directory; give a study its own |

**Reasoning effort follows the model, not the role (`models.py`):**

- Every `zai:glm-*` model runs at `xhigh`, which PydanticAI sends to GLM-5.3 as `reasoning_effort: "max"`. A test checks this on the wire. We decided on this deliberately.
- `anthropic:claude-opus-5-5` runs at `medium`, its default. It thinks more at each level than Opus 5 did, cannot turn thinking off, and rejects a forced `tool_choice`.
- Every other model runs at `high`.

Changing effort per role is a code change, not a setting today. If the study needs effort arms, say so; that is a small addition.

**How the caps are enforced.** Each call's share is its PydanticAI `UsageLimits.cost_limit`, checked before every request and after every response. A call can therefore exceed its share by at most the one request that crossed it, so the worst case per run is the cap plus one request per call. A model with no price is refused at startup. `src/research_loop/prices.toml` corrects genai-prices, which listed `glm-5.3-flash` at half its price and had no `glm-5.3-flashx`.

**Cache modes, which matter for controlled comparisons:**

| Mode | Behavior |
|---|---|
| `live` | Reads entries up to one day old and writes |
| `record` | Writes only |
| `replay` | Reads entries of any age, never writes; a miss is returned to the model as an error |
| `reuse` | Reads recorded entries of any age; a miss goes live and is recorded |
| `off` | Neither |

Searches are keyed on the query as search engines read it: Unicode-normalized, case-folded, with whitespace collapsed. Operators, quotes, and word order stay in the key, so a model that words a query differently misses the recording.

### What each run records

**Postgres, table `runs`:** id, parent_run_id, mode, workflow_version, question, status, and:

- `config`: models and effort per role, limits, prompt fingerprint, evidence version, fetch version, cache mode, git commit, notes, and blocked URLs;
- plan, report, and ledger;
- `checks`: citation problems, the support level of each statement, questions not established, unreached sources, evidence counts by access level, quotes and quotes verified, and review reasons;
- cost_usd, error, trace_id, started_at, and finished_at.

**Postgres, table `run_calls`:** one row per agent call, with role, question_id, model, status (succeeded, failed, or cancelled), `usage`, cost_usd, output, error, started_at, and finished_at.

- `usage` holds requests, tool_calls, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, and details such as reasoning_tokens.
- `messages` holds the full message history: every prompt, tool call, and tool result, with strings over 50,000 characters cut.

**Logfire:** each run is one trace, and every span carries `run_id`. The PydanticAI instrumentation records each model request, tool call, and retry with content and timing.

- The cost in Postgres is authoritative. Logfire's cost display uses its own price data and can differ, for GLM Flash for example.
- Per-request timing, such as time to first token or model time against tool time, is available from Logfire spans and from `run_calls.messages`, whose model responses are timestamped.

**Durations:** run duration is `finished_at - started_at` on `runs`, and call duration the same on `run_calls`.

**Commands.** `research scout "question" [--note ...] [--block URL] [--out DIR] [--no-persist]` runs Scout. `research show <run id>` renders a stored run. `research doctor [--smoke]` checks keys, prices, the database, and the network; `--smoke` makes one paid call of under one cent per model. `research db migrate|status|reconcile` manages the database.

## 2. What exists and what does not

- **Scout has never run live.** It is implemented and tested offline (140 tests), and we will start the first live run ourselves, on st07 (section 6). Treat every Scout cost below as a projection from the previous design.
- **No eval harness yet.** A Pydantic Evals setup is planned but not built. The planned setup:
  - a dataset of about eight real questions;
  - deterministic evaluators for citation integrity, quote-verified rate, full-text share, required sources, answer match, budget, and duration;
  - a rubric judge ported from the old code (section 5.7);
  - a "fixed evidence" lane that re-synthesizes stored ledgers;
  - a "live retrieval" lane.

  Your design can shape it, and it should say what the harness must provide.
- **No replay or cut-at-request-k tool yet.** The previous design's replay scripts are in git tag `archive/pre-scout-2026-09`, written for its own orchestrator. Scout stores full messages per call, so replays can be rebuilt; say which ones the study needs.
- **Not in Scout:** gap analysis, deep dives, and an LLM verifier. They return later in a "Long-horizon" mode, which this study does not cover.

## 3. Prices

These are USD per million tokens, from the genai-prices data PydanticAI prices every call with, including our corrections.

| Model | Input, 100k-token prompt | Input, 1M-token prompt | Cached input | Output |
|---|---:|---:|---:|---:|
| `openai:gpt-6-sol` | 2.00 | 4.00 (long-context tier) | 0.20 | 10.00 |
| `openai:gpt-6-luna` | 0.10 | 0.20 | 0.01 | 0.50 |
| `openai:gpt-5.6-sol` | 4.00 | 8.00 | 0.40 | 20.00 |
| `openai:gpt-5.6-luna` | 0.20 | 0.40 | 0.02 | 1.20 |
| `anthropic:claude-opus-5-5` | 4.00 | 4.00 | 0.20 | 20.00 |
| `anthropic:claude-sonnet-5` | 2.00 | 2.00 | 0.20 | 10.00 |
| `anthropic:claude-haiku-4-5` | 1.00 | 1.00 | 0.10 | 5.00 |
| `zai:glm-5.3` | 1.40 | 1.40 | 0.26 | 4.40 |
| `zai:glm-5.3-flash` | 0.15 | 0.15 | 0.03 | 0.50 |
| `zai:glm-5.3-flashx` | 0.37 | 0.37 | 0.075 | 1.25 |
| `google:gemini-3.8-flash` | 0.75 | 0.75 | 0.075 | 3.75 |
| `openai:gpt-6-astra` | 10.00 | | 1.00 | 50.00 |

**Notes:**

- **Tiers and promotions.** OpenAI models charge about twice as much for input on very long prompts. GPT-5.6 Sol's price is promotional, and Gemini 3.8 Flash's doubles on 2027-01-01.
- **Enabled providers.** OpenAI, Z.ai, and Anthropic have keys; Google does not, and xAI is not used.
- **Model IDs.** Check any new ID with `research doctor --smoke` first. Some IDs were only days old when first used.
- **Reasoning tokens** are billed as output. Anthropic does not report them separately, so our data shows 0 reasoning for Opus.
- **Z.ai Flash** is said by a third party to share its weights with FlashX. FlashX serves about 200 output tokens per second against Flash's roughly 98, so it would only answer faster, not better. This is untested.

## 4. How the previous design differed

All the data in section 5 comes from the previous design. It ran six roles: planner, scouts, gap analyst, deep dives, synthesizer, and verifier with follow-up rounds. When a loop hit its limit, a tool-free salvage call wrote its result. Its prompts differed from Scout's. Scout's prompts also say that tool text is untrusted, name the access levels, and ask for a direct answer first.

Its scout limits also changed over time:

| When | Requests | Tool calls | Tokens |
|---|---:|---:|---:|
| README run | 12 | 24 | 100,000 to 400,000 |
| Study pilots | 24 | 48 | 2,000,000 |
| Last pilot | 12 | 16 productive, 12 misses | |

Its web fetch and scholarly fetch were separate tools, and its search tool was named `duckduckgo_search`. Use its numbers as priors, not as Scout's costs.

## 5. Measurements from the previous design, 23 to 25 September 2026

These come from 343 stored agent calls in 63 jobs. The per-call numbers are computed from the archive (section 9).

### 5.1 Cost, tokens, requests, and time per call

Columns: `n` is calls. `ok` is calls that succeeded; the rest mostly hit a limit and were salvaged. Cost is USD per call as median, 90th percentile, and maximum. Tokens are per call, summed over its requests. Cache is the share of input tokens read from cache.

| Role | Model | Effort | n | ok | Cost med / p90 / max | Input tok (med) | Output tok (med) | Reasoning (med) | Requests med / max | Tool calls med / max | Cache | Seconds med / p90 / max |
|---|---|---|---:|---:|---|---:|---:|---:|---|---|---:|---|
| planner | gpt-6-sol | high | 71 | 71 | 0.014 / 0.020 / 0.022 | 1,282 | 1,222 | 427 | 1 / 1 | 0 | 34% | 24 / 33 / 41 |
| planner | gpt-6-luna | high | 14 | 14 | 0.000 / 0.001 / 0.001 | 1,561 | 459 | 390 | 1 / 1 | 0 | 0% | 8 / 9 / 11 |
| planner | gpt-5.6-sol | high | 6 | 6 | 0.016 / 0.017 / 0.017 | 465 | 661 | 173 | 1 / 1 | 0 | 0% | 12 / 13 / 13 |
| planner | claude-opus-5-5 | medium | 2 | 2 | 0.052 / 0.054 / 0.054 | 2,789 | 1,920 | n/a | 1 / 1 | 0 | 0% | 19 / 22 / 22 |
| planner | claude-opus-5 | high | 4 | 4 | 0.049 / 0.089 / 0.089 | 1,624 | 1,641 | n/a | 1 / 2 | 0 | 0% | 21 / 29 / 29 |
| scout | glm-5.3 | high | 65 | 19 | 0.201 / 0.341 / 0.486 | 359,192 | 9,423 | 5,868 | 13 / 24 | 25 / 48 | 87% | 219 / 413 / 612 |
| scout | glm-5.3-flash | high | 12 | 8 | 0.016 / 0.027 / 0.048 | 106,655 | 12,346 | 2,762 | 8 / 20 | 16 / 48 | 74% | 270 / 559 / 638 |
| scout | gpt-6-luna | high | 1 | 1 | 0.003 | 35,914 | 2,414 | 956 | 7 | 6 | 74% | 42 |
| scout salvage | glm-5.3 | high | 36 | 34 | 0.070 / 0.109 / 0.152 | 22,052 | 9,378 | 1,831 | 1 / 2 | 0 | 27% | 102 / 147 / 227 |
| scout salvage | glm-5.3-flash | high | 4 | 4 | 0.013 | 56,167 | 14,075 | 1,764 | 2 / 2 | 0 | 36% | 210 / 237 / 237 |
| deep_dive | gpt-6-sol | high | 12 | 5 | 0.258 / 0.333 / 0.366 | 313,340 | 8,043 | 954 | 11 / 14 | 79 / 80 | 81% | 227 / 320 / 330 |
| deep_dive | gpt-5.6-sol | high | 12 | 0 | 0.340 / 0.656 / 0.835 | 176,835 | 2,263 | 599 | 6 / 13 | 39 / 71 | 71% | 75 / 151 / 180 |
| deep_dive | glm-5.3 | high | 8 | 4 | 0.102 / 0.195 / 0.456 | 178,175 | 10,719 | 4,784 | 12 / 12 | 23 / 54 | 81% | 159 / 364 / 1,233 |
| gap_analyst | gpt-6-sol | medium | 8 | 7 | 0.079 / 0.111 / 0.142 | 27,919 | 967 | 378 | 1 / 2 | 0 | 19% | 26 / 29 / 40 |
| synthesizer | claude-opus-5-5 | medium | 5 | 5 | 0.590 / 1.118 / 1.118 | 65,496 | 13,108 | n/a | 1 / 2 | 0 | 21% | 110 / 226 / 226 |
| synthesizer | claude-opus-5 | high | 2 | 2 | 0.471 / 0.574 / 0.574 | 39,986 | 10,844 | n/a | 1 / 1 | 0 | 0% | 108 / 111 / 111 |
| synthesizer | gpt-5.6-sol | high | 4 | 4 | 0.186 / 0.195 / 0.195 | 24,305 | 3,222 | 1,468 | 1 / 1 | 0 | 0% | 38 / 48 / 48 |
| synthesizer | glm-5.3 | high | 6 | 5 | 0.079 / 0.164 / 0.208 | 48,529 | 7,562 | 1,449 | 2 / 2 | 0 | 43% | 88 / 180 / 256 |
| synthesizer | glm-5.3 | xhigh (max) | 3 | 1 | see note | | | | 1 | 0 | | up to 1,802 |
| synthesizer | glm-5.3-flash | xhigh (max) | 1 | 1 | 0.029 | 27,631 | 50,567 | 39,702 | 1 | 0 | 0% | 819 |
| verifier | gpt-6-sol | high | 11 | 10 | 0.195 / 0.242 / 0.264 | 41,695 | 9,158 | 3,848 | 1 / 1 | 0 | 0% | 157 / 198 / 244 |
| verifier | gpt-5.6-sol | high | 7 | 6 | 0.226 / 0.338 / 0.406 | 20,066 | 6,349 | 4,454 | 1 / 1 | 0 | 0% | 75 / 130 / 204 |

**Reading notes:**

- **Scout sizes.** The `glm-5.3` scout rows mix 12-, 24-, and last-pilot budgets. The Flash scouts are 12 calls, 11 of them in one trial on one deep question, all at `high` effort. **No scout has run at max effort.** Scout now defaults to max for Flash, and its cost and time there are unknown.
- **Synthesis ledger size.** The synthesizer rows are from large ledgers: 4 to 5 scouts plus deep dives, with 40,000 to 65,000 input tokens. A Scout ledger (up to four scouts, no deep dives) should be smaller. Opus at $0.43 to $1.12 is why the synthesis share is $0.40.
- **GLM-5.3 at max.** Unstreamed synthesis on `glm-5.3` at max hit the 600-second read timeout three times, in 1,802 seconds, and wrote no report. Flash at max streamed its reasoning for about 11 minutes, then sent the whole report after about 2.5 minutes of silence. Z.ai appears not to stream an output tool call's arguments. A report that long cannot fit Scout's 6-minute deadline.
- **Growth with loop length.** PydanticAI's `total_tokens` counts every request's full input, cached or not, plus output, so it grows with roughly the square of the requests. Cached input is billed at about 20% of the input price (Z.ai), or 5% to 10% (OpenAI and Anthropic).

### 5.2 Whole-job costs for real questions

These are previous-design jobs that were not trials. The design ran a full plan, scouts, gap analysis, deep dives, synthesis, and verification.

| Job | Question | Questions planned | Status | Cost | Minutes | Cost by role |
|---|---|---:|---|---:|---:|---|
| 253311b6 | p01 pilot: SWE-bench family scope and versions | 3 | succeeded | 2.66 | 20.0 | scout 0.63, deep dive 1.05, synth 0.47, verifier 0.34, gap 0.14, planner 0.04 |
| 46044486 | p01 again | 3 | succeeded | 2.94 | 22.3 | scout 0.62, deep dive 1.11, synth 0.57, verifier 0.41 |
| 31eba511 | p01 again | 3 | succeeded | 2.50 | 19.3 | scout 0.78, deep dive 1.15, verifier 0.32, synth 0.06 |
| 062b8cec | st07 (README question) | 5 | succeeded | 3.68 | 16.4 | scout 1.61, deep dive 1.11, verifier 0.44, synth 0.37 |
| **bf89797d** | **st07, the baseline** | 3 | succeeded | **2.26** | **11.8** | scout 0.55, deep dive 1.18, verifier 0.23, synth 0.17, gap 0.12 |
| 78a2a6f9 | st07 on the `value` lineup | 4 | succeeded | 2.24 | 25.0 | scout 0.78, deep dive 0.94, verifier 0.29, synth 0.17 |
| 2722a06e | DRB-II task2+ | 5 | succeeded | 3.12 | 41.9 | scout 1.44, deep dive 0.81, verifier 0.41, synth 0.37 |
| 3bccbe6d | DRB-II task2+ | 4 | succeeded | 4.30 | 37.4 | scout 1.28, synth 1.33, deep dive 1.11, verifier 0.44 |
| 7f456a01 | DRB-II task2+ with budget notes | 5 | failed at $5 cap | 5.03 | 59.9 | synth 1.71, scout 1.54, deep dive 1.16 |
| 7532c708 | DRB-II task2+ | 5 | failed: finishing prompt too large | 1.67 | 11.1 | scout 1.65 |
| 974042de | DRB-II task2+ | 7 | failed: Z.ai balance exhausted (HTTP 429) | 0.75 | 3.9 | scout 0.73 |
| 09a92b1a | DRB-II task2+ | 5 | failed: NUL byte in stored PDF text | 2.69 | 23.4 | scout 1.84, deep dive 0.69 |
| b796008c | DRB-II task2+ (pilot 8) | 4 | failed: synthesis timeout | 0.75 | 53.1 | deep dive 0.53, scout 0.13 |
| 7241f865 | st01 on `gpt-6-luna` in every role | 1 | succeeded | 0.03 | 1.5 | all tiny |

A shallow question is cheap, as the st01 run shows. Deep DRB-II tasks cost $3 to $5 in the old design, over 40 minutes, and often failed. Under Scout's $0.75 and 6-minute defaults, deep tasks will mostly come back `partial`. The study should decide whether Scout is meant for them at all, or whether they belong to Long-horizon.

### 5.3 Tools: outcomes and latency

The totals below come from 3,938 stored tool calls in the previous design. Its tool names differ from Scout's: `web_fetch` corresponds to `fetch`, and `duckduckgo_search` to `web_search`.

| Tool | Calls | Useful | Main failures | Latency med / p90 (s) |
|---|---:|---:|---|---|
| web_fetch | 1,621 | 61% | 404: 218, 403: 196, unsupported content (mostly PDFs, fixed later): 89, connect timeout or error: 68, unsafe URL: 32, 429: 5 | 1.1 / 7.5 |
| duckduckgo_search | 1,404 | 64% | unavailable after 3 tries: 381, no results: 123 | 3.8 / 9.1 (includes retries) |
| scholar_search | 228 | 92% | arXiv read timeout: 12 | 2.4 / 7.2 |
| scholar_get | 63 | 65% | unsupported ID: 13, HTTP error: 9 | 0.2 / 0.9 |
| scholar_fetch | 326 | about 79% without error | HTTP error: 41, other: 26 | 0.1 / 3.4 |

The study's pilots 4 and 5 break this down further. There, 44% of scout tool calls returned nothing usable. 37% of fetches failed with 403 or 404; most 404s were URLs a model guessed. DuckDuckGo was unavailable on 31% of searches.

Fixes that later designs carry:

- A 403 is retried once with a browser user agent; 5 of 12 refusing sites then served the page.
- A 404 returns a hint to search rather than guess.
- A host that fails to connect twice is skipped for the rest of the run.
- PDFs are recognized by their first bytes.

Search stays on DuckDuckGo for now; a paid search API is a candidate for a later decision.

### 5.4 Where time goes, and depth

**README run** (bf89797d, scouts on glm-5.3 at high):

| Loop | Requests | Cut off | Model s | Tools s | Salvage s | Request at which each cited source was first returned |
|---|---:|---|---:|---:|---:|---|
| Scout q1 | 12 | yes | 74 | 9 | 101 | 1, 1, 1, 1, 3, 3, 9 |
| Scout q2 | 10 | no | 151 | 12 | | 1, 1, 1, 3, 4, 4, 5, 7, 7, 8 |
| Scout q3 | 11 | no | 200 | 20 | | 1, 1, 1, 1, 3, 3, 3, 3, 3, 4, 4, 4 |
| Deep dive q1 | 6 | yes | 34 | 28 | 151 | 1, 1, 1, 1, 1, 2, 3, 3, 3, 3 |
| Deep dive q2 | 6 | yes | 36 | 19 | 77 | 1, 1, 1, 1, 1, 1, 2, 3, 3, 3, 4, 4, 4, 5, 5 |

- **Model time dominates.** Scouts spent 91% of their loop time waiting on the model, 6 to 18 seconds per request, and 9% on tools; deep dives spent 60% on the model. A faster model (FlashX) could shorten runs; faster tools would barely help.
- **Shallow questions** (README run): scouts had 66% of the sources they cited by request 3, 83% by request 4, 97% by request 8, and all by request 9.
- **Deep questions** (DRB-II task2+):

  | Pilot | Share of cited sources returned by request | Note |
  |---|---|---|
  | 2 | 26% by 3, 48% by 10, 86% by 20 | |
  | 4 | 38% by 10, 85% by 20 | |
  | 5 | 58% by 8, 90% by 20 | |
  | 6 | 67% by 8, 98% by 16 | with budget notes |

  Scouts covering several countries each were still finding sources at their last request, whatever the limit. One subject per scout works better.

- **Token limits.** At 400,000 tokens, limits stopped loops at 10 to 17 requests, before their request limits. At 2,000,000, request and tool-call limits bound instead. A 24-request scout needs about 900,000 tokens. Scout's 12 requests at 400,000 tokens should fit, but check it.

### 5.5 Findings that shaped Scout's defaults

- **Budget notes.** With per-request notes, 9 of 10 loops returned their result on their own, against 0 of 16 without them. Salvage calls had taken 1.6 to 2.9 minutes each, about a third of each research phase. Scout keeps the notes and drops salvage.
- **Productive calls and misses.** In pilot 8, scouts spent all 48 tool calls in 14 to 17 requests, mostly on empty searches and failed fetches. One scout had 8 of 9 searches empty and 28 of 36 fetches failed, and returned no claims. This is why Scout budgets productive calls and misses separately: 16 and 12.
- **Scout trial.** All six arms scouted the same five-question plan for DRB-II task2+ with recorded tools. A fixed synthesizer (`gpt-6-luna`) wrote a report from each arm, graded once by the judge. Scout-only reports score low and get 0 on analysis points, so only the differences between arms matter.

  | Arm | Answered | Claims | Cited sources | Quotes not found | Scouting cost | Grade |
  |---|---|---:|---:|---:|---:|---:|
  | glm-5.3, high (baseline) | 5/5 | 72 | 51 | 8/122 | $1.84 | 0.21 |
  | glm-5.3-flash, high, run 1 | 5/5 | 66 | 48 | 3/72 | $0.17 | 0.22 |
  | glm-5.3-flash, high, run 2 | 5/5 | 66 | 52 | 3/105 | $0.15 | 0.22 |
  | glm-5.3-flash, high, run 3 | 4/5 | 57 | 39 | 6/95 | $0.19 | 0.12 |
  | Three Flash runs merged | 5/5 | 189 | 119 | 12/272 | $0.51 | 0.26 |
  | glm-5.3, low | 5/5 | 62 | 42 | 6/69 | $1.23 | 0.18 |

  The trial's pre-set rule decided Flash replaces glm-5.3: Flash matched it within its own run-to-run spread, at about a tenth of the cost. Flash is less steady on broad questions; its run 3 lost a two-country question.
- **Planner.** Three trials found that neither best-of-3 planning nor a landscape survey before planning made plans steadier.
  - `gpt-6-sol` costs a third of Opus's price per plan.
  - `gpt-6-sol` split a seven-country question by country in most runs. Opus paired the countries.
  - Opus refused to plan a question on T-cell exhaustion as a biological risk, hence the fallback.
  - A prompt asking for one question per subject made both models plan per subject, but on `gpt-6-sol` it filled the whole question range, adding scouts, so it was not adopted.
- **Synthesizers**, re-synthesized on the same pilot-8 ledger with one grading each:

  | Synthesizer | Cost | Time | Rubric | Unsupported statements |
  |---|---:|---:|---:|---|
  | Opus 5.5, medium | $0.43 | 92 s | 0.21 | 14 of 27 |
  | glm-5.3-flash, max | $0.03 | 819 s | 0.24 | 22 of 46 |

  A comparison of synthesizers was planned (glm-5.3 high, Opus medium, Flash max, same ledgers, $5 cap) and shelved. Run-to-run variation in synthesis is larger than the judge's (section 5.7).
- **Verification varies.** One verification of the same report gave 12 of 46 statements unsupported one time and 21 of 39 another. A synthesizer-prompt trial of about $6 decided nothing for this reason. Scout has no LLM verifier; design around the judge and deterministic checks.

### 5.6 How calls and jobs failed

| Role | Failure | Count |
|---|---|---:|
| scout | Hit a usage limit | 37 |
| scout | Cancelled | 6 |
| scout | Output failed its checks twice | 5 |
| scout | Other | 4 |
| deep_dive | Hit a usage limit | 22 |
| deep_dive | Other | 3 |
| synthesizer | Cancelled | 2 |
| synthesizer | Timeout (provider error) | 1 |
| verifier | Other | 2 |
| gap_analyst | Prompt too large for a retry | 1 |

At the job level, the failures were:

- **Z.ai returned HTTP 429 when the balance ran out**, not for a rate limit. Only the message tells the two apart. Check balances before any paid step.
- **The $5 cap was reached** during a second verification round.
- **A NUL byte in stored PDF text** failed a job. Fixed: NUL bytes are now stripped before storage.
- **An unstreamed synthesis timed out.** Fixed: synthesis is now streamed.
- **Opus refused a question.** Fixed: the fallback model takes refused calls.

### 5.7 The judge

- **Setup.** One call returns a verdict for every rubric point, and an output validator requires exactly one verdict per point. It runs on `openai:gpt-6-sol` at high effort and reads the whole report: answer, key statements, caveats, and sources (judge version 2).
- **Cost.** $0.008 to $0.02 per short report and $0.04 to $0.10 per DRB-II report.
- **Stability:**

  | Report | Graded | Scores |
  |---|---|---|
  | st07 run 78a2a6f9 | 3 times | 5/9, 5/9, 5/9 |
  | task2+ run 2722a06e | 3 times | 11/72 each time |
  | task2+ run 3bccbe6d | 3 times | 14, 13, 14 of 72 |

  So the judge varies by about one point in 72, much less than synthesis varies between runs.
- **Effort.** At low effort the judge missed points that reports stated plainly.
- **Wording.** It read incidental details as required, for example "(announced February 2026)", so rubric points must state one fact each. It must also credit equivalent wording. One paraphrase was marked unmet at low effort.
- **Cross-vendor check.** The earlier plan used a second judge from another vendor to guard against self-preference (`gpt-6-sol` is also a synthesizer candidate). It was never run.
- **Code.** The judge (`RubricJudge` in `src/research_loop/evals.py`) and its grading of stored runs are at the archive tag, to be ported.

## 6. Questions and graders available

These are our own cases. st01 to st05 are active; st06 and st07 were retired as study questions because their rubrics were ours, not experts'. st07 stays as the before-and-after baseline, since it is the only question with a graded run on the old design.

| ID | Question | Type | Scored by |
|---|---|---|---|
| st01 | At which conference, and in which year, was the paper "Attention Is All You Need" published? | One fact a model knows; measures over-searching (a good run stops almost at once) | Exact answer: NeurIPS 2017, NIPS 2017, Neural Information Processing Systems 2017, "NeurIPS, 2017", or "NIPS, 2017" |
| st02 | The first author of the paper that introduced residual networks (ResNet) left Microsoft Research in 2016. Which research lab did he join? | Two hops | Exact answer: Facebook AI Research, FAIR, Facebook AI Research (FAIR), Meta AI, Meta AI Research, or Facebook |
| st03 | According to OpenAI's August 2024 announcement of SWE-bench Verified, how many professional software developers annotated the SWE-bench samples? Give the number. | A number from one primary source | Exact answer: 93 |
| st04 | Which team won the video captioning track of the ImageNet Large Scale Visual Recognition Challenge (ILSVRC) 2016? | False premise | Rubric, 4 points (below) |
| st05 | For GPT-3 175B, Chinchilla, and Llama 2 70B, give each model's parameter count, number of training tokens, and context length as reported in its original paper, citing the source of each number. | Numbers from three primary sources | Rubric, 11 points (below) |
| st06 (retired) | Does chain-of-thought prompting help language models under about 10 billion parameters? Summarize what the research literature found, keeping preprints and published papers distinct. | Scholarly synthesis | Rubric, 6 points |
| st07 (baseline) | Is SWE-bench Verified still a trustworthy measure of coding-agent progress? | Contested and recent | Rubric, 9 points (below) |

The st04 rubric:

- Information recall:
  - States that ILSVRC 2016 had no video captioning track.
  - Names the video track ILSVRC did run: object detection from video (VID).
  - Does not name a winner of a captioning track.
- Analysis:
  - Says what evidence the absence rests on, such as the ILSVRC 2016 results or task pages.

The st05 rubric was checked against the papers:

- Information recall:
  - GPT-3: 175 billion parameters.
  - GPT-3: about 300 billion training tokens.
  - GPT-3: 2,048-token context.
  - Chinchilla: 70 billion parameters.
  - Chinchilla: 1.4 trillion training tokens.
  - Chinchilla: says the paper does not report a context length, rather than giving one as the paper's.
  - Llama 2 70B: 70 billion parameters.
  - Llama 2 70B: 2 trillion training tokens.
  - Llama 2 70B: 4,096-token context.
- Presentation:
  - Cites the original paper for each model.
  - Presents the numbers as a table or an equally compact comparison.

The st07 rubric:

- Information recall:
  - SWE-bench Verified is a 500-instance subset of SWE-bench.
  - OpenAI released it in August 2024.
  - Human annotators screened instances for underspecified issues and unfair or overly specific tests.
  - Documents contamination or memorization concerns for public GitHub repositories.
  - OpenAI stopped reporting SWE-bench Verified scores (announced February 2026).
  - Gives OpenAI's reasons: flawed tests in many tasks it audited, and frontier models reproducing gold patches or task details.
  - Reports METR's finding that roughly half of test-passing patches would not be merged by maintainers.
- Analysis:
  - Separates what a passing score measures from general coding ability.
  - Labels scores a vendor reports about its own model as vendor claims.

**st07 baselines on the old design:**

- bf89797d: rubric 0.556 (5 of 9), $2.26, 11.8 minutes. Quotes verified 97.8%, sources observed 100%, verifier-supported statements 100%.
- 78a2a6f9 (`value` lineup): also 0.556, $2.24, 25 minutes.
- The notes those runs used: "Keep preprints and published papers distinct." and "Label scores a vendor reports about its own model as vendor claims."

**DeepResearch Bench II tasks (deep).** These are English tasks with rubrics written by experts, one fact per point, licensed CC BY 4.0. They come from `https://raw.githubusercontent.com/imlrz/DeepResearch-Bench-II/main/tasks_and_rubrics.jsonl`, at positions 1, 7, 20, and 31 among the English tasks.

| Task | Theme | Rubric points | Old-design grades |
|---|---|---:|---|
| task2+ | Finance and business (pensions in seven Southeast and South Asian countries) | 72 | 0.153 (run 2722a06e), 0.19 (run 3bccbe6d) |
| task8 | Science and technology | 52 | |
| task17+ | Software development (low-code platforms) | 75 | |
| task26 | Health (T-cell exhaustion) | 71 | |

Each task comes with blocked URLs: the expert reports its rubric was written from, which the tools refuse to fetch. They were chosen by a rule, not by reading them: within each of the four largest English themes, the lowest-index CC BY 4.0 task with 50 to 80 rubric points.

**Other questions used before**, without graders:

- "Compare PydanticAI, LangGraph, and AutoGen approaches to multi-agent research workflows ... prefer official documentation."
- "Determine whether PostgreSQL advisory locks are appropriate for preventing duplicate execution of the same background job across multiple workers ... prefer PostgreSQL primary documentation."
- "Compare two current frontier language models for evidence-heavy web research ..."

**Scoring cautions:**

- Exact-answer matching compares the whole normalized answer, so "93 developers" scores 0. Read every miss by hand.
- st01 and st02 are answerable from training data on purpose; they measure how quickly a run stops.

## 7. Methods that worked, and mistakes to avoid

**These worked in the previous study:**

- **Recorded tools.** A reference run uses cache mode `record`, and later arms use `reuse`. Arms then see the same search results and pages, and differences come from the models rather than the web changing. Report how many of each arm's calls were served from the recording.
- **Paired comparisons.** Compare each arm with the reference on the same questions, question by question.
- **Fixed grader.** One judge model, prompt, and version for all arms.
- **Fixed synthesizer for research arms.** Grade scout variants through reports that one fixed synthesizer writes, so only the research differs.
- **Replay one role.** Send the same stored prompt, or the same ledger, to another model. For example, re-synthesize stored ledgers to compare synthesizers without paying for research again.
- **Decision rules written before running.** State in advance what result adopts which option, including "undecided", and never change a rule after seeing data.
- **Decide, then stop.** Do not rerun comparisons that are decided. Add samples only to undecided ones.
- **Questions are added, never edited.** Once a question has run, its wording, answers, and rubric stay fixed, so results stay comparable.
- **Steps.** Run the study in small steps, and log each one: what ran, its cost, what it found, and what it changed.

**These went wrong:**

- **Too little data to decide.** The synthesizer-prompt trial cost about $6 and decided nothing, because one verification per report varies more than the prompts differed. Check first that the sample size can detect the difference you are looking for, given the variance already known.
- **Costs double-counted.** A trial script counted the cost of calls running at the same time against each other, and overstated its spend by about a third. Attribute cost per call or per run from `run_calls.cost_usd`, never by timing.
- **A measure that measured the wrong thing.** Plan agreement was measured by shared words, so it measured wording, not how plans split the question. The follow-up measured it by the named entities each question covers.
- **Results lost.** A trial kept its results only in memory and lost them when it crashed. Store every paid trial run in Postgres. Scout does this automatically when `DATABASE_URL` is set; mark study runs, for example with a note or `parent_run_id`.
- **Stalls misread.** Watching `rchar` for a stall misreads streaming, because it does not count socket reads. Use the socket's `bytes_received` (`ss -ti`).
- **Hidden network access.** A warm local benchmark cache hid a dataset download that CI refuses. Tests must pass with an empty cache.

## 8. Constraints and preferences

- **Our money.**
  - Estimate every paid step from the most expensive call observed, retries included, not from the average.
  - Give each step a hard dollar cap, and enforce it in code.
  - Tell us if an estimate grows, and get approval before spending past it.
  - Screen prompt variants on cheap models first.
  - Earlier trials of about $6 and $9.46 (the latter over-reported) were "way too much" for what they decided.
- **Ask about design decisions.** Where results are stored, what is recorded or dropped, and anything that could lose a paid run's records are our decisions. Name the trade-off and recommend, then ask.
- **Check balances before paid steps.** A Z.ai 429 usually means an exhausted balance; `research doctor --smoke` tells the two apart.
- **Tests stay offline.** Tests and evals are separate: evals never run inside pytest or CI.
- **Documents** are in plain, complete sentences, with sentence-case diagram labels and no slogan formatting.
- **Architecture.** Keep workflow design separate from model choice. No new infrastructure (Temporal, DBOS, Redis, a vector database) without a concrete need. Long-horizon mode is out of scope for this study.

## 9. Where the data is

- **Previous design's code:** git tag `archive/pre-scout-2026-09`. It holds the settings study's full decision log (`docs/settings-study.md`), model routing (`docs/model-routing.md`), the p01 token profile (`long_horizon/agentic_se/PROMPT_SIZES.md`), and the trial and replay scripts under `scripts/`.
- **Previous design's data:** `~/research-loop-archive/2026-09-25/`. Its `README.md` explains how to restore the dump. It contains:
  - `research_loop.dump`, a `pg_dump`;
  - one JSONL file per table: `research_jobs` (with `effective_config`, plan, report, verification, ledger, and review reasons), `research_tasks` (role, model_id, effective_config, output, usage with cost, started and finished times, error), `research_tool_events` (tool name, hashed arguments, summarized result, outcome, called and returned times, cache hit), and `research_task_messages` (full transcripts for 251 tasks);
  - `benchmark_outputs.tgz`, with the study's step records, trial records, grades, and the recorded search and fetch cache.
- **Local copies:** `benchmark_outputs/` in the repository (git-ignored) still holds the same records, including `grades/baseline-bf89797d-st07.jsonl` and `grades/step3-judge-variation.jsonl`.
- **New runs:** the `runs` and `run_calls` tables in the `research_loop` database (`DATABASE_URL`), and Logfire traces. Example queries:

  ```sql
  -- Cost, tokens, and time per role and model across runs.
  select c.role, c.model, count(*), percentile_cont(0.5) within group (order by c.cost_usd) as median_usd,
         max(c.cost_usd), percentile_cont(0.5) within group (order by (c.usage->>'input_tokens')::int) as median_input,
         percentile_cont(0.5) within group (order by extract(epoch from c.finished_at - c.started_at)) as median_s
  from run_calls c group by 1, 2;

  -- Per run: status, cost, duration, and why it needs review.
  select id, status, cost_usd, finished_at - started_at as took, checks->'review_reasons', config->'models'
  from runs order by started_at desc;
  ```

## 10. What we need the study to decide

These are the questions we want answered, roughly in priority order. The design should say which it can answer within a small total budget, and which it defers.

1. **What does a Scout run cost, and how long does it take, by question type (shallow, medium, deep) under the defaults?** This is the basis of every other cost estimate. Include worst cases.
2. **Scout effort.** Flash at max against Flash at high: cost, time, whether it fits the deadline, and quality. Max is our default, but it is untested for scouts.
3. **Synthesizer choice for Scout-sized ledgers.** Candidates are Opus 5.5 at medium (default), `gpt-6-sol`, `glm-5.3` at high, and Flash (too slow at max; perhaps at high). Weigh cost, time within the deadline, rubric score, and share of statements at the `read` support level.
4. **Budget shape.**
   - How many questions to plan (1 to 4, or up to 8).
   - Scout requests and call budgets.
   - Whether the $0.40 synthesis share and $0.75 total are right, and what the allocation should be per question type.
5. **Deadline fit.** How often scouts are cut off at 4.5 minutes, and at what cost to quality. Whether FlashX's speed is worth 2.5 times the price.
6. **Planner.** Is `gpt-6-sol` worth it over `gpt-6-luna` (about 50 times cheaper per plan) for Scout?
7. **Retrieval.** How much DuckDuckGo failures and failed fetches cost in wasted calls. This is an input to a later decision on search providers.
8. **Measures.** Which to track as regression criteria for Scout: rubric score, answer match, citation integrity, share of statements read, quote-verified rate, cost, and time.

For each part, say:

- the arms;
- the questions it uses and why;
- repeats, and the variance you assume;
- cache modes (record, then reuse);
- what is graded, and how;
- the decision rules, written before running;
- a worst-case cost estimate and a hard cap;
- what code must exist first: the eval harness, a replay tool, effort per role as a setting, and so on;
- in what order to run it, with stopping points where we decide whether to continue.

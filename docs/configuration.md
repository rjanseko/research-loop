# Configuration inventory

This is the audit sheet for Scout's **code defaults checked on 29 September 2026, checkout `852c46a`**. It covers the settings accepted by [`config.py`](../src/research_loop/config.py), the CLI overrides, and the fixed limits that materially affect a run. Values here are code defaults, **not a copy of a developer's `.env` or provider account limits**. The token and dollar limits below apply to Scout when it calls provider APIs using this project’s keys; they do not describe a Codex desktop session’s ChatGPT plan allowance. No credential value belongs in this file.

For an actual run, its saved `config` in `run.json` or `research show RUN_ID --format json` is the effective snapshot, including the chosen depth and model settings. `research doctor` checks the current environment without making a model call; `research doctor --smoke` makes paid calls. Explicit `Settings(...)` arguments override exported environment variables, which override `.env` in the working directory, which override the defaults below. Nested variables use `__`, for example `RESEARCH_LIMITS__SCOUT_REQUESTS=24`. A CLI option such as `--depth` or `--max-usd` then applies to that run.

## Models and general settings

Model values use `provider:model@effort`; effort must be `low`, `medium`, `high`, or `xhigh`. A live provider may be `openai`, `anthropic`, `zai`, `google`, or `deepseek`; the offline dry model uses `fake`. Verify model IDs with `research doctor --smoke` before a paid run because IDs can become stale.

| Setting | Committed default | Effect |
|---|---|---|
| `RESEARCH_ENV` | `development` | Environment label. |
| `RESEARCH_MODELS__PLANNER` | `openai:gpt-6-sol@high` | Plan and gap analysis. |
| `RESEARCH_MODELS__SCOUT` | `openai:gpt-6-luna@high` | Scouts and deep dives. |
| `RESEARCH_MODELS__SCOUT_ALT` | unset | When set, every other scout and deep dive in a **deep** run uses it. |
| `RESEARCH_MODELS__SYNTHESIZER` | `anthropic:claude-opus-5-5@medium` | Report synthesis. Must be an `anthropic:` model: the report is built from Claude's citations. |
| `RESEARCH_MODELS__SYNTHESIZER_FALLBACKS` | `{anthropic:claude-opus-5-5: anthropic:claude-opus-5@medium}` | Anthropic server-side continuation if the primary synthesis is declined; usage is priced for both attempts when returned. |
| `RESEARCH_MODELS__FALLBACK` | `openai:gpt-6-sol@high` | Planner fallback on refusal or provider error; scouts have no fallback. The synthesizer has the separate server-side fallback above. |
| `RESEARCH_MODELS__JUDGE` | `openai:gpt-6-sol@high` | Rubric and quality judges; audit and diagnosis use their own configured defaults. |
| `RESEARCH_MODELS__AUDIT` | `zai:glm-5.3@high` | Support auditor for standalone commands and studies with `audit = true`; `--model` or a study `audit_model` overrides it. |
| `RESEARCH_MODELS__DIAGNOSE` | `zai:glm-5.3@high` | Diagnosis judge for standalone commands and studies with `diagnose = true`; `--model` or a study `diagnose_model` overrides it. |
| `RESEARCH_MODELS__CHEAP` | `openai:gpt-6-luna@low` | Replaces every model but the synthesizer in a `study run --cheap` check, including audit and diagnosis models. |
| `RESEARCH_MODELS__CHEAP_SYNTHESIZER` | `anthropic:claude-haiku-4-5-20251001@low` | The synthesizer in a `study run --cheap` check; must be an `anthropic:` model. |
| `RESEARCH_MODELS__DRY` | `fake:fuzz@high` | Replaces every model in a `study run --dry` check; only the offline `fake:` provider is accepted. |
| `RESEARCH_ENABLED_PROVIDERS` | empty | Empty means every provider with a key is enabled; otherwise a comma-separated allowlist. |
| `RESEARCH_TOKENS_PER_MINUTE` | `{"openai:gpt-6-luna": 2000000}` | Initial per-model scout pacing limit. An OpenAI response can replace an unset default with the reported limit; setting this variable fixes the value. `{}` disables pacing. |
| `DATABASE_URL` or `RESEARCH_DATABASE_URL` | unset | Postgres persistence. `.env.example` supplies a local URL if copied to `.env`. |
| `RESEARCH_LOGFIRE` | `true` | Tracing switch; `LOGFIRE_TOKEN` or an authenticated Logfire project can enable export. |
| `RESEARCH_CACHE_MODE` | `live` | `off`, `live`, `record`, `replay`, or `reuse`; see [cache and acquisition](#search-reading-history-and-cache). |
| `RESEARCH_CACHE_DIR` | `.cache/research-loop` | Search, page, and scholarly cache root. |
| `RESEARCH_STUDY_CACHE_ROOT` | `.cache/studies` | A named study normally uses `reuse` under this root. Explicit cache settings still win. |
| `RESEARCH_SEARCH_ENGINE` | `duckduckgo` | Comma-separated chain of `duckduckgo`, `serper`, `brave`, `exa`; the next engine runs only after an empty result or failure. `hybrid` expands to `duckduckgo,exa`. |
| `RESEARCH_READ_FALLBACK` | unset | Unset means `oa`, then `exa` and `firecrawl` if their keys exist. Empty disables fallback; a comma-separated value sets the exact order. |
| `RESEARCH_OFFLINE_WORLD` | unset | Seed for the fake-model/offline-world harness; not a live-run setting. |
| `RESEARCH_OFFLINE_FAULT_RATE` | `0.2` | Fault frequency in that harness. |

Credentials and service identification default to **unset**: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `ZAI_API_KEY`, `GOOGLE_API_KEY`, `DEEPSEEK_API_KEY`, `EXA_API_KEY`, `SERPER_API_KEY`, `BRAVE_API_KEY`, `FIRECRAWL_API_KEY`, `CORE_API_KEY`, `OPENALEX_API_KEY`, `LOGFIRE_TOKEN`, and `CROSSREF_MAILTO`. `CROSSREF_MAILTO` is contact identification, not a secret. A selected paid search engine or paid reader needs its corresponding key. An existing `.env` that explicitly sets `RESEARCH_CACHE_MODE` or `RESEARCH_CACHE_DIR` keeps that override during a named study; remove those assignments to use the study-specific `reuse` cache. Source: [`Settings`](../src/research_loop/config.py), [`models.py`](../src/research_loop/models.py).

## Effective depth limits

These are the values of `ScoutLimits().for_depth(depth)` with no environment overrides. Every `ScoutLimits` row is overrideable as `RESEARCH_LIMITS__<FIELD>`; quick and deep overrides use `RESEARCH_LIMITS__QUICK__<FIELD>` or `RESEARCH_LIMITS__DEEP__<FIELD>`. The final `follow_up` row is a depth-tier setting only. Only the fields declared by `DepthTier` accept a depth-specific override: `max_questions`, `cost_usd`, `synthesis_usd`, `research_seconds`, `deadline_seconds`, `followup_cost_usd`, `followup_deadline_seconds`, `deep_dive_seconds`, `scout_productive_calls`, `deep_dive_requests`, `deep_dive_productive_calls`, `deep_dive_misses`, and `follow_up`. A depth override changes only its named fields. Values are seconds, dollars, tokens, or counts as labeled. The planner chooses depth unless `research scout --depth` fixes it.

| Field and unit | Quick | Standard | Deep |
|---|---:|---:|---:|
| `max_questions` count | 2 | 4 | 8 |
| `parallel_scouts` count | 8 | 8 | 8 |
| `cost_usd` ordinary-run soft envelope | $2.14 | $3.60 | $3.60* |
| `planner_usd` | $0.05 | $0.05 | $0.05 |
| `synthesis_usd` | $1.96 | $2.45 | $2.45 |
| `deadline_seconds` ordinary-run deadline | 360 | 1,320 | 2,520* |
| `research_seconds` scout deadline | 240 | 900 | 1,800 |
| `request_timeout_seconds` per planner, gap, or synthesis request | 120 | 120 | 120 |
| `scout_request_timeout_seconds` per scout or deep-dive request | 600 | 600 | 600 |
| `scout_requests` model requests | 30 | 30 | 30 |
| `scout_productive_calls` | 128 | 128 | 192 |
| `scout_misses` | 16 | 16 | 16 |
| `scout_tokens` total token limit | 8,000,000 | 8,000,000 | 8,000,000 |
| `guarded_scout_max_output_tokens` per request | 48,000 | 48,000 | 48,000 |
| `synthesis_tokens` total token limit | 200,000 | 200,000 | 200,000 |
| `synthesis_max_output_tokens` per request | 32,000 | 32,000 | 32,000 |
| `followup_cost_usd` follow-up soft envelope | $4.06 | $4.35 | $5.85 |
| `followup_deadline_seconds` | 1,500 | 1,500 | 2,520 |
| `gap_usd` | $0.10 | $0.10 | $0.10 |
| `gap_seconds` | 45 | 45 | 45 |
| `max_gaps` count | 3 | 3 | 3 |
| `deep_dive_usd` per dive | $0.35 | $0.35 | $0.35 |
| `deep_dive_seconds` per dive | 240 | 240 | 480 |
| `deep_dive_requests` model requests | 12 | 12 | 30 |
| `deep_dive_productive_calls` | 16 | 16 | 128 |
| `deep_dive_misses` | 8 | 8 | 16 |
| `quick.follow_up` / `deep.follow_up` | `false` | `false` | `true` |

*Deep automatically uses follow-up, so its active envelope is **$5.85** and its active whole-run deadline is **2,520 seconds**. `--follow-up` turns on the follow-up envelope at quick or standard depth. The ordinary `cost_usd` value still exists in the effective deep object but is not the active envelope.* The time limits count from the start of the run, including planning. Gap and dive deadlines are also bounded by the remaining whole-run time, with **90 seconds reserved for synthesis**. Source: [`ScoutLimits`](../src/research_loop/config.py), [`_Run.execute`](../src/research_loop/scout.py).

The ordinary scout share is `(cost_usd - planner_usd - synthesis_usd) / number_of_questions`, rounded to four decimals. In follow-up mode it is `(followup_cost_usd - planner_usd - synthesis_usd - gap_usd - max_gaps × deep_dive_usd) / number_of_questions`. With the maximum number of questions, that is **$0.065** quick, **$0.275** standard, **$0.175** standard with follow-up, and **$0.275** deep. These are per-call soft cost limits; `--max-usd` adds a shared pre-dispatch hard cap. Since scout-v15 a scout's share also counts its own paid searches and page reads (`ExternalSpend.by_question`), and its tools are withdrawn with a note once the share or `scout_tokens` would not cover two more requests, each taken as twice the average so far (`LoopBudget.out_of_money`).

At the committed defaults, the planner and gap analyzer each have a **2-request, 100,000-total-token** limit and a **16,000-token output** cap per request. The synthesizer has a **1-request** limit in addition to `synthesis_tokens`. These values can be overridden through `RESEARCH_MODEL_CALLS__...` below. A scout's framework tool-call limit is `productive + misses + 12 batch slack + 16 re-read slack`: **76** for an ordinary quick/standard scout, **92** for a deep scout, **52** for a quick/standard deep dive, and **76** for a deep-run dive. Re-reading a memoized page does not spend the productive/miss loop budget. A scout result keeps at most **5 open-item names of 60 characters each**; longer or sentence-like items are dropped. Source: [`scout.py`](../src/research_loop/scout.py), [`budget_notes.py`](../src/research_loop/budget_notes.py), [`agents.py`](../src/research_loop/agents.py), [`models.py`](../src/research_loop/models.py).

## Model call limits

These defaults live in `Settings.model_calls` and can be overridden with `RESEARCH_MODEL_CALLS__<FIELD>`. A run records their effective values in `config.model_calls`; evaluation commands read the same environment-backed settings. Defaults are unchanged by making them configurable.

| Field | Default | Applies to |
|---|---:|---|
| `planner_requests` | 2 | Planner and gap analyzer request cap. |
| `planner_tokens` | 100,000 | Planner and gap analyzer total token cap. |
| `planner_max_output_tokens` | 16,000 | Planner and gap analyzer output cap per request. |
| `synthesizer_requests` | 1 | Synthesizer PydanticAI request cap: one request, not cached. Provider SDK retries are off; only the retry wrapper sends it again, before a reply starts (`scout-429-v6`). |
| `rubric_timeout_seconds` | 600 | Rubric grading model request. |
| `rubric_max_output_tokens` | 16,000 | Rubric grading request under a hard cap. |
| `audit_timeout_seconds` | 600 | Support audit model request. |
| `audit_max_output_tokens` | 16,000 | Support audit request under a hard cap. |
| `quality_timeout_seconds` | 180 | Quality assessment model request. |
| `quality_max_output_tokens` | 5,000 | Quality assessment output cap. |
| `connect_timeout_seconds` | 5 | OpenAI model client connection. |
| `smoke_requests` | 3 | `research doctor --smoke` requests per distinct model. |
| `smoke_cost_usd` | $0.05 | Soft cost cap for each smoke-tested model. |

Source: [`config.py`](../src/research_loop/config.py), [`scout.py`](../src/research_loop/scout.py), [`models.py`](../src/research_loop/models.py), [`evals.py`](../src/research_loop/evals.py), [`audit.py`](../src/research_loop/audit.py), [`quality.py`](../src/research_loop/quality.py), [`doctor.py`](../src/research_loop/doctor.py).

## Timeouts, waits, and retries

| Behavior | Current value | Source |
|---|---|---|
| Planner fallback deadline | 90 seconds; on failure it makes one research question from the original question | [`scout.py`](../src/research_loop/scout.py) |
| Failed/cancelled call recording wait | Up to 5 seconds | [`scout.py`](../src/research_loop/scout.py) |
| `research doctor --smoke` | By default, up to 3 model requests and a $0.05 soft cost limit per distinct planner, scout, synthesizer, or fallback model; configurable above | [`doctor.py`](../src/research_loop/doctor.py) |
| Database connection | 3-second connect timeout for doctor’s migration check; 5 seconds for migration preflight and `research db` commands | [`doctor.py`](../src/research_loop/doctor.py), [`cli.py`](../src/research_loop/cli.py) |
| Scout model request timeout | `scout_request_timeout_seconds` above (600 seconds); a timeout is **not retried** | [`models.py`](../src/research_loop/models.py), [`rate_limit.py`](../src/research_loop/rate_limit.py) |
| Model 429 retry (every run role) | At most 2 retries, only when the provider gives a retry time; add 0.1 seconds after reset | [`rate_limit.py`](../src/research_loop/rate_limit.py) |
| Model transient connection retry (every run role) | 1 retry after 1 second; no timeout retry | [`rate_limit.py`](../src/research_loop/rate_limit.py) |
| Provider SDK retry | 0 for every run role since `scout-429-v6`; the retry wrapper (`RateLimitModel`) retries timed 429s, server errors, and connection faults for all of them, and a run records its retries in `checks.retries`. Guarded requests also disable the planner's fallback. Evaluation commands without a hard cap keep the client default of 2 | [`models.py`](../src/research_loop/models.py), [`scout.py`](../src/research_loop/scout.py) |
| Agent output validation | 1 retry after an invalid planner, scout, gap, or synthesizer output; a second failure ends the call | [`agents.py`](../src/research_loop/agents.py) |
| Scout token pacing | 60-second sliding window targeting 90% of the configured or provider-reported limit; an empty window admits its first request even if that estimate exceeds the target. New text is estimated at 4 bytes/token | [`rate_limit.py`](../src/research_loop/rate_limit.py) |
| Web search failure retry | 3 attempts total, waiting 2 then 6 seconds; no retry for an empty result or a refused hard-cap reservation | [`web.py`](../src/research_loop/web.py) |
| Scholarly metadata retry | 2 attempts for 429 or 503; `Retry-After` defaults to 1 second and is clamped to 0–3 seconds | [`scholar.py`](../src/research_loop/scholar.py) |
| Scholarly provider concurrency | 2 requests per provider | [`scholar.py`](../src/research_loop/scholar.py) |
| Page fetch retry | 403 gets one browser-header attempt; 2 connection failures to a host stop later fetches from that host during the run | [`web.py`](../src/research_loop/web.py), [`acquisition.py`](../src/research_loop/acquisition.py) |

Shared minimum intervals between starts of requests, per provider: **DuckDuckGo 1 s, OpenAlex 0.2 s, Crossref 0.2 s, arXiv 3 s, Exa 0.2 s, Serper 0.1 s, Brave 0.05 s**. Search engines have separate retry sequences and rate slots when chained. Source: [`acquisition.py`](../src/research_loop/acquisition.py).

## Search, reading, history, and cache

| Behavior | Current value | Source |
|---|---|---|
| Search choices | DuckDuckGo default; optional ordered chain of DuckDuckGo, Serper, Brave, Exa | [`config.py`](../src/research_loop/config.py), [`web.py`](../src/research_loop/web.py) |
| Serper / Brave result request | 10 results each | [`web.py`](../src/research_loop/web.py) |
| Exa search | `auto` type; highlights capped at 600 characters per result; result count left to Exa's API default | [`web.py`](../src/research_loop/web.py) |
| Web search HTTP timeout | 30 seconds for Exa, Serper, and Brave | [`web.py`](../src/research_loop/web.py) |
| Public fetch | HTTPS only, at most 3 redirects, 15-second request timeout; 5,000,000 decoded bytes normally, or 25,000,000 for responses labeled PDF or `application/octet-stream` | [`acquisition.py`](../src/research_loop/acquisition.py), [`web.py`](../src/research_loop/web.py) |
| PDF and fetch window | First 30 PDF pages; up to 40,000 extracted characters per tool call, with `start` to page through; requested window clamped to at least 1,000 characters | [`web.py`](../src/research_loop/web.py), [`acquisition.py`](../src/research_loop/acquisition.py) |
| Scholarly search | Default `limit=5`, clamped to 1–25 for OpenAlex; up to 5 arXiv results additionally; 15-second HTTP timeout and 2,000,000-byte response cap | [`scholar.py`](../src/research_loop/scholar.py), [`tools.py`](../src/research_loop/tools.py) |
| Scholarly text | Query truncated to 300 characters for OpenAlex and 120 for arXiv; abstracts capped at 1,500 characters | [`scholar.py`](../src/research_loop/scholar.py) |
| Scout prompt history | Every request carries the scout's whole history; trimming old pages was removed in scout-v15 | [`models.py`](../src/research_loop/models.py) |
| Reading fallback | Try `oa`, Exa, Firecrawl in configured order when fetch fails for 401/403/429/451/500/502/503/504, transport error, empty/oversized/unsupported page; not for 404 | [`reading.py`](../src/research_loop/reading.py), [`web.py`](../src/research_loop/web.py) |
| Fallback page acceptance | At least 500 characters and not a short challenge page | [`reading.py`](../src/research_loop/reading.py) |
| Fallback paid HTTP timeout | Exa 30 seconds; Firecrawl HTTP 60 seconds with a requested 45-second scrape timeout | [`reading.py`](../src/research_loop/reading.py) |
| Cache | `live`: read entries up to 86,400 seconds old and write; `record`: write only; `replay`: read any age with no network on a miss; `reuse`: read any age and write misses; `off`: neither | [`acquisition.py`](../src/research_loop/acquisition.py) |
| Cache/memo size | Cache skips entries over 128,000 encoded characters; one run memoizes up to 64 full documents | [`acquisition.py`](../src/research_loop/acquisition.py) |

Under `--max-usd`, the fixed reservations for external calls are **Exa search $0.010, Serper $0.001, Brave $0.005, Exa page $0.002, Firecrawl page $0.0054**. Exa's reported charge replaces its reservation on success. Other prices are in [`prices.toml`](../src/research_loop/prices.toml) and the bundled pricing library. The guard reserves each model request using an input upper bound and its output cap, then settles returned priced usage; failed requests keep their reservation except an unprocessed 429. Its current policy is `usage-anchor-v7`, including reservation for a configured server-side synthesis fallback. Before a reply with recorded usage, its input reservation is twice the serialized request bytes plus 16,000 tokens; afterward it starts from the last reply’s billed input and output tokens, adds newly serialized bytes and 4,000 tokens, and refuses an input bound above 1,000,000 tokens. Source: [`web.py`](../src/research_loop/web.py), [`reading.py`](../src/research_loop/reading.py), [`study_budget.py`](../src/research_loop/study_budget.py).

The four committed model-price overrides in [`prices.toml`](../src/research_loop/prices.toml) are in USD per million tokens. They are the rates the budget guard uses for these models, not a claim about live vendor prices. The offline fake model uses `openai:gpt-6-luna` as a fixed pricing reference for simulated usage; it makes no provider call. Other model rates come from the pinned `genai-prices` package.

| Model | Input | Cached input | Output |
|---|---:|---:|---:|
| `zai:glm-5.3-flash` | $0.15 | $0.03 | $0.50 |
| `zai:glm-5.3-flashx` | $0.37 | $0.075 | $1.25 |
| `deepseek:deepseek-flash` | $0.30 | $0.006 | $1.20 |
| `deepseek:deepseek-v4-pro` | $1.32 | $0.044 | $3.96 |

## Run and evaluation overrides

| Surface | Options that change behavior | Default / rule |
|---|---|---|
| `research scout` | `--depth`, `--follow-up`, `--max-usd`, repeatable `--note`, `--block`, `--block-title`, `--case`, `--study`, `--arm`, `--replicate`, `--no-persist`, `--out` | Depth `auto`; follow-up off except for deep; no hard cap for an ordinary question; arm `default`; replicate `1`. Frozen `--case` requires `--max-usd` and persistence. |
| `research rescout` / `synthesize` | Required `--model` and `--max-usd`; optional `--study`, `--arm`, `--replicate`, `--out` | Rerun one stage against a stored plan or ledger. |
| `research grade` / `assess` | Required `--case` and `--max-usd` | Use the configured judge model. |
| `research audit` | Required `--max-usd`; optional `--model` | Checks stored report statements with `RESEARCH_MODELS__AUDIT` unless overridden; one or more run IDs. |
| `research diagnose` | Optional `--model`; `--max-usd` unless `--free` | Uses `RESEARCH_MODELS__DIAGNOSE` unless overridden; `--free` uses stored grades without model calls. |
| `research study plan/run` | TOML spec, `--dry`, `--cheap`, `--seeds`, `--out` | `plan` is free. Real `run` uses the spec; dry uses `RESEARCH_MODELS__DRY` (default `fake:fuzz@high`); cheap uses `RESEARCH_MODELS__CHEAP` (default `openai:gpt-6-luna@low`) and a $0.25 ceiling. Dry `--seeds` defaults to 1. |
| `research fuzz` | `--runs`, `--seed`, `--one`, `--fault-rate` | 100 runs from seed 0 at fault rate 0.2. |
| `research show` / `breakdown` | `show --format md` or `show --format json` (default `md`), optional `show --provenance`; a stored run ID | Read-only presentation of stored runs; `--provenance` adds exact cited passages and tool receipts to Markdown. |
| `research doctor` | `--smoke` | Plain doctor is free; smoke calls configured models. |
| `research db status/migrate/reconcile` | `reconcile --older-than MINUTES`, `--apply` | `--older-than` is required for reconcile; `--apply` is required to mutate stale rows. |

Study specs in [`study.py`](../src/research_loop/study.py) have these fields. Required values have no committed default:

| TOML field | Default | Meaning |
|---|---|---|
| `study` | required | Study name. |
| `kind` | `scout` | `scout`, `rescout`, or `synthesize`. |
| `cases` / `sources` | empty lists | Scout studies need cases; rerun studies need stored source run IDs. |
| `arms` | required, at least one | List of arms to compare. |
| `arms[].name` | required | Unique arm name. |
| `arms[].args` | `[]` | Extra CLI arguments for that arm. |
| `arms[].ref` | unset | Optional Git ref to run in a temporary worktree. |
| `arms[].env` | `{}` | Environment overrides for that arm. |
| `replicates` | 1 | Repetitions of each arm and target. |
| `cap_usd`, `estimate_usd`, `ceiling_usd` | required | Per-run hard cap, planning estimate, and parent study ceiling. |
| `grade`, `grade_estimate_usd`, `grade_cap_usd` | `false`, $0.06, $1.00 | Rubric grading and its estimate/cap. |
| `audit`, `audit_model`, `audit_estimate_usd`, `audit_cap_usd` | `false`, unset, $0.04, $0.30 | Support audit and its estimate/cap; an unset model uses `RESEARCH_MODELS__AUDIT`. |
| `diagnose`, `diagnose_model`, `diagnose_estimate_usd`, `diagnose_cap_usd` | `false`, unset, $0.15, $0.75 | Rubric diagnosis and its estimate/cap; an unset model uses `RESEARCH_MODELS__DIAGNOSE`. |

A study snapshots model IDs, effort, and model-call limits from its parent environment once before running arms. Each arm’s `env` overrides that snapshot; dry and cheap mode then replace model IDs. For study audit and diagnosis models, an explicit spec value wins over the arm’s environment, parent environment, `.env`, and committed default. A cheap study uses the first case or source per arm and one replicate, with a **$0.25** study ceiling and at most **$0.25 per run**. It sets run, grade, audit, and diagnosis cost estimates to **$0.06, $0.01, $0.01, and $0.03**, respectively. A dry study uses fake models and overrides the limits to 20 seconds of research, 60 seconds for an ordinary run, 130 seconds with follow-up, 5 seconds for gap analysis, 8 seconds per deep dive, and 1 second per model request. Quick dry runs allow **2 questions**, a **$0.30** soft envelope with **$0.12** for synthesis, 3 seconds of research, and a 30-second ordinary deadline; deep dry runs use 20 seconds of research, 60 seconds ordinary or 130 seconds with follow-up, and 8-second dives. It uses `RESEARCH_DRY_DATABASE_URL` if set; otherwise it derives a `research_dry` database from `DATABASE_URL`. PostgreSQL tests use `RESEARCH_TEST_DATABASE_URL` and require a test-named database. Evaluation calls default to **600 seconds / 16,000 output tokens** for rubric grades and support audits, and **180 seconds / 5,000 output tokens** for quality assessment; see the model-call settings above. Sources: [`study.py`](../src/research_loop/study.py), [`evals.py`](../src/research_loop/evals.py), [`audit.py`](../src/research_loop/audit.py), [`quality.py`](../src/research_loop/quality.py), [`tests/conftest.py`](../tests/conftest.py).

## Version markers and audit procedure

Runs record workflow, prompt, evidence, fetch, budget, and rate-limit versions so old observations are not silently compared with changed behavior. Markers on this checkout are **`scout-v21`**, **`scout-followup-v22`**, **`scout-research-v17`**, **`scout-synthesis-v9`**, **evidence 8**, **fetch 19**, **`usage-anchor-v7`**, and **`scout-429-v6`**. The external support audit is version **3**, rubric judge version **2**, quality evaluator version **1**, and diagnosis version **1**. Cache keys use version **1**; unlike the run versions, this marker invalidates cache entries. The current prompt fingerprints are `115ddcc4f1e87a6b2b3d659dcd1956d62b691b51053b875e389a347f36fbb26f` for an ordinary run and `ce8c9f1c44e709bd542f3b5491106902cdd8d62bc45872efa5d7369a3e51b41f` with follow-up. Source: [`scout.py`](../src/research_loop/scout.py), [`schemas.py`](../src/research_loop/schemas.py), [`acquisition.py`](../src/research_loop/acquisition.py), [`study_budget.py`](../src/research_loop/study_budget.py), [`rate_limit.py`](../src/research_loop/rate_limit.py), [`audit.py`](../src/research_loop/audit.py), [`evals.py`](../src/research_loop/evals.py), [`quality.py`](../src/research_loop/quality.py), [`diagnose.py`](../src/research_loop/diagnose.py), [`prompts.py`](../src/research_loop/prompts.py).

To audit a proposed configuration change: compare this sheet with the cited code and `.env.example`; run `research doctor` for a free key, price, database, and DNS check (its displayed search-engine label currently recognizes only exact `exa`, and its Logfire export status checks only `LOGFIRE_TOKEN`; inspect `RESEARCH_SEARCH_ENGINE` and authenticated Logfire project settings directly); inspect a stored run's `config` and `research breakdown RUN_ID` for the values and pacing actually used. For a behavioral change, follow the versioning, offline validation, and paid-study rules in [`AGENTS.md`](../AGENTS.md) and [`evaluation.md`](evaluation.md). Update this sheet when a default or fixed limit changes. Original source audit on 28 September 2026: all 75 numeric cells in the then-current effective-depth table matched `ScoutLimits().for_depth(...)`; fixed retry, timeout, acquisition, budget, and evaluation values were checked against the cited modules. The depth rows, model-call defaults, and version markers above were rechecked against this checkout on 29 September 2026. These checks cover committed source values, not provider-side limits or local `.env` overrides.

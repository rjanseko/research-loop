# Model routing: cost and quality per role

Status as of 2026-09-25. This is desk research: list prices, vendor and third-party benchmarks,
OpenRouter's catalog and usage rankings, and the p01 calibration runs. No new paid run was made.
It picks the lineups to benchmark. Per [benchmarks.md](benchmarks.md), the routing decision itself
should come from those models running through this harness. `scripts/route_costs.py` reproduces
every dollar figure here.

## Recommendation

Adopt the `value` preset, which carries the "Adopt" column below with prompt caching on every
route, then trial the cheaper options one role at a time:

| Role | Now | Adopt | Trial next | Thinking |
|---|---|---|---|---|
| Planner | Opus 5 | `anthropic:claude-opus-5-5` | none: about $0.03 a question on any model | `medium` |
| Scout | GLM-5.3 | keep `zai:glm-5.3`. Make `zai:glm-5.3-flash` the cheap scout | `zai:glm-5.3-flash` as the main scout | `high` |
| Gap analyst | GPT-5.6 Sol | `openai:gpt-6-sol` | none: GLM-5.3 would save $0.02 | `medium` |
| Deep dive | GPT-5.6 Sol | `openai:gpt-6-sol`, with prompt caching | `zai:glm-5.3`, if scout trials show search quality holds | `high` |
| Synthesizer | Opus 5 | `anthropic:claude-opus-5-5` | `zai:glm-5.3`, then `anthropic:claude-sonnet-5` | `medium` |
| Verifier | GPT-5.6 Sol | `openai:gpt-6-sol` | none. Keep it from a different vendor than the synthesizer | `high` |

Cost per question on the p01 profile:

| | As the pilot ran (little loop caching outside GLM) | With prompt caching configured |
|---|---:|---:|
| A `quality` preset | $3.20 | $2.32 |
| **B `value` preset** | **$2.03 (−37%)** | **$1.61 (−50%)** |
| F all trials pass (Z.ai-heavy) | $0.59 (−82%) | $0.61 (−74%) |

B moves no role to a weaker model: each change is a successor that is as cheap or cheaper and
scores at least as well. The trials trade benchmark standing for price, so they need the harness.

## Each role

**Planner.** One short call, about 1,100 input and 1,200 output tokens. It costs $0.007–0.03 on
any candidate, and a bad plan wastes everything after it. Use the strongest model: Opus 5.5.

**Scout.** Three parallel tool loops of about 16 requests each. Each request resends the whole
history, so a scout reads about 240,000 input tokens and writes about 11,000. Input price and
cached-input price decide the cost. Scouts gather breadth; the gap analyst and deep dives exist to
repair what they miss.
- **GLM-5.3** costs about $0.56 a question for three scouts, because 84% of its loop input is billed as
  cached. Its BrowseComp score, 75.9%, is the lowest of the current routes; frontier models score
  about 90%.
- **GLM-5.3-Flash** costs $0.03 a question. It is at or near the top of OpenRouter's tool-calling
  usage ranking, next to DeepSeek V4.1 Flash, and scores 78.4 on Toolathlon. No BrowseComp number is
  published. It runs on `zai`, which the repo already supports, and the `glm-heavy` preset already
  uses it as the cheap scout.
- Other options: **GPT-6 Luna** $0.05–0.09; DeepSeek V4.1 Flash and MiMo-V2.5, about $0.04–0.07 through
  OpenRouter; Gemini 3.8 Flash $0.34–0.49; GPT-6 Sol $0.91–1.88. GPT-6 Sol as scout would cost
  more than any other role.

**Gap analyst.** One call reading the projected ledger (about 23,000 input tokens) and writing a
short gap list. It costs $0.04–0.06 on GPT-6 Sol or GLM-5.3. It decides where the expensive deep
dives go, so the cheaper model does not pay for itself. GPT-6 Sol.

**Deep dive.** Two tool loops of about 170,000 input tokens each, plus salvage. At 53% of today's
cost, it is the biggest lever.
- GPT-5.6 Sol costs $1.70 a question. The pilot's usage shows only about 5% of its loop input
  billed as cached.
- GPT-6 Sol halves that to $0.85, and caching brings it to $0.39.
- GLM-5.3 would be $0.25–0.27. But a deep dive's job is hard retrieval, where GLM is weakest on
  BrowseComp.
- Through OpenRouter, Tencent Hy4 preview ($0.13–0.21) and MiMo-V2.5-Pro ($0.06–0.10) are cheaper
  still, with no search benchmark this doc could find. Kimi K3 (BrowseComp 91.2%) costs
  $0.59–0.86, no less than GPT-6 Sol.

**Synthesizer.** One call: about 26,000 input tokens (40,000 on Anthropic's tokenizer) and 11,000
output. It writes the report that DeepResearch Bench II grades, and a failed synthesis wastes
research that cost several times more.
- Opus 5.5 costs $0.38, against $0.47 for Opus 5, and is ahead on every benchmark Anthropic
  published for both.
- GLM-5.3 costs $0.09 and has the one in-harness data point: in the evidence-v4 rerun of p01 it
  replaced Opus 5, because the Anthropic account had no credits. That run had fewer unsupported
  verifier checks (25% vs 31%) and the same number of major findings (2). Several fixes landed in
  the same rerun, so this shows direction, not effect size.
- Sonnet 5 costs $0.19.

**Verifier.** One call: about 33,000 input tokens and 9,000 output. It checks the report against
the ledger, and should not be the model that wrote the report. GPT-6 Sol costs $0.16. If the
synthesizer moves to GLM, the verifier must stay off GLM.

## Where the money goes

| Lineup | Planner | Scout | Gap | Deep dive | Synthesizer | Verifier | Total | Cached |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| A `quality` preset | 0.04 | 0.56 | 0.12 | **1.70** | 0.47 | 0.31 | **3.20** | 2.32 |
| B `value` preset | 0.03 | 0.56 | 0.06 | 0.85 | 0.38 | 0.16 | **2.03** | 1.61 |
| C B, GLM-5.3-Flash scouts | 0.03 | 0.03 | 0.06 | 0.85 | 0.38 | 0.16 | 1.51 | 1.05 |
| D B, GLM-5.3 synthesizer | 0.03 | 0.56 | 0.06 | 0.85 | 0.09 | 0.16 | 1.74 | 1.32 |
| E B, GLM-5.3 deep dives | 0.03 | 0.56 | 0.06 | 0.25 | 0.38 | 0.16 | 1.44 | 1.49 |
| F Z.ai-heavy: Flash scouts, GLM gap, deep dives, synthesis; GPT-6 Sol verifier | 0.03 | 0.03 | 0.04 | 0.25 | 0.09 | 0.16 | 0.59 | 0.61 |
| G B, OpenRouter: DeepSeek V4 Flash scouts, Hy4 deep dives | 0.03 | 0.06 | 0.06 | 0.21 | 0.38 | 0.16 | 0.89 | 0.79 |
| H floor: GPT-6 Luna everywhere, Sonnet 5 synthesizer | 0.00 | 0.09 | 0.00 | 0.04 | 0.19 | 0.01 | 0.34 | 0.27 |
| I Opus 5.5 everywhere | 0.03 | 5.46 | 0.17 | 2.53 | 0.38 | 0.38 | 8.95 | 4.37 |

USD per question, list prices from `genai-prices` 0.1.8 on 2026-09-25. "Total" uses the loop
caching the pilot's usage showed; "Cached" assumes 80% of every tool loop's input is read from cache.

- **The deep dive and the scouts are loops; everything else is single calls.** Moving the loops
  is where money is saved. Moving the finishing calls buys little and risks the report.
- **Caching helps OpenAI and Anthropic loops, not GLM.** GLM already caches 84%, so lineups built
  on it gain nothing from the switch (E and F cost slightly more under the 80% assumption).
- **Putting a premium model everywhere is the wrong trade.** I costs almost three times A, mostly
  on scouts. The pilot scouts stopped on request and tool-call limits, not on reasoning.

## OpenRouter

OpenRouter's site is blocked from this environment. Prices come from the copy of its catalog in
`genai-prices`, and rankings from web search.

- **Nothing on OpenRouter beats the adopted lineup on the evidence found.** For the synthesizer
  and verifier, no open model has published results that challenge Opus 5.5 and GPT-6 Sol. For deep
  dives, the one open model with a frontier BrowseComp score, Kimi K3 (91.2%), costs as much as GPT-6 Sol.
- **Its top tool-calling models by usage** are GLM-5.3 Flash, DeepSeek V4.1 Flash, and Tencent
  Hy4 preview. Usage measures adoption, not quality. GLM-5.3 Flash is reachable through `zai`;
  the other two through `openrouter` (lineup G).
- **OpenRouter is a provider.** Set `OPENROUTER_API_KEY` and write a route as
  `openrouter:vendor/model`, for example `RESEARCH_VALUE_DEEP_MODEL=openrouter:tencent/hy4-preview`.
  `research-diagnose --network` checks that `openrouter.ai` is reachable.
- **Unpriced models turn off the dollar caps.** PydanticAI prices calls from `genai-prices`, not from
  the cost OpenRouter reports, and `genai-prices` has no entry for Hy4 preview. Its cost is `None`, and
  `_record_spend` then stops tracking the job's spend, so the job cost cap cannot hold.
  `research-diagnose --prices` flags such a route before any call, and `--smoke` warns again after one. The script prices Hy4 from its OpenRouter
  listing ($0.834 / $0.042 cached / $2.501). Any unpriced model needs a price source before a paid run.
- **Catalog prices can lag.** The catalog lists GPT-5.6 Sol at $2 / $10. OpenAI lists $4 / $20,
  a promotional price available at least through 2026-11-21. This doc uses OpenAI's price.

## Quality evidence

Public numbers only narrow the field. They come from other scaffolds, tools, and effort settings,
and sources disagree: two reports give GLM-5.3 an Artificial Analysis index of 45 and 60, under
different index versions.

| Model | $ in / cached / out per M | Evidence relevant to this loop |
|---|---|---|
| Claude Opus 5.5 (2026-09-22) | 4 / 0.20 / 20 | Top of the Artificial Analysis index at max effort (58). BrowseComp about 90–91%. Ahead of Opus 5 on every benchmark Anthropic published for both |
| Claude Sonnet 5 | 2 / 0.20 / 10 | Below Opus 5.5 overall. Anthropic reports less hallucination and sycophancy than Sonnet 4.6 |
| GPT-6 Sol (2026-09-22) | 2 / 0.20 / 10 | About one index point above GPT-5.6 Sol. OpenAI puts it at 90–95% of GPT-6 Astra's practical capability for 20% of the cost. No BrowseComp number published |
| GPT-5.6 Sol | 4 / 0.40 / 20 (promotional) | BrowseComp 92.2%, second on that leaderboard |
| GPT-6 Luna | 0.10 / 0.01 / 0.50 | DeepSWE 66.6%, Agents' Last Exam 50.9%. No search benchmark published |
| GLM-5.3 | 1.40 / 0.26 / 4.40 | BrowseComp 75.9%. Synthesizer in the p01 evidence-v4 rerun |
| GLM-5.3-Flash | 0.075 / 0.015 / 0.25 | Toolathlon 78.4, AutomationBench 48.8. Artificial Analysis index 57 in one report, 42 in another. At or near the top of OpenRouter's tool-calling usage |
| DeepSeek V4.1 Flash | 0.10–0.22 / 0.003–0.02 / 0.20–0.66 | Terminal-Bench 2.1 90.6. At or near the top of OpenRouter's tool-calling usage. Peak-hour prices are about double. No BrowseComp number found |
| Tencent Hy4 preview | 0.834 / 0.042 / 2.501 | 770B mixture-of-experts. Trades wins with Kimi K3 and GLM-5.3; behind Opus 5 and GPT-5.6. Not in `genai-prices` |
| Kimi K3 | 3 / 0.30 / 15 | BrowseComp 91.2% |

BrowseComp is close to saturated at the top, with the leaders within 1 point of each other. It still
separates GLM-5.3 from the frontier. That gap, and whether GLM-5.3-Flash shares it, is what the
trials must measure.

## Compatibility checked

The pinned `pydantic-ai-slim` 2.48.0 has profiles for Opus 5.5, Sonnet 5, GPT-6 Sol and Luna,
GLM-5.3 and GLM-5.3-Flash, and DeepSeek V4:

- **Opus 5.5 rejects a forced `tool_choice`.** When a `thinking` setting is present, PydanticAI
  switches its structured output to native JSON-schema output, which Opus 5.5 supports. Every
  route sets `thinking`, so nothing breaks. Opus 5.5 cannot turn thinking off: never give it
  `thinking=False`.
- **Opus 5.5 effort levels are not Opus 5's.** Its default is `medium`, and at a given level it
  thinks more than Opus 5 did. Keeping `high` would spend more output tokens than modeled here.
- **GPT-6 Sol and Luna have reasoning effort and prompt-cache breakpoints.** Like GPT-5.6, they
  do not accept `minimal`, which no route uses.
- **Checked on the wire, offline.** PydanticAI's own Anthropic and OpenAI clients were run on a
  local mock transport with `value`'s routes. The synthesizer request to Opus 5.5 carried
  `cache_control`, adaptive thinking with `effort: medium`, and a JSON-schema output format with
  no forced `tool_choice`. The deep-dive request to GPT-6 Sol carried `prompt_cache_key` and `high`
  reasoning, on both the Responses and Chat Completions APIs.
- **Check new IDs first.** Run `research-diagnose --smoke` on each new ID before a paid run. GPT-6
  Sol and Opus 5.5 are three days old.

## Using the value preset

`value` has `quality`'s limits, so a comparison changes models, thinking effort, and caching only:

- **Planner and synthesizer:** `anthropic:claude-opus-5-5` at `medium`.
- **Gap analyst, deep dive, and verifier:** `openai:gpt-6-sol`.
- **Scout:** `quality`'s `zai:glm-5.3`, with its override `RESEARCH_SCOUT_MODEL`.
- **Cheap scout:** `zai:glm-5.3-flash`, shared with `glm-heavy` through `RESEARCH_GLM_CHEAP_MODEL`.
  It only takes low-difficulty questions that do not need primary sources, so moving it off GPT-5.6
  Luna is low risk.
- **Prompt caching** on every route. A route's `prompt_cache` flag sends `anthropic_cache` to
  Anthropic and a stable `openai_prompt_cache_key` to OpenAI, chosen from the model at call time,
  so an override to another provider keeps working. Z.ai, Google, xAI, and OpenRouter cache on
  their own.

Its planner, gap analyst, deep dive, synthesizer, and verifier have their own overrides
(`RESEARCH_VALUE_*_MODEL`, in [setup.md](setup.md#model-routing)), so the trials change one role of
`value` without touching `quality`:

```
RESEARCH_VALUE_SYNTH_MODEL=zai:glm-5.3     # trial D
RESEARCH_VALUE_DEEP_MODEL=zai:glm-5.3      # trial E
```

The scout trial (C) sets `RESEARCH_SCOUT_MODEL=zai:glm-5.3-flash`, which `quality` shares. Run it
as its own `--policies value` run.

`RESEARCH_ALT_DEEP_MODEL` (`xai:grok-4.5`) returned HTTP 403 in the v4 rerun. Point it at a provider
you have, or leave verification rounds at 0 as the study does. After the first paid run, compare the
deep dives' `usage.cache_read_tokens` against `quality`'s to confirm caching took effect.

## How to confirm it

Run A against B first, in one run: `research-bench examples/benchmark_suite.toml --policies
quality value --paid`. Then run B against one role change at a time, in order of expected saving:
C (Flash scouts), D (GLM synthesizer), E (GLM deep dives). Each trial is a `--policies value` run
with one override, and the manifest's policy snapshot records the routes. Keep the core suite and
graph version fixed.

| Role changed | Lane | Watch |
|---|---|---|
| Scout | BrowseComp | exact-answer match, then the official grader; cost per correct answer; unique-search rate |
| Deep dive | BrowseComp, DRB-II | primary-source rate, observed-source rate, salvage count |
| Synthesizer | DRB-II through the official evaluator (`--export-reports`) | rubric score, supported-claim rate |
| Verifier | DRB-II | **Hold the grader fixed.** The supported-claim rates are judged by the run's own verifier, so changing the verifier changes the ruler. Rescore with one fixed judge, or use official graders |

The core suite (20 BrowseComp and 12 DRB-II cases) costs about $65 per lineup at B's p01 rate.
Start with A and B on a few cases each.

## Tool-loop context

Scouts and deep dives resend their whole history each turn. `scripts/loop_costs.py` replays one
loop request by request on the p01 profile and prices changes to that history. It simulates the
provider cache instead of assuming a share: a request reads from cache the prefix it shares with
the previous request. `value`'s loop models, USD per question:

| Change to the loops | Caching works | No caching |
|---|---:|---:|
| Baseline, resend everything | 1.48 | 2.76 |
| Mask tool results older than the last 3 turns | 1.63 (+10%) | 2.18 (−21%) |
| The same, moving the cutoff only every 4 turns | 1.50 (+1%) | 2.31 (−16%) |
| 40% fewer turns (budget awareness, as BATS reported) | 1.20 (−19%) | 1.70 (−38%) |
| Two tool calls per turn | 1.34 (−10%) | 1.87 (−32%) |
| Repeated fetches return a stub (20% of result tokens) | 1.44 (−3%) | 2.58 (−6%) |
| Batched masking, two calls per turn, dedup, fewer turns | 1.15 (−22%) | 1.28 (−54%) |

- **Masking fights caching.** Masking a result changes the prompt from that point, so everything
  after it is billed fresh. When caching works, masking every turn costs more than it saves, even
  though it sends a third fewer tokens. It pays only where the loop is not cached.
- **Fewer turns save money under any caching.** Each turn saved removes one whole resend. The levers
  are telling the loop its remaining budget and letting it make several tool calls per turn. The
  loop gathering less in fewer turns is the risk to measure.
- **`keep_recent_tool_results` is per-turn masking.** The existing experimental option trims
  results as they age out of its window, so under working caching the model above expects it to
  cost more, not less. Leave it off for cached routes unless a run shows otherwise.
- **The savings are a model, not a result.** Per-turn sizes are solved from the profile's totals
  with an assumed 2,500-token starting prompt. Fetches are larger than searches in practice. Run
  `--hit-rate 0.8` for imperfect caching and `--lineup` for other models.

### Budget notes experiment

`ResearchConfig.budget_notes` tests the fewer-turns lever. The named loops end every model
request with a note like this one (`budget_notes.py`):

> [Research budget] 14 of 20 model requests and 31 of 40 tool calls left, this request included.
> Put independent searches and fetches in one turn as parallel tool calls. Return your result while
> a request is left: a run that reaches either limit is cut off, and its result is written from
> truncated tool output.

The note goes on the newest request only and later requests resend it unchanged, so caching still
works. PydanticAI already runs parallel tool calls concurrently, and each call counts against
`max_tool_calls`. Compare one run with and one without, on the same policy and cases:

```
research-bench examples/benchmark_suite.toml --policies value --paid --max-cases 10
research-bench examples/benchmark_suite.toml --policies value --paid --max-cases 10 --budget-notes deep_dive
```

Watch the deep dives' requests per task, tool calls per request, and salvage count, then cost per
correct answer on BrowseComp and the rubric score on DRB-II. Notes change what the model sees, so
the two runs have different config fingerprints, and each deep-dive task's `effective_config`
records `budget_notes`. Try `--budget-notes scout deep_dive` after the deep dive holds up.

## Caveats

- **The cost model checks tokens, not prices.** The p01 costs in PROMPT_SIZES.md are PydanticAI
  estimates from `genai-prices`, not provider invoices. The model's $3.20 for A against the
  recorded $2.94 confirms the token profile and the cache shares, which come from provider-reported
  usage. It does not confirm list prices. Check invoices after the first paid run.
- **One token profile.** p01 is a scholarly literature question; BrowseComp and GAIA cases use
  different tools and loop lengths.
- **Tokenizers and thinking.** Anthropic input is scaled by 1.52, and every other vendor is treated
  like OpenAI. The different amounts of thinking output each model writes are not modeled.
- **Guessed cache shares.** Cache shares for vendors the pilot did not use are guesses (50%).
- **Prices that change.** GPT-5.6 Sol's price is promotional. Gemini 3.8 Flash doubles on
  2027-01-01, and DeepSeek has peak-hour rates. Rerun the script with `--date`.
- **A mislabel in PROMPT_SIZES.md.** It calls the pilot's deep-dive model GPT-6 Astra at $20 per
  million input tokens. The recorded cost matches GPT-5.6 Sol at $4 / $20, the default deep-dive
  route.

## Sources

- Prices: [`genai-prices`](https://pypi.org/project/genai-prices/) 0.1.8, including its OpenRouter
  catalog; [GPT-5.6 Sol model page (OpenAI)](https://developers.openai.com/api/docs/models/gpt-5.6-sol);
  [VentureBeat on GPT-6 Sol and Luna](https://venturebeat.com/technology/openai-releases-gpt-6-sol-and-luna-models-slashing-api-costs-50-or-more);
  [Hy4 preview on OpenRouter](https://openrouter.ai/tencent/hy4-preview)
- OpenRouter usage: [tool-calling collection](https://openrouter.ai/collections/tool-calling-models),
  [rankings](https://openrouter.ai/rankings)
- [Introducing GPT-6 Sol and Luna (OpenAI)](https://openai.com/index/introducing-gpt-6-sol-and-luna/),
  [GPT-6 Sol vs GPT-5.6 Sol](https://www.orcarouter.ai/blog/gpt-6-sol-vs-gpt-5-6-sol)
- [Claude Opus 5.5 benchmarks (Vellum)](https://www.vellum.ai/blog/claude-opus-5-5-benchmarks-explained),
  [Opus 5.5 on Artificial Analysis](https://artificialanalysis.ai/models/releases/claude-opus-5-5),
  [Migrating to Claude Opus 5.5](https://platform.claude.com/docs/en/models/opus-5-5/migration-guide)
- [BrowseComp leaderboard (BenchLM)](https://benchlm.ai/benchmarks/browsecomp),
  [GLM-5.3 on Artificial Analysis](https://artificialanalysis.ai/models/glm-5-3),
  [GLM-5.3-Flash benchmarks (DataCamp)](https://www.datacamp.com/blog/glm-5-3-flash),
  [Kimi K3 benchmarks](https://codersera.com/blog/kimi-k3-benchmarks-comparison-2026/),
  [Tencent Hy4 preview benchmarks](https://www.developersdigest.tech/blog/tencent-hy4-preview-770b-open-moe-2026),
  [DeepSeek V4.1 Flash review](https://blog.buildfastwithai.com/deepseek-v4-1-flash-review)
- [DeepResearch Bench II](https://github.com/imlrz/DeepResearch-Bench-II)

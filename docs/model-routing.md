# Model routing: cost and quality per role

Status as of 2026-09-25. This is desk research: prices, vendor and third-party benchmarks, and the
p01 calibration runs, with no new paid run. It picks lineups to benchmark; per
[benchmarks.md](benchmarks.md), the routing decision itself should come from those models running
through this harness. `scripts/route_costs.py` reproduces every dollar figure here.

## Recommendation

| Role | Now | Recommended | Thinking | Why |
|---|---|---|---|---|
| Planner | Opus 5, `high` | `anthropic:claude-opus-5-5` | `medium` | about $0.03 a question on any model, so use the best one. Opus 5.5 is cheaper than Opus 5 and ahead on every benchmark Anthropic published for both |
| Scout | GLM-5.3, `high` | keep `zai:glm-5.3`; trial `openai:gpt-6-luna` | `high` | GLM's cached input keeps three scouts near $0.56. BrowseComp 75.9% against about 90% for frontier models. That gap is what the deep dives exist to close. Luna would cost about $0.05–0.09 but has no published search score |
| Gap analyst | GPT-5.6 Sol, `medium` | `openai:gpt-6-sol` | `medium` | same price tier, half the rate: $2 / $10 against $4 / $20 |
| Deep dive | GPT-5.6 Sol, `high` | `openai:gpt-6-sol` | `high` | the largest cost, about half of each question. GPT-6 Sol halves it and is scored slightly above GPT-5.6 Sol |
| Synthesizer | Opus 5, `high` | `anthropic:claude-opus-5-5` | `medium` | writes the report that DRB-II grades. Sonnet 5 saves another $0.19 a question and is worth an A/B test |
| Verifier | GPT-5.6 Sol, `high` | `openai:gpt-6-sol` | `high` | keep it from a different vendor than the synthesizer, so the report is not checked by the model that wrote it |

Expected effect on the p01 profile: **$3.20 → $2.03 a question (−37%)** from the model swaps alone,
and **→ $1.61 (−50%)** once tool-loop prompt caching is configured, with no model moving to a
weaker tier. The swaps are configuration only. The thinking changes and caching need a
`policy.py` edit; see [What to change](#what-to-change).

## Where the money goes

Scouts and deep dives resend their whole history on every request, so their input tokens grow
with the square of the number of turns ([PROMPT_SIZES.md](../long_horizon/agentic_se/PROMPT_SIZES.md#problem-3-agent-loops-grow-with-every-turn)).
Their cost is set by the **price of input tokens, and of cached input tokens**, more than by
output prices. Finishing calls are single requests, and their output is a larger share.

The model below prices the p01 token profile: 3 scouts (one salvaged), gap analysis, 2 deep dives
(both salvaged), synthesis, and verification. Prices are list prices from `genai-prices` 0.1.8 on
2026-09-25. On the current defaults it predicts **$3.20** a question. p01 was billed $2.66 with one
deep dive and $2.94 with two, so the model runs about 10% high.

USD per question, loop caching as the pilot's bills imply it happened:

| Lineup | Planner | Scout | Gap | Deep dive | Synthesizer | Verifier | Total |
|---|---:|---:|---:|---:|---:|---:|---:|
| A current defaults | 0.04 | 0.56 | 0.12 | **1.70** | 0.47 | 0.31 | **3.20** |
| B successors (recommended) | 0.03 | 0.56 | 0.06 | 0.85 | 0.38 | 0.16 | **2.03** |
| C B, Sonnet 5 synthesizer | 0.03 | 0.56 | 0.06 | 0.85 | 0.19 | 0.16 | 1.85 |
| D budget: DeepSeek scouts (needs the provider added), Luna gap, Sonnet 5 planner and synthesizer | 0.02 | 0.07 | 0.00 | 0.85 | 0.19 | 0.16 | 1.28 |
| E floor: GPT-6 Luna everywhere but a Sonnet 5 synthesizer | 0.00 | 0.09 | 0.00 | 0.04 | 0.19 | 0.01 | 0.34 |
| F Opus 5.5 everywhere | 0.03 | 5.46 | 0.17 | 2.53 | 0.38 | 0.38 | 8.95 |

With caching configured on every tool loop (80% of loop input read from cache), the totals are
A $2.32, B $1.61, C $1.42, D $0.83, E $0.27, and F $4.37.

What this shows:

- **The deep dive is the lever.** It is 53% of today's cost. The pilot's bill ($0.66 for 158,600
  input tokens on GPT-5.6 Sol) implies only about 5% of that input was billed as cached, against
  about 84% for the GLM scouts. GPT-6 Sol halves the rate, and caching halves it again.
- **Scouts are already cheap** because GLM-5.3 caches well. GPT-6 Luna or DeepSeek V4 Flash would
  cut them to under $0.10, but neither has a measured search score in this loop. DeepSeek is not
  a supported provider yet: `settings.py` and `diagnose.py` would need a `deepseek` entry.
- **Putting a premium model everywhere is the wrong trade.** F costs almost three times A,
  mostly on scouts. The pilot scouts stopped on request and tool-call counts, not on reasoning.
- **Finishing calls are cheap to make good.** Opus 5.5 synthesis costs $0.38. A synthesis or
  verification failure throws away research that cost several times that.

## Quality evidence

Public numbers only narrow the field. They were run with other scaffolds, tools, and effort
settings, and sources disagree with each other (two reports give GLM-5.3 an Artificial Analysis
index of 45 and 60, under different index versions).

| Model | $ in / cached / out per M | Evidence relevant to this loop |
|---|---|---|
| Claude Opus 5.5 (released 2026-09-22) | 4 / 0.20 / 20 | Tops the Artificial Analysis index at max effort (58). BrowseComp about 90–91%. Cheaper than Opus 5 and ahead on every benchmark Anthropic published for both |
| Claude Sonnet 5 | 2 / 0.20 / 10 | Below Opus 5.5 overall. Anthropic reports lower hallucination and sycophancy than Sonnet 4.6. No DRB-II number found |
| GPT-6 Sol (2026-09-22) | 2 / 0.20 / 10 | About one index point above GPT-5.6 Sol at half the price. OpenAI puts it at 90–95% of GPT-6 Astra's practical capability for 20% of the cost. No BrowseComp number published; GPT-5.6 Sol has 92.2% |
| GPT-5.6 Sol (current default) | 4 / 0.40 / 20 | BrowseComp 92.2%, second on that leaderboard |
| GPT-6 Luna | 0.10 / 0.01 / 0.50 | Matches earlier flagships on OpenAI's agent benchmarks (DeepSWE 66.6%, Agents' Last Exam 50.9%). No search benchmark published |
| GLM-5.3 (current scout) | 1.40 / 0.26 / 4.40 | BrowseComp 75.9% |
| DeepSeek V4 Flash (V4.1) | 0.22 / 0.007 / 0.66 | Strong coding and agent numbers (Terminal-Bench 2.1 90.6). No BrowseComp number found. Peak-hour prices are about double |
| Kimi K3 | 3 / 0.30 / 15 | BrowseComp 91.2%, but priced like a frontier model |

BrowseComp is close to saturated at the top: the leaders are within 1 point of each other. It
still separates GLM-5.3 (75.9%) from the frontier. That is the one quality gap in the
recommended lineup worth measuring, not assuming.

## Compatibility checked

The pinned `pydantic-ai-slim` 2.48.0 already has profiles for every recommended model:

- **Opus 5.5** rejects a forced `tool_choice`. When a `thinking` setting is present, PydanticAI
  switches its structured output to native JSON-schema output, which Opus 5.5 supports. Every
  route sets `thinking`, so no code change is needed. Opus 5.5 cannot turn thinking off, so
  never give an Opus 5.5 route `thinking=False`.
- **Opus 5.5 effort levels are not Opus 5's.** Its default is `medium`, and at a given level it
  thinks more than Opus 5 did. Keeping `high` on the planner and synthesizer would spend more
  output tokens than the table assumes. Use `medium`, then raise it only if DRB-II scores ask for it.
- **GPT-6 Sol and Luna** are in the OpenAI profile, with reasoning effort and prompt-cache
  breakpoints. Like GPT-5.6, they do not accept `minimal`; no route uses it.
- `research-diagnose --smoke` should still pass on each new ID before a paid run. GPT-6 Sol and
  Opus 5.5 are three days old.

## What to change

1. **Model IDs (configuration only).** In `.env`:

   ```
   RESEARCH_PLANNER_MODEL=anthropic:claude-opus-5-5
   RESEARCH_SYNTH_MODEL=anthropic:claude-opus-5-5
   RESEARCH_GAP_MODEL=openai:gpt-6-sol
   RESEARCH_DEEP_MODEL=openai:gpt-6-sol
   RESEARCH_VERIFY_MODEL=openai:gpt-6-sol
   RESEARCH_CHEAP_SCOUT_MODEL=openai:gpt-6-luna
   ```

   `RESEARCH_CHEAP_SCOUT_MODEL` also moves from GPT-5.6 Luna ($0.20 / $1.20) to GPT-6 Luna
   ($0.10 / $0.50). `RESEARCH_ALT_DEEP_MODEL` (`xai:grok-4.5`) returned HTTP 403 in the v4 rerun.
   Point it at a provider you have, or leave verification rounds at 0 as the study does.
2. **Thinking effort (code).** Environment overrides replace only the model, so the Opus 5.5
   `medium` effort needs `policy.py` edits to the planner and synthesizer routes.
3. **Prompt caching (code).** Add `{"anthropic_cache": True}` to Anthropic routes and a
   per-route `openai_prompt_cache_key` to OpenAI routes, then compare
   `usage.cache_read_tokens` for deep dives before and after. Caching changes what is billed,
   not what the model sees, so it does not change `prompts_sha256`. It does change the policy
   snapshot and the config fingerprint.

## How to confirm it

Compare A against B, then B against each single-role change (C for the synthesizer, GPT-6 Luna
scouts), with the core suite and graph version held fixed. Each lineup is a separate
`research-bench` run with its own `.env`. The manifest's policy snapshot records the routes.

| Role changed | Lane | Watch |
|---|---|---|
| Scout | BrowseComp | exact-answer match, then the official grader; cost per correct answer; unique-search rate |
| Deep dive | BrowseComp, DRB-II | primary-source rate, observed-source rate, salvage count |
| Synthesizer | DRB-II through the official evaluator (`--export-reports`) | rubric score, supported-claim rate |
| Verifier | DRB-II | **hold the grader fixed**: the supported-claim rates are judged by the run's own verifier, so changing the verifier changes the ruler. Rescore reports with one fixed judge, or use official graders |

The core suite (20 BrowseComp and 12 DRB-II cases) costs about $65 per lineup at B's p01 rate,
so five lineups cost about $325 before retries. Start with A and B on a few cases each.

## Caveats

- One token profile. p01 is a scholarly literature question; BrowseComp and GAIA cases use
  different tools and loop lengths.
- Tokenizers differ. The model scales Anthropic input by 1.52 and treats other vendors like
  OpenAI. It does not model the different amounts of thinking output each model writes.
- Cache shares for providers the pilot did not use are guesses. DeepSeek peak-hour prices and
  Gemini 3.8 Flash's price doubling on 2027-01-01 are in `genai-prices`; run the script with
  `--date` to see them.
- [PROMPT_SIZES.md](../long_horizon/agentic_se/PROMPT_SIZES.md) calls the pilot's deep-dive
  model GPT-6 Astra at $20 per million input tokens, and gives GPT-5.6 Sol's price as $2 / $10.
  The pilot bills match GPT-5.6 Sol at $4 / $20, the default deep-dive route. The model here uses
  that price.

## Sources

- Prices: [`genai-prices`](https://pypi.org/project/genai-prices/) 0.1.8, cross-checked with
  [VentureBeat on GPT-6 Sol and Luna](https://venturebeat.com/technology/openai-releases-gpt-6-sol-and-luna-models-slashing-api-costs-50-or-more)
  and the [DeepSeek V4.1 Flash review](https://blog.buildfastwithai.com/deepseek-v4-1-flash-review)
- [Introducing GPT-6 Sol and Luna (OpenAI)](https://openai.com/index/introducing-gpt-6-sol-and-luna/),
  [GPT-6 Sol vs GPT-5.6 Sol](https://www.orcarouter.ai/blog/gpt-6-sol-vs-gpt-5-6-sol)
- [Claude Opus 5.5 benchmarks (Vellum)](https://www.vellum.ai/blog/claude-opus-5-5-benchmarks-explained),
  [Opus 5.5 on Artificial Analysis](https://artificialanalysis.ai/models/releases/claude-opus-5-5),
  [Migrating to Claude Opus 5.5](https://platform.claude.com/docs/en/models/opus-5-5/migration-guide)
- [BrowseComp leaderboard (BenchLM)](https://benchlm.ai/benchmarks/browsecomp),
  [GLM-5.3 on Artificial Analysis](https://artificialanalysis.ai/models/glm-5-3),
  [Kimi K3 benchmarks](https://codersera.com/blog/kimi-k3-benchmarks-comparison-2026/)
- [DeepResearch Bench II](https://github.com/imlrz/DeepResearch-Bench-II)

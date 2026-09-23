# Long-horizon agentic software engineering campaign

`campaign.toml` fixes the research scope, publication window, source tiers, eleven question IDs, per-question budgets, and required outputs. It is a campaign specification, not a completed literature review, and holds no unverified findings. `research-campaign` runs each question through `research-graph-v1`, then synthesizes the completed questions into one campaign report.

## Workflow

```bash
research-campaign --dry-run                        # validate the spec; no calls
research-campaign --question q01 --paid --persist  # one question
research-campaign --all-questions --paid --persist # every question, one after another
research-campaign --aggregate                      # merge completed evidence; no calls
research-campaign --synthesize --dry-run           # check synthesis inputs and prompt size; no calls
research-campaign --synthesize --paid --persist    # write the campaign report, catalogs, and hypotheses
```

The spec is validated in full when it loads (`campaign_spec.py`): a missing or mistyped setting, an unknown key, or inconsistent limits fail `--dry-run` rather than a paid run. `--spec PATH` selects another spec (default: this campaign), `--output DIR` another output folder (default `benchmark_outputs/long_horizon_campaign`, which git ignores), and `--policy` another model policy. `--persist` stores jobs in Postgres; see [setup](../../docs/setup.md#postgres).

Before paid runs, confirm routes and balances with `research-diagnose --smoke`, set provider spending caps, and review the scope. Every paid invocation repeats that preflight, local checks first and then small live calls to each model; a failed or unpriced route stops it before any research call.

## Running questions

Each question's objective is rendered from the campaign title, the question text, the publication window, and the source policy. The run uses normalized acquisition with the cache in `record` mode, so results never depend on earlier cached calls but later runs can replay them. The spec's research notes reach every role as constraints.

A failed question is recorded and the run moves on. After `execution.max_failed_questions` failures (2 by default) it stops and lists the questions it did not run, because repeated failures usually share a cause such as credentials or a provider outage. The CLI exits non-zero unless every question completed, and names each failed question with its error type. Gap analysis, synthesis, and verification are refused before the model call when one validation retry would not fit the role's token limit (`PromptExceedsRetryBudget`); the question's ledger up to that point is kept. Interrupting the run (Ctrl-C) stops it, and the manifest records `failed` with `CancelledError`, as does the question's job in Postgres.

A completed question writes `outputs.question_files` to `<output>/<question id>/`:

| File | Contents |
|---|---|
| `report.md` | The answer, caveats, the verifier's unsupported or major findings, and how many quoted passages and cited sources were not found in tool output |
| `report.json`, `verification.json` | The final report and the verifier's checks |
| `evidence_ledger.json` | Every research result, with unique claim IDs such as `q1/c3`; the report and verification cite only these IDs |
| `bibliography.json` | Distinct sources; preprint and publication records stay separate |
| `run.json` | Written last: job ID, cost, config fingerprint, a hash of the rendered objective, `review_reasons`, and the SHA-256 of every other file. Its presence marks the question completed |

The files are written to a hidden staging folder that then replaces the question's folder in one step, so a rerun that fails or is interrupted leaves the previous outputs whole.

Each invocation also writes its own manifest under `<output>/manifests/`, with the redacted policy, run limits and full run configuration, the prompt fingerprint, acquisition, evidence version, git state including a hash of uncommitted changes, and each question's outcome and cost.

Completed is not the same as sound. Each completed question records `review_reasons`: what it left unresolved, such as unsupported or major verifier findings or a verifier still asking for research. The CLI lists completed questions that need review, and campaign synthesis records each input's reasons in its manifest. The pilot's p01, for example, completed with 13 of 42 verifier checks unsupported, 2 rated major, and the verifier still asking for research.

## Budgets

`[execution]` turns into policy and run settings for every question:

| Setting | Effect |
|---|---|
| `question_cost_limit_usd` | `ModelPolicy.job_cost_limit`, a soft cap per question |
| `question_reserve_usd` | Part of that cap the planner, scouts, and deep dives must leave for gap analysis, synthesis, verification, and salvage |
| `deep_dive_cost_limit_usd` | Lowers the deep-dive route's per-call cap |
| `scout_max_requests`, `scout_max_tool_calls`, `scout_total_tokens_limit` | Replace the limits on every scout route |
| `planner_question_min`, `planner_question_max` | The planner's question range |
| `max_parallel_scouts`, `max_parallel_deep_dives`, `max_deep_dives_per_round`, `max_verification_rounds` | `ResearchConfig` concurrency and round limits |
| `question_timeout_seconds` | `ResearchConfig.max_run_seconds`: a question still running after an hour is recorded failed (`TimeoutError`) |

Campaigns also turn on salvage, so a scout or deep dive that exhausts its budget summarizes what it gathered instead of failing the question. The [README](../../README.md#budgets) explains the soft cap, the reserve, and salvage. Provider spending caps remain the hard limit.

The current spec gives each question a $5 cap with a $2.50 reserve (about $4 expected per question), scouts of 16 requests, 36 tool calls, and 500k tokens, and up to two deep dives per question at $1.25 each, run one at a time so each budget check sees the previous one's spend. It plans one to three subquestions and skips verification rounds in this first pass.

## Calibration pilot

`pilot.toml` matches `campaign.toml` except for its identity fields and one known-answer question, `p01`, about SWE-bench, SWE-bench Lite, and SWE-bench Verified; a test keeps the two files in sync. The pilot checks evidence quality against verifiable facts and exercises the source policy on a 2023 preprint later published at ICLR 2024, at the edge of the window, and on a vendor-published benchmark variant.

```bash
research-campaign --spec campaigns/long_horizon_agentic_se/pilot.toml --question p01 --paid --persist \
  --output benchmark_outputs/long_horizon_pilot
```

Per-task usage in Postgres then shows what each step cost, which is how the campaign budgets were set. [PROMPT_SIZES.md](PROMPT_SIZES.md) records the pilot's measurements.

## Campaign synthesis

`--aggregate` merges the completed questions' ledgers and bibliographies into `<output>/campaign/`. Each claim gets a campaign ref, the question ID plus the ledger claim ID, such as `q01/q1/c3`; a repeated ID in an older ledger gets a `~2` suffix, and a citation of that id includes every copy.

A completed question counts only if its objective hash still matches the spec and its files still match the hashes in its `run.json`; a folder edited after publication is reported and left out. Editing a question, the window, or the source policy therefore requires rerunning the affected questions, while budget-only edits keep earlier outputs usable. A run recorded without an objective hash never counts. Question IDs cannot be `.`, `..`, `campaign`, or `manifests`, which would collide with output folders.

`--synthesize --paid` runs the same preflight, then one campaign-level synthesizer job under the `[synthesis]` limits, and writes `outputs.campaign_files`:

- Every finding, catalog entry, and hypothesis must cite existing claim refs; an invented ref gets one retry. The route allows two requests, the initial call and that retry.
- The prompt carries one source table, listing each cited work once with its title, url, and date, and each question's report claims (statement, claim refs, the lowest confidence among the cited claims, supporting and contradicting work ids and counts, source type, and publication status), caveats, unresolved questions, contradictions, and verifier findings. `source_count` counts distinct works whose evidence supports the claim. One work counts once when it is cited at two locators, fetched through two providers, or named by both its DOI and a doi.org link. Evidence that does not support the claim is counted separately in `contradicting_source_count`, and the types and statuses cover supporting sources only. A claim whose quote or source was not found in tool output, or that cites a retracted source, is flagged on the whole claim. The synthesizer is told not to present that claim, a claim with contradicting sources, or an unsupported or major finding as well supported. `source_count` is what makes a finding well supported, not the number of distinct source-type strings. Excerpts stay in the aggregated ledger.
- A completed question whose report cites no ledger claim gives synthesis none of its evidence. It is listed in the manifest's `uncited_question_ids`, in the report's scope line, and by `--aggregate` and `--synthesize --dry-run`.
- Synthesis requires every question to be complete unless `--allow-partial` is passed; a partial synthesis is labeled in the report and manifest.
- `--synthesize --dry-run` reports the prompt size against `synthesis.max_prompt_chars` and the tokens one citation retry would need against `synthesis.total_tokens_limit`, and `--synthesize` refuses a prompt whose retry would not fit before any call. The retry is counted at the synthesizer's output cap (`max_output_tokens`, 36,000), not the smaller per-question synthesizer allowance. Loading the spec does not run this check, so question runs and `--aggregate` are unaffected by the synthesis limits. Eleven questions at the p01 rerun's depth are 463,000 to 510,000 characters of the 600,000 cap, and a prompt at that cap fits one retry inside 600,000 tokens; see [PROMPT_SIZES.md](PROMPT_SIZES.md#headroom).

The campaign synthesizer is a PydanticAI agent outside `research-graph-v1`. It reuses the synthesizer route and adds no graph node or `ResearchRole`.

## Metadata pilot

`scripts/scholar_pilot.py` probes the public scholarly endpoints for this campaign's topic with no model provider:

```bash
.venv/bin/python scripts/scholar_pilot.py
```

It writes work IDs, titles, publication statuses, and endpoint errors, without abstracts or article text, to `benchmark_outputs/scholar_pilot.json`. An unreachable endpoint is recorded as an error, not as a finding.

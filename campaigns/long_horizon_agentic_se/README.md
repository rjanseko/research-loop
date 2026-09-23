# Long-horizon agentic software engineering campaign

`campaign.toml` fixes the research scope, source tiers, question IDs, and required outputs. It is a campaign specification, not a completed literature review. Use `research-campaign --dry-run` to validate the spec without model calls. When the model accounts have credit and provider caps are set, run `research-campaign --question q01 --paid --persist` for one bounded question. The CLI performs a live model preflight before research calls; this preflight itself makes small paid calls. `--all-questions` is an explicit sequential campaign run and can incur substantial provider costs. The campaign spec guides the planner to one to three subquestions, caps deep-dive research and disables verification retry rounds for the first pass; provider spending caps remain the hard account-level guardrail. Each question exports the `outputs.question_files` to `benchmark_outputs/long_horizon_campaign/<question id>/`, which is gitignored. `run.json` is written last and marks the question as completed. Each invocation writes its own manifest under `manifests/`. Preserve preprint and publication versions as separate source records.

## Calibration pilot

`pilot.toml` matches `campaign.toml` except for its identity fields and one known-answer question about SWE-bench, SWE-bench Lite, and SWE-bench Verified. A test keeps the two files in sync. The pilot checks evidence quality against facts you can verify, and it tests the source policy on:

- a 2023 preprint later published at ICLR 2024, on the edge of the publication window;
- a vendor-published benchmark variant.

Run it separately from the campaign:

```
research-campaign --spec campaigns/long_horizon_agentic_se/pilot.toml --question p01 --paid --persist --output benchmark_outputs/long_horizon_pilot
```

The per-task usage in Postgres then shows what each step cost, which is how to set budgets for the campaign questions. Each question runs under the `[execution]` limits:

- a $5 question cap, with a $2 reserve for gap analysis, synthesis, verification, and salvage;
- a 400k-token scout limit;
- research notes that guide the scholarly search and ask scouts to finish before their budget runs out.

## Campaign synthesis

Catalogs and hypotheses come from campaign synthesis over completed questions:

- `research-campaign --aggregate` makes no model calls. It merges the completed questions' ledgers and bibliographies into `campaign/`. Each claim gets a campaign ref such as `q01/c3`; a claim ID repeated within a question gets a suffix such as `q01/c3~2`.
- `research-campaign --synthesize --dry-run` makes no calls. It reports which questions are complete and the prompt size against `synthesis.max_prompt_chars`.
- `research-campaign --synthesize --paid [--persist]` runs the same preflight as question runs. It then makes one campaign-level synthesizer job under the `[synthesis]` limits and writes the `outputs.campaign_files`. Every finding, catalog entry, and hypothesis must cite existing claim refs; an invented ref triggers a bounded retry. Synthesis requires every question to be complete unless `--allow-partial` is passed. Partial runs are labeled in the report and manifest.

The campaign synthesizer is a campaign-level PydanticAI agent outside `research-graph-v1`. It reuses the synthesizer route and adds no graph node or `ResearchRole`.

The metadata pilot uses only public scholarly endpoints and no model provider. Run `python scripts/scholar_pilot.py` in the project environment. It writes a small, source-ID-only record to `benchmark_outputs/scholar_pilot.json`. A missing endpoint is recorded as an error and does not create a false finding.

Before a paid model run, verify model IDs and account balances with `research-diagnose --live`, set provider spending caps, and review the intended scope. The default benchmark cache mode is `off` so its results cannot depend on earlier tool calls.

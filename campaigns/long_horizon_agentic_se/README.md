# Long-horizon agentic software engineering campaign

`campaign.toml` fixes the research scope, source tiers, question IDs, and required outputs. It is a campaign specification, not a completed literature review. Use `research-campaign --dry-run` to validate the spec without model calls. When the model accounts have credit and provider caps are set, run `research-campaign --question q01 --paid --persist` for one bounded question. The CLI performs a live model preflight before research calls; this preflight itself makes small paid calls. `--all-questions` is an explicit sequential campaign run and can incur substantial provider costs. A failed question is recorded and the run moves on to the next one; after `execution.max_failed_questions` failures (2 by default) it stops and lists the questions it did not run, because repeated failures usually share a cause. The CLI exits non-zero unless every question completed and names each failed question with its error type. The campaign spec guides the planner to one to three subquestions, caps deep-dive research and disables verification retry rounds for the first pass; provider spending caps remain the hard account-level guardrail. Each question exports the `outputs.question_files` to `benchmark_outputs/long_horizon_campaign/<question id>/`, which is gitignored. `run.json` is written last and marks the question as completed. It records a hash of the rendered question objective (title, question text, publication window, and source policy). Each invocation writes its own manifest under `manifests/`. Preserve preprint and publication versions as separate source records.

## Calibration pilot

`pilot.toml` matches `campaign.toml` except for its identity fields and one known-answer question about SWE-bench, SWE-bench Lite, and SWE-bench Verified. A test keeps the two files in sync. The pilot checks evidence quality against facts you can verify, and it tests the source policy on:

- a 2023 preprint later published at ICLR 2024, on the edge of the publication window;
- a vendor-published benchmark variant.

Run it separately from the campaign:

```
research-campaign --spec campaigns/long_horizon_agentic_se/pilot.toml --question p01 --paid --persist --output benchmark_outputs/long_horizon_pilot
```

The per-task usage in Postgres then shows what each step cost, which is how to set budgets for the campaign questions. Each question runs under the `[execution]` limits:

- a $5 question cap, with a $2.50 reserve for gap analysis, synthesis, verification, and salvage (about $4 expected per question);
- scouts limited to 16 requests, 36 tool calls, and 500k tokens;
- up to 2 deep dives per question, capped at $1.25 each and run one at a time, so each budget check sees the previous one's spend;
- research notes that guide the scholarly search and ask scouts to finish before their budget runs out.

## Campaign synthesis

Catalogs and hypotheses come from campaign synthesis over completed questions:

- `research-campaign --aggregate` makes no model calls. It merges the completed questions' ledgers and bibliographies into `campaign/`. A completed question whose objective hash no longer matches the spec is left out and reported, so editing a question, the window, or the source policy requires rerunning the affected questions. Budget-only spec edits keep earlier outputs usable. Runs recorded before objective hashes are accepted only while the spec file is byte-for-byte unchanged. Question IDs cannot be `.`, `..`, `campaign`, or `manifests`. Each claim gets a campaign ref: the campaign question plus the ledger claim ID, such as `q01/q1/c3`. The run ledger already makes claim IDs unique (`q1/c3`, `q1/c3~2`), and a repeated ID in an older ledger gets a `~2` suffix.
- `research-campaign --synthesize --dry-run` makes no calls. It reports which questions are complete and the prompt size against `synthesis.max_prompt_chars`.
- `research-campaign --synthesize --paid [--persist]` runs the same preflight as question runs. It then makes one campaign-level synthesizer job under the `[synthesis]` limits and writes the `outputs.campaign_files`. Every finding, catalog entry, and hypothesis must cite existing claim refs; an invented ref triggers a bounded retry. The synthesis prompt carries each question's verifier findings (unsupported or major checks) and each evidence item's `quote_check`, and the synthesizer is told not to present those statements, or findings resting on quotes not found in tool output, as well supported. Each question's `report.md` also states how many quoted passages were not found in the text its research tools returned, and in which claims. Synthesis requires every question to be complete unless `--allow-partial` is passed. Partial runs are labeled in the report and manifest.

The campaign synthesizer is a campaign-level PydanticAI agent outside `research-graph-v1`. It reuses the synthesizer route and adds no graph node or `ResearchRole`.

The metadata pilot uses only public scholarly endpoints and no model provider. Run `python scripts/scholar_pilot.py` in the project environment. It writes a small, source-ID-only record to `benchmark_outputs/scholar_pilot.json`. A missing endpoint is recorded as an error and does not create a false finding.

Before a paid model run, verify model IDs and account balances with `research-diagnose --live`, set provider spending caps, and review the intended scope. The default benchmark cache mode is `off` so its results cannot depend on earlier tool calls.

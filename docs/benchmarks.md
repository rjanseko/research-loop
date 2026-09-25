# Benchmarks

The benchmark layer separates three things: model quality, research-system quality, and retrieval-environment quality. Public leaderboards help pick candidates; routing decisions should come from the same models running through this harness.

## Lanes

### BrowseComp: scouting and hard retrieval

- 1,266 hard web-research questions with short, objectively checkable answers.
- Upstream encrypts questions and answers with a per-row canary to slow contamination. The adapter caches only the encrypted CSV and decrypts rows in memory.
- The local `ReferenceAnswerMatch` score is a fast smoke metric, not a replacement for the official semantic grader.
- `EvalIntegrity` flags benchmark-aware searches, for example searching for BrowseComp itself instead of the question's domain.

Use it to measure search strategy, persistence, source discovery, cost per correct answer, and breadth-versus-depth scout policies.

### DeepResearch Bench II: synthesis, evidence, and reports

- 132 expert-grounded long-form tasks with 9,430 fine-grained rubrics across information recall, analysis, and presentation.
- Tasks may block source URLs derived from the expert report behind the rubric. The adapter keeps the rubrics and blocked URLs, and the blocked URLs become hard run constraints for every role.
- The fetch tools refuse blocked sources, including redirects to them, and evidence citing one gets a retry; see [acquisition.md](acquisition.md#blocked-sources). The audit separates refused fetches, completed fetches, sightings in search results, and citations.
- `--export-reports DIR` writes `idx-<n>.md` files for the official DRB-II evaluator. Other benchmarks get safe single-component file names derived from benchmark and case IDs.

Use it to measure evidence coverage, synthesis, structure, citation behavior, and the value of a premium synthesizer.

### GAIA: orchestration and mixed tools

GAIA is gated upstream and may include local attachments. The adapter reads an authorized local snapshot (JSONL or Parquet) and never redistributes it. Attachment cases run in the normalized or the multimodal [attachment lane](attachments.md); keep the two apart when comparing policies, and set `include_attachments = false` for a text-only GAIA lane.

### Original USTC DeepResearch Bench

The adapter reads the local `query.jsonl` from the official repository, for reproducing the original RACE/FACT workflow. DRB-II is the stronger default report lane because its rubrics are more atomic and diagnostic.

### FutureSearch Deep Research Bench (RetroSearch)

RetroSearch freezes page content behind a search-like interface, which makes FutureSearch DRB especially valuable. FutureSearch asks prospective runners to request access, so the adapter reads an authorized local task export; it neither redistributes tasks nor simulates RetroSearch. With access, add a RetroSearch-backed `search`/`fetch` capability as a separate tool mode, and never compare a RetroSearch run with a DuckDuckGo run as though only the model changed.

### Generic JSONL

`kind = "jsonl"` reads any local file with `objective` (or `prompt`, `question`, `problem`, `task`) and optional `answer`, `output_mode`, `attachments`, `blocked_urls`, `rubrics`, and `metadata` fields. `research-bench` also accepts a plain JSON list of objectives or `{name, objective}` objects, such as `examples/benchmark_cases.json`.

## Suites

A suite is a TOML file of sources:

```toml
name = "research-loop-public-core"

[[sources]]
name = "browsecomp"
kind = "browsecomp"
limit = 20
seed = 17

[[sources]]
name = "drb2-en"
kind = "deepresearch_bench_2"
limit = 12
seed = 17
languages = ["en"]
```

Sampling is deterministic by `seed`. Adapters normalize every case into `BenchmarkCaseSpec`. Public datasets download into `RESEARCH_BENCHMARK_CACHE` through a temporary file, so an interrupted download leaves no partial cache entry. `examples/benchmark_suite.toml` is the core suite; `examples/benchmark_suite_full.example.toml` adds commented GAIA, original DRB, and FutureSearch sources.

## Running

```bash
research-bench examples/benchmark_suite.toml \
  --policies quality breadth glm-heavy \
  --paid --all-cases \
  --max-concurrency 1 \
  --export-reports benchmark_outputs
```

- The default policy is `synthetic`, which makes no provider or web calls. Real policies require `--paid`, and then run one case unless `--max-cases N` or `--all-cases` is given. Run `synthetic` on its own, not alongside real policies.
- These flags limit the number of cases, not spend. Each case fans out several model calls, and provider billing limits remain the hard backstop.
- Start with `--max-concurrency 1` (or `RESEARCH_BENCHMARK_CONCURRENCY`); each case is already parallel inside.
- Runs are in memory by default. `--repository postgres` (or `--persist`) stores jobs, tasks, tool events, and effective configuration; run `research-db migrate` first.
- `--attachment-mode multimodal` switches to the multimodal attachment lane.
- `--budget-notes deep_dive` (or `scout deep_dive`) is experimental: those tool loops end each model request with a note of the requests and tool calls they have left, and invite parallel tool calls. See [model-routing.md](model-routing.md#budget-notes-experiment).
- [Logfire tracing](setup.md#logfire-tracing) can show timing, retries, and token usage without prompt or tool content.

## Manifests and outcomes

Every run writes a sanitized experiment manifest to `RESEARCH_BENCHMARK_OUTPUT` (default `benchmark_outputs/`), or to `--manifest-output`. It records the git commit and dirty flag, with `tree_sha256` hashing any uncommitted changes and untracked files (ignored files such as `.env` are left out); package versions, extraction libraries included; redacted policy snapshots; the effective run configuration; `prompts_sha256`, a fingerprint of every agent's instructions and output schema; graph, evidence, and evaluator versions; acquisition backends and fetch version; the suite file's hash and each source's `dataset_sha256`; the selected case IDs; and each run's job and root-run IDs. It never contains raw prompts, answers, credentials, or local paths. With `--judge`, it also records the judges (see [Rubric judge](#rubric-judge)).

A failed case does not stop the suite. Its run record gets `status = "failed"` and the exception type only, because provider error messages can carry response bodies; for the same reason the console report omits errors. The manifest's `status` is `completed` when every case succeeded, `completed_with_failures` when some failed, and `failed` when all failed or the suite itself errored or was cancelled, in which case `error` holds the exception type; `failed_cases` counts failures across policies. Interrupting a run (Ctrl-C) marks its unfinished cases `failed` with `CancelledError`. A succeeded run also records `review_reasons`, what it left unresolved, which is empty for a clean result, and, for a case with blocked URLs, `blocked_sources`: how many blocked sources its tools refused, fetched anyway, showed in search results, and saw cited. The manifest records counts only, never the blocked URLs. The CLI exits non-zero unless the status is `completed`.

Scores outlive the process. Each succeeded run record holds `scores`, the value of every metric that applied to the case (a metric that did not apply has no entry, so it is never averaged in as a pass or a failure), and `measures`, the counts behind them: checked and unsupported claims, tool calls, research tool calls, tokens, cost (`null` when unpriced), quotes and sources and how many were not found, attachments, and integrity flags. It also records `duration_seconds` and, if an evaluator itself failed, `evaluator_failures`. `summary` holds one entry per policy: cases run, succeeded, failed, and needing review, the succeeded cases' total cost (`null` once any was unpriced), and each metric's mean with the number of cases it covers. None of it contains prompts, answers, search queries, or URLs, and benchmark runs never store [full transcripts](setup.md#full-transcripts).

## Comparability

Compare runs only when these match:

- **Graph version.** Keep `research-graph-v1` fixed while comparing policies.
- **Tool stack.** Benchmarks always use normalized acquisition: DuckDuckGo search, the shared `web_fetch`, and the scholarly tools, with web and scholarly caches off so no result depends on an earlier run. See [acquisition.md](acquisition.md).
- **Fetch version.** Version 2 pages through long documents and shares fetches within a case; version 3 also refuses blocked sources. It is part of the configuration fingerprint.
- **Evidence version.** Version 2 adds verbatim quotes checked against tool output; version 3 checks every role's output against the run (see [Metrics](#metrics)).
- **Tool history.** `ResearchConfig.keep_recent_tool_results` (experimental, off by default) changes what scouts and deep dives are sent: older tool results they have read go as a short note. It is part of `run_config`, so it changes the config fingerprint, and each research task's `effective_config` records it. Compare runs with it on only against runs with it on.
- **Budget notes.** `ResearchConfig.budget_notes` (experimental, off by default; `--budget-notes`) appends a note to every request of the named tool loops, so it changes what they are sent. It appears in `run_config`, and so in the config fingerprint, only when set, and each affected task's `effective_config` records it. The note's text is code in `budget_notes.py`, not an agent instruction, so it changes `tree_sha256`, not `prompts_sha256`.
- **Ledger prompts.** Gap analysis, synthesis, and verification receive a projection of the ledger: null and empty fields omitted, each source listed once and cited by `source_id` (sources that differ only in hidden fields share a row; IDs are numbered once over the whole ledger, so a source keeps its ID in every prompt, where each prompt used to number its own sources from `s1`), and a quote in place of its excerpt unless the quote was not found in tool output. The verifier sees only the claims the report cites and every claim a contradiction names; other results keep their question and conclusion without claims, so the verifier can still ask for follow-ups on questions the report leaves out. Stored evidence is unchanged, so the evidence version does not change. `prompts_sha256` hashes instruction text and output schemas. Those instructions name `source_id`, so an instruction edit changes the fingerprint. Excerpt length, which source fields are hidden, and the long-horizon claim projection are code in `ledger.py` and `long_horizon.py`; they change `tree_sha256` when the tree is dirty, not `prompts_sha256`. A finishing prompt that cannot fit one validation retry fails the run before that call (`PromptExceedsRetryBudget`).
- **Prompt caching.** A route's `prompt_cache` changes what a call is billed, not what the model sees, so scores stay comparable while cost does not. It appears in the policy snapshot, and so in the config fingerprint, only when set; presets without it keep their earlier fingerprint.
- **Refusal fallback.** A route's `refusal_fallback` appears in the policy snapshot only when set. Setting it on the presets' planner and synthesizer changed their fingerprints on 25 September 2026. A job whose call was refused and rerun on the fallback shows both tasks, each with its model.
- **Attachment mode.** Never merge normalized and multimodal results into one number.
- **Review reasons.** `review_reasons` also flag a run whose web and scholarly tool calls mostly reached no source, as behind a network that allows only listed domains ([acquisition.md](acquisition.md#telemetry-and-privacy)). Manifests written before this can count fewer runs as needing review for the same results. They are not scores, so the evaluator version and fingerprint do not change.
- **Source citations and planner budget.** Reports cite sources inline as `[sN]` with ledger-wide IDs that the synthesizer must back with its listed claims, and the verifier's instructions define `severity`. Earlier runs' verifiers could rate supported claims `major` for citation numbers the synthesizer and verifier numbered differently, so their `review_reasons` and severity counts are not comparable with later runs. Graded short answers drop inline citations before exact-match scoring, so `Exact Answer: 1997 [s4]` scores as `1997`, and exported reports end with a "Sources" section for the IDs they cite. A planner sees a `budget` only under a job cap, which benchmark policies do not set. These instruction changes change `prompts_sha256`. Since 25 September 2026, an inline citation that no listed claim backs is dropped, with a caveat naming it, instead of costing the synthesis a retry; a run's `tree_sha256` records the change, and reports from before it may carry citations a retry corrected. The same day, the synthesizer was asked for Markdown headings and pipe tables, a `title`, and an `executive_summary` (both optional fields of `FinalReport`), which changes `prompts_sha256`; its inline citations are checked like the answer's.
- **Evaluator version.** Scores are comparable only under one set of metric definitions; `evaluator_version` changes when one does.

The manifest's `config_fingerprint` hashes policies, the run configuration, attachment, tool, and repository modes, acquisition, the prompt fingerprint, dataset hashes, and the evidence and evaluator versions, so editing a prompt, a limit, or a dataset changes it. Code identity is the commit plus `tree_sha256`: two different uncommitted trees on one commit get different hashes, but prefer committing before named comparison runs.

## Metrics

Local metrics, computed with Pydantic Evals:

- supported-claim rate and major-error-free rate
- primary-source rate
- supported claims per research tool call, and per dollar
- unique-search rate
- blocked-source compliance: fails when a blocked source was fetched or cited as evidence; refused fetches and sightings in search results are recorded but pass
- eval-integrity rate
- exact-answer match when a short reference answer exists
- verbatim-quote rate: the share of evidence quotes found in text the research tools returned
- observed-source rate: the share of URL-cited evidence whose source appeared in text the research tools returned
- attachment citation coverage

A metric that does not apply to a case records no score, so averages cover only the cases it assessed: the claim rates need at least one verifier-checked claim, claims per dollar needs a known nonzero cost, blocked-source compliance needs blocked URLs, eval integrity needs a leakage-sensitive case, and attachment coverage needs attachments. A case's `cost_usd` is the job's own spend, and it is unknown (`null`) once any billed call has no pricing data.

The claim rates measure groundedness as judged by the run's own verifier, not factual correctness.

Evidence version 2 splits each evidence item into an `excerpt` (the model's summary) and an optional verbatim `quote`. After each scout, deep dive, or salvage call, code checks every quote against all the text that run's tools returned: fetched pages and papers, search results, abstracts, and attachment chunks. Matching ignores case, whitespace, typographic quotes and dashes, and hyphenated line breaks, and accepts `...` and bracketed insertions when the remaining parts appear in order. The verdict is stored as `quote_check` (`verified` or `not_found`); it is hidden from the model's output schema and overwritten if a model sets it. The verbatim-quote rate needs no model judgment, but it shows only that the wording was in the tool output, not that the source supports the claim, and it does not check paraphrases.

Evidence version 3 checks each role's output against the run, so every reference in a result resolves. Plans must have at least one question, unique IDs, and no more than the policy's maximum. Research results are filed under the question they were asked, and their evidence may cite only the run's attachments. Gaps and verifier follow-ups must name planned questions. The report and verifier checks may cite only the ledger's claim IDs. A mismatch gets one retry that names it; if the model repeats it, the case fails with `UnexpectedModelBehavior` instead of scoring a result that does not hold together.

Version 3 also checks each URL-cited evidence item's source against the same tool output as its quote. It is `observed` when the URL appears there, ignoring scheme, `www.`, query, fragment, and trailing slash, or when its DOI or arXiv ID does, and `not_found` otherwise; attachment sources are covered by the ID check above. Like `quote_check`, `source_check` is set by code and hidden from the model's output schema. It shows that a tool returned the source, not that the source supports the claim, and it is separate from blocked-source auditing.

Evidence version 4 compares quotes on their letters and digits only, after Unicode normalization and case folding, and accepts each `...` or bracket-separated part wherever it appears in the same piece of tool output, in either order. The p01 pilot showed why: all 8 of its `not_found` quotes were faithful quotes of text the run had fetched. PDF extraction had put spaces inside words ("s olutions") in four, the model had written list bullets as semicolons in one and dropped code comment signs in another, and one joined two passages out of document order. They caused both of p01's major verifier findings. One known gap remains: pypdf can place a page's running header inside a sentence, and a quote that spans it is still `not_found`. Quote rates under versions 3 and 4 are not comparable.

### Rubric judge

`--judge MODEL` (with `--paid`) adds `RubricJudge` (`evals.py`), which grades each report against its case's rubric with one model call. The judge gets the question, the rubric points numbered within each category, and the report as a reader sees it: the answer, its key statements and caveats, and its Sources list, without the verifier's verdicts (`JUDGE_VERSION` 2; version 1 read only the answer and sources, and missed points a report stated only as a key statement). It returns a verdict for every point: met when the report itself states what the point describes, consistently with it, or, for a point asking that something be absent, when the report does not do it. A verdict missing or repeated gets a retry. It scores `rubric`, the share of all points met, `rubric:<category>` for each category, and `rubric_cost_usd`, the judge call's own cost, which is not part of the job's. The overall score's reason lists unmet points by category and number only, never their text. Cases without a rubric get no score. The judge runs at high thinking effort by default: at low effort it marked points unmet that reports stated plainly, one of them in a section heading. A second judge needs its own name, `--judge NAME=MODEL`, and scores under that name. Only reports with a rubric are sent to the judge, and the judge's provider receives the rubric.

Rubric scores come from a model, so they are comparable only under one judge model and one judge prompt: the manifest's `judges` records each judge's name, model, thinking effort, and `JUDGE_VERSION`, which changes when the judge's instructions or verdict schema do. The default metrics and `evaluator_version` are unaffected by adding a judge. These rubric scores are not the official DRB-II scores.

Keep official scoring separate: use the official BrowseComp and GAIA semantic graders for published comparisons, and the official DRB-II evaluator for rubric scores.

## Grading stored runs

`research-grade SUITE --job CASE_ID=JOB_ID ...` grades finished jobs stored in Postgres against the suite's cases, without running anything again. `--jobs-from RECORD` takes the succeeded runs of a settings-study step record (`examples/settings_study.py`) instead. It rebuilds each job's output from its stored report, verification, evidence ledger, and task usage through the same code `research-bench` uses, and applies the default metrics plus any `--judge`s, `--repeat N` times each; several jobs can answer one case. Scores go to a JSONL file under `benchmark_outputs/grades/` (or `--output`), one row per job and repeat, with the judges' reasons and settings; the console shows scores only.

Postgres keeps tool arguments as hashes. A job whose tasks all kept a [transcript](setup.md#full-transcripts) is graded from its real tool calls. Otherwise unique-search rate and eval integrity get no score, and blocked-source compliance scores only a failure from cited sources, since the fetched URLs are unknown. A job run with `research-bench` and graded this way scores the same as it did live, apart from those. Repeats of the same job differ only in the judges' scores, so their spread is the judge's own variation, which `docs/settings-study.md` uses.

## Anti-contamination rules

1. Never commit decrypted BrowseComp questions or answers.
2. Never print leakage-sensitive benchmark inputs in CI logs.
3. Never let an agent search for benchmark answer dumps, evaluator artifacts, or decrypted mirrors. Leakage-sensitive cases add this to every role's constraints.
4. Persist search and tool traces so benchmark-aware behavior can be audited. Persisted tool arguments are hashed; raw arguments stay in memory only long enough for the blocked-source and integrity checks.
5. Treat public benchmark scores as potentially contaminated over time, and keep a private holdout set for routing decisions.
6. Keep retrieval conditions identical when comparing models. Native provider search makes a production-system benchmark, not a clean model benchmark.

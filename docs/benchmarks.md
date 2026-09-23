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
- [Logfire tracing](setup.md#logfire-tracing) can show timing, retries, and token usage without prompt or tool content.

## Manifests and outcomes

Every run writes a sanitized experiment manifest to `RESEARCH_BENCHMARK_OUTPUT` (default `benchmark_outputs/`), or to `--manifest-output`. It records the git commit and dirty flag, with `tree_sha256` hashing any uncommitted changes and untracked files (ignored files such as `.env` are left out); package versions, extraction libraries included; redacted policy snapshots; the effective run configuration; `prompts_sha256`, a fingerprint of every agent's instructions and output schema; graph, evidence, and evaluator versions; acquisition backends and fetch version; the suite file's hash and each source's `dataset_sha256`; the selected case IDs; and each run's job and root-run IDs. It never contains raw prompts, answers, credentials, or local paths.

A failed case does not stop the suite. Its run record gets `status = "failed"` and the exception type only, because provider error messages can carry response bodies; for the same reason the console report omits errors. The manifest's `status` is `completed` when every case succeeded, `completed_with_failures` when some failed, and `failed` when all failed or the suite itself errored or was cancelled, in which case `error` holds the exception type; `failed_cases` counts failures across policies. Interrupting a run (Ctrl-C) marks its unfinished cases `failed` with `CancelledError`. A succeeded run also records `review_reasons`, what it left unresolved, which is empty for a clean result, and, for a case with blocked URLs, `blocked_sources`: how many blocked sources its tools refused, fetched anyway, showed in search results, and saw cited. The manifest records counts only, never the blocked URLs. The CLI exits non-zero unless the status is `completed`.

Scores outlive the process. Each succeeded run record holds `scores`, the value of every metric that applied to the case (a metric that did not apply has no entry, so it is never averaged in as a pass or a failure), and `measures`, the counts behind them: checked and unsupported claims, tool calls, research tool calls, tokens, cost (`null` when unpriced), quotes and sources and how many were not found, attachments, and integrity flags. It also records `duration_seconds` and, if an evaluator itself failed, `evaluator_failures`. `summary` holds one entry per policy: cases run, succeeded, failed, and needing review, the succeeded cases' total cost (`null` once any was unpriced), and each metric's mean with the number of cases it covers. None of it contains prompts, answers, search queries, or URLs.

## Comparability

Compare runs only when these match:

- **Graph version.** Keep `research-graph-v1` fixed while comparing policies.
- **Tool stack.** Benchmarks always use normalized acquisition: DuckDuckGo search, the shared `web_fetch`, and the scholarly tools, with web and scholarly caches off so no result depends on an earlier run. See [acquisition.md](acquisition.md).
- **Fetch version.** Version 2 pages through long documents and shares fetches within a case; version 3 also refuses blocked sources. It is part of the configuration fingerprint.
- **Evidence version.** Version 2 adds verbatim quotes checked against tool output; version 3 checks every role's output against the run (see [Metrics](#metrics)).
- **Ledger prompts.** Gap analysis, synthesis, and verification receive a projection of the ledger: null and empty fields omitted, each source listed once and cited by `source_id` (sources that differ only in hidden fields share a row), and a quote in place of its excerpt unless the quote was not found in tool output. The verifier sees only the claims the report cites and every claim a contradiction names; other results keep their question and conclusion without claims, so the verifier can still ask for follow-ups on questions the report leaves out. Stored evidence is unchanged, so the evidence version does not change. `prompts_sha256` hashes instruction text and output schemas. Those instructions name `source_id`, so an instruction edit changes the fingerprint. Excerpt length, which source fields are hidden, and the campaign claim projection are code in `ledger.py` and `campaign.py`; they change `tree_sha256` when the tree is dirty, not `prompts_sha256`. A finishing prompt that cannot fit one validation retry fails the run before that call (`PromptExceedsRetryBudget`).
- **Attachment mode.** Never merge normalized and multimodal results into one number.
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

Keep official scoring separate: use the official BrowseComp and GAIA semantic graders for published comparisons, and the official DRB-II evaluator for rubric scores.

## Anti-contamination rules

1. Never commit decrypted BrowseComp questions or answers.
2. Never print leakage-sensitive benchmark inputs in CI logs.
3. Never let an agent search for benchmark answer dumps, evaluator artifacts, or decrypted mirrors. Leakage-sensitive cases add this to every role's constraints.
4. Persist search and tool traces so benchmark-aware behavior can be audited. Persisted tool arguments are hashed; raw arguments stay in memory only long enough for the blocked-source and integrity checks.
5. Treat public benchmark scores as potentially contaminated over time, and keep a private holdout set for routing decisions.
6. Keep retrieval conditions identical when comparing models. Native provider search makes a production-system benchmark, not a clean model benchmark.

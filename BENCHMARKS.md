# Benchmark strategy

The benchmark layer separates **model quality**, **research-system quality**, and **retrieval-environment quality**. Public leaderboards are useful for candidate selection; routing decisions should ultimately come from the same models running through this project's own harness.

## Lanes

### BrowseComp — scout / difficult retrieval lane

- 1,266 hard web-research questions in the official release.
- Short, objectively checkable answers.
- Upstream distributes questions and answers encrypted with a per-row canary to slow benchmark contamination.
- The adapter caches only the encrypted CSV and decrypts rows in memory.
- The local deterministic `ReferenceAnswerMatch` score is a fast smoke metric, not a replacement for the official semantic grader.
- `EvalIntegrity` flags suspicious benchmark-aware search queries (for example searching for BrowseComp itself instead of solving the domain question).

Use this lane to measure search strategy, persistence, source discovery, cost/correct answer, and breadth-vs-depth scout policies.

### DeepResearch Bench II — synthesis / evidence / report lane

- 132 expert-grounded long-form tasks.
- 9,430 fine-grained rubrics across information recall, analysis, and presentation.
- Each task may specify blocked source URLs derived from the expert report used to create the rubric.
- The adapter preserves the rubrics and blocked URLs.
- Blocked URLs are propagated as hard run constraints to planner, scouts, deep dives, synthesis, and verification.
- Tool traces are audited for blocked-source access.
- `--export-reports` writes `idx-<n>.md` files that can be passed to the official DRB-II evaluator.

Use this lane to measure evidence coverage, synthesis, structure, citation behavior, and the value of a premium final synthesizer.

### GAIA — orchestration / mixed-tool lane

GAIA is gated upstream and may include local attachments. The adapter therefore accepts an authorized local snapshot and never redistributes the dataset.

For v3, attachment metadata is preserved but the generic research loop does not yet ingest arbitrary local PDFs/media into every provider. For comparable model runs, start with `include_attachments = false`. The next attachment increment should normalize local document/media extraction before comparing providers.

### Original USTC DeepResearch Bench

The adapter consumes the local `query.jsonl` used by the official repository. This remains useful for reproducing the original RACE/FACT workflow, but DRB-II is the stronger default for our new gold/report lane because its rubrics are more atomic and diagnostically useful.

### FutureSearch Deep Research Bench / RetroSearch

FutureSearch DRB is especially valuable because RetroSearch freezes page content while preserving a search-like interface. FutureSearch currently asks prospective benchmark runners to contact them for access. The adapter therefore consumes an authorized local task export; it does not redistribute tasks or simulate RetroSearch.

Once credentials/access are available, implement a dedicated normalized `search`/`fetch` capability backed by RetroSearch and run it as a separate benchmark tool mode. Do **not** compare a RetroSearch run against a DuckDuckGo run as though only the model changed.

## Suite manifests

A suite is a TOML file:

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

Sampling is deterministic by `seed`. Source-specific adapters normalize all cases into `BenchmarkCaseSpec`.

## Run

```bash
research-bench examples/benchmark_suite.toml \
  --policies quality breadth glm-heavy \
  --max-concurrency 1 \
  --export-reports benchmark_outputs
```

A benchmark case already fans out several subagents internally, so start with outer concurrency `1`.

## Metrics

Shared local metrics:

- supported claim rate
- major-error-free rate
- primary-source rate
- supported claims / research tool call
- supported claims / dollar
- unique-search rate
- blocked-source compliance
- eval-integrity rate
- deterministic exact-answer match when a short reference answer exists

Keep official benchmark scoring separate. In particular, use the official BrowseComp/GAIA semantic grading for published comparisons and the official DRB-II evaluator for rubric scores.

## Anti-contamination rules

1. Never commit decrypted BrowseComp questions or answers.
2. Never print leakage-sensitive benchmark inputs in CI logs.
3. Never let an agent search for benchmark answer dumps, evaluator artifacts, or decrypted mirrors.
4. Persist search/tool traces so benchmark-aware behavior can be audited.
5. Treat public benchmark scores as potentially contaminated over time; maintain a private holdout/gold set for routing decisions.
6. Keep retrieval conditions identical when comparing models. Native provider search is a production-system benchmark, not a clean model benchmark.

## Attachment lanes (v4+)

File-backed tasks now have two explicit modes:

- `normalized` (default): every model uses the same deterministic local extraction and attachment search/read tools. Use this for clean model/router comparisons.
- `multimodal`: keeps normalized tools, but additionally supplies image bytes and visually dependent/scanned PDFs through PydanticAI `BinaryContent`. Use this only when vision/document understanding is intentionally part of the evaluation.

Do not merge normalized and multimodal results into one leaderboard number. They test different systems.

Attachment-backed evidence is cited by stable `attachment_id` plus a locator such as `page 7`, `sheet 'Results', rows 42-81`, or `entire image`. Benchmark output now includes attachment-tool calls and attachment citation coverage.

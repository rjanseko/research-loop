# Research Loop (v5 graph, v6 lab)

Evidence-first, role-routed PydanticAI research orchestration with a typed Pydantic Graph control plane, normalized attachments, reproducible benchmark adapters, and Postgres telemetry.

## Local research lab quick start

```bash
make setup
source .venv/bin/activate
pytest -q
research-diagnose                         # safe with no API keys
```

For the local Postgres database, copy `.env.example` to an ignored `.env`; the CLI reads it automatically and exported variables take precedence. Then:

```bash
cp .env.example .env
chmod 600 .env
docker compose up -d postgres
research-db status
research-db migrate
research-db status
research-bench examples/benchmark_cases.json --policies synthetic --repository postgres
```

The synthetic policy exercises the real graph and Postgres writes without model or web calls. A sanitized experiment manifest is written under `RESEARCH_BENCHMARK_OUTPUT` (default `benchmark_outputs/`). The manifest records policy/model settings, package versions, benchmark selection, graph version, timestamps, and job/root-run IDs; it omits raw benchmark prompts, answers, credentials, and local paths.

Before a paid run, set the provider keys and verified `RESEARCH_*_MODEL` overrides in the environment. Then validate the configured routes and run a small benchmark:

```bash
research-diagnose --policy quality --attachments --smoke
research-bench examples/benchmark_suite.toml --policies quality --paid --repository postgres --max-concurrency 1
```

`--smoke` makes bounded provider calls; without it, diagnosis checks local configuration and model profiles only. `research-bench` defaults to synthetic runs and disposable in-memory mode. Real policies require `--paid` and run one suite case unless `--max-cases N` or `--all-cases` is specified. `--repository postgres` persists runs. Keep `research-graph-v1` fixed while comparing policies. See [`LOCAL_SETUP.md`](LOCAL_SETUP.md) for environment details.

### Optional Logfire traces

Install `pip install -e ".[observability]"`, then set `RESEARCH_LOGFIRE_ENABLED=true` and `LOGFIRE_TOKEN` in your ignored `.env` (or run `logfire auth` followed by `logfire projects use <project-name>`). Benchmark runs and `research-diagnose --smoke` then trace PydanticAI model calls, tools, retries, and usage. Tracing is off by default. The integration excludes prompts, completions, tool arguments/results, and binary content from exported spans. It uses an isolated tracer so Pydantic Evals case spans, which contain protected benchmark inputs, are not exported by this setup. It sends to Logfire only when a token is available; Postgres remains the durable record of jobs and research evidence. Library callers can opt in by calling `configure_logfire(ResearchSettings.from_env())` before `ResearchLoop.run(...)`.

## v5 architecture

v5 changes **workflow topology**, not agent semantics. The public `ResearchLoop.run(...)` interface remains compatible with v4, while the internal research algorithm is now an explicit `pydantic-graph` `GraphBuilder` workflow.

The graph is versioned independently from model routing:

```text
graph_version = research-graph-v1
policy        = quality | breadth | glm-heavy | ...
benchmark     = browsecomp | gaia | drb2 | ...
```

This lets experiments distinguish a better **model policy** from a better **research algorithm**.

New in v5:

- `ResearchLoop` is backed by `pydantic_graph.GraphBuilder`;
- explicit map/join fan-out for scouts and deep dives;
- explicit decisions for initial escalation and verifier follow-up research;
- bounded concurrency remains controlled by `ResearchConfig` semaphores;
- graph state is intentionally small and contains no evidence ledger;
- evidence is appended only after parallel joins;
- `LegacyResearchLoop` preserves the v4 plain-async orchestrator for parity/regression tests;
- `research-graph` renders the topology as Mermaid;
- `research-graph-v1` is stored in job/benchmark metadata;
- deterministic parity coverage exercises both initial escalation and a verifier-driven research cycle;
- all v4 attachment, benchmark, provenance, routing, telemetry, and Postgres behavior remains in place.

See [`GRAPH.md`](GRAPH.md), [`ATTACHMENTS.md`](ATTACHMENTS.md), and [`BENCHMARKS.md`](BENCHMARKS.md).

## Architecture

```text
CLI
 │
 ▼
Session
 │
 ▼
Run
 │
 ▼
ResearchLoop                 public facade
 │
 ▼
ResearchGraph                control plane
 │
 ├── Plan
 │     │
 │     ▼
 │   Map<Scout> ───────────────┐
 │                             ▼
 │                         Join results
 │                             │
 │                             ▼
 │                       Gap analysis
 │                         /       \
 │                    gaps         ready
 │                     │             │
 │               Map<DeepDive>       │
 │                     │             │
 │                   Join            │
 │                     └──────┬──────┘
 │                            ▼
 │                       Synthesize
 │                            │
 │                            ▼
 │                         Verify
 │                         /    \
 │                  follow-up   pass/max rounds
 │                      │          │
 │                Map<DeepDive>    │
 │                      │          │
 │                    Join         │
 │                      └──► Synthesize
 │                                  │
 │                                  └──► Verify
 │
 ├── PydanticAI Agents         model/tool semantics
 ├── ModelPolicy               provider/model routing
 ├── AttachmentCorpus          normalized local evidence
 └── EvidenceLedger            append-only run data plane
 │
 ▼
Postgres                      durable history + telemetry
```

The ownership boundary is deliberate:

```text
PydanticAI Agent   owns agent/model/tool semantics
Pydantic Graph     owns workflow topology
Run                owns execution lifecycle
ModelPolicy        owns model selection
EvidenceLedger     owns current research evidence
Postgres           owns durable history + telemetry
CLI                owns presentation
```

Pydantic Graph does **not** become the durable database and does not replace `Run`.

## Why evidence is not graph state

Pydantic Graph parallel branches share mutable graph state. v5 therefore does not let scouts append directly to shared state.

Parallel workers return typed `ResearchResult` values:

```text
Map<Scout>
  ├── ResearchResult
  ├── ResearchResult
  └── ResearchResult
          │
          ▼
        Join
          │
          ▼
Record evidence  ← serial step
```

Only the serial record step appends to `EvidenceLedger`.

`ResearchGraphState` contains control data only: objective, plan, workflow phase, and verification-round counters.

## Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[all]'
```

The graph-backed version targets the current Pydantic v2 line:

```text
pydantic-ai   >= 2.47, < 3
pydantic-graph >= 2.47, < 3
```

For a smaller production install:

```bash
pip install -e '.[attachments,postgres]'
```

## Render the research algorithm

```bash
research-graph
```

or:

```bash
research-graph --direction TB --output research-graph.mmd
```

The output is Mermaid generated by the executable graph itself, rather than a separately maintained architecture diagram.

## Production research

```python
from research_loop import (
    AttachmentMode,
    ResearchConfig,
    ResearchConstraints,
    ResearchLoop,
    ResearchToolMode,
    get_policy,
)
from research_loop.repository import PostgresResearchRepository

loop = ResearchLoop(
    get_policy("quality"),
    ResearchConfig(
        tool_mode=ResearchToolMode.ADAPTIVE,
        attachment_mode=AttachmentMode.NORMALIZED,
    ),
    repository=PostgresResearchRepository(existing_async_psycopg_pool),
)

outcome = await loop.run(
    "Compare the claims in the attached report with current primary sources.",
    session_id=session.id,
    root_run_id=run.id,
    constraints=ResearchConstraints(
        attachment_paths=["/local/path/report.pdf"],
    ),
)
print(outcome.report.answer)
```

The model never receives the host file path. Attachments are exposed through stable IDs and normalized tools.

## Legacy async baseline

The old orchestration remains intentionally available:

```python
from research_loop import LegacyResearchLoop

legacy = LegacyResearchLoop(get_policy("quality"), ResearchConfig(...))
```

This exists for regression/parity work, not because production should maintain two unrelated systems indefinitely. Once the graph implementation has accumulated real benchmark history, the legacy implementation can be retired.

## Benchmarking

```bash
research-bench examples/benchmark_suite.toml \
  --policies quality breadth glm-heavy \
  --paid --all-cases \
  --max-concurrency 1 \
  --export-reports benchmark_outputs
```

Every benchmark report now carries `graph_version=research-graph-v1` in its experiment metadata. Model policies remain independently named.

Normalized attachment mode remains the default for fair cross-provider comparisons:

```bash
research-bench examples/benchmark_suite_full.example.toml \
  --policies quality breadth glm-heavy \
  --paid --all-cases \
  --attachment-mode normalized
```

Use a separate multimodal lane when pixels/scanned documents are part of the capability being evaluated.

## Database

Apply the existing migrations:

```text
migrations/001_research.sql
migrations/002_research_attachments.sql
```

No v5 schema migration is required. `graph_version` is stored in the existing JSON effective configuration, keeping topology versioning independent of schema evolution.

## Tests

```bash
pytest -q
```

The parity test uses deterministic agent outputs and intentionally triggers:

1. ordinary parallel scouts;
2. a low-confidence initial deep dive;
3. synthesis and verification;
4. a verifier-requested second deep dive;
5. re-synthesis and successful verification.

It compares an order-insensitive semantic fingerprint of graph-backed and legacy outcomes.

## What v5 deliberately does not add

- DBOS, Temporal, Prefect, or another durable workflow runtime;
- graph-state snapshots pretending to be crash-resumable execution;
- a learned router;
- additional agent roles;
- event sourcing;
- another evidence store.

With the v6 lab bootstrap in place, the next milestone is measurement: run small paid smoke tests and scout tournaments, then derive `ModelPolicy` changes from persisted telemetry rather than public leaderboards.

## Codex handoff

This repository export includes:

- `AGENTS.md` — stable repository-level coding constraints for Codex;
- `CODEX_TASK.md` — the v6 Research Lab Bootstrap specification;
- `LOCAL_SETUP.md` — local bootstrap instructions;
- `.env.example` — credential/configuration template;
- `compose.yaml` — optional local PostgreSQL 16 instance;
- `Makefile` and `scripts/` — convenience commands.

For further work, start from the repository root and keep `AGENTS.md` and `CODEX_TASK.md` as the architecture and acceptance references.

## Scholarly research and first campaign

Install `pip install -e '.[all]'` for normalized web and scholarly PDF extraction. `research-diagnose --scholar-live` checks the public metadata endpoints without model calls. The provider-neutral adapters, cache modes, source-status rules, and optional local GROBID fallback are described in [SCHOLAR.md](SCHOLAR.md).

The first long-horizon software-engineering research campaign is specified in [campaign.toml](campaigns/long_horizon_agentic_se/campaign.toml). Run `PYTHONPATH=src .venv/bin/python scripts/scholar_pilot.py` for a free metadata pilot. Validate the campaign with `research-campaign --dry-run`. After provider balances and spending caps are ready, `research-campaign --question q01 --paid --persist` runs a single question and exports its evidence. After questions complete, `research-campaign --synthesize --paid` writes the campaign report, catalogs, and hypotheses; each must cite claims from the aggregated evidence ledger. See the [campaign README](campaigns/long_horizon_agentic_se/README.md). The spec does not contain unverified research findings.

Each question runs under `execution.question_cost_limit_usd`, which sets `ModelPolicy.job_cost_limit`. The cap is soft: it is checked before every agent call, each call's `cost_limit` is clamped to the remaining budget, and parallel calls already in flight can overshoot it. PydanticAI can only enforce cost limits for models with pricing data. The paid preflight marks unpriced routes `WARN`, which blocks the campaign. If a call still returns no price, the job stops before its next call with `JobBudgetExceeded`. The manifest records each question's cost and, on failure, the exception type. Keep provider-side spending caps as the hard limit.

`execution.question_reserve_usd` is the part of each question's cap that the planner, scouts, and deep dives must leave unspent. It pays for gap analysis, synthesis, verification, and salvage calls. The campaign also enables `ResearchConfig.salvage_exhausted_research`. With it on, a scout or deep dive that hits a usage limit makes one tool-free "salvage" call that summarizes the evidence it already gathered. If nothing was gathered or no budget remains, the question continues with an empty, zero-confidence result for that subquestion instead of failing. Salvage calls are marked `salvage: true` in task config. Their stored prompt keeps hashes of the replayed tool output, not the text. Salvage is off by default, so benchmark behavior is unchanged.

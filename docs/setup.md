# Setup

## Install

```bash
make setup      # python3 -m venv .venv && pip install -e '.[all]'; PYTHON=python3.12 make setup picks the interpreter
make test       # pytest -q
```

Or by hand:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[all]'
pytest -q
```

Python 3.12 or later is required. The package tracks the PydanticAI and Pydantic Graph 2.x line (`>=2.47,<3`). `all` installs every optional extra; install fewer with, for example, `pip install -e '.[attachments,postgres]'`.

| Extra | Adds |
|---|---|
| `scholarly` | httpx, Trafilatura, and pypdf for web and scholarly full-text extraction |
| `attachments` | pypdf, python-docx, openpyxl, Beautiful Soup, and Pillow for local attachments |
| `postgres` | psycopg with its connection pool |
| `eval` | Pydantic Evals, which `research-bench` needs |
| `benchmark` | pandas and pyarrow for Parquet datasets such as GAIA |
| `observability` | Logfire |
| `test` | pytest, pytest-asyncio, and reportlab |

The test suite runs offline. `tests/conftest.py` refuses model-provider requests and fails any test that resolves or connects to a non-loopback host.

## Configuration

The CLIs read an ignored `.env` in the current directory; exported environment variables take precedence. Model calls are the exception: they authenticate only with `OPENROUTER_API_KEY` already exported in the process environment, and a value in `.env` is ignored. Start from the template and keep it private:

```bash
cp .env.example .env
chmod 600 .env
```

| Variable | Default | Used for |
|---|---|---|
| `DATABASE_URL` | unset | Postgres DSN for `research-db`, `--persist`, and `--repository postgres` |
| `OPENROUTER_API_KEY` | unset | OpenRouter credential. Export it in the environment; `.env` cannot set it |
| `RESEARCH_*_MODEL` | see below | Model route overrides, each `openrouter:<author>/<slug>`. Settings refuse any other value when they load, so an id copied from an older `.env.example`, such as `anthropic:claude-opus-5`, fails before a job starts |
| `RESEARCH_BENCHMARK_CACHE` | `.cache/research-loop` | Downloaded datasets and the acquisition cache |
| `RESEARCH_BENCHMARK_OUTPUT` | `benchmark_outputs` | Experiment manifests and pilot outputs (ignored by git) |
| `RESEARCH_BENCHMARK_CONCURRENCY` | `1` | Default `research-bench --max-concurrency` |
| `RESEARCH_SCHOLAR_CACHE_MODE` | `live` | Web and scholarly cache mode for library runs; see [acquisition.md](acquisition.md#caching) |
| `OPENALEX_API_KEY` | unset | Reliable OpenAlex search, which is rate-limited without a key |
| `CROSSREF_MAILTO` | unset | Contact address for Crossref's polite pool |
| `GROBID_URL` | unset | Local GROBID service for PDF extraction, for example `http://127.0.0.1:8070` |
| `RESEARCH_LOGFIRE_ENABLED`, `LOGFIRE_TOKEN` | `false`, unset | Optional tracing; see [below](#logfire-tracing) |

Never commit `.env` or API keys.

## Model routing

Each policy in `policy.py` routes every role to a model. These variables replace a route's model, keeping its limits:

| Variable | Route |
|---|---|
| `RESEARCH_PLANNER_MODEL` | Planner |
| `RESEARCH_SCOUT_MODEL` | Scout (`quality`, `glm-heavy`) |
| `RESEARCH_BREADTH_SCOUT_MODEL` | Scout and cheap scout (`breadth`) |
| `RESEARCH_CHEAP_SCOUT_MODEL` | Cheap scout for low-difficulty questions that do not need primary sources (`quality`) |
| `RESEARCH_GLM_CHEAP_MODEL` | Cheap scout (`glm-heavy`) |
| `RESEARCH_MULTIMODAL_MODEL` | Scout for questions that need image input |
| `RESEARCH_GAP_MODEL` | Gap analyst (`quality`, `breadth`) |
| `RESEARCH_GLM_GAP_MODEL` | Gap analyst (`glm-heavy`) |
| `RESEARCH_DEEP_MODEL` | Deep dive |
| `RESEARCH_ALT_DEEP_MODEL` | Deep dives in verification rounds |
| `RESEARCH_SYNTH_MODEL` | Synthesizer, including campaign synthesis |
| `RESEARCH_VERIFY_MODEL` | Verifier |

Without an override, each route uses its entry in `DEFAULT_MODELS` in `policy.py`. `.env.example` lists the same values, and a test fails if the two drift apart. Every id is an OpenRouter model (`openrouter:<author>/<slug>`). Model IDs still go stale, and a listing on OpenRouter is not proof of inference access, so confirm every route with `research-diagnose --smoke` before a paid run.

## Postgres

Postgres is optional; without it, runs stay in memory. The Compose file starts a local PostgreSQL 16 with development-only credentials that match `.env.example`:

```bash
docker compose up -d --wait postgres     # or: make postgres-up
research-db status
research-db migrate
research-db status
```

Migrations live in `migrations/` and are checksummed: `research-db migrate` refuses to continue if an applied file has changed. Commands that persist (`research-bench --persist`, `research-campaign --persist`) check for pending migrations before any model call.

The database keeps jobs, the plan, every role task with its prompt, effective configuration, output, usage, and parent task, tool events, and attachment manifests. Each finished job also keeps its evidence ledger, whose unique claim IDs are the ones its report and verification cite (task outputs keep each worker's own IDs), and its `review_reasons`; a failed job keeps the evidence gathered before it failed. Text in tool arguments is stored as hashes and lengths, and fetched content as hashes and sizes, so protected benchmark inputs and article text stay out of it. Graph and policy versions are recorded in each job's effective configuration, so topology changes need no migration.

A run that fails, is interrupted, or passes its `ResearchConfig.max_run_seconds` deadline records itself as failed. Only a process killed outright leaves jobs and tasks `running`. To close those out:

```bash
research-db reconcile --older-than 120          # count jobs running for over 120 minutes, and their tasks
research-db reconcile --older-than 120 --apply  # mark them failed (Abandoned)
```

It also closes running tasks whose job has already finished. Choose a threshold longer than any run still in progress; `finished_at` stays empty, because when the process died is unknown.

## Checking readiness

```bash
research-diagnose                                    # local checks only; no provider calls
research-diagnose --scholar-live                     # also probe the public scholarly endpoints
research-diagnose --policy quality --attachments --smoke   # bounded paid calls to each configured model
```

Without `--smoke`, diagnosis checks dependencies, graph construction, tool construction, writable directories, the database and its migrations, `OPENROUTER_API_KEY`, and each route's model profile. `--smoke` makes one small structured-output call per distinct model, with a tool call where the role needs tools and an image where `--multimodal` asks for one. It reports `WARN` for a model without pricing data, because cost caps cannot be enforced for it.

Failed smoke checks give a short reason without the provider's response body. A credit-balance failure, including OpenRouter's HTTP 402, needs funding on that account; HTTP 403 needs account or model access checked; HTTP 404 usually means a wrong model ID.

## First runs

A synthetic run persists real jobs, tasks, and tool events without any provider call:

```bash
research-bench examples/benchmark_cases.json --policies synthetic --repository postgres --max-concurrency 1
```

Once `--smoke` passes, a first paid benchmark run is one case by default:

```bash
research-bench examples/benchmark_suite.toml --policies quality --paid --repository postgres --max-concurrency 1
```

See [benchmarks.md](benchmarks.md) for suites and manifests, and the [campaign README](../campaigns/long_horizon_agentic_se/README.md) for campaign runs.

## Logfire tracing

Tracing is off by default. To enable it, install the `observability` extra, set `RESEARCH_LOGFIRE_ENABLED=true`, and either set `LOGFIRE_TOKEN` in `.env` or run `logfire auth` followed by `logfire projects use <project-name>`. Then restrict the saved credentials with `chmod 700 .logfire` and `chmod 600 .logfire/*.json`.

Benchmark runs, campaign runs, and `research-diagnose --smoke` then trace PydanticAI model calls, tools, retries, and usage. Prompts, completions, tool arguments and results, and binary content are excluded from spans. The integration uses an isolated tracer, so Pydantic Evals case spans, which contain protected benchmark inputs, are not exported. Without a token, nothing is sent to Logfire. Library callers opt in with `configure_logfire(ResearchSettings.from_env())` before `ResearchLoop.run(...)`.

Postgres, not Logfire, is the durable record of jobs and evidence.

## Coding agents

Run Codex or Claude Code from the repository root so they pick up [AGENTS.md](../AGENTS.md), which records the architectural boundaries and validation rules.

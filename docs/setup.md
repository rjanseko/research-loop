# Setup

## Install

```bash
make setup      # .venv with the pinned dependencies in requirements.lock
make test       # pytest -q
make lint       # ruff check .
```

Or by hand:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.lock
pip install --no-deps -e .
pytest -q
```

`requirements.lock` pins every dependency of every extra, for all platforms, so installs and CI get the same versions. `pip install -e '.[all]'` still works and takes the newest versions `pyproject.toml` allows. After changing dependencies in `pyproject.toml`, run `make lock` (it keeps existing pins where it can); `make lock UPGRADE=1` moves everything to the newest allowed versions. CI fails if the lockfile no longer matches `pyproject.toml`.

Python 3.12 or later is required. The package tracks the PydanticAI and Pydantic Graph 2.x line (`>=2.47,<3`). `all` installs every optional extra; install fewer with, for example, `pip install -e '.[attachments,postgres]'`.

| Extra | Adds |
|---|---|
| `scholarly` | httpx, Trafilatura, pypdf, and fontTools for web and scholarly full-text extraction |
| `attachments` | pypdf, fontTools, python-docx, openpyxl, Beautiful Soup, and Pillow for local attachments |
| `postgres` | psycopg with its connection pool |
| `eval` | Pydantic Evals, which `research-bench` needs |
| `benchmark` | pandas and pyarrow for Parquet datasets such as GAIA |
| `observability` | Logfire |
| `test` | pytest, pytest-asyncio, reportlab, and ruff (pinned, with the lint rules set in `pyproject.toml`) |

PDF reports (`research-report`, `write_report`, and `--report-dir` in `examples/run_research.py`) need a LaTeX engine on `PATH`, which pip can't install. Any of these works: TeX Live (`apt install texlive-latex-recommended texlive-latex-extra latexmk`, or MacTeX on macOS), MiKTeX, or the single-binary [Tectonic](https://tectonic-typesetting.github.io). The other formats need nothing extra, and without an engine the `.tex` file is still written for you to compile elsewhere. The test that compiles a PDF is skipped when no engine is installed.

The test suite runs offline, and GitHub Actions runs `ruff check .` and `pytest -q` on every push to `master` and every pull request (`.github/workflows/ci.yml`). `tests/conftest.py` refuses model-provider requests and fails any test that resolves or connects to a non-loopback host.

`tests/test_postgres.py` runs against a real, disposable Postgres: migrations applied in order and once, a failing migration rolled back, a full synthetic run's job, tasks, tool events, attachments, and ledger stored, failures recorded, and `research-db reconcile`. It is skipped unless `RESEARCH_TEST_DATABASE_URL` names a database with `test` in its name, because each test drops that database's `public` schema. CI starts a Postgres 16 service for it; locally:

```bash
docker compose up -d --wait postgres
docker compose exec postgres createdb -U research research_test
RESEARCH_TEST_DATABASE_URL=postgresql://research:research@127.0.0.1:5432/research_test pytest -q -m postgres
```

## Configuration

The CLIs read an ignored `.env` in the current directory; exported environment variables take precedence. Start from the template and keep it private:

```bash
cp .env.example .env
chmod 600 .env
```

| Variable | Default | Used for |
|---|---|---|
| `DATABASE_URL` | unset | Postgres DSN for `research-db`, `--persist`, and `--repository postgres` |
| `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, `XAI_API_KEY`, `ZAI_API_KEY`, `OPENROUTER_API_KEY` | unset | Provider credentials |
| `RESEARCH_ENABLED_PROVIDERS` | providers with a key | Comma-separated subset of `openai,anthropic,google,xai,zai,openrouter` |
| `RESEARCH_*_MODEL` | see below | Model route overrides, each `provider:model` |
| `RESEARCH_BENCHMARK_CACHE` | `.cache/research-loop` | Downloaded datasets and the acquisition cache |
| `RESEARCH_BENCHMARK_OUTPUT` | `benchmark_outputs` | Experiment manifests and pilot outputs (ignored by git) |
| `RESEARCH_BENCHMARK_CONCURRENCY` | `1` | Default `research-bench --max-concurrency` |
| `RESEARCH_SCHOLAR_CACHE_MODE` | `live` | Web and scholarly cache mode for library runs; see [acquisition.md](acquisition.md#caching) |
| `OPENALEX_API_KEY` | unset | Reliable OpenAlex search, which is rate-limited without a key |
| `CROSSREF_MAILTO` | unset | Contact address for Crossref's polite pool |
| `SEMANTIC_SCHOLAR_API_KEY` | unset | A dedicated rate limit for `research-long-horizon --basis-papers`; see [acquisition.md](acquisition.md#citation-snowballing-basis-papers) |
| `GROBID_URL` | unset | Local GROBID service for PDF extraction, for example `http://127.0.0.1:8070` |
| `RESEARCH_LOGFIRE_ENABLED`, `LOGFIRE_TOKEN` | `false`, unset | Optional tracing; see [below](#logfire-tracing) |

Never commit `.env` or API keys.

## Model routing

Each policy in `policy.py` routes every role to a model. These variables replace a route's model, keeping its limits:

| Variable | Route |
|---|---|
| `RESEARCH_PLANNER_MODEL` | Planner |
| `RESEARCH_SCOUT_MODEL` | Scout (`quality`, `glm-heavy`, `value`) |
| `RESEARCH_BREADTH_SCOUT_MODEL` | Scout and cheap scout (`breadth`) |
| `RESEARCH_CHEAP_SCOUT_MODEL` | Cheap scout for low-difficulty questions that do not need primary sources (`quality`) |
| `RESEARCH_GLM_CHEAP_MODEL` | Cheap scout (`glm-heavy`, `value`) |
| `RESEARCH_MULTIMODAL_MODEL` | Scout for questions that need image input |
| `RESEARCH_GAP_MODEL` | Gap analyst (`quality`, `breadth`) |
| `RESEARCH_GLM_GAP_MODEL` | Gap analyst (`glm-heavy`) |
| `RESEARCH_DEEP_MODEL` | Deep dive |
| `RESEARCH_ALT_DEEP_MODEL` | Deep dives in verification rounds |
| `RESEARCH_SYNTH_MODEL` | Synthesizer, including long-horizon synthesis |
| `RESEARCH_VERIFY_MODEL` | Verifier |
| `RESEARCH_VALUE_PLANNER_MODEL`, `RESEARCH_VALUE_GAP_MODEL`, `RESEARCH_VALUE_DEEP_MODEL`, `RESEARCH_VALUE_SYNTH_MODEL`, `RESEARCH_VALUE_VERIFY_MODEL` | Planner, gap analyst, deep dive, synthesizer, and verifier (`value`) |

Planner, gap analyst, deep dive, synthesizer, and verifier variables without `VALUE` apply to every preset except `value`, which has its own, so pointing `quality` at another model leaves the `value` lineup unchanged. Every other variable is shared by the presets named beside it.

An override must be `provider:model` for one of the providers above; any other value, such as a `together:` id, stops the CLIs when settings load, before any job starts. OpenRouter serves many vendors' models under one key, written `openrouter:vendor/model`, for example `openrouter:tencent/hy4-preview`. PydanticAI prices calls from the `genai-prices` table, not from OpenRouter's reported cost, so a model missing from that table has no known cost and the job's dollar cap stops working. `research-diagnose --smoke` warns about such a route. Without an override, each route uses its entry in `DEFAULT_MODELS` in `policy.py`. `.env.example` lists the same values, and a test fails if the two drift apart. Model IDs still go stale, and a model listed by a provider is not proof of inference access, so confirm every route with `research-diagnose --smoke` before a paid run.

## Postgres

Postgres is optional; without it, runs stay in memory. The Compose file starts a local PostgreSQL 16 with development-only credentials that match `.env.example`:

```bash
docker compose up -d postgres     # or: make postgres-up
research-db status
research-db migrate
research-db status
```

Migrations live in `src/research_loop/migrations/`, ship in the installed package, and are checksummed: `research-db migrate` refuses to continue if an applied file has changed. Commands that persist (`research-bench --persist`, `research-long-horizon --persist`, `examples/run_research.py --persist`) check for pending migrations before any model call.

The database keeps jobs, the plan, every role task with its prompt, effective configuration, output, usage, and parent task, tool events, and attachment manifests. Each finished job also keeps its evidence ledger, whose unique claim IDs are the ones its report and verification cite (task outputs keep each worker's own IDs), and its `review_reasons`; a failed job keeps the evidence gathered before it failed. Text in tool arguments is stored as hashes and lengths, and fetched content as hashes and sizes, so protected benchmark inputs and article text stay out of it. Graph and policy versions are recorded in each job's effective configuration, so topology changes need no migration.

### Full transcripts

For analysing your own runs, a repository can also keep each task's full transcript: `PostgresResearchRepository(pool, capture_transcripts=True)`, or `--capture` with `--persist` on `examples/run_research.py` and `examples/readme_example.py`. Every agent call then gets a `research_task_messages` row holding its PydanticAI messages as JSON: the prompt, each model response with its own token usage, and the real tool arguments and results, such as search queries and fetched text. String values over 50,000 characters are cut, and `truncated_values` counts them. Capture makes no extra model calls and changes no prompts; its cost is database size, mostly fetched text, which runs up to about 12,000 characters per fetch.

`scripts/analyze_run.sql` analyses one job from these tables: cost, tokens, and time by role and model, each research task's input tokens request by request, every search and fetch with its real query or URL, repeated calls, which fetched URLs the evidence ledger cites, and the verifier's checks by severity. Pass the job ID: `docker compose exec -T postgres psql -U research -d research_loop -v job=<job_id> < scripts/analyze_run.sql`. Its first and last sections work on any stored job; the rest need a captured run.

Capture is off by default, and `research-bench` never turns it on, because transcripts hold exactly the benchmark inputs and fetched content that tool events hash. The table comes from migration `004_research_task_messages.sql`, so run `research-db migrate` after updating; persisting commands refuse to start until it is applied.

```sql
-- One task's transcript, one part per row: its kind, tool, and the start of its content.
select m.ordinality as step, p.value->>'part_kind' as kind, p.value->>'tool_name' as tool,
       left(coalesce(p.value->>'content', p.value->>'args'), 200) as content
  from research_task_messages t,
       jsonb_array_elements(t.messages) with ordinality m,
       jsonb_array_elements(m.value->'parts') p
 where t.task_id = '<task_id>'
 order by m.ordinality;
```

A run that fails, is interrupted, or passes its `ResearchConfig.max_run_seconds` deadline records itself as failed. Only a process killed outright leaves jobs and tasks `running`. To close those out:

```bash
research-db reconcile --older-than 120          # count jobs running for over 120 minutes, and their tasks
research-db reconcile --older-than 120 --apply  # mark them failed (Abandoned)
```

It also closes running tasks whose job has already finished. Choose a threshold longer than any run still in progress; `finished_at` stays empty, because when the process died is unknown.

## Preparing for paid runs

A paid run needs keys for the providers its policy routes to, network access to those providers and to the sources the tools fetch, and a spending plan.

### Provider keys

By default the `quality` policy spreads its roles across five providers (`DEFAULT_MODELS` in `policy.py`):

| Key | Roles it serves by default |
|---|---|
| `ANTHROPIC_API_KEY` | Planner, synthesizer |
| `OPENAI_API_KEY` | Gap analyst, deep dive, verifier, cheap scout |
| `ZAI_API_KEY` | Scout |
| `GOOGLE_API_KEY` | Multimodal scout, used only for questions that need image input |
| `XAI_API_KEY` | Deep dives in verification rounds |

Routes do not fall back to another provider on their own, so every route a policy uses needs either its provider's key or a `RESEARCH_*_MODEL` override that points it at a provider you do have (see [Model routing](#model-routing)). To run on one provider, override every route to it, for example `RESEARCH_DEEP_MODEL=anthropic:<model>`, and set only that provider's key.

`OPENALEX_API_KEY` and `CROSSREF_MAILTO` are optional but worth setting: OpenAlex is rate-limited without a key, and Crossref gives better service to requests that include a contact address.

Put the keys in `.env` locally. In a hosted or sandboxed environment, such as a Claude Code cloud session, add them as environment variables in the environment's settings instead, and never paste them into a chat or a log. A new session picks up changed variables.

### Network access

Sandboxed environments often allow only some hosts. Each provider you use must be reachable:

| Provider | Host |
|---|---|
| Anthropic | `api.anthropic.com` |
| OpenAI | `api.openai.com` |
| Google | `generativelanguage.googleapis.com` |
| xAI | `api.x.ai` |
| Z.ai | `api.z.ai` |
| OpenRouter | `openrouter.ai` |

The tools also need the scholarly APIs (`api.openalex.org`, `export.arxiv.org`, `api.crossref.org`, `api.opencitations.net`, `aclanthology.org`, and `api.semanticscholar.org` for basis papers), the search engines the web search tool tries in turn (the `ddgs` library queries Wikipedia, DuckDuckGo, Brave, Google, Mojeek, Yahoo, and others, not only DuckDuckGo), and whatever pages `web_fetch` follows. Those pages can be on any site, so under a domain allowlist most fetches fail. For real research runs, use a level of network access that allows general public web access.

`research-diagnose --network` checks all of this without model calls; see [Checking readiness](#checking-readiness). If a run's web and scholarly tools still mostly fail to reach their sources, its `review_reasons` say so.

### Before the first query

- **Budget and scope.** Decide how many queries, which policy (`quality`, `breadth`, or `value`), and whether to use benchmark cases or your own questions. Each route has a cost cap per call, but that does not limit the total spend of a batch.
- **Model IDs.** The defaults can go stale. Run `research-diagnose --policy quality --smoke` ([below](#checking-readiness)), which makes one small paid call per distinct model, and fix any failing route with an override rather than by editing `policy.py`.
- **Postgres (optional).** Without `DATABASE_URL`, runs stay in memory. To keep jobs and evidence, point `DATABASE_URL` at a Postgres database ([above](#postgres)); the Compose service needs Docker.

Then run a single case before you run more:

```bash
make setup
research-diagnose --policy quality --network --smoke
research-bench examples/benchmark_suite.toml --policies quality --paid --max-concurrency 1
```

## Checking readiness

```bash
research-diagnose                                    # local checks only; no provider calls
research-diagnose --scholar-live                     # also probe the public scholarly endpoints
research-diagnose --network                          # also check the network reaches providers, tools, and the web
research-diagnose --prices                           # each route model's price per million tokens; --update-prices fetches the latest first
research-diagnose --policy quality --attachments --smoke   # bounded paid calls to each configured model
```

Without `--smoke`, diagnosis checks dependencies, graph construction, tool construction, writable directories, the database and its migrations, provider credentials, and each route's model profile. `--smoke` makes one small structured-output call per distinct model, with a tool call where the role needs tools and an image where `--multimodal` asks for one. It reports `WARN` for a model without pricing data, because cost caps cannot be enforced for it.

`--prices` lists each distinct model the policy routes to, the roles that use it, and its price per million input and output tokens, without model calls. Prices come from the [genai-prices](https://github.com/pydantic/genai-prices) data PydanticAI computes `cost_usd` with, so a model it lists without a price runs with an unknown cost and unenforceable caps. They are the base rate, priced at 100,000 tokens: some models charge more per token for long prompts. `--update-prices` first fetches the latest price data, as `pydantic_ai.prices.update_in_background()` does, and reports whether that worked; otherwise the prices bundled with the installed genai-prices are used.

`--network` sends one HEAD request to each host a run needs, through `HTTPS_PROXY` when one is set, and counts any HTTP response as reachable. It checks the API host of each provider the policy routes to (`FAIL` for a provider with a credential, `WARN` otherwise), then three groups: the scholarly APIs, the search engines, and three ordinary sites standing in for the pages `web_fetch` follows. A group fails when none of its hosts answer and warns when some do not. A proxy that refuses a host is reported as `refused by the proxy`. Providers passing while the ordinary sites fail means the network allows only listed domains: models will answer, but evidence gathering will not work.

Failed smoke checks give a short reason without the provider's response body. A credit-balance failure, including HTTP 402, needs funding on that provider account; HTTP 403 needs account or model access checked; HTTP 404 usually means a wrong model ID.

## First runs

A synthetic run persists real jobs, tasks, and tool events without any provider call:

```bash
research-bench examples/benchmark_cases.json --policies synthetic --repository postgres --max-concurrency 1
python examples/run_research.py "Your question" --persist    # one question; prints its job ID
```

A stored job is a row in `research_jobs`, keyed by that ID:

```sql
select status, review_reasons, final_report->>'answer' from research_jobs where id = '<job_id>';
```

Once `--smoke` passes, a first paid benchmark run is one case by default:

```bash
research-bench examples/benchmark_suite.toml --policies quality --paid --repository postgres --max-concurrency 1
```

The built-in policies cap each call but set no total per run (`job_cost_limit`). For a first single question with a total cap, `examples/readme_example.py` runs the README's example question on the `quality` policy under a $4 cap (`--budget`), $1.50 of it held for synthesis and verification (`--reserve`), with three to five questions, two deep dives per round, and one verification round. Scouts get 400,000 tokens, as in the calibration pilot, instead of the policy's 100,000, and keep their dollar caps. A scout or deep dive that runs out of tokens or budget, or gives up after repeated tool errors, writes its result from what it gathered instead of failing the run (`ResearchConfig.salvage_exhausted_research`, off by default). It uses the `normalized` tools, as benchmarks and long-horizon studies do, so a failed fetch comes back to the model as an error result; in the library's default `adaptive` mode, a model without a native fetch uses PydanticAI's, which raises on any HTTP error such as a 404. Under a cap, the planner also sees the budget and the per-call caps, and is told to keep questions focused and plan fewer when the budget is small. The script prints the report and the sources its `[sN]` citations name, and writes the report, sources, verification, review reasons, cost, and the policy and configuration it ran with to `benchmark_outputs/readme_example/`; add `--persist` to store the job in Postgres too. Without `--paid` it runs the synthetic policy, for free. The cap is soft, and holds only while every model has pricing data, which `--smoke` checks. The record includes the evidence ledger, so `research-report <record>.json` renders the run as a PDF and Markdown beside it.

```bash
python examples/readme_example.py --paid --persist
```

See [benchmarks.md](benchmarks.md) for suites and manifests, and the [long-horizon README](../long_horizon/agentic_se/README.md) for long-horizon runs.

## Logfire tracing

Tracing is off by default. To enable it, install the `observability` extra, set `RESEARCH_LOGFIRE_ENABLED=true`, and either set `LOGFIRE_TOKEN` in `.env` or run `logfire auth` followed by `logfire projects use <project-name>`. Then restrict the saved credentials with `chmod 700 .logfire` and `chmod 600 .logfire/*.json`.

Benchmark runs, long-horizon runs, `examples/run_research.py`, and `research-diagnose --smoke` then trace PydanticAI model calls, tools, retries, and usage. Each research job is one trace: a `research job` span carrying only the job ID and policy name, with every agent call of the job, parallel scouts and deep dives included, nested under it. Search Logfire by `job_id` to find the trace for a Postgres job row. Prompts, completions, tool arguments and results, and binary content are excluded from spans. The integration uses an isolated tracer, so Pydantic Evals case spans, which contain protected benchmark inputs, are not exported. Without a token, nothing is sent to Logfire. Library callers opt in with `configure_logfire(ResearchSettings.from_env())` before `ResearchLoop.run(...)`.

Postgres, not Logfire, is the durable record of jobs and evidence.

## Coding agents

Run Codex or Claude Code from the repository root so they pick up [AGENTS.md](../AGENTS.md), which records the architectural boundaries and validation rules.

Two agent skills that ship inside the `logfire` package are linked into the repository: `logfire-evals`, for the Pydantic Evals metrics in `evals.py`, and `logfire-instrumentation`, for the tracing in `observability.py`. The links live in `.agents/skills/` (read by Codex and other agents) and `.claude/skills/` (read by Claude Code) and point into `.venv`, so they resolve once `make setup` has installed the dependencies. `make setup` repairs them when [uv](https://docs.astral.sh/uv/) is installed; after upgrading `logfire`, run `make skills`. To add another bundled skill, run `VIRTUAL_ENV=$PWD/.venv uvx library-skills --claude --skill NAME`; `uvx library-skills list --claude` shows what the installed packages offer.

Two more skills are copied into both directories as ordinary files rather than linked, so they work before setup: `library-skills`, which tells agents how to check and repair the links above, and `frontend-design` (Apache 2.0, with its `LICENSE.txt`), visual design guidance from Anthropic's `claude-plugins-official` plugins, used for the README's diagrams and pages. `library-skills` never changes copied skills; to update one, copy it again from its source.

# Local setup for Codex

## 1. Open the repository

Run Codex from the repository root so it automatically receives `AGENTS.md` instructions.

The v5 graph remains frozen; the v6 local lab commands are available. See `CODEX_TASK.md` for the bootstrap acceptance criteria.

## 2. Bootstrap Python

```bash
make setup
make test
```

Or manually:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[all]'
pytest -q
```

## 3. Optional local PostgreSQL

```bash
docker compose up -d postgres
cp .env.example .env
chmod 600 .env
research-db status
research-db migrate
research-db status
```

The Compose credentials are development-only defaults. Change them for any non-local environment.

## 4. Provider credentials

For the first real experiment, start with only the providers you intend to compare. The CLI reads an ignored `.env` in the current directory automatically; exported environment variables take precedence. Do not commit credentials.

Run `research-diagnose` with no credentials first. After setting provider keys and verified `RESEARCH_*_MODEL` overrides, run `research-diagnose --policy quality --smoke` to check the exact routes with bounded provider calls. Defaults are not guaranteed to stay current.

## 5. Synthetic persistence smoke

```bash
research-bench examples/benchmark_cases.json --policies synthetic --repository postgres --max-concurrency 1
```

This uses no provider calls. The output manifest goes to `RESEARCH_BENCHMARK_OUTPUT`; jobs, role tasks, tool events, and effective configuration remain in Postgres after the process exits.

## 6. First paid smoke

```bash
research-diagnose --policy quality --attachments --smoke
research-bench examples/benchmark_suite.toml --policies quality --paid --repository postgres --max-concurrency 1
```

A failed smoke check reports a short, redacted reason. A credit balance failure needs funding on that provider account; HTTP 403 needs account or model access checked. A model appearing in a provider's model list does not prove inference access. Wait for smoke checks to pass before a paid benchmark.

The default benchmark tool mode is normalized for comparable runs. Keep protected benchmark inputs and credentials out of logs and version control.

## Optional Logfire tracing

Install `pip install -e ".[observability]"`. Set `RESEARCH_LOGFIRE_ENABLED=true` and `LOGFIRE_TOKEN` in the ignored `.env`, or run `logfire auth` followed by `logfire projects use <project-name>` to save local project credentials. Benchmark and `research-diagnose --smoke` commands instrument PydanticAI with prompt, completion, tool argument/result, and binary content capture disabled. The isolated Logfire tracer leaves Pydantic Evals case spans unexported. Without an enabled setting, tracing is off; without a token, the SDK does not export traces to Logfire. Keep the write token out of version control. After project selection, restrict local credentials with `chmod 700 .logfire` and `chmod 600 .logfire/*.json`.

## Scholarly acquisition

Install `pip install -e '.[scholarly]'` to enable Trafilatura web extraction and pypdf PDF fallback. Run `research-diagnose --scholar-live` to probe public scholarly endpoints without paid model calls. Optional `.env` settings: `OPENALEX_API_KEY`, `CROSSREF_MAILTO`, `RESEARCH_SCHOLAR_CACHE_MODE=live`, and `GROBID_URL=http://127.0.0.1:8070` for a local GROBID service. Benchmarks override the cache mode to `off` for comparable runs. See [SCHOLAR.md](SCHOLAR.md).

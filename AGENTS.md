# Repository instructions

This repository implements a benchmarkable, graph-backed PydanticAI research loop.

## Architectural boundaries

Preserve these ownership rules unless the task explicitly changes them:

- PydanticAI Agent owns model/tool semantics.
- Pydantic Graph owns workflow topology only.
- `Run` owns one execution lifecycle.
- `ModelPolicy` owns model selection and budgets.
- `EvidenceLedger` owns in-run research evidence.
- Postgres owns durable history and telemetry.
- CLI owns presentation.

Keep `ResearchGraphState` small and control-plane-only. Do not put the evidence ledger or mutable branch results in shared graph state. Parallel workers return typed values; serial join/record steps update evidence.

Do not add DBOS, Temporal, Redis, a vector database, event sourcing, a learned router, or new agent roles unless the task explicitly requires them.

## Compatibility

- Preserve the public `ResearchLoop.run(...)` contract.
- Keep `LegacyResearchLoop` until graph/legacy parity is intentionally retired.
- Treat `research-graph-v1` as frozen during benchmark work unless the task is specifically about topology.
- Model IDs in policy defaults may be stale or placeholders. Verify provider availability before paid runs; prefer configuration over hard-coding new IDs.

## Validation

For code changes, run the narrowest relevant tests first, then `pytest -q` when practical. Tests must stay offline: `tests/conftest.py` refuses model-provider requests and fails any test that reaches a non-loopback host, so script models with `FunctionModel` and serve fetches with the `serve` and `public_urls` fixtures. Keep benchmark inputs and secrets out of logs. Never commit `.env` or API keys.

Graph and legacy orchestrators share role calls, prompts, and job lifecycle through `AsyncResearchLoop`. Change a prompt or role call there, not in a graph step, and treat any change to a model-visible prompt as a behavior change.

## Documentation

`README.md` is the entry point. Update the matching document with a change: `docs/graph.md` for topology, `docs/acquisition.md` for web and scholarly tools, `docs/attachments.md` for file ingestion, `docs/benchmarks.md` for benchmark semantics and comparability, `docs/setup.md` for configuration and environment variables, and `campaigns/long_horizon_agentic_se/README.md` for campaign behavior. `docs/architecture-review.md` is a dated review; add status notes rather than rewriting its findings.

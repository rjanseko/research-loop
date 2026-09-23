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

For code changes, run the narrowest relevant tests first, then `pytest -q` when practical. Keep benchmark inputs and secrets out of logs. Never commit `.env` or API keys.

Use `GRAPH.md` for topology changes, `ATTACHMENTS.md` for file-ingestion changes, and `BENCHMARKS.md` for benchmark semantics.

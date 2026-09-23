# Codex task: v6 Research Lab Bootstrap

## Objective

Turn the frozen v5 architecture into an operational local research lab without changing the research algorithm.

The desired endpoint is:

```text
v5 architecture (frozen)
        ↓
v6 operational readiness
        ├── configuration / secrets bootstrap
        ├── research-doctor
        ├── Postgres-backed benchmark CLI
        ├── migration/status CLI
        ├── model capability smoke tests
        └── reproducible experiment manifests
        ↓
first paid smoke run
        ↓
small scout tournament
```

## Required work

### 1. Settings / environment

Add one typed settings layer for:

- `DATABASE_URL`
- provider API keys / provider enablement
- model-route overrides
- benchmark cache/output directories
- safe experiment defaults

`.env.example` exists as a template. Loading a local `.env` is acceptable for development, but environment variables must remain the source of truth and secrets must never be logged.

### 2. `research-doctor`

Add a CLI command that reports actionable pass/fail/warn checks for:

- Python/package/runtime compatibility
- required optional dependencies for the requested mode
- outbound web access
- benchmark cache/output writability
- database connectivity
- migration status
- configured provider credentials
- configured model IDs
- cheap model smoke invocation
- structured-output capability required by the assigned role
- tool-calling capability when the role requires tools
- attachment/multimodal capability when configured

Doctor should be useful with zero credentials: unavailable providers should be reported as skipped/warned, not crash the command.

Do not perform expensive benchmark calls from doctor.

### 3. Persistent benchmark execution

Extend `research-bench` so a user can select Postgres persistence using `DATABASE_URL` rather than editing Python. Preserve in-memory mode for tests and disposable runs.

Every persisted experiment must include at least:

- graph version
- policy name
- effective route/model settings
- benchmark/source/case ID
- start/end timestamps
- task/tool telemetry already captured by the repository

### 4. Database bootstrap

Add CLI support equivalent to:

```bash
research-db status
research-db migrate
```

Reuse the existing SQL migrations rather than inventing an ORM migration framework.

### 5. Provider/model smoke tests

Before benchmark campaigns, provide a cheap way to validate the exact model routes configured for a policy. Do not assume policy default model IDs are current. Surface unsupported or unavailable model IDs clearly.

### 6. Experiment reproducibility

Write an experiment manifest/report containing:

- graph version
- policy snapshot
- benchmark manifest
- attachment mode
- normalized/adaptive tool mode
- relevant package versions
- start time
- run IDs

Do not include API keys, database passwords, local host paths, or raw protected benchmark inputs.

## Non-goals for v6

Do not add:

- new graph topology
- new agent roles
- a learned model router
- DBOS / Temporal / durable workflow infrastructure
- Redis
- vector storage
- full observability platform
- UI/dashboard work

## Acceptance criteria

1. `pytest -q` passes.
2. Existing graph/legacy parity remains green.
3. `research-doctor` runs successfully with no API keys and reports skipped providers.
4. `research-doctor` can validate a configured provider/model without exposing credentials.
5. `research-db migrate` initializes an empty Postgres database from the existing migrations.
6. `research-bench` can run in both in-memory and Postgres-backed modes.
7. A persisted synthetic/fake-model experiment can be inspected after process exit.
8. Effective configuration and `research-graph-v1` are captured with each experiment.
9. README contains exact local setup and first-smoke-run commands.

## Suggested execution order

1. Read `README.md` and `GRAPH.md` for boundaries relevant to this task.
2. Run the existing tests before editing.
3. Implement settings + database bootstrap.
4. Implement `research-doctor` with injectable checks so it is testable without network/provider credentials.
5. Wire Postgres persistence into `research-bench`.
6. Add experiment manifest capture.
7. Add/extend tests.
8. Update README with the shortest path from clone → setup → doctor → database → synthetic run → paid smoke run.

Prefer small, reviewable changes. Preserve the existing architecture rather than introducing a new framework.

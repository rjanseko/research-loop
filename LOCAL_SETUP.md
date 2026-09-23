# Local setup for Codex

## 1. Open the repository

Run Codex from the repository root so it automatically receives `AGENTS.md` instructions.

The current implementation is v5. The next scoped task is in `CODEX_TASK.md`.

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
set -a; source .env; set +a
./scripts/db_migrate.sh
```

The Compose credentials are development-only defaults. Change them for any non-local environment.

## 4. Provider credentials

For the first real experiment, start with only the providers you intend to compare. Keep credentials in the environment or an ignored `.env` file. Do not commit them.

Before paid benchmark runs, verify the exact model IDs configured in `src/research_loop/policy.py` or through route overrides; defaults are not guaranteed to stay current.

## 5. Suggested first Codex prompt

```text
Read AGENTS.md and CODEX_TASK.md. Implement the v6 Research Lab Bootstrap milestone. Preserve research-graph-v1 and the public ResearchLoop.run contract. Run the existing test suite before editing, work incrementally, and finish by running pytest -q and summarizing any remaining blockers for a real paid smoke test.
```

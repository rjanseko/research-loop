# Repository instructions

This repository implements Scout, a benchmarkable PydanticAI research workflow: a planner splits a question into research questions, parallel scouts research them, code checks their evidence, and a synthesizer writes a cited report. An opt-in follow-up adds one gap analysis and one deep dive before synthesis.

## Architectural boundaries

Preserve these ownership rules unless the task explicitly changes them:

- PydanticAI agents own model and tool semantics. The agents, their output validators, and their instructions live in `agents.py` and `prompts.py`.
- `scout.py` owns the workflow: the order of steps, the money and time allocated to each call, and what a failed call leaves behind. `_Run` owns one execution lifecycle, from recording the run to finishing it.
- `config.py` owns settings: models per role, limits, and provider rate limits. `models.py` builds models from them.
- `EvidenceLedger` in `evidence.py` owns a run's research evidence, and code in `evidence.py` sets every quote, source, and access mark. A model never sets them.
- `budget_notes.py` owns a scout's loop budget; `study_budget.py` owns hard dollar caps; `rate_limit.py` owns rate-limit retries and pacing.
- Postgres owns durable history (`store.py`, `migrations/`), and Logfire owns traces.
- The CLI and `render.py` own presentation.

Parallel scouts return typed `ResearchResult` values and never touch the ledger; the run adds their results to the ledger after they finish. The synthesizer sees only the checked ledger, never raw tool output. Keep it that way.

Do not add DBOS, Temporal, Redis, a vector database, event sourcing, a learned router, or new agent roles unless the task explicitly requires them. The roles are the planner, scout, gap analyzer, and synthesizer; a deep dive is a scout call. The long-horizon mode is deferred, not planned for any current task.

## Compatibility

- Keep the `research` command and `scout(...)` in `scout.py` working as the README documents them.
- Treat workflow `scout-v1` and its prompt fingerprint as frozen during study work unless the task is specifically about the workflow. Follow-up (`scout-followup-v1`), fixed-plan rescouts (`scout-research-v1`), and fixed-ledger synthesis (`scout-synthesis-v1`) carry their own versions. Change a version when its behavior changes.
- Do not edit a frozen case in `study_cases.jsonl`, a packet in `quality_packets.jsonl`, or the grading judge's prompt and verdict rules without bumping its version. Stored grades are compared by those versions.
- When a change alters the budget guard or rate-limit behavior, bump `BUDGET_POLICY_VERSION` or `RATE_LIMIT_POLICY_VERSION`, since runs record them.
- Model IDs in the defaults may be stale. Verify them with `research doctor --smoke` before paid runs, and prefer configuration over hard-coding new IDs.

## Paid runs

Every command that calls a model costs money: `scout`, `synthesize`, `rescout`, `grade`, `assess`, and `doctor --smoke`. Get the user's approval before each paid run or batch, with an estimate based on the most expensive comparable call and a hard `--max-usd` cap. A paid comparison must be able to reach a decision: run-to-run variation on the frozen cases is several rubric points, so one run per arm cannot separate small differences. The OpenAI account's gpt-6-luna limit is 200,000 tokens a minute and pacing is per run, so do not run two Luna-scout runs at once.

## Validation

For code changes, run the narrowest relevant tests first, then `pytest -q` when practical. `make lint` runs ruff 0.16 with its default rules; a deliberate blind `except Exception` carries `# noqa: BLE001` and the reason. Tests must stay offline: `tests/conftest.py` refuses model-provider requests and fails any test that reaches a non-loopback host, so script models with `FunctionModel` and serve fetches with the `serve` and `public_urls` fixtures. Keep benchmark inputs and secrets out of logs. Never commit `.env` or API keys.

Change a prompt in `prompts.py` or an agent in `agents.py`, not in `scout.py`. Treat any change to text a model sees as a behavior change, including the scouts' budget notes in `budget_notes.py`, which the prompt fingerprint does not cover.

## Documentation

`README.md` is the full guide: setup, configuration, commands, how to read a report, limits, models, the study tools, and troubleshooting. Update it with any change to commands, settings, limits, or outputs, and keep its Mermaid diagram in step with `scout.py`. Record every graded or paid study run, with its run IDs, costs, and what it showed, in `docs/quality-calibration.md` or `docs/high-level-study-evaluation.md`. `docs/lessons.md` records what the first design learned; add to it rather than rewriting it. The first design's code is at the git tag `archive/pre-scout-2026-09`.

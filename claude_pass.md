# Claude Code handoff: research-loop

Work from `/home/ryan/workspace/research-loop`. Start by reading `AGENTS.md`. The user-supplied roadmap is at `/home/ryan/Downloads/research_loop_plan_summary.md`; treat it as project context, with the user's current request and `AGENTS.md` taking precedence. This handoff contains no secrets or source excerpts.

## Goal and current state

The repository is a benchmarkable, graph-backed PydanticAI research loop. We have prepared its local research lab and the first campaign, **State of the Art in Long-Horizon Agentic Software Engineering, 2024–2026**. The campaign has not made a paid model run yet. Its full report, catalogs, and evidence-tested hypotheses are still pending.

There are many **uncommitted changes** from this work and earlier sessions. Preserve them. Do not reset, clean, or overwrite the working tree. In particular, `.env` and `.logfire/` are local ignored credentials; do not display, copy, commit, or include their contents in prompts, logs, or reports.

Key implementation areas:

- `src/research_loop/scholar.py`: provider-neutral search/get/references/citations/fetch over OpenAlex, Crossref, arXiv, ACL Anthology, and OpenCitations. Preprint and publisher records remain distinct. OpenReview is explicitly disabled. Optional local GROBID PDF extraction falls back to pypdf.
- `src/research_loop/web.py`: normalized HTTPS page fetching with Trafilatura, Beautiful Soup fallback, bounded responses, URL checks, and cache modes.
- `src/research_loop/schemas.py`: additive scholarly source provenance and publication status.
- `src/research_loop/telemetry.py` and `repository.py`: raw tool arguments remain in temporary memory for benchmark audits; persisted Postgres tool arguments and fetched results are hashed or reduced to IDs/counts.
- `src/research_loop/diagnose.py`, `db.py`, `experiment.py`, `benchmark.py`, `settings.py`: local preflight, migration CLI, persistent benchmarks, and sanitized manifests. `research-diagnose --scholar-live` probes public metadata endpoints without model calls; `--live`/`--smoke` makes small paid model calls.
- `src/research_loop/campaign.py` and `campaigns/long_horizon_agentic_se/campaign.toml`: a one-question-at-a-time campaign launcher, fixed source policy, run limits, and output manifest. `research-campaign --dry-run` is free; `--paid` performs a live model preflight before a research run. `--persist` writes to Postgres.
- `SCHOLAR.md`, `BENCHMARKS.md`, `LOCAL_SETUP.md`, and `README.md` describe the current behavior.

Architecture constraints: keep PydanticAI responsible for agent/model/tool semantics, Pydantic Graph for topology, `Run` for one execution lifecycle, `ModelPolicy` for routes and budgets, `EvidenceLedger` for in-run evidence, Postgres for durable history, and CLI for presentation. Keep `ResearchGraphState` small. Preserve `ResearchLoop.run(...)`, `LegacyResearchLoop`, and frozen `research-graph-v1` topology. Do not add new agent roles or infrastructure such as DBOS, Redis, or a vector database.

## Validation already completed

- `pytest -q`: **47 passed** at the last full run.
- `research-diagnose --scholar-live`: all five scholarly endpoints responded; Postgres connectivity and migrations passed. Model routes remained unverified without paid smoke.
- `scripts/scholar_pilot.py`: free metadata pilot returned 10 records from OpenAlex, Crossref, arXiv, and ACL Anthology, plus three OpenCitations citation IDs, with no endpoint errors. Output is ignored under `benchmark_outputs/scholar_pilot.json`.
- `research-bench examples/benchmark_cases.json --policies synthetic --persist --max-cases 1 --max-concurrency 1`: passed and wrote a completed manifest with no model calls.
- `research-campaign --dry-run`: validates the campaign and selects `q01` without model calls.

## Good next pass

1. Inspect `git status --short`, then read the modules and tests above. Review for concrete correctness, provider API, security, cache, telemetry, and budget issues. The first independent architecture review specifically called out preprint/version conflation, cache contamination, full-text telemetry, unsafe fetching, and unbounded fan-out; these were addressed in part, so verify the implementation rather than assuming the risks are gone.
2. Make small, high-confidence fixes with focused tests if you find defects. Preserve benchmark audit behavior and the graph/legacy parity contract. Run the narrow tests, then `pytest -q` when practical.
3. Report what you changed, what you tested, and remaining risks. In particular, note that DNS validation before fetch does not by itself eliminate DNS rebinding without an egress firewall; OpenReview remains disabled; and the campaign catalogs/hypotheses require completed research and synthesis.

Avoid paid model calls or the full campaign until the user has confirmed account credit and provider spending caps. The earlier model smoke encountered insufficient credits. The intended split was roughly $10 each for OpenAI, Anthropic, and Z.AI; xAI and OpenRouter are not required. The campaign CLI defaults to one question and performs a paid model-access preflight before research calls. Once funded and requested by the user, the first command is `research-campaign --question q01 --paid --persist`.

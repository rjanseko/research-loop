"""Reproducible, bounded launcher for a research campaign question."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import tomllib
from contextlib import AsyncExitStack
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .async_orchestrator import ResearchConfig
from .db import migration_files, migration_status
from .diagnose import run_diagnose
from .experiment import _fingerprint, _git_state, _packages, _safe_value, write_manifest
from .observability import configure_logfire
from .orchestrator import ResearchLoop
from .policy import get_policy
from .repository import InMemoryResearchRepository, PostgresResearchRepository
from .schemas import ResearchConstraints, ResearchRole
from .settings import ResearchSettings
from .tools import ResearchToolMode


CAMPAIGN_FILE = Path(__file__).resolve().parents[2] / "campaigns" / "long_horizon_agentic_se" / "campaign.toml"


def load_campaign(path: Path) -> dict[str, Any]:
    campaign = tomllib.loads(path.read_text(encoding="utf-8"))
    if campaign.get("graph_version") != "research-graph-v1":
        raise ValueError("campaign requires research-graph-v1")
    questions = campaign.get("questions") or []
    ids = [item.get("id") for item in questions]
    if not ids or any(not isinstance(item, str) or not item for item in ids) or len(ids) != len(set(ids)) or any(not item.get("text") for item in questions):
        raise ValueError("campaign needs unique question IDs and nonempty question text")
    return campaign


def render_objective(campaign: dict[str, Any], question: dict[str, str]) -> str:
    source_policy = campaign["source_policy"]
    return "\n".join([
        campaign["title"],
        f"Question {question['id']}: {question['text']}",
        f"Publication window: {campaign['period_start']} to {campaign['period_end']}.",
        "Source policy:",
        *[f"{key}: {value}" for key, value in source_policy.items()],
        "Keep preprints, submissions, reviews, and published versions distinct. Report uncertainty and contradictions.",
    ])


def _check_database(dsn: str) -> None:
    import psycopg
    with psycopg.connect(dsn, autocommit=True, connect_timeout=5) as conn:
        pending = [m.name for m, state in migration_status(conn, migration_files()) if state != "applied"]
    if pending:
        raise RuntimeError("database migrations pending or changed; run research-db migrate")


async def run_campaign(
    path: Path,
    *,
    question_ids: list[str],
    policy_name: str,
    settings: ResearchSettings,
    output_dir: Path,
    persist: bool,
) -> Path:
    campaign = load_campaign(path)
    questions = {item["id"]: item for item in campaign["questions"]}
    unknown = set(question_ids) - set(questions)
    if unknown:
        raise ValueError(f"unknown campaign question IDs: {', '.join(sorted(unknown))}")
    if persist and not settings.database_dsn:
        raise ValueError("DATABASE_URL required for --persist")
    configure_logfire(settings)
    policy = get_policy(policy_name, model_overrides=settings.model_overrides)
    execution = campaign["execution"]
    policy.planner_question_range = (
        int(execution["planner_question_min"]), int(execution["planner_question_max"])
    )
    deep_route = policy.routes[ResearchRole.DEEP_DIVE]
    policy.routes[ResearchRole.DEEP_DIVE] = replace(
        deep_route,
        cost_limit=min(deep_route.cost_limit or float("inf"), float(execution["deep_dive_cost_limit_usd"])),
    )
    run_config = ResearchConfig(
        tool_mode=ResearchToolMode.NORMALIZED,
        scholarly_cache_mode="record",
        max_parallel_scouts=int(execution["max_parallel_scouts"]),
        max_deep_dives_per_round=int(execution["max_deep_dives_per_round"]),
        max_verification_rounds=int(execution["max_verification_rounds"]),
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "campaign_manifest.json"
    spec_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    policy_snapshot = _safe_value(policy.snapshot())
    acquisition = {"search": "duckduckgo", "web_fetch": "trafilatura+bs4",
                   "scholar": ["openalex", "crossref", "arxiv", "acl", "opencitations"],
                   "cache_mode": "record"}
    manifest = {
        "schema_version": 1, "campaign_id": campaign["id"], "experiment_id": str(uuid4()),
        "campaign_spec_sha256": spec_hash,
        "config_fingerprint": _fingerprint({"spec": spec_hash, "policy": policy_snapshot, "acquisition": acquisition}),
        "git": _git_state(), "graph_version": campaign["graph_version"],
        "policy": policy_snapshot, "tool_mode": "normalized",
        "acquisition": acquisition, "packages": _packages(),
        "cache_mode": "record", "persistent": persist,
        "run_limits": {
            "max_parallel_scouts": run_config.max_parallel_scouts,
            "max_deep_dives_per_round": run_config.max_deep_dives_per_round,
            "max_verification_rounds": run_config.max_verification_rounds,
        },
        "started_at": datetime.now(UTC).isoformat(), "finished_at": None,
        "status": "running", "questions": [],
    }
    write_manifest(manifest_path, manifest)
    try:
        async with AsyncExitStack() as stack:
            pool = None
            if persist:
                await asyncio.to_thread(_check_database, settings.database_dsn)
                from psycopg_pool import AsyncConnectionPool
                pool = await stack.enter_async_context(
                    AsyncConnectionPool(conninfo=settings.database_dsn, open=False)
                )
            for question_id in question_ids:
                backend = PostgresResearchRepository(pool) if pool else InMemoryResearchRepository()
                loop = ResearchLoop(
                    policy,
                    run_config,
                    repository=backend,
                )
                question = questions[question_id]
                outcome = await loop.run(
                    render_objective(campaign, question),
                    constraints=ResearchConstraints(notes=[
                        "Use primary benchmark papers and official repositories for benchmark claims.",
                        "Label publication status and include DOI/arXiv/OpenAlex/ACL IDs when available.",
                    ]),
                )
                folder = output_dir / question_id
                folder.mkdir(parents=True, exist_ok=True)
                (folder / "report.md").write_text(outcome.report.answer + "\n", encoding="utf-8")
                (folder / "evidence_ledger.json").write_text(json.dumps({
                    key: [item.model_dump(mode="json") for item in values]
                    for key, values in outcome.ledger.results.items()
                }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
                bibliography = list({
                    json.dumps(source.model_dump(mode="json"), sort_keys=True): source.model_dump(mode="json")
                    for source in outcome.ledger.sources()
                }.values())
                (folder / "bibliography.json").write_text(json.dumps(bibliography, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
                (folder / "verification.json").write_text(outcome.verification.model_dump_json(indent=2) + "\n", encoding="utf-8")
                manifest["questions"].append({
                    "id": question_id, "job_id": str(outcome.job_id),
                    "claim_count": outcome.ledger.claim_count(),
                    "source_count": len(bibliography), "status": "completed",
                })
                write_manifest(manifest_path, manifest)
        manifest["status"] = "completed"
    except Exception:
        manifest["status"] = "failed"
        raise
    finally:
        manifest["finished_at"] = datetime.now(UTC).isoformat()
        write_manifest(manifest_path, manifest)
    return manifest_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one bounded long-horizon research campaign question")
    parser.add_argument("--spec", type=Path, default=CAMPAIGN_FILE)
    parser.add_argument("--question", default=None, help="Question ID; defaults to the first question")
    parser.add_argument("--all-questions", action="store_true", help="Run every question sequentially")
    parser.add_argument("--policy", choices=("quality", "breadth", "glm-heavy"), default="quality")
    parser.add_argument("--paid", action="store_true", help="Authorize model provider calls")
    parser.add_argument("--dry-run", action="store_true", help="Validate and print selected question IDs only")
    parser.add_argument("--persist", action="store_true", help="Store runs in Postgres")
    parser.add_argument("--output", type=Path, default=Path("benchmark_outputs/long_horizon_campaign"))
    args = parser.parse_args()
    if args.all_questions and args.question:
        parser.error("choose --question or --all-questions")
    try:
        campaign = load_campaign(args.spec)
        ids = [item["id"] for item in campaign["questions"]]
        selected = ids if args.all_questions else [args.question or ids[0]]
        if set(selected) - set(ids):
            raise ValueError("unknown campaign question ID")
        if args.dry_run:
            print(f"Campaign {campaign['id']}: {', '.join(selected)}")
            return
        if not args.paid:
            parser.error("model runs require --paid; use --dry-run to validate without calls")
        settings = ResearchSettings.from_env()
        local_checks = run_diagnose(settings, policy_name=args.policy, smoke=False)
        critical = {"runtime", "dependencies", "graph", "web_tools", "scholar_tools", "cache", "output"}
        if args.persist:
            critical.update({"database", "migrations"})
        if any(check.name in critical and check.status != "PASS" for check in local_checks):
            raise RuntimeError("local preflight failed; run research-diagnose for details")
        checks = run_diagnose(settings, policy_name=args.policy, smoke=True)
        failed_routes = [check.name for check in checks if check.name.startswith("model:") and check.status != "PASS"]
        if failed_routes:
            raise RuntimeError("live model preflight failed; run research-diagnose --live for details")
        manifest = asyncio.run(run_campaign(
            args.spec, question_ids=selected, policy_name=args.policy,
            settings=settings, output_dir=args.output, persist=args.persist,
        ))
    except Exception as exc:
        parser.exit(1, f"Campaign failed ({type(exc).__name__}); check settings and run manifest.\n")
    print(f"Campaign manifest: {manifest}")


if __name__ == "__main__":
    main()

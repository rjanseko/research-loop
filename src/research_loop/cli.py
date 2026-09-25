"""The `research` command: run Scout, show a stored run, check the setup, and manage the database."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import ValidationError

from .config import Settings


def _settings(parser: argparse.ArgumentParser) -> Settings:
    try:
        return Settings()
    except ValidationError as exc:
        parser.exit(2, f"Configuration is invalid:\n{exc}\n")


def _record_from_row(row: dict[str, Any]) -> dict[str, Any]:
    seconds = (row["finished_at"] - row["started_at"]).total_seconds() if row.get("finished_at") else None
    return {"run_id": str(row["id"]), "question": row["question"], "status": row["status"], "plan": row["plan"],
            "report": row["report"], "ledger": row["ledger"] or {}, "checks": row["checks"] or {},
            "cost_usd": row["cost_usd"], "seconds": seconds, "trace_id": row["trace_id"], "config": row["config"],
            "error": row["error"]}


async def _scout(args: argparse.Namespace, settings: Settings) -> int:
    from .db import open_migrated_pool
    from .render import render_markdown
    from .scout import ConfigError, check_config, scout
    from .store import MemoryStore, PostgresStore
    from .telemetry import configure_logfire

    try:
        check_config(settings)
    except ConfigError as exc:
        print(f"Cannot run: {exc}", file=sys.stderr)
        return 2
    configure_logfire(settings)
    limits, models = settings.limits, settings.models
    print(f"Scout: up to ${limits.cost_usd:.2f} and {limits.deadline_seconds / 60:.0f} minutes; planner {models.planner}, "
          f"scouts {models.scout}, synthesizer {models.synthesizer}.", file=sys.stderr)
    async with AsyncExitStack() as stack:
        if settings.database_dsn and not args.no_persist:
            store: Any = PostgresStore(await open_migrated_pool(stack, settings.database_dsn))
        else:
            store = MemoryStore()
        run = await scout(args.question, settings=settings, store=store, notes=args.note, blocked_urls=args.block)
    record = run.to_record()
    markdown = render_markdown(record)
    print(markdown)
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "report.md").write_text(markdown, encoding="utf-8")
        (args.out / "run.json").write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Wrote {args.out / 'report.md'} and {args.out / 'run.json'}", file=sys.stderr)
    stored = "stored" if isinstance(store, PostgresStore) else "not stored (no DATABASE_URL, or --no-persist)"
    print(f"Run {run.run_id}: {run.status}, ${run.cost_usd:.2f}, {run.seconds / 60:.1f} minutes, {stored}.",
          file=sys.stderr)
    return 1 if run.status == "failed" else 0


async def _show(args: argparse.Namespace, settings: Settings) -> int:
    from .db import open_migrated_pool
    from .render import render_markdown
    from .store import load_run

    async with AsyncExitStack() as stack:
        row = await load_run(await open_migrated_pool(stack, settings.database_dsn), args.run_id)
    if row is None:
        print(f"No run {args.run_id}", file=sys.stderr)
        return 1
    record = _record_from_row(row)
    print(json.dumps(record, indent=2, ensure_ascii=False, default=str) if args.format == "json" else render_markdown(record))
    return 0


def _db(args: argparse.Namespace, settings: Settings, parser: argparse.ArgumentParser) -> int:
    import psycopg

    from .db import apply_migrations, migration_files, migration_status, reconcile

    migrations = migration_files()
    try:
        with psycopg.connect(settings.database_dsn, autocommit=True, connect_timeout=5) as conn:
            if args.db_command == "reconcile":
                runs, calls = reconcile(conn, args.older_than, apply=args.apply)
                verb = "Marked" if args.apply else "Would mark"
                print(f"{verb} {runs} run(s) and {calls} call(s) failed (Abandoned)."
                      + ("" if args.apply else " Pass --apply to do it."))
                return 0
            if args.db_command == "migrate":
                print(f"Applied {len(apply_migrations(conn, migrations))} migration(s).")
            for migration, state in migration_status(conn, migrations):
                print(f"{state:7} {migration.name}")
    except psycopg.Error as exc:
        parser.exit(1, f"Database connection or migration failed ({type(exc).__name__}). Check DATABASE_URL.\n")
    except RuntimeError as exc:
        parser.exit(1, f"{exc}\n")
    return 0


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="research", description="Cited answers to research questions")
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("scout", help="Research a question and print a cited answer (calls paid models)")
    run.add_argument("question")
    run.add_argument("--note", action="append", default=[], help="A requirement every role follows; repeat for more")
    run.add_argument("--block", action="append", default=[], metavar="URL",
                     help="A source no tool may fetch and no evidence may cite; repeat for more")
    run.add_argument("--out", type=Path, help="Also write report.md and run.json here")
    run.add_argument("--no-persist", action="store_true", help="Keep the run in memory even when DATABASE_URL is set")

    show = commands.add_parser("show", help="Render a stored run")
    show.add_argument("run_id", type=UUID)
    show.add_argument("--format", choices=("md", "json"), default="md")

    doctor = commands.add_parser("doctor", help="Check keys, prices, the database, and the network")
    doctor.add_argument("--smoke", action="store_true", help="Also make one small paid call per configured model")

    db = commands.add_parser("db", help="Apply migrations, show their state, or close out abandoned runs")
    db.add_argument("db_command", choices=("status", "migrate", "reconcile"))
    db.add_argument("--older-than", type=float, metavar="MINUTES",
                    help="reconcile: runs still running this long after they started count as abandoned")
    db.add_argument("--apply", action="store_true", help="reconcile: make the changes instead of counting them")

    args = parser.parse_args(argv)
    settings = _settings(parser)
    if args.command in ("show", "db") and not settings.database_dsn:
        parser.error("this command needs DATABASE_URL; see docs/setup.md")
    if args.command == "db" and args.db_command == "reconcile" and not (args.older_than and args.older_than > 0):
        parser.error("reconcile needs --older-than MINUTES, longer than any run still in progress")
    try:
        if args.command == "scout":
            code = asyncio.run(_scout(args, settings))
        elif args.command == "show":
            code = asyncio.run(_show(args, settings))
        elif args.command == "doctor":
            from .doctor import run_doctor
            code = asyncio.run(run_doctor(settings, smoke=args.smoke))
        else:
            code = _db(args, settings, parser)
    except KeyboardInterrupt:
        print("Interrupted; the run is recorded as cancelled.", file=sys.stderr)
        code = 130
    sys.exit(code)


if __name__ == "__main__":
    main()

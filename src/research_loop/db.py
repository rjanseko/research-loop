from __future__ import annotations

import argparse
import asyncio
import hashlib
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .settings import ResearchSettings


MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


@dataclass(frozen=True)
class Migration:
    name: str
    path: Path
    sha256: str


def migration_files(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    files = sorted(directory.glob("[0-9][0-9][0-9]_*.sql"))
    if not files:
        raise RuntimeError(f"no SQL migrations found in {directory}")
    return [
        Migration(path.name, path, hashlib.sha256(path.read_bytes()).hexdigest())
        for path in files
    ]


def migration_status(conn: Any, migrations: list[Migration]) -> list[tuple[Migration, str]]:
    exists = conn.execute("select to_regclass('research_schema_migrations')").fetchone()[0]
    applied: dict[str, str] = {}
    if exists:
        applied = dict(conn.execute("select name, sha256 from research_schema_migrations").fetchall())
    result = []
    for migration in migrations:
        saved = applied.get(migration.name)
        state = "pending" if saved is None else "applied" if saved == migration.sha256 else "changed"
        result.append((migration, state))
    return result


def pending_migrations(dsn: str, *, connect_timeout: int = 5) -> list[str]:
    """Names of SQL migrations the database at `dsn` has not applied, or applied in another version."""
    import psycopg

    with psycopg.connect(dsn, autocommit=True, connect_timeout=connect_timeout) as conn:
        return [migration.name for migration, state in migration_status(conn, migration_files()) if state != "applied"]


async def open_migrated_pool(stack: AsyncExitStack, dsn: str) -> Any:
    """Open a connection pool on `stack`; fail before any paid model call if migrations are pending."""
    if await asyncio.to_thread(pending_migrations, dsn):
        raise RuntimeError("database migrations are pending or changed; run research-db migrate")
    from psycopg_pool import AsyncConnectionPool

    return await stack.enter_async_context(AsyncConnectionPool(conninfo=dsn, open=False))


def apply_migrations(conn: Any, migrations: list[Migration]) -> list[str]:
    with conn.transaction():
        conn.execute(
            """create table if not exists research_schema_migrations (
                name text primary key,
                sha256 text not null,
                applied_at timestamptz not null default now()
            )"""
        )
    states = migration_status(conn, migrations)
    changed = [migration.name for migration, state in states if state == "changed"]
    if changed:
        raise RuntimeError(f"applied SQL migration changed: {', '.join(changed)}")
    applied = []
    for migration, state in states:
        if state == "applied":
            continue
        with conn.transaction():
            conn.execute(migration.path.read_text(encoding="utf-8"))
            conn.execute(
                "insert into research_schema_migrations (name, sha256) values (%s, %s)",
                (migration.name, migration.sha256),
            )
        applied.append(migration.name)
    return applied


_STALE_JOB = "status = 'running' and created_at < now() - make_interval(secs => %s)"
_ABANDONED = {"type": "Abandoned",
              "detail": "Still running when research-db reconcile ran; the process ended without recording a result."}


def reconcile(conn: Any, older_than_minutes: float, *, apply: bool) -> tuple[int, int]:
    """Close out records a killed process left "running"; return (jobs, tasks) affected.

    Jobs still running `older_than_minutes` after they were created are marked failed, and so are
    running tasks of jobs that are no longer running. finished_at stays empty, since when the
    process died is unknown. Without `apply`, nothing changes and the counts say what would.
    """
    seconds = older_than_minutes * 60
    if not apply:
        jobs = conn.execute(f"select count(*) from research_jobs where {_STALE_JOB}", (seconds,)).fetchone()[0]
        tasks = conn.execute(
            f"""select count(*) from research_tasks t join research_jobs j on j.id = t.job_id
                 where t.status = 'running' and (j.status <> 'running' or j.id in
                       (select id from research_jobs where {_STALE_JOB}))""",
            (seconds,),
        ).fetchone()[0]
        return jobs, tasks
    from psycopg.types.json import Jsonb

    with conn.transaction():
        jobs = conn.execute(f"update research_jobs set status = 'failed', error = %s where {_STALE_JOB}",
                            (Jsonb(_ABANDONED), seconds)).rowcount
        tasks = conn.execute(
            """update research_tasks t set status = 'failed', error = %s from research_jobs j
                where j.id = t.job_id and t.status = 'running' and j.status <> 'running'""",
            (Jsonb(_ABANDONED),),
        ).rowcount
    return jobs, tasks


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage research-loop SQL migrations and stale records")
    parser.add_argument("command", choices=("status", "migrate", "reconcile"))
    parser.add_argument("--older-than", type=float, metavar="MINUTES",
                        help="reconcile: jobs still running this long after they started count as abandoned")
    parser.add_argument("--apply", action="store_true", help="reconcile: make the changes instead of listing counts")
    args = parser.parse_args()
    if args.command == "reconcile" and not (args.older_than and args.older_than > 0):
        parser.error("reconcile needs --older-than MINUTES, longer than any run still in progress")
    settings = ResearchSettings.from_env()
    if not settings.database_dsn:
        parser.error("DATABASE_URL is required; set it in the environment")

    try:
        import psycopg
    except ImportError:
        parser.error("Postgres support is missing; install the postgres extra")

    migrations = migration_files()
    try:
        with psycopg.connect(settings.database_dsn, autocommit=True, connect_timeout=5) as conn:
            if args.command == "reconcile":
                jobs, tasks = reconcile(conn, args.older_than, apply=args.apply)
                if args.apply:
                    print(f"Marked {jobs} job(s) and {tasks} task(s) failed (Abandoned).")
                else:
                    print(f"Would mark {jobs} job(s) and {tasks} task(s) failed; pass --apply to do it.")
                return
            if args.command == "migrate":
                applied = apply_migrations(conn, migrations)
                print(f"Applied {len(applied)} migration(s).")
            for migration, state in migration_status(conn, migrations):
                print(f"{state:7} {migration.name}")
    except psycopg.Error as exc:
        parser.exit(1, f"Database connection or migration failed ({type(exc).__name__}). Check DATABASE_URL and server status.\n")
    except RuntimeError as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()

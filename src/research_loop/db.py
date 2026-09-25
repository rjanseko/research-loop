from __future__ import annotations

import asyncio
import hashlib
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Inside the package, so an installed wheel carries them.
MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


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
        raise RuntimeError("database migrations are pending or changed; run `research db migrate`")
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


_STALE_RUN = "status = 'running' and started_at < now() - make_interval(secs => %s)"
_ABANDONED = {"type": "Abandoned",
              "message": "Still running when reconcile ran; the process ended without recording a result."}


def reconcile(conn: Any, older_than_minutes: float, *, apply: bool) -> tuple[int, int]:
    """Close out records a killed process left "running"; return (runs, calls) affected.

    Runs still running `older_than_minutes` after they started are marked failed, and so are running
    calls of runs that are no longer running. finished_at stays empty, since when the process died
    is unknown. Without `apply`, nothing changes and the counts say what would.
    """
    seconds = older_than_minutes * 60
    stale_calls = f"""c.status = 'running' and (r.status <> 'running' or r.id in
                      (select id from runs where {_STALE_RUN}))"""
    if not apply:
        runs = conn.execute(f"select count(*) from runs where {_STALE_RUN}", (seconds,)).fetchone()[0]
        calls = conn.execute(f"select count(*) from run_calls c join runs r on r.id = c.run_id where {stale_calls}",
                             (seconds,)).fetchone()[0]
        return runs, calls
    from psycopg.types.json import Jsonb

    with conn.transaction():
        calls = conn.execute(
            f"update run_calls c set status = 'failed', error = %s from runs r where r.id = c.run_id and {stale_calls}",
            (Jsonb(_ABANDONED), seconds)).rowcount
        runs = conn.execute(f"update runs set status = 'failed', error = %s where {_STALE_RUN}",
                            (Jsonb(_ABANDONED), seconds)).rowcount
    return runs, calls

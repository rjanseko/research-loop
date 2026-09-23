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


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage research-loop SQL migrations")
    parser.add_argument("command", choices=("status", "migrate"))
    args = parser.parse_args()
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

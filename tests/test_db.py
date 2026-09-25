from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path

import pytest

from research_loop.db import apply_migrations, migration_files, migration_status


class _Cursor:
    def __init__(self, row=None, rows=None):
        self.row = row
        self.rows = rows or []

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self):
        self.applied = {}
        self.tracking_exists = False
        self.sql = []

    def transaction(self):
        return nullcontext()

    def execute(self, query, params=None):
        self.sql.append(query)
        if query.startswith("select to_regclass"):
            return _Cursor(row=("research_schema_migrations" if self.tracking_exists else None,))
        if query.startswith("select name, sha256"):
            return _Cursor(rows=list(self.applied.items()))
        if query.startswith("create table if not exists research_schema_migrations"):
            self.tracking_exists = True
        if query.startswith("insert into research_schema_migrations"):
            self.applied[params[0]] = params[1]
        return _Cursor()


def test_migrations_apply_in_order_and_are_idempotent(tmp_path: Path) -> None:
    (tmp_path / "001_first.sql").write_text("create table first();")
    (tmp_path / "002_second.sql").write_text("create table second();")
    migrations = migration_files(tmp_path)
    conn = FakeConnection()
    assert [state for _, state in migration_status(conn, migrations)] == ["pending", "pending"]
    assert apply_migrations(conn, migrations) == ["001_first.sql", "002_second.sql"]
    assert apply_migrations(conn, migrations) == []
    assert [state for _, state in migration_status(conn, migrations)] == ["applied", "applied"]


def test_migrations_reject_changed_sql(tmp_path: Path) -> None:
    path = tmp_path / "001_first.sql"
    path.write_text("select 1;")
    conn = FakeConnection()
    apply_migrations(conn, migration_files(tmp_path))
    path.write_text("select 2;")
    with pytest.raises(RuntimeError, match="changed"):
        apply_migrations(conn, migration_files(tmp_path))


def test_repository_migrations_are_numbered_in_order() -> None:
    names = [migration.name for migration in migration_files()]
    assert names == sorted(names)
    assert names[:4] == ["001_research.sql", "002_research_attachments.sql", "003_research_evidence.sql",
                         "004_research_task_messages.sql"]


class _ReconcileConnection:
    def __init__(self):
        self.statements: list[tuple[str, bool]] = []
        self.in_transaction = False

    def transaction(self):
        connection = self

        class _Transaction:
            def __enter__(self):
                connection.in_transaction = True

            def __exit__(self, *_):
                connection.in_transaction = False

        return _Transaction()

    def execute(self, query, params=None):
        self.statements.append((" ".join(query.split()).split()[0], self.in_transaction))
        cursor = _Cursor(row=(3,))
        cursor.rowcount = 2
        return cursor


def test_reconcile_only_counts_unless_asked_to_apply() -> None:
    from research_loop.db import reconcile

    conn = _ReconcileConnection()
    assert reconcile(conn, 120, apply=False) == (3, 3)
    assert conn.statements == [("select", False), ("select", False)]

    conn = _ReconcileConnection()
    assert reconcile(conn, 120, apply=True) == (2, 2)
    assert conn.statements == [("update", True), ("update", True)]  # jobs first, then their tasks, in one transaction


def test_reconcile_requires_an_age_threshold(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    import sys

    from research_loop.db import main

    monkeypatch.setattr(sys, "argv", ["research-db", "reconcile"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2
    assert "--older-than" in capsys.readouterr().err

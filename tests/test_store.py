"""Run records. The Postgres tests run only when RESEARCH_TEST_DATABASE_URL names a disposable database
whose name contains "test"; each drops and recreates its public schema. CI provides one; locally:

    docker compose up -d --wait postgres
    docker compose exec postgres createdb -U research research_test
    RESEARCH_TEST_DATABASE_URL=postgresql://research:research@127.0.0.1:5432/research_test pytest -m postgres
"""
from __future__ import annotations

import os
from contextlib import AsyncExitStack
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.usage import RunUsage

from research_loop.store import (
    MESSAGE_MAX_CHARS,
    MemoryStore,
    PostgresStore,
    error_record,
    transcript,
)

DSN_ENV = "RESEARCH_TEST_DATABASE_URL"
MESSAGES = [ModelRequest(parts=[UserPromptPart("research this")]), ModelResponse(parts=[TextPart("a\x00nswer")])]


def test_errors_keep_type_status_and_a_short_message() -> None:
    record = error_record(ModelHTTPError(429, "glm-5.3-flash", body={"error": {"message": "Insufficient balance"}}))
    assert record["type"] == "ModelHTTPError" and record["status_code"] == 429
    assert "Insufficient balance" in record["message"]
    assert len(error_record(RuntimeError("x" * 5000))["message"]) == 1000


def test_transcripts_cut_only_oversized_strings() -> None:
    long = [ModelRequest(parts=[UserPromptPart("y" * (MESSAGE_MAX_CHARS + 10))])]
    content = transcript(long)[0]["parts"][0]["content"]
    assert content.startswith("y" * MESSAGE_MAX_CHARS) and content.endswith("[cut 10 chars]")


async def test_memory_store_keeps_runs_and_calls_like_postgres() -> None:
    store = MemoryStore()
    run_id = uuid4()
    await store.start_run(run_id, mode="scout", workflow_version="scout-v1", question="Q?", config={"limits": {}})
    call_id = await store.start_call(run_id, role="scout", model="zai:glm-5.3-flash", question_id="q1")
    await store.finish_call(call_id, status="succeeded", usage=RunUsage(requests=2, input_tokens=10),
                            cost_usd=Decimal("0.01"), output={"ok": True}, messages=MESSAGES)
    await store.finish_run(run_id, status="complete", cost_usd=Decimal("0.01"), trace_id="ab" * 16)
    assert store.runs[run_id]["status"] == "complete" and store.runs[run_id]["cost_usd"] == 0.01
    call = store.calls[call_id]
    assert (call["status"], call["cost_usd"], call["usage"]["requests"]) == ("succeeded", 0.01, 2)
    assert call["messages"][0]["parts"][0]["content"] == "research this"
    with pytest.raises(TypeError, match="unknown run fields"):
        await store.finish_run(run_id, verdict="fine")


@pytest.fixture
def dsn() -> str:
    if not os.getenv(DSN_ENV):
        pytest.skip(f"set {DSN_ENV} to run Postgres integration tests")
    import psycopg
    from psycopg.conninfo import conninfo_to_dict

    dsn = os.environ[DSN_ENV]
    if "test" not in str(conninfo_to_dict(dsn).get("dbname", "")):
        pytest.fail(f"{DSN_ENV} must name a disposable database with 'test' in its name; tests drop its schema")
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("drop schema public cascade")
        conn.execute("create schema public")
    return dsn


@pytest.mark.postgres
async def test_postgres_store_round_trips_a_run_after_migrating(dsn: str) -> None:
    import psycopg

    from research_loop.db import (
        apply_migrations,
        migration_files,
        open_migrated_pool,
        pending_migrations,
        reconcile,
    )
    from research_loop.store import load_calls, load_run, save_grade

    assert pending_migrations(dsn) == ["001_scout.sql"]
    async with AsyncExitStack() as stack:
        with pytest.raises(RuntimeError, match="research db migrate"):
            await open_migrated_pool(stack, dsn)
    with psycopg.connect(dsn, autocommit=True) as conn:
        assert apply_migrations(conn, migration_files()) == ["001_scout.sql"]

    async with AsyncExitStack() as stack:
        pool = await open_migrated_pool(stack, dsn)
        store = PostgresStore(pool)
        run_id, parent = uuid4(), uuid4()
        await store.start_run(parent, mode="long_horizon", workflow_version="v", question="Study", config={})
        await store.start_run(run_id, mode="scout", workflow_version="scout-v1", question="Q\x00?",
                              config={"models": {"scout": "zai:glm-5.3-flash"}}, parent_run_id=parent,
                              input_hash="ab" * 32, study_id="s1", arm="high", replicate=2)
        call_id = await store.start_call(run_id, role="scout", model="zai:glm-5.3-flash", question_id="q1")
        await store.finish_call(call_id, status="failed", usage=RunUsage(requests=1), messages=MESSAGES,
                                error=RuntimeError("boom"), stop_reason="limit reached", tool_seconds=12.5)
        await store.finish_run(run_id, status="partial", report={"title": "T"}, ledger={"q1": []},
                               checks={"citation_problems": []}, cost_usd=Decimal("0.0123"), trace_id="f" * 32,
                               cache={"mode": "reuse", "by_provider": {}})
        await save_grade(pool, {"id": uuid4(), "run_id": run_id, "case_id": "st05-scaling-table",
                                "judge_model": "openai:gpt-6-sol", "judge_thinking": "high", "judge_version": 2,
                                "rubric_version": "1", "status": "succeeded", "score": 0.5,
                                "points": [{"category": "analysis", "point": 1, "met": True}],
                                "usage": {"requests": 1}, "cost_usd": Decimal("0.02"), "messages": None, "error": None,
                                "budget_cap_usd": Decimal("0.10"), "reserved_usd": Decimal("0.08"),
                                "budget_policy": "byte-reserve-v1"})
        row = await load_run(pool, run_id)
        (call,) = await load_calls(pool, run_id)
    assert (row["status"], row["question"], row["parent_run_id"], row["report"], row["cost_usd"]) == (
        "partial", "Q?", parent, {"title": "T"}, Decimal("0.0123"))
    assert (row["study_id"], row["arm"], row["replicate"], row["cache"]["mode"]) == ("s1", "high", 2, "reuse")
    assert (call["stop_reason"], call["tool_seconds"]) == ("limit reached", Decimal("12.5"))
    with psycopg.connect(dsn, autocommit=True) as conn:
        status, messages, error = conn.execute("select status, messages, error from run_calls").fetchone()
        assert status == "failed" and error["message"] == "boom"
        assert messages[1]["parts"][0]["content"] == "answer"  # NUL removed
        # The study run a killed process left running is closed out.
        assert reconcile(conn, 0, apply=False) == (1, 0)
        assert reconcile(conn, 0, apply=True) == (1, 0)
        assert conn.execute("select status from runs where id = %s", (parent,)).fetchone() == ("failed",)
        assert conn.execute("select case_id, score from grades").fetchone() == ("st05-scaling-table", Decimal("0.5"))

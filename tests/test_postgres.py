"""Integration tests against a real, disposable Postgres.

They run only when RESEARCH_TEST_DATABASE_URL names a database whose name contains "test"; each
test drops and recreates its public schema. CI provides one; locally, for example:

    docker compose up -d --wait postgres
    docker compose exec postgres createdb -U research research_test
    RESEARCH_TEST_DATABASE_URL=postgresql://research:research@127.0.0.1:5432/research_test pytest -m postgres
"""
from __future__ import annotations

import os
from contextlib import AsyncExitStack
from pathlib import Path
from uuid import uuid4

import pytest

DSN_ENV = "RESEARCH_TEST_DATABASE_URL"

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not os.getenv(DSN_ENV), reason=f"set {DSN_ENV} to run Postgres integration tests"),
]


@pytest.fixture
def dsn() -> str:
    import psycopg
    from psycopg.conninfo import conninfo_to_dict

    dsn = os.environ[DSN_ENV]
    if "test" not in str(conninfo_to_dict(dsn).get("dbname", "")):
        pytest.fail(f"{DSN_ENV} must name a disposable database with 'test' in its name; tests drop its schema")
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("drop schema public cascade")
        conn.execute("create schema public")
    return dsn


def _migrate(dsn: str) -> list[str]:
    import psycopg

    from research_loop.db import apply_migrations, migration_files

    with psycopg.connect(dsn, autocommit=True) as conn:
        return apply_migrations(conn, migration_files())


def _rows(dsn: str, query: str, params: tuple = ()) -> list[tuple]:
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as conn:
        return conn.execute(query, params).fetchall()


@pytest.mark.asyncio
async def test_migrations_apply_once_in_order_and_gate_the_pool(dsn: str) -> None:
    from research_loop.db import migration_files, open_migrated_pool, pending_migrations

    names = [migration.name for migration in migration_files()]
    assert pending_migrations(dsn) == names
    async with AsyncExitStack() as stack:
        with pytest.raises(RuntimeError, match="pending"):
            await open_migrated_pool(stack, dsn)

    assert _migrate(dsn) == names
    assert _migrate(dsn) == []
    assert pending_migrations(dsn) == []
    async with AsyncExitStack() as stack:
        pool = await open_migrated_pool(stack, dsn)
        async with pool.connection() as conn:
            assert (await (await conn.execute("select 1")).fetchone()) == (1,)


def test_a_failing_migration_leaves_no_trace(dsn: str, tmp_path: Path) -> None:
    import psycopg

    from research_loop.db import apply_migrations, migration_files

    (tmp_path / "001_ok.sql").write_text("create table ok_table (id int);", encoding="utf-8")
    (tmp_path / "002_broken.sql").write_text("create table half_done (id int); select no_such_column;",
                                             encoding="utf-8")
    with psycopg.connect(dsn, autocommit=True) as conn, pytest.raises(psycopg.Error):
        apply_migrations(conn, migration_files(tmp_path))
    assert _rows(dsn, "select name from research_schema_migrations") == [("001_ok.sql",)]
    assert _rows(dsn, "select to_regclass('half_done')") == [(None,)]  # rolled back with its migration


@pytest.mark.asyncio
async def test_a_synthetic_run_persists_every_record(dsn: str, tmp_path: Path) -> None:
    from research_loop.db import open_migrated_pool
    from research_loop.policy import get_policy
    from research_loop.repository import PostgresResearchRepository
    from research_loop.schemas import ResearchConstraints
    from research_loop.settings import ResearchSettings
    from research_loop.synthetic import SyntheticResearchLoop

    _migrate(dsn)
    notes = tmp_path / "notes.txt"
    notes.write_text("Alpha project uses PostgreSQL for durable history.", encoding="utf-8")
    async with AsyncExitStack() as stack:
        pool = await open_migrated_pool(stack, dsn)
        loop = SyntheticResearchLoop(get_policy("synthetic"), repository=PostgresResearchRepository(pool),
                                     settings=ResearchSettings.from_env({}))
        outcome = await loop.run("Synthetic objective", constraints=ResearchConstraints(attachment_paths=[str(notes)]))

    ((status, plan, report, verification, ledger, reasons, finished),) = _rows(
        dsn, """select status, plan, final_report, verification, evidence_ledger, review_reasons, finished_at
                  from research_jobs where id = %s""", (outcome.job_id,))
    assert status == "succeeded" and finished is not None
    assert plan["questions"] and report["answer"] and verification is not None and reasons is not None
    # The stored ledger's claim IDs are the ones the report cites.
    stored_claims = {claim["id"] for results in ledger.values() for result in results for claim in result["claims"]}
    assert set(outcome.report.claims[0].claim_ids) <= stored_claims

    tasks = _rows(dsn, "select role, status, finished_at from research_tasks where job_id = %s", (outcome.job_id,))
    assert {"planner", "scout", "synthesizer", "verifier"} <= {role for role, _, _ in tasks}
    assert all(state == "succeeded" and done is not None for _, state, done in tasks)
    assert _rows(dsn, """select count(*) from research_tool_events e join research_tasks t on t.id = e.task_id
                          where t.job_id = %s""", (outcome.job_id,))[0][0] > 0
    ((name, sha256),) = _rows(dsn, "select name, sha256 from research_attachments where job_id = %s", (outcome.job_id,))
    assert name == "notes.txt" and sha256 == outcome.attachments.records[0].sha256

    # research-report --job-id renders the stored job as the run itself would.
    from research_loop.render import ReportDocument, _load_job, render_markdown

    stored = ReportDocument.from_record(await _load_job(dsn, outcome.job_id))
    assert stored.review_reasons == outcome.review_reasons and stored.job_id == str(outcome.job_id)
    assert render_markdown(stored).split("## Findings")[1] == render_markdown(
        ReportDocument.from_outcome(outcome)).split("## Findings")[1]


@pytest.mark.asyncio
async def test_failures_are_recorded_and_reconcile_closes_abandoned_runs(dsn: str) -> None:
    import psycopg
    from pydantic_ai import Agent
    from pydantic_ai.exceptions import UsageLimitExceeded

    from research_loop.async_orchestrator import AsyncResearchLoop
    from research_loop.db import open_migrated_pool, reconcile
    from research_loop.policy import ModelPolicy, ModelRoute
    from research_loop.repository import PostgresResearchRepository
    from research_loop.schemas import ResearchRole
    from research_loop.settings import ResearchSettings

    _migrate(dsn)
    route = ModelRoute("test", 1, 5, 10_000)
    agent = Agent(output_type=str)

    @agent.tool_plain
    def lookup() -> str:
        return "value"

    async with AsyncExitStack() as stack:
        repository = PostgresResearchRepository(await open_migrated_pool(stack, dsn))
        loop = AsyncResearchLoop(ModelPolicy("p", {role: route for role in ResearchRole}),
                                 repository=repository, settings=ResearchSettings.from_env({}))
        # TestModel calls the tool first, so the second request trips request_limit=1.
        with pytest.raises(UsageLimitExceeded):
            await loop.run_agent_job("over budget", agent=agent, role=ResearchRole.SYNTHESIZER, route=route, prompt="x")
        # A process killed mid-run leaves its job and task "running".
        abandoned = await repository.create_job(session_id=uuid4(), root_run_id=uuid4(), objective="killed",
                                                policy_name="p", config={})
        await repository.start_task(job_id=abandoned, parent_task_id=None, role=ResearchRole.SCOUT,
                                    question_id="q1", prompt="x", model_id="test", effective_config={})

    ((error, task_status),) = _rows(dsn, """select j.error, t.status from research_jobs j
                                             join research_tasks t on t.job_id = j.id where j.objective = 'over budget'""")
    assert error == {"type": "UsageLimitExceeded"} and task_status == "failed"

    _rows(dsn, "update research_jobs set created_at = now() - interval '2 hours' where id = %s returning id", (abandoned,))
    with psycopg.connect(dsn, autocommit=True) as conn:
        assert reconcile(conn, 60, apply=False) == (1, 1)
        assert reconcile(conn, 60, apply=True) == (1, 1)
        assert reconcile(conn, 60, apply=True) == (0, 0)
    ((status, error),) = _rows(dsn, "select status, error from research_jobs where id = %s", (abandoned,))
    assert status == "failed" and error["type"] == "Abandoned"


@pytest.mark.asyncio
async def test_capture_stores_each_task_transcript_only_when_asked(dsn: str) -> None:
    from pydantic_ai import Agent

    from research_loop.async_orchestrator import AsyncResearchLoop
    from research_loop.db import open_migrated_pool
    from research_loop.policy import ModelPolicy, ModelRoute
    from research_loop.repository import PostgresResearchRepository
    from research_loop.schemas import ResearchRole
    from research_loop.settings import ResearchSettings

    _migrate(dsn)
    route = ModelRoute("test", 5, 5, 10_000)
    agent = Agent(output_type=str)

    @agent.tool_plain
    def lookup() -> str:
        return "the looked-up value"

    async with AsyncExitStack() as stack:
        pool = await open_migrated_pool(stack, dsn)
        for objective, capture in (("captured", True), ("not captured", False)):
            loop = AsyncResearchLoop(ModelPolicy("p", {role: route for role in ResearchRole}),
                                     repository=PostgresResearchRepository(pool, capture_transcripts=capture),
                                     settings=ResearchSettings.from_env({}))
            await loop.run_agent_job(objective, agent=agent, role=ResearchRole.SYNTHESIZER, route=route, prompt="x")

    ((messages, count, cut),) = _rows(dsn, """select m.messages, m.message_count, m.truncated_values
                                              from research_task_messages m join research_tasks t on t.id = m.task_id
                                              join research_jobs j on j.id = t.job_id where j.objective = 'captured'""")
    kinds = [part["part_kind"] for message in messages for part in message["parts"]]
    assert count == len(messages) and cut == 0
    assert "tool-call" in kinds and "tool-return" in kinds
    assert "the looked-up value" in str(messages)  # the real tool result, not a hash
    assert all(message.get("usage") for message in messages if message["kind"] == "response")  # per-response usage
    assert _rows(dsn, """select count(*) from research_task_messages m join research_tasks t on t.id = m.task_id
                         join research_jobs j on j.id = t.job_id where j.objective = 'not captured'""") == [(0,)]

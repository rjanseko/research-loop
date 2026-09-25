from uuid import uuid4

import pytest

from research_loop.repository import (
    CapturingResearchRepository,
    InMemoryResearchRepository,
)
from research_loop.schemas import ResearchRole, ToolEvent


@pytest.mark.asyncio
async def test_inmemory_repository_tracks_task_and_tool_event():
    repo = InMemoryResearchRepository()
    job_id = await repo.create_job(
        session_id=uuid4(),
        root_run_id=uuid4(),
        objective="test",
        policy_name="quality",
        config={},
    )
    task_id = await repo.start_task(
        job_id=job_id,
        parent_task_id=None,
        role=ResearchRole.SCOUT,
        question_id="q1",
        prompt="find evidence",
        model_id="test:model",
        effective_config={},
        attempt=0,
    )
    await repo.record_tool_events(task_id, [ToolEvent(tool_name="web_search")])
    await repo.finish_task(
        task_id,
        status="succeeded",
        output={"ok": True},
        usage={"tool_calls": 1},
        agent_run_id="r1",
        conversation_id="c1",
    )

    assert repo.tasks[task_id]["status"] == "succeeded"
    assert repo.tool_events[0]["tool_name"] == "web_search"


@pytest.mark.asyncio
async def test_inmemory_repository_tracks_attachment_manifest():
    repo = InMemoryResearchRepository()
    job_id = await repo.create_job(
        session_id=uuid4(),
        root_run_id=uuid4(),
        objective="file research",
        policy_name="quality",
        config={},
    )
    manifest = [
        {
            "attachment_id": "att-1-deadbeef",
            "name": "report.pdf",
            "kind": "pdf",
            "media_type": "application/pdf",
            "sha256": "deadbeef",
            "size_bytes": 123,
            "extractor": "pdf",
            "chunk_count": 4,
            "metadata": {"page_count": 2},
            "truncated": False,
            "requires_multimodal": False,
            "extraction_error": None,
        }
    ]
    await repo.save_attachments(job_id, manifest)
    assert repo.jobs[job_id]["attachments"] == manifest


@pytest.mark.asyncio
async def test_capture_repository_keeps_backend_ids_and_telemetry() -> None:
    backend = InMemoryResearchRepository()
    seen = []
    repo = CapturingResearchRepository(backend, on_job_created=seen.append)
    job_id = await repo.create_job(session_id=uuid4(), root_run_id=uuid4(), objective="synthetic", policy_name="synthetic", config={})
    task_id = await repo.start_task(job_id=job_id, parent_task_id=None, role=ResearchRole.SCOUT, question_id="q1", prompt="fixture", model_id="synthetic:fake", effective_config={})
    await repo.record_tool_events(task_id, [ToolEvent(tool_name="synthetic_search")])
    await repo.finish_task(task_id, status="succeeded", output={}, usage={"total_tokens": 0}, agent_run_id=None, conversation_id=None)
    await repo.finish_job(job_id, status="succeeded", final_report={}, verification={})
    assert seen == [job_id]
    assert repo.jobs[job_id]["status"] == backend.jobs[job_id]["status"] == "succeeded"
    assert repo.tasks[task_id]["status"] == backend.tasks[task_id]["status"] == "succeeded"
    assert repo.tool_events[0]["tool_name"] == backend.tool_events[0]["tool_name"]


class _RecordingConnection:
    def __init__(self, statements):
        self.statements = statements

    async def execute(self, query, params=None):
        self.statements.append((" ".join(query.split()), params))


class _RecordingPool:
    def __init__(self):
        self.statements = []

    def connection(self):
        pool = self

        class _Context:
            async def __aenter__(self):
                return _RecordingConnection(pool.statements)

            async def __aexit__(self, *_):
                return None

        return _Context()


@pytest.mark.asyncio
async def test_postgres_finish_job_stores_the_ledger_and_review_reasons() -> None:
    pytest.importorskip("psycopg")
    from research_loop.repository import PostgresResearchRepository

    pool = _RecordingPool()
    job_id = uuid4()
    ledger = {"q1": [{"question_id": "q1", "claims": [{"id": "q1/c1"}]}]}
    await PostgresResearchRepository(pool).finish_job(
        job_id, status="succeeded", final_report={"answer": "a"}, verification={"checks": []},
        evidence_ledger=ledger, review_reasons=["the verifier checked none of the report's statements"],
    )
    ((query, params),) = pool.statements
    assert "evidence_ledger = %s" in query and "review_reasons = %s" in query
    stored = {getattr(param, "obj", param) for param in params if not isinstance(getattr(param, "obj", param), (dict, list))}
    assert job_id in stored
    assert ledger in [getattr(param, "obj", None) for param in params]


def test_nul_characters_are_removed_before_postgres_sees_them() -> None:
    from research_loop.repository import without_nul

    value = {"text\x00key": ["page\x00 one", {"n": 1, "quote": "a\x00b"}], "ok": "plain", "none": None}
    assert without_nul(value) == {"textkey": ["page one", {"n": 1, "quote": "ab"}], "ok": "plain", "none": None}
    assert without_nul(("x\x00",)) == ["x"]

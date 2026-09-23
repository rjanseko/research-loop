from uuid import uuid4

import pytest

from research_loop.repository import InMemoryResearchRepository
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

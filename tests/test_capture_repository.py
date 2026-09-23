from __future__ import annotations

from uuid import uuid4

import pytest

from research_loop.repository import CapturingResearchRepository, InMemoryResearchRepository
from research_loop.schemas import ResearchRole, ToolEvent


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

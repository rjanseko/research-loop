from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID, uuid4

from .schemas import ResearchRole, ToolEvent
from .telemetry import jsonable, safe_tool_args, safe_tool_result


def _pg_json(value: Any):
    try:
        from psycopg.types.json import Jsonb
    except ImportError as exc:
        raise RuntimeError("Install the postgres extra: pip install -e '.[postgres]'") from exc
    return Jsonb(jsonable(value))


class ResearchRepository(Protocol):
    async def create_job(
        self,
        *,
        session_id: UUID,
        root_run_id: UUID,
        objective: str,
        policy_name: str,
        config: dict[str, Any],
    ) -> UUID: ...

    async def save_plan(self, job_id: UUID, plan: dict[str, Any]) -> None: ...

    async def save_attachments(self, job_id: UUID, attachments: list[dict[str, Any]]) -> None: ...

    async def start_task(
        self,
        *,
        job_id: UUID,
        parent_task_id: UUID | None,
        role: ResearchRole,
        question_id: str | None,
        prompt: str,
        model_id: str,
        effective_config: dict[str, Any],
        attempt: int = 0,
    ) -> UUID: ...

    async def finish_task(
        self,
        task_id: UUID,
        *,
        status: str,
        output: dict[str, Any] | None,
        usage: dict[str, Any] | None,
        agent_run_id: str | None,
        conversation_id: str | None,
        error: dict[str, Any] | None = None,
    ) -> None: ...

    async def record_tool_events(self, task_id: UUID, events: list[ToolEvent]) -> None: ...

    async def finish_job(
        self,
        job_id: UUID,
        *,
        status: str,
        final_report: dict[str, Any] | None,
        verification: dict[str, Any] | None,
        error: dict[str, Any] | None = None,
        evidence_ledger: dict[str, Any] | None = None,
        review_reasons: list[str] | None = None,
    ) -> None: ...


class NullResearchRepository:
    async def create_job(self, **_: Any) -> UUID:
        return uuid4()

    async def save_plan(self, *_: Any, **__: Any) -> None:
        return None

    async def save_attachments(self, *_: Any, **__: Any) -> None:
        return None

    async def start_task(self, **_: Any) -> UUID:
        return uuid4()

    async def finish_task(self, *_: Any, **__: Any) -> None:
        return None

    async def record_tool_events(self, *_: Any, **__: Any) -> None:
        return None

    async def finish_job(self, *_: Any, **__: Any) -> None:
        return None


@dataclass
class InMemoryResearchRepository:
    jobs: dict[UUID, dict[str, Any]] = field(default_factory=dict)
    tasks: dict[UUID, dict[str, Any]] = field(default_factory=dict)
    tool_events: list[dict[str, Any]] = field(default_factory=list)

    async def create_job(self, **kwargs: Any) -> UUID:
        job_id = uuid4()
        self.jobs[job_id] = {
            "id": job_id,
            **kwargs,
            "status": "running",
            "created_at": datetime.now(UTC),
        }
        return job_id

    async def save_plan(self, job_id: UUID, plan: dict[str, Any]) -> None:
        self.jobs[job_id]["plan"] = plan

    async def save_attachments(self, job_id: UUID, attachments: list[dict[str, Any]]) -> None:
        self.jobs[job_id]["attachments"] = attachments

    async def start_task(self, **kwargs: Any) -> UUID:
        task_id = uuid4()
        self.tasks[task_id] = {
            "id": task_id,
            **kwargs,
            "status": "running",
            "started_at": datetime.now(UTC),
        }
        return task_id

    async def finish_task(self, task_id: UUID, **kwargs: Any) -> None:
        self.tasks[task_id].update(kwargs)
        self.tasks[task_id]["finished_at"] = datetime.now(UTC)

    async def record_tool_events(self, task_id: UUID, events: list[ToolEvent]) -> None:
        for index, event in enumerate(events):
            self.tool_events.append(
                {
                    "task_id": task_id,
                    "call_index": index,
                    **event.model_dump(mode="json"),
                }
            )

    async def finish_job(self, job_id: UUID, **kwargs: Any) -> None:
        self.jobs[job_id].update(kwargs)
        self.jobs[job_id]["finished_at"] = datetime.now(UTC)


class PostgresResearchRepository:
    """Thin repository over an existing psycopg AsyncConnectionPool.

    The application owns pool lifecycle, matching the broader architecture where
    Postgres is infrastructure rather than agent semantics.
    """

    def __init__(self, pool: Any) -> None:
        self.pool = pool

    async def create_job(self, **kwargs: Any) -> UUID:
        job_id = uuid4()
        async with self.pool.connection() as conn:
            await conn.execute(
                """
                insert into research_jobs
                    (id, session_id, root_run_id, objective, status, policy_name, effective_config)
                values (%s, %s, %s, %s, 'running', %s, %s)
                """,
                (
                    job_id,
                    kwargs["session_id"],
                    kwargs["root_run_id"],
                    kwargs["objective"],
                    kwargs["policy_name"],
                    _pg_json(kwargs["config"]),
                ),
            )
        return job_id

    async def save_plan(self, job_id: UUID, plan: dict[str, Any]) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                "update research_jobs set plan = %s where id = %s",
                (_pg_json(plan), job_id),
            )

    async def save_attachments(self, job_id: UUID, attachments: list[dict[str, Any]]) -> None:
        if not attachments:
            return
        async with self.pool.connection() as conn:
            for item in attachments:
                await conn.execute(
                    """
                    insert into research_attachments
                        (id, job_id, attachment_id, name, kind, media_type, sha256, size_bytes,
                         extractor, chunk_count, truncated, requires_multimodal, metadata, extraction_error)
                    values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    on conflict (job_id, attachment_id) do update set
                        name = excluded.name,
                        kind = excluded.kind,
                        media_type = excluded.media_type,
                        sha256 = excluded.sha256,
                        size_bytes = excluded.size_bytes,
                        extractor = excluded.extractor,
                        chunk_count = excluded.chunk_count,
                        truncated = excluded.truncated,
                        requires_multimodal = excluded.requires_multimodal,
                        metadata = excluded.metadata,
                        extraction_error = excluded.extraction_error
                    """,
                    (
                        uuid4(),
                        job_id,
                        item["attachment_id"],
                        item["name"],
                        item["kind"],
                        item["media_type"],
                        item["sha256"],
                        item["size_bytes"],
                        item["extractor"],
                        item["chunk_count"],
                        item["truncated"],
                        item["requires_multimodal"],
                        _pg_json(item.get("metadata") or {}),
                        item.get("extraction_error"),
                    ),
                )

    async def start_task(self, **kwargs: Any) -> UUID:
        task_id = uuid4()
        async with self.pool.connection() as conn:
            await conn.execute(
                """
                insert into research_tasks
                    (id, job_id, parent_task_id, role, question_id, prompt, model_id,
                     status, effective_config, attempt, started_at)
                values (%s, %s, %s, %s, %s, %s, %s, 'running', %s, %s, now())
                """,
                (
                    task_id,
                    kwargs["job_id"],
                    kwargs["parent_task_id"],
                    kwargs["role"].value,
                    kwargs["question_id"],
                    kwargs["prompt"],
                    kwargs["model_id"],
                    _pg_json(kwargs["effective_config"]),
                    kwargs.get("attempt", 0),
                ),
            )
        return task_id

    async def finish_task(self, task_id: UUID, **kwargs: Any) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                """
                update research_tasks
                   set status = %s,
                       output = %s,
                       usage = %s,
                       agent_run_id = %s,
                       conversation_id = %s,
                       error = %s,
                       finished_at = now()
                 where id = %s
                """,
                (
                    kwargs["status"],
                    _pg_json(kwargs.get("output")),
                    _pg_json(kwargs.get("usage")),
                    kwargs.get("agent_run_id"),
                    kwargs.get("conversation_id"),
                    _pg_json(kwargs.get("error")),
                    task_id,
                ),
            )

    async def record_tool_events(self, task_id: UUID, events: list[ToolEvent]) -> None:
        if not events:
            return
        async with self.pool.connection() as conn:
            for index, event in enumerate(events):
                await conn.execute(
                    """
                    insert into research_tool_events
                        (id, task_id, call_index, tool_name, tool_call_id, tool_kind,
                         provider_name, args, result, outcome, called_at, returned_at)
                    values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        uuid4(),
                        task_id,
                        index,
                        event.tool_name,
                        event.tool_call_id,
                        event.tool_kind,
                        event.provider_name,
                        _pg_json(safe_tool_args(event.args)),
                        _pg_json(safe_tool_result(event.tool_name, event.result)),
                        event.outcome,
                        event.called_at,
                        event.returned_at,
                    ),
                )

    async def finish_job(self, job_id: UUID, **kwargs: Any) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                """
                update research_jobs
                   set status = %s,
                       final_report = %s,
                       verification = %s,
                       error = %s,
                       evidence_ledger = %s,
                       review_reasons = %s,
                       finished_at = now()
                 where id = %s
                """,
                (
                    kwargs["status"],
                    _pg_json(kwargs.get("final_report")),
                    _pg_json(kwargs.get("verification")),
                    _pg_json(kwargs.get("error")),
                    _pg_json(kwargs.get("evidence_ledger")),
                    _pg_json(kwargs.get("review_reasons")),
                    job_id,
                ),
            )


class CapturingResearchRepository:
    """Keep run-local telemetry for benchmark scoring while delegating durable writes."""

    def __init__(self, backend: ResearchRepository, *, on_job_created: Callable[[UUID], None] | None = None) -> None:
        self.backend = backend
        self.on_job_created = on_job_created
        self.memory = InMemoryResearchRepository()
        self.jobs = self.memory.jobs
        self.tasks = self.memory.tasks
        self.tool_events = self.memory.tool_events

    async def create_job(self, **kwargs: Any) -> UUID:
        job_id = await self.backend.create_job(**kwargs)
        self.jobs[job_id] = {"id": job_id, **kwargs, "status": "running", "created_at": datetime.now(UTC)}
        if self.on_job_created:
            self.on_job_created(job_id)
        return job_id

    async def save_plan(self, job_id: UUID, plan: dict[str, Any]) -> None:
        await self.backend.save_plan(job_id, plan)
        self.jobs[job_id]["plan"] = plan

    async def save_attachments(self, job_id: UUID, attachments: list[dict[str, Any]]) -> None:
        await self.backend.save_attachments(job_id, attachments)
        self.jobs[job_id]["attachments"] = attachments

    async def start_task(self, **kwargs: Any) -> UUID:
        task_id = await self.backend.start_task(**kwargs)
        self.tasks[task_id] = {"id": task_id, **kwargs, "status": "running", "started_at": datetime.now(UTC)}
        return task_id

    async def finish_task(self, task_id: UUID, **kwargs: Any) -> None:
        await self.backend.finish_task(task_id, **kwargs)
        self.tasks[task_id].update(kwargs)
        self.tasks[task_id]["finished_at"] = datetime.now(UTC)

    async def record_tool_events(self, task_id: UUID, events: list[ToolEvent]) -> None:
        await self.backend.record_tool_events(task_id, events)
        await self.memory.record_tool_events(task_id, events)

    async def finish_job(self, job_id: UUID, **kwargs: Any) -> None:
        await self.backend.finish_job(job_id, **kwargs)
        self.jobs[job_id].update(kwargs)
        self.jobs[job_id]["finished_at"] = datetime.now(UTC)

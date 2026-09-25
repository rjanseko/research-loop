"""Durable records of runs and their agent calls: Postgres, or memory when there is no database.

A run row holds what a reader or an eval needs later: the question, configuration, plan, report,
evidence ledger, deterministic checks, cost, and the Logfire trace ID. A call row holds one agent
call's model, usage, cost, output, and full messages, so a run can be examined, re-checked, or
re-synthesized after Logfire's retention has passed.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter
from pydantic_ai.usage import RunUsage

# Longest string a stored transcript keeps whole. Fetches return at most 12,000 characters per call,
# so this cuts mainly oversized prompts.
MESSAGE_MAX_CHARS = 50_000
_ERROR_MESSAGE_CHARS = 1_000


def jsonable(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if is_dataclass(value) and not isinstance(value, type):
        return jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [jsonable(item) for item in value]
    return str(value)


def without_nul(value: Any) -> Any:
    """`value` with NUL characters removed from every string: Postgres stores none, and PDF text can hold one."""
    if isinstance(value, str):
        return value.replace("\x00", "") if "\x00" in value else value
    if isinstance(value, dict):
        return {without_nul(key): without_nul(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [without_nul(item) for item in value]
    return value


def usage_record(usage: RunUsage) -> dict[str, Any]:
    fields = ("requests", "tool_calls", "input_tokens", "output_tokens", "cache_read_tokens",
              "cache_write_tokens", "details")
    return {name: jsonable(getattr(usage, name, None)) for name in fields}


def error_record(exc: BaseException) -> dict[str, Any]:
    """A failure's type, HTTP status, and message, cut short. A Z.ai 429 is often an empty balance, which
    only the message tells apart from a rate limit."""
    record: dict[str, Any] = {"type": type(exc).__name__, "message": str(exc)[:_ERROR_MESSAGE_CHARS]}
    if isinstance(exc, ModelHTTPError):
        record["status_code"] = exc.status_code
    return record


def transcript(messages: list[ModelMessage]) -> list[Any]:
    """A call's messages as JSON, with strings past MESSAGE_MAX_CHARS cut."""
    def bounded(value: Any) -> Any:
        if isinstance(value, str) and len(value) > MESSAGE_MAX_CHARS:
            return value[:MESSAGE_MAX_CHARS] + f" [cut {len(value) - MESSAGE_MAX_CHARS} chars]"
        if isinstance(value, list):
            return [bounded(item) for item in value]
        if isinstance(value, dict):
            return {key: bounded(item) for key, item in value.items()}
        return value

    return bounded(ModelMessagesTypeAdapter.dump_python(messages, mode="json"))


class RunStore(Protocol):
    async def start_run(self, run_id: UUID, *, mode: str, workflow_version: str, question: str,
                        config: dict[str, Any], parent_run_id: UUID | None = None) -> None: ...

    async def finish_run(self, run_id: UUID, **fields: Any) -> None:
        """Set any of: status, plan, report, ledger, checks, cost_usd, usage, error, trace_id."""

    async def start_call(self, run_id: UUID, *, role: str, model: str, question_id: str | None = None) -> UUID: ...

    async def finish_call(self, call_id: UUID, *, status: str, usage: RunUsage | None = None,
                          cost_usd: Decimal | None = None, output: Any = None,
                          messages: list[ModelMessage] | None = None, error: BaseException | None = None) -> None: ...


_RUN_FIELDS = ("status", "plan", "report", "ledger", "checks", "cost_usd", "usage", "error", "trace_id")


@dataclass
class MemoryStore:
    """Runs and calls kept in this process, in the same shape Postgres stores them."""

    runs: dict[UUID, dict[str, Any]] = field(default_factory=dict)
    calls: dict[UUID, dict[str, Any]] = field(default_factory=dict)

    async def start_run(self, run_id: UUID, *, mode: str, workflow_version: str, question: str,
                        config: dict[str, Any], parent_run_id: UUID | None = None) -> None:
        self.runs[run_id] = {"id": run_id, "parent_run_id": parent_run_id, "mode": mode,
                             "workflow_version": workflow_version, "question": question, "status": "running",
                             "config": jsonable(config), "started_at": datetime.now(UTC)}

    async def finish_run(self, run_id: UUID, **fields: Any) -> None:
        unknown = set(fields) - set(_RUN_FIELDS)
        if unknown:
            raise TypeError(f"unknown run fields: {', '.join(sorted(unknown))}")
        self.runs[run_id].update({key: jsonable(value) for key, value in fields.items()},
                                 finished_at=datetime.now(UTC))

    async def start_call(self, run_id: UUID, *, role: str, model: str, question_id: str | None = None) -> UUID:
        call_id = uuid4()
        self.calls[call_id] = {"id": call_id, "run_id": run_id, "role": role, "model": model,
                               "question_id": question_id, "status": "running", "started_at": datetime.now(UTC)}
        return call_id

    async def finish_call(self, call_id: UUID, *, status: str, usage: RunUsage | None = None,
                          cost_usd: Decimal | None = None, output: Any = None,
                          messages: list[ModelMessage] | None = None, error: BaseException | None = None) -> None:
        self.calls[call_id].update(
            status=status, usage=usage_record(usage) if usage else None,
            cost_usd=float(cost_usd) if cost_usd is not None else None, output=jsonable(output),
            messages=transcript(messages) if messages else None,
            error=error_record(error) if error else None, finished_at=datetime.now(UTC))


def _json(value: Any) -> Any:
    from psycopg.types.json import Jsonb

    return None if value is None else Jsonb(without_nul(jsonable(value)))


class PostgresStore:
    """Runs and calls in Postgres, through a psycopg AsyncConnectionPool the caller owns."""

    def __init__(self, pool: Any) -> None:
        self.pool = pool

    async def start_run(self, run_id: UUID, *, mode: str, workflow_version: str, question: str,
                        config: dict[str, Any], parent_run_id: UUID | None = None) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                """insert into runs (id, parent_run_id, mode, workflow_version, question, status, config)
                   values (%s, %s, %s, %s, %s, 'running', %s)""",
                (run_id, parent_run_id, mode, workflow_version, without_nul(question), _json(config)),
            )

    async def finish_run(self, run_id: UUID, **fields: Any) -> None:
        unknown = set(fields) - set(_RUN_FIELDS)
        if unknown:
            raise TypeError(f"unknown run fields: {', '.join(sorted(unknown))}")
        columns = [name for name in _RUN_FIELDS if name in fields]
        values = [fields[name] if name in ("status", "trace_id", "cost_usd") else _json(fields[name]) for name in columns]
        # Column names come from _RUN_FIELDS, never from the caller; values are parameters.
        assignments = ", ".join(f"{name} = %s" for name in columns)
        async with self.pool.connection() as conn:
            await conn.execute(f"update runs set {assignments}, finished_at = now() where id = %s", (*values, run_id))

    async def start_call(self, run_id: UUID, *, role: str, model: str, question_id: str | None = None) -> UUID:
        call_id = uuid4()
        async with self.pool.connection() as conn:
            await conn.execute(
                "insert into run_calls (id, run_id, role, question_id, model, status) values (%s, %s, %s, %s, %s, 'running')",
                (call_id, run_id, role, question_id, model),
            )
        return call_id

    async def finish_call(self, call_id: UUID, *, status: str, usage: RunUsage | None = None,
                          cost_usd: Decimal | None = None, output: Any = None,
                          messages: list[ModelMessage] | None = None, error: BaseException | None = None) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                """update run_calls set status = %s, usage = %s, cost_usd = %s, output = %s, messages = %s,
                          error = %s, finished_at = now() where id = %s""",
                (status, _json(usage_record(usage)) if usage else None, cost_usd, _json(output),
                 _json(transcript(messages)) if messages else None, _json(error_record(error)) if error else None,
                 call_id),
            )


async def load_run(pool: Any, run_id: UUID) -> dict[str, Any] | None:
    """A stored run's row, or None when there is none."""
    from psycopg.rows import dict_row

    async with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cursor:
        await cursor.execute("select * from runs where id = %s", (run_id,))
        return await cursor.fetchone()

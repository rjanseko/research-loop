from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Iterable

from pydantic import BaseModel

from .schemas import ToolEvent


def jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if is_dataclass(value):
        return jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [jsonable(v) for v in value]
    if hasattr(value, "__dict__"):
        return {k: jsonable(v) for k, v in vars(value).items() if not k.startswith("_")}
    return str(value)


def usage_snapshot(usage: Any) -> dict[str, Any]:
    """Provider-neutral JSON snapshot of PydanticAI RunUsage."""
    fields = (
        "requests",
        "tool_calls",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cache_write_tokens",
        "cache_read_tokens",
        "details",
        "cost",
    )
    return {name: jsonable(getattr(usage, name)) for name in fields if hasattr(usage, name)}


def _parts(messages: Iterable[Any]) -> Iterable[Any]:
    for message in messages:
        for part in getattr(message, "parts", ()):
            yield part


def extract_tool_events(messages: Iterable[Any]) -> list[ToolEvent]:
    try:
        from pydantic_ai.messages import (
            NativeToolCallPart,
            NativeToolReturnPart,
            ToolCallPart,
            ToolReturnPart,
        )
    except ImportError as exc:  # pragma: no cover - runtime dependency
        raise RuntimeError("PydanticAI is required to extract agent tool telemetry") from exc

    calls: dict[str, ToolEvent] = {}
    order: list[str] = []

    for part in _parts(messages):
        if isinstance(part, (ToolCallPart, NativeToolCallPart)):
            call_id = part.tool_call_id
            if call_id not in calls:
                order.append(call_id)
            calls[call_id] = ToolEvent(
                tool_name=part.tool_name,
                tool_call_id=call_id,
                tool_kind=getattr(part, "tool_kind", None),
                provider_name=getattr(part, "provider_name", None),
                args=jsonable(part.args),
                called_at=getattr(part, "timestamp", None),
            )
        elif isinstance(part, (ToolReturnPart, NativeToolReturnPart)):
            call_id = part.tool_call_id
            event = calls.get(call_id)
            if event is None:
                event = ToolEvent(tool_name=part.tool_name, tool_call_id=call_id)
                calls[call_id] = event
                order.append(call_id)
            event.result = jsonable(part.content)
            event.outcome = getattr(part, "outcome", None)
            event.returned_at = getattr(part, "timestamp", None)
            if not event.provider_name:
                event.provider_name = getattr(part, "provider_name", None)

    return [calls[call_id] for call_id in order]


def compact_tool_result(value: Any, *, max_chars: int = 8_000) -> Any:
    """Bound persisted tool-return volume while retaining useful provenance."""
    normalized = jsonable(value)
    encoded = json.dumps(normalized, ensure_ascii=False, default=str)
    if len(encoded) <= max_chars:
        return normalized
    return {"truncated": True, "preview": encoded[:max_chars], "original_chars": len(encoded)}

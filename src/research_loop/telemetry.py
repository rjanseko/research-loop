from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

import httpx
from pydantic import BaseModel
from pydantic_ai.exceptions import ModelHTTPError

from .schemas import ToolEvent


def error_snapshot(exc: BaseException) -> dict[str, Any]:
    """Persist a failure category without provider response bodies or prompt fragments."""
    snapshot: dict[str, Any] = {"type": type(exc).__name__}
    if isinstance(exc, ModelHTTPError):
        snapshot["status_code"] = exc.status_code
    return snapshot


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
        yield from getattr(message, "parts", ())


def safe_tool_result(tool_name: str, value: Any) -> Any:
    """Persist tool provenance, never fetched article text or long provider payloads."""
    normalized = jsonable(value)
    if tool_name.startswith("scholar_") and isinstance(normalized, str):
        try:
            normalized = json.loads(normalized)
        except ValueError:
            pass
    encoded = json.dumps(normalized, ensure_ascii=False, default=str)
    if tool_name.startswith("scholar_") and isinstance(normalized, dict):
        works = normalized.get("works") or []
        return {
            "operation": normalized.get("operation"),
            "provider_errors": normalized.get("provider_errors", []),
            "result_count": len(works),
            "work_ids": [item.get("provider_id") for item in works[:10] if isinstance(item, dict)],
            "cache_hits": normalized.get("cache_hits", 0),
            "truncated": normalized.get("truncated", False),
            "content_sha256": normalized.get("content_sha256"),
            "response_sha256": hashlib.sha256(encoded.encode()).hexdigest(),
            "response_chars": len(encoded),
        }
    if len(encoded) > 2000 or any(word in tool_name.lower() for word in ("fetch", "page", "attachment")):
        summary = {"response_sha256": hashlib.sha256(encoded.encode()).hexdigest(), "response_chars": len(encoded)}
        if isinstance(normalized, dict):  # the tool's own error code and HTTP status, not content
            summary |= {key: normalized[key] for key in ("error", "status") if key in normalized}
        return summary
    return normalized


def safe_tool_args(value: Any) -> Any:
    """Keep argument shape while hashing text that may contain benchmark inputs or secrets."""
    normalized = jsonable(value)
    if isinstance(normalized, str):
        try:
            normalized = json.loads(normalized)
        except ValueError:
            return {"sha256": hashlib.sha256(normalized.encode()).hexdigest(), "chars": len(normalized)}
    if not isinstance(normalized, dict):
        return None
    result: dict[str, Any] = {}
    for key, item in normalized.items():
        if isinstance(item, str):
            result[key + "_sha256"] = hashlib.sha256(item.encode()).hexdigest()
            result[key + "_chars"] = len(item)
        elif isinstance(item, (int, float, bool)) or item is None:
            result[key] = item
        else:
            result[key] = "[omitted]"
    return result


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


def _subclass_names(cls: type) -> set[str]:
    return {name for sub in cls.__subclasses__() for name in (sub.__name__, *_subclass_names(sub))}


# Failures before any HTTP status: no connection, a proxy that refused the host, a timeout, or a
# broken protocol. Web and scholarly tools report them by exception class name.
NETWORK_ERRORS = frozenset(_subclass_names(httpx.TransportError))


def unreached_source(result: Any) -> str | None:
    """Why a web or scholarly tool result reached no source, or None when it did or failed otherwise.

    A fetch names a network error; a search that failed twice is `SearchUnavailable`; a scholarly
    call counts only when it returned nothing and a provider failed on the network. HTTP statuses,
    blocked sources, and empty searches reached their source and are not counted.
    """
    if isinstance(result, str) and result[:1] == "{":
        try:
            result = json.loads(result)
        except ValueError:
            return None
    if not isinstance(result, dict):
        return None
    error = str(result.get("error") or "")
    if error in NETWORK_ERRORS:
        return error
    if error.startswith("SearchUnavailable"):
        return "SearchUnavailable"
    if result.get("works") or result.get("text"):
        return None
    for provider_error in result.get("provider_errors") or ():
        name = str(provider_error).rpartition(":")[2]
        if name in NETWORK_ERRORS:
            return name
    return None


@dataclass
class SourceReach:
    """A job's web and scholarly tool calls, and those that reached no source, by reason."""

    calls: int = 0
    unreached: Counter[str] = field(default_factory=Counter)

    def add(self, events: Iterable[ToolEvent]) -> None:
        for event in events:
            name = event.tool_name.lower()
            if not event.is_research_tool or "attachment" in name or event.result is None:
                continue
            self.calls += 1
            if reason := unreached_source(event.result):
                self.unreached[reason] += 1

    def review_reason(self) -> str | None:
        """A review reason when at least three calls, and at least half, reached no source."""
        failed = self.unreached.total()
        if failed < 3 or failed * 2 < self.calls:
            return None
        reasons = ", ".join(reason for reason, _ in self.unreached.most_common(3))
        return f"{failed} of {self.calls} web and scholarly tool calls reached no source ({reasons})"

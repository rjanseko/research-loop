"""Optional trimming of tool results a research agent has already read.

A scout or deep dive resends its whole conversation on every model request, so each fetched
window is paid for again on every later request. With `ResearchConfig.keep_recent_tool_results`
set, results the model has already seen, other than the most recent few, are replaced by a
short stub before each request. What the model is sent changes; nothing else does: PydanticAI
writes the processed history back into the run's messages, so the trimmer keeps each original
and `restore` puts them back before tool telemetry, the quote and source checks, or salvage
read the messages.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart

# Results shorter than this cost little to resend and are kept whole.
MIN_TRIM_CHARS = 2_000
# Characters of a trimmed result the model still sees.
TRIMMED_HEAD_CHARS = 500
TRIMMED_NOTE = (
    "[Trimmed to save context: you already read this {tool} result ({chars} characters). Its start "
    "follows; call {tool} again with the same arguments if you need the full text, for example to quote it.]\n"
)


class ToolResultTrimmer:
    """History processor for one agent run; `restore` undoes it on the run's messages."""

    def __init__(self, keep_recent: int) -> None:
        if keep_recent < 0:
            raise ValueError("keep_recent must be at least 0")
        self.keep_recent = keep_recent
        self.originals: dict[str, ToolReturnPart] = {}

    def __call__(self, messages: list[ModelMessage]) -> list[ModelMessage]:
        returns = [
            (index, position, part)
            for index, message in enumerate(messages)
            if isinstance(message, ModelRequest)
            for position, part in enumerate(message.parts)
            if isinstance(part, ToolReturnPart)
        ]
        # The most recent results stay whole, and so do those in the last request, which the
        # model has not read yet.
        older = [item for item in returns[: max(len(returns) - self.keep_recent, 0)] if item[0] != len(messages) - 1]
        result = list(messages)
        for index, position, part in older:
            if part.tool_call_id in self.originals:
                continue  # trimmed on an earlier request
            text = part.model_response_str()
            if len(text) < MIN_TRIM_CHARS:
                continue
            self.originals[part.tool_call_id] = part
            stub = TRIMMED_NOTE.format(tool=part.tool_name, chars=len(text)) + text[:TRIMMED_HEAD_CHARS]
            parts = list(result[index].parts)
            parts[position] = replace(part, content=stub)
            result[index] = replace(result[index], parts=parts)
        return result

    def restore(self, messages: list[Any]) -> list[Any]:
        """`messages` with every trimmed tool result put back as the tools returned it."""
        if not self.originals:
            return list(messages)
        restored = []
        for message in messages:
            if isinstance(message, ModelRequest) and any(
                isinstance(part, ToolReturnPart) and part.tool_call_id in self.originals for part in message.parts
            ):
                message = replace(message, parts=[
                    self.originals.get(part.tool_call_id, part) if isinstance(part, ToolReturnPart) else part
                    for part in message.parts
                ])
            restored.append(message)
        return restored

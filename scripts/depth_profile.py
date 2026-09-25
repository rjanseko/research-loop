"""How deep each scout and deep dive searched, and where its time went, from a job's transcripts.

For each research tool loop of a stored job, this reads its captured transcript (`--capture`) and
reports:

- the request at which each source its result cites was first returned by a tool, matched the way
  `source_check` matches (quotes.ToolOutputIndex): URL ignoring scheme, `www.`, query, and
  fragment, or DOI or arXiv ID. A source no tool returned is counted as not matched;
- whether the loop stopped on its own limit, in which case a salvage call wrote its result and its
  curve is cut off: it shows only that the task needed at least that many requests. `stopped_by`
  names the limit it was closest to: tokens, requests, tool calls, or dollars. A limit that should not
  bind showing up here, as tokens did in the first pilot, means the study's limits need changing;
- its time split into model time (a request sent until its response arrived) and tool time (the
  response arrived until the next request went out with the tool results), and the salvage call's
  time;
- for a deep dive, `scouted`: how many of the sources it cited the scout on the same question had already
  returned. A deep dive gets the gap, not the scout's evidence, so a high share means its early requests
  found the scout's sources again, and its depth curve partly measures that repetition.

The depth curve at the end is the share of all cited sources that had been returned by request k.
docs/settings-study.md explains how the settings study uses it. Output holds counts and times, not
URLs or queries.

    .venv/bin/python scripts/depth_profile.py <job_id>
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from research_loop.quotes import ToolOutputIndex, tool_texts
from research_loop.schemas import ResearchResult, SourceRef, ToolEvent

RESEARCH_ROLES = ("scout", "deep_dive")


@dataclass
class LoopProfile:
    role: str
    question_id: str
    requests: int
    cut_off: bool
    # Request (1-based) at which each distinct cited source was first returned, None if never.
    first_seen: list[int | None] = field(default_factory=list)
    model_seconds: float = 0.0
    tool_seconds: float = 0.0
    salvage_seconds: float | None = None
    stopped_by: str | None = None
    # Deep dives only: how many of the sources it cited its question's scout had already returned. A deep dive
    # is not given the scout's evidence, so a high count means it spent requests finding them again.
    from_scout: int | None = None


# The route limits a task records in effective_config, and the usage each is measured against.
_LIMITS = (("tokens", "total_tokens_limit", "total_tokens"), ("requests", "max_requests", "requests"),
           ("tool calls", "max_tool_calls", "tool_calls"), ("dollars", "cost_limit", "cost"))


def stopping_limit(usage: dict[str, Any], config: dict[str, Any]) -> str:
    """The limit a stopped loop had used the largest share of.

    PydanticAI stops a loop when its next request would pass a limit, so the one that stopped it need
    not be fully used; the closest to full is the best evidence without the error message, which
    Postgres does not keep.
    """
    shares = {name: float(usage.get(used) or 0) / float(config[limit])
              for name, limit, used in _LIMITS if config.get(limit)}
    return max(shares, key=shares.get) if shares else "unknown"


def _cited_sources(result: ResearchResult) -> list[SourceRef]:
    """Each URL-cited source once, in citation order."""
    seen: set[str] = set()
    sources = []
    for claim in result.claims:
        for item in claim.evidence:
            if item.source.url is not None and str(item.source.url) not in seen:
                seen.add(str(item.source.url))
                sources.append(item.source)
    return sources


def loop_texts(messages: list[Any]) -> list[str]:
    """Every tool result text in one loop's messages."""
    from pydantic_ai.messages import ModelRequest, ToolReturnPart

    events = [ToolEvent(tool_name=part.tool_name, result=part.content) for message in messages
              if isinstance(message, ModelRequest) for part in message.parts if isinstance(part, ToolReturnPart)]
    return tool_texts(events)


def profile_loop(messages: list[Any], result: ResearchResult | None) -> tuple[int, list[int | None], float, float]:
    """Requests made, first-seen request per cited source, and model and tool seconds, from one loop's messages."""
    from pydantic_ai.messages import ModelRequest, ModelResponse, ToolReturnPart

    texts_by_request: list[list[str]] = []
    model_seconds = tool_seconds = 0.0
    sent: datetime | None = None
    arrived: datetime | None = None
    for message in messages:
        if isinstance(message, ModelRequest):
            if arrived is not None and message.timestamp is not None:
                tool_seconds += (message.timestamp - arrived).total_seconds()
            returns = [part for part in message.parts if isinstance(part, ToolReturnPart)]
            if returns and texts_by_request:
                events = [ToolEvent(tool_name=part.tool_name, result=part.content) for part in returns]
                texts_by_request[-1].extend(tool_texts(events))
            sent, arrived = message.timestamp, None
        elif isinstance(message, ModelResponse):
            if sent is not None and message.timestamp is not None:
                model_seconds += (message.timestamp - sent).total_seconds()
            texts_by_request.append([])
            arrived = message.timestamp
    first_seen: list[int | None] = []
    if result is not None:
        indexes = []
        cumulative: list[str] = []
        for texts in texts_by_request:
            cumulative.extend(texts)
            indexes.append(ToolOutputIndex(cumulative))
        for source in _cited_sources(result):
            first_seen.append(next((k for k, index in enumerate(indexes, 1) if index.observed(source)), None))
    return len(texts_by_request), first_seen, model_seconds, tool_seconds


async def load_profiles(dsn: str, job_id: UUID) -> list[LoopProfile]:
    import psycopg
    from pydantic_ai.messages import ModelMessagesTypeAdapter

    async with await psycopg.AsyncConnection.connect(dsn) as conn:
        rows = await (await conn.execute(
            "select t.id, t.role, t.question_id, t.status, t.parent_task_id, t.output, t.started_at, t.finished_at,"
            " m.messages, t.usage, t.effective_config"
            " from research_tasks t left join research_task_messages m on m.task_id = t.id"
            " where t.job_id = %s and t.role = any(%s) order by t.started_at", (job_id, list(RESEARCH_ROLES)))).fetchall()
    tasks = {row[0]: row for row in rows}
    # A salvage call is a child of the loop it wrote the result for, with the same role and question.
    salvage = {row[4]: row for row in rows if row[4] in tasks and tasks[row[4]][1] == row[1] and tasks[row[4]][2] == row[2]}
    profiles = []
    skipped: list[str] = []
    scout_texts: dict[str, list[str]] = {}
    for task_id, role, question_id, _status, _parent, _output, _started, _finished, messages, _usage, _config in rows:
        if role == "scout" and messages is not None and task_id not in {row[0] for row in salvage.values()}:
            scout_texts.setdefault(question_id or "", []).extend(
                loop_texts(ModelMessagesTypeAdapter.validate_python(messages)))
    for task_id, role, question_id, status, _parent, output, _started, _finished, messages, usage, config in rows:
        if task_id in {row[0] for row in salvage.values()}:
            continue
        if messages is None:
            # A job run without --capture has none; one task can also lack it when storing it failed, as
            # a transcript with a NUL character did before the repository stripped them.
            if not any(row[8] is not None for row in rows):
                raise SystemExit(f"job {job_id} has no transcripts; run it with --capture")
            skipped.append(f"{role} {question_id or '-'}")
            continue
        child = salvage.get(task_id)
        final = child[5] if child else (output if status == "succeeded" else None)
        result = ResearchResult.model_validate(final) if final else None
        requests, first_seen, model_s, tool_s = profile_loop(ModelMessagesTypeAdapter.validate_python(messages), result)
        from_scout = None
        if role == "deep_dive" and result is not None:
            scouted = ToolOutputIndex(scout_texts.get(question_id or "", []))
            from_scout = sum(scouted.observed(source) for source in _cited_sources(result))
        profiles.append(LoopProfile(
            role=role, question_id=question_id or "", requests=requests, cut_off=child is not None,
            first_seen=first_seen, model_seconds=model_s, tool_seconds=tool_s,
            salvage_seconds=(child[7] - child[6]).total_seconds() if child else None,
            stopped_by=stopping_limit(usage or {}, config or {}) if child else None,
            from_scout=from_scout,
        ))
    if skipped:
        print(f"skipped, no transcript: {', '.join(skipped)}")
    return profiles


def render(profiles: list[LoopProfile]) -> str:
    lines = ["role       question requests stopped_by model_s tool_s salvage_s scouted  first seen at request (- = not matched)"]
    for p in profiles:
        seen = ", ".join(str(k) if k else "-" for k in sorted(p.first_seen, key=lambda k: (k is None, k or 0)))
        salvage = f"{p.salvage_seconds:9.1f}" if p.salvage_seconds is not None else " " * 9
        scouted = f"{p.from_scout:>3}/{len(p.first_seen):<3}" if p.from_scout is not None else " " * 7
        lines.append(f"{p.role:<10} {p.question_id:<8} {p.requests:>8} {p.stopped_by or '-':>10} "
                     f"{p.model_seconds:7.1f} {p.tool_seconds:6.1f} {salvage} {scouted}  {seen}")
    for role in RESEARCH_ROLES:
        group = [p for p in profiles if p.role == role]
        cited = [k for p in group for k in p.first_seen]
        if not cited:
            continue
        deepest = max(p.requests for p in group)
        curve = [sum(1 for k in cited if k is not None and k <= depth) / len(cited) for depth in range(1, deepest + 1)]
        lines.append(f"\n{role}: share of {len(cited)} cited sources returned by request k"
                     f" ({sum(k is None for k in cited)} not matched)")
        lines.append("  " + "  ".join(f"k={k}:{share:.2f}" for k, share in enumerate(curve, 1)))
        model = sum(p.model_seconds for p in group)
        tools = sum(p.tool_seconds for p in group)
        lines.append(f"  model {model:.0f}s, tools {tools:.0f}s: model is {model / (model + tools):.0%} of loop time")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    from research_loop.settings import ResearchSettings

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("job_id", type=UUID)
    args = parser.parse_args(argv)
    settings = ResearchSettings.from_env()
    if not settings.database_dsn:
        parser.error("reads the job from Postgres; set DATABASE_URL (see docs/setup.md#postgres)")
    print(render(asyncio.run(load_profiles(settings.database_dsn, args.job_id))))


if __name__ == "__main__":
    main()

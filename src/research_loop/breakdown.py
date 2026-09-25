"""Where a stored run's money and time went, call by call, and why each call stopped.

Reads only what the run store records: the run row and its call rows. Time is measured from the run's
start. A scout's model time is its call's duration less its tool time, so it includes the little time
PydanticAI itself spends between requests.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any


def _seconds(start: datetime | None, end: datetime | None) -> float | None:
    return (end - start).total_seconds() if start and end else None


def _fmt(seconds: float | None) -> str:
    return "-" if seconds is None else f"{seconds:.0f}s"


def _money(value: Any) -> str:
    return "-" if value is None else f"${float(value):.4f}"


def _tokens(usage: dict[str, Any] | None) -> tuple[int, int, int, int, int]:
    usage = usage or {}
    details = usage.get("details") or {}
    reasoning = sum(v for k, v in details.items() if "reasoning" in k and isinstance(v, int))
    return (usage.get("requests") or 0, usage.get("input_tokens") or 0, usage.get("cache_read_tokens") or 0,
            usage.get("output_tokens") or 0, reasoning)


def breakdown(run: dict[str, Any], calls: list[dict[str, Any]]) -> str:
    """A plain-text account of `run`'s cost, elapsed time, and stop reasons from its call rows."""
    start = run["started_at"]
    limits = (run.get("config") or {}).get("limits") or {}
    lines = [(f"Run {run['id']}: {run['status']}, {_money(run.get('cost_usd'))}, "
              f"{_fmt(_seconds(start, run.get('finished_at')))} elapsed")]
    if run.get("study_id"):
        lines.append(f"Study {run['study_id']}, arm {run.get('arm')}, replicate {run.get('replicate')}")
    lines.append(f"Input {run.get('input_hash') or '-'}")

    lines += ["", "Calls (seconds from run start):",
              (f"  {'role':<12}{'q':<4}{'status':<10}{'start':>6}{'dur':>6}{'tools':>7}{'model':>7}"
               f"{'req':>5}{'in':>9}{'cached':>9}{'out':>8}{'think':>8}{'cost':>10}  stop reason")]
    by_role: dict[str, float] = {}
    for call in calls:
        offset = _seconds(start, call.get("started_at"))
        duration = _seconds(call.get("started_at"), call.get("finished_at"))
        tools = float(call["tool_seconds"]) if call.get("tool_seconds") is not None else None
        model = duration - tools if duration is not None and tools is not None else None
        requests, inputs, cached, outputs, reasoning = _tokens(call.get("usage"))
        cost = call.get("cost_usd")
        by_role[call["role"]] = by_role.get(call["role"], 0.0) + float(cost or 0)
        lines.append(f"  {call['role']:<12}{call.get('question_id') or '':<4}{call['status']:<10}"
                     f"{_fmt(offset):>6}{_fmt(duration):>6}{_fmt(tools):>7}{_fmt(model):>7}"
                     f"{requests:>5}{inputs:>9}{cached:>9}{outputs:>8}{reasoning:>8}{_money(cost):>10}"
                     f"  {call.get('stop_reason') or '-'}")

    lines += ["", "Cost by role:", *(f"  {role:<12}{_money(cost)}" for role, cost in by_role.items())]
    if (total := run.get("cost_usd")) is not None:
        lines.append(f"  {'run total':<12}{_money(total)}")

    phases = _phases(start, calls, limits)
    if phases:
        lines += ["", "Time:", *(f"  {name}: {value}" for name, value in phases)]

    cache = run.get("cache") or {}
    if cache:
        lines += ["", f"Cache ({cache.get('mode', '-')}):"]
        for provider, tally in sorted((cache.get("by_provider") or {}).items()):
            lookups = tally.get("hits", 0) + tally.get("misses", 0)
            rate = f"{tally.get('hits', 0) / lookups:.0%}" if lookups else "-"
            lines.append(f"  {provider:<12}{tally.get('hits', 0)} hits, {tally.get('misses', 0)} misses ({rate}), "
                         f"{tally.get('writes', 0)} written")
    return "\n".join(lines) + "\n"


def _phases(start: datetime, calls: list[dict[str, Any]], limits: dict[str, Any]) -> list[tuple[str, str]]:
    """Planning, research, and synthesis as spans of the run, and how much of each deadline each used."""
    def span(role: str) -> tuple[float | None, float | None]:
        rows = [c for c in calls if c["role"] == role]
        begins = [_seconds(start, c.get("started_at")) for c in rows]
        ends = [_seconds(start, c.get("finished_at")) for c in rows]
        begins_known = [b for b in begins if b is not None]
        ends_known = [e for e in ends if e is not None]
        return (min(begins_known) if begins_known else None, max(ends_known) if ends_known else None)

    phases: list[tuple[str, str]] = []
    plan_begin, plan_end = span("planner")
    if plan_begin is not None:
        phases.append(("planning", f"{_fmt(plan_begin)} to {_fmt(plan_end)}"))
    scout_begin, scout_end = span("scout")
    if scout_begin is not None:
        research = limits.get("research_seconds")
        phases.append(("research", f"{_fmt(scout_begin)} to {_fmt(scout_end)}"
                       + (f" (research deadline at {research:.0f}s)" if research else "")))
    synth_begin, synth_end = span("synthesizer")
    if synth_begin is not None:
        deadline = limits.get("deadline_seconds")
        left = f", {deadline - synth_begin:.0f}s left before the run deadline at {deadline:.0f}s" if deadline else ""
        phases.append(("synthesis", f"{_fmt(synth_begin)} to {_fmt(synth_end)}{left}"))
    return phases

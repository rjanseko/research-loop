"""Which limits stop a run's calls, and how much of each budget the calls use, from stored runs only.

    .venv/bin/python scripts/budget_bottlenecks.py --since 2026-09-27
    .venv/bin/python scripts/budget_bottlenecks.py --study search-rescout-task8-v14

It makes no model calls. For every planner, scout, deep dive, gap analysis, and synthesis call of the chosen
real runs (cheap and fake-model runs are left out), it names what stopped the call and how much of its dollar
share, time window, requests, productive calls, input tokens, and per-request output cap it used, by role and
workflow version. A limit that binds while money and time are left is a bottleneck; a cut-off keeps no claims.
Pacing waits and 429 pauses are only in Logfire, so they are not counted here.
"""
from __future__ import annotations

import argparse
import statistics
from collections import Counter, defaultdict
from typing import Any

import psycopg
from pydantic_ai.messages import ModelMessagesTypeAdapter, ModelResponse

from research_loop.budget_notes import tool_yield
from research_loop.config import Settings

# Stop reasons and errors, as stored, sorted into what stopped the call.
_BUCKETS = (
    ("returned on its own", "own"), ("returned a result", "own"),
    ("productive calls were spent", "productive calls"), ("misses were spent", "misses"),
    ("dollar share was spent", "dollar share (note)"), ("last request", "requests"),
    ("research deadline was close", "deadline (returned)"), ("deadline passed", "deadline (cut off)"),
    ("Request timed out", "request timeout"), ("cost_limit", "dollar share (cut off)"),
    ("StudyBudgetRefusal", "hard cap"), ("would exceed", "hard cap"), ("request_limit", "requests (cut off)"),
    ("total_tokens_limit", "input tokens"), ("output failed its checks", "output checks"),
    ("Exceeded maximum output retries", "output checks"), ("status_code: 5", "provider 5xx"),
    ("status_code: 429", "rate limit"), ("SSLError", "network"), ("run was cancelled", "cancelled"),
)
# The per-call limits each role runs under: (dollar-share field, time field, request field, productive field).
_ROLE_LIMITS = {
    "scout": ("scout", "research_seconds", "scout_requests", "scout_productive_calls"),
    "deep_dive": ("deep_dive_usd", "deep_dive_seconds", "deep_dive_requests", "deep_dive_productive_calls"),
    "gap_analyzer": ("gap_usd", "gap_seconds", None, None),
    "planner": ("planner_usd", None, None, None),
    "synthesizer": ("synthesis_usd", None, None, None),
}


def bucket(stop_reason: str | None, error: dict[str, Any] | None, status: str) -> str:
    text = " ".join(filter(None, [stop_reason, str(error or "")]))
    for needle, name in _BUCKETS:
        if needle in text:
            return name
    return "own" if status == "succeeded" and not stop_reason else (stop_reason or status)[:40]


def share(role: str, limits: dict[str, Any], questions: int, follow_up: bool) -> float | None:
    field = _ROLE_LIMITS.get(role, (None,))[0]
    if field is None or not limits:
        return None
    if field != "scout":
        return float(limits[field])
    if follow_up:
        return (limits["followup_cost_usd"] - limits["planner_usd"] - limits["synthesis_usd"] - limits["gap_usd"]
                - limits["max_gaps"] * limits["deep_dive_usd"]) / max(questions, 1)
    return (limits["cost_usd"] - limits["planner_usd"] - limits["synthesis_usd"]) / max(questions, 1)


def depth_limits(config: dict[str, Any]) -> dict[str, Any]:
    """The limits the run ran at: the stored standard limits with its depth's overrides."""
    limits = dict(config.get("limits") or {})
    depth = config.get("depth")
    if depth in ("quick", "deep"):
        limits |= {key: value for key, value in (limits.get(depth) or {}).items() if value is not None}
    return limits


def pct(values: list[float], q: float) -> float:
    return sorted(values)[min(len(values) - 1, int(q * len(values)))] if values else float("nan")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--since", default="2026-09-27", help="runs started on or after this date")
    parser.add_argument("--study", help="only this study's runs")
    args = parser.parse_args()
    url = Settings().database_url
    url = url.get_secret_value() if hasattr(url, "get_secret_value") else url
    where = ["r.started_at >= %s", "coalesce(r.config->'models'->>'scout', '') not like '%%@low'",
             "coalesce(r.config->'models'->>'scout', '') not like 'fake:%%'",
             "coalesce(r.study_id, '') not like '%%-cheap'"]
    params: list[Any] = [args.since]
    if args.study:
        where.append("r.study_id = %s")
        params.append(args.study)
    query = f"""select r.id, r.workflow_version, r.config, jsonb_array_length(coalesce(r.plan->'questions', '[]')),
                       r.status, r.cost_usd, r.checks->>'external_usd', c.role, c.status, c.stop_reason, c.error,
                       c.usage, c.cost_usd, extract(epoch from c.finished_at - c.started_at), c.tool_seconds, c.messages
                from run_calls c join runs r on r.id = c.run_id where {' and '.join(where)}"""
    stops: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    use: dict[tuple[str, str], dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    runs: dict[Any, dict[str, Any]] = {}
    with psycopg.connect(url) as connection:
        rows = connection.execute(query, params).fetchall()
    for (run_id, version, config, questions, run_status, run_cost, external, role, status, stop_reason, error,
         usage, cost, seconds, tool_seconds, messages) in rows:
        config = config or {}
        limits = depth_limits(config)
        family = f"{version} {config.get('depth') or ''}".strip()
        key = (role, family)
        stops[key][bucket(stop_reason, error, status)] += 1
        runs[run_id] = {"family": family, "status": run_status, "cost": float(run_cost or 0),
                        "external": float(external or 0),
                        "cap": limits.get("followup_cost_usd" if config.get("follow_up") else "cost_usd")}
        usage = usage or {}
        measures = use[key]
        allowed = share(role, limits, questions, bool(config.get("follow_up")))
        if allowed and cost is not None:
            measures["dollar share"].append(float(cost) / allowed)
        fields = _ROLE_LIMITS.get(role, (None, None, None, None))
        if fields[1] and seconds is not None and limits.get(fields[1]):
            measures["time window"].append(float(seconds) / limits[fields[1]])
        if seconds and tool_seconds is not None:
            measures["tool share of time"].append(float(tool_seconds) / float(seconds))
        if fields[2] and limits.get(fields[2]) and usage.get("requests"):
            measures["requests"].append(usage["requests"] / limits[fields[2]])
        if role in ("scout", "deep_dive") and limits.get("scout_tokens") and usage.get("input_tokens"):
            measures["input tokens"].append(usage["input_tokens"] / limits["scout_tokens"])
        if fields[3] and limits.get(fields[3]) and messages:
            parsed = ModelMessagesTypeAdapter.validate_python(messages)
            measures["productive calls"].append(tool_yield(parsed).productive / limits[fields[3]])
            cap = (config.get("study_budget") or {}).get("scout_max_output_tokens")
            outputs = [m.usage.output_tokens for m in parsed if isinstance(m, ModelResponse) and m.usage]
            if cap and outputs:
                measures["output cap (largest reply)"].append(max(outputs) / cap)

    print(f"# Where budgets bind: {len(runs)} runs, {len(rows)} calls since {args.since}"
          + (f", study {args.study}" if args.study else "") + "\n")
    print("## What stopped each call\n")
    print("| Role | Version and depth | Calls | Stopped by |")
    print("|---|---|---|---|")
    for (role, family), counter in sorted(stops.items()):
        total = sum(counter.values())
        cells = ", ".join(f"{name} {n} ({n / total:.0%})" for name, n in counter.most_common())
        print(f"| {role} | {family} | {total} | {cells} |")
    print("\n## How much of each limit calls used (median, 90th percentile, largest)\n")
    print("| Role | Version and depth | Limit | Median | p90 | Max | Calls at 90% or more |")
    print("|---|---|---|---|---|---|---|")
    for (role, family), measures in sorted(use.items()):
        for name, values in measures.items():
            near = sum(v >= 0.9 for v in values)
            print(f"| {role} | {family} | {name} | {statistics.median(values):.0%} | {pct(values, 0.9):.0%} "
                  f"| {max(values):.0%} | {near} of {len(values)} |")
    print("\n## Runs\n")
    print("| Version and depth | Runs | Complete | Median cost | Median paid search and reading | Median cost / envelope |")
    print("|---|---|---|---|---|---|")
    families: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for run in runs.values():
        families[run["family"]].append(run)
    for family, group in sorted(families.items()):
        complete = sum(run["status"] == "complete" for run in group)
        envelope = [run["cost"] / run["cap"] for run in group if run["cap"]]
        print(f"| {family} | {len(group)} | {complete} | ${statistics.median(r['cost'] for r in group):.3f} "
              f"| ${statistics.median(r['external'] for r in group):.3f} "
              + (f"| {statistics.median(envelope):.0%} |" if envelope else "| |"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

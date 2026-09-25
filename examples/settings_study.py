"""Run the settings study's questions: each once, generously limited, recorded, and stored for replay.

docs/settings-study.md describes the study. This runs the cases of examples/settings_study.toml
(or the ones --cases names) one at a time on a paid preset, `value` by default, and writes a step
record naming the job each case produced, which `research-grade --jobs-from` reads.

Every paid run is set up the way the study's replays and depth analysis need it:

- Scouts and deep dives get generous limits (below), so where searching stops paying off shows in
  their transcripts instead of being cut off; a loop that still reaches a limit is salvaged. Gap
  analysis, synthesis, and verification get token limits that fit the larger ledgers this makes.
- Each case runs under a total USD cap (--budget, $5), $1 of it held for synthesis and verification.
- The job is stored in Postgres with every agent's full messages (research_task_messages).
- Every search, fetch, and scholarly response is recorded in the study's own cache directory, in
  `record` mode by default, so later replays can reuse exactly what these runs saw.

Without --paid, the synthetic policy runs the same cases with scripted outputs and no model or web
calls, which checks the runner, the record, and grading for free.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from contextlib import AsyncExitStack
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from research_loop import ResearchConfig, ResearchConstraints, ResearchLoop, get_policy
from research_loop.benchmarks import BenchmarkCaseSpec, load_suite
from research_loop.db import open_migrated_pool
from research_loop.experiment import git_state
from research_loop.observability import configure_logfire
from research_loop.policy import ModelPolicy, ModelRoute
from research_loop.repository import (
    InMemoryResearchRepository,
    PostgresResearchRepository,
)
from research_loop.schemas import ResearchRole
from research_loop.settings import ResearchSettings
from research_loop.synthetic import SyntheticResearchLoop
from research_loop.tools import ResearchToolMode

SUITE = Path(__file__).with_name("settings_study.toml")
PAID_POLICIES = ("value", "quality", "breadth", "glm-heavy")
# The reference runs' research limits (docs/settings-study.md, step 1). Dollar caps stay the preset's.
# The token limit counts every request's input, cached or not, plus output, summed over the loop; each
# request resends the loop's history, so it grows with the square of the requests. At 400k it stopped
# the pilot's loops at 14 to 17 requests, well before their request limits. 2M lets the request and
# tool-call limits bind; the dollar caps, which caching keeps well below token counts, bound the cost.
STUDY_TOKENS = 2_000_000
SCOUT_LIMITS = {"max_requests": 24, "max_tool_calls": 48, "total_tokens_limit": STUDY_TOKENS}
# The pilot's deep dives had every source they cited by request 3 but ran on to 13; 12 requests leaves
# four times that to confirm the plateau without paying for the preset's 20.
DEEP_DIVE_LIMITS = {"max_requests": 12, "max_tool_calls": 80, "total_tokens_limit": STUDY_TOKENS}
# The roles that read the research. Deeper research makes a larger ledger, and a finishing call refuses a
# prompt that could not fit one validation retry (about 2 x prompt + 3 x answer) in its token limit: the
# second pilot's five scouts made a 139,000-character gap-analysis prompt, which the preset's 70k refused.
# 600k covers ledgers several times that; dollars, not tokens, bound these calls. scripts/study_preflight.py
# checks a stored run's research against them before a paid step.
FINISHING_TOKENS = 600_000
FINISHING_ROLES = (ResearchRole.GAP_ANALYST, ResearchRole.SYNTHESIZER, ResearchRole.VERIFIER)
STUDY_QUESTION_RANGE = (3, 8)
# The models every paid `value` step runs on, whatever the environment's RESEARCH_*_MODEL overrides say, so
# changing .env for everyday runs cannot change the study partway through. Opus 5.5 plans and synthesizes, as
# the preset intends; the verifier stays on another vendor. Verification-round deep dives use the alternate,
# which needs RESEARCH_ALT_DEEP_MODEL=zai:glm-5.3, since the preset's own alternate is on xAI.
STUDY_MODELS = {
    "planner": "openai:gpt-6-sol",
    # Flash, as the scout trial decided (docs/settings-study.md), and glm-5.3 synthesizing at SYNTHESIS_EFFORT,
    # both chosen 25 September 2026. The value preset keeps glm-5.3 scouting and Opus 5.5 synthesizing until a
    # pilot confirms them, so build() sets these two routes itself.
    "scout": "zai:glm-5.3-flash",
    "gap_analyst": "openai:gpt-6-sol",
    "deep_dive": "openai:gpt-6-sol",
    "synthesizer": "zai:glm-5.3",
    "verifier": "openai:gpt-6-sol",
    "cheap_scout": "zai:glm-5.3-flash",
    "alternate_deep_dive": "zai:glm-5.3",
    # Where the planner and synthesizer go when a model refuses a question, as Opus 5.5 refused task26.
    "refusal_fallback": "openai:gpt-6-sol",
}
# GLM-5.3's highest reasoning effort: PydanticAI maps the unified `xhigh` to Z.ai's `max`. Its output
# allowance covers the reasoning as well as the report, so it is raised from the preset's 32,000.
SYNTHESIS_EFFORT = "xhigh"
SYNTHESIS_MAX_TOKENS = 64_000
CACHE_MODES = ("record", "reuse", "replay", "off")


def _limited(route: ModelRoute | None, limits: dict[str, int]) -> ModelRoute | None:
    return replace(route, **limits) if route else None


def build(paid: bool, budget: float, reserve: float, settings: ResearchSettings, *,
          policy_name: str = "value", cache_mode: str = "record",
          budget_notes: tuple[ResearchRole, ...] = ()) -> tuple[ModelPolicy, ResearchConfig]:
    """The study's policy and run configuration; without `paid`, the synthetic policy with the same run setup."""
    config = ResearchConfig(
        # The README example's trimmed run, which fits the budget: two deep dives a round and one
        # verification round. Its three to five questions are widened below.
        max_verification_rounds=1,
        max_deep_dives_per_round=2,
        salvage_exhausted_research=True,
        tool_mode=ResearchToolMode.NORMALIZED,
        scholarly_cache_mode=cache_mode,
        budget_notes=budget_notes,
    )
    if not paid:
        return get_policy("synthetic"), config
    policy = get_policy(policy_name, model_overrides=settings.model_overrides)
    policy.job_cost_limit = budget
    policy.job_reserve_usd = reserve
    # Three to eight questions: the second pilot's seven-country task got five, so three scouts each covered
    # two countries and ran into their request limit, while the one-country scout stopped by itself at 15.
    # A plan too small to give each scout one subject makes breadth look like depth. Scouts cost about $0.25
    # each and grow linearly with their number, where a longer loop grows with the square of its requests.
    policy.planner_question_range = STUDY_QUESTION_RANGE
    policy.routes[ResearchRole.SCOUT] = _limited(policy.routes[ResearchRole.SCOUT], SCOUT_LIMITS)
    policy.cheap_scout = _limited(policy.cheap_scout, SCOUT_LIMITS)
    policy.routes[ResearchRole.DEEP_DIVE] = _limited(policy.routes[ResearchRole.DEEP_DIVE], DEEP_DIVE_LIMITS)
    policy.alternate_deep_dive = _limited(policy.alternate_deep_dive, DEEP_DIVE_LIMITS)
    for role in FINISHING_ROLES:
        policy.routes[role] = _limited(policy.routes[role], {"total_tokens_limit": FINISHING_TOKENS})
    if policy_name == "value":
        scout = replace(policy.routes[ResearchRole.SCOUT], model=STUDY_MODELS["scout"])
        policy.routes[ResearchRole.SCOUT] = scout
        synthesizer = policy.routes[ResearchRole.SYNTHESIZER]
        policy.routes[ResearchRole.SYNTHESIZER] = replace(
            synthesizer, model=STUDY_MODELS["synthesizer"], thinking=SYNTHESIS_EFFORT,
            settings={**synthesizer.settings, "max_tokens": SYNTHESIS_MAX_TOKENS})
        lineup = {role.value: route.model for role, route in policy.routes.items()} | {
            "cheap_scout": policy.cheap_scout.model, "alternate_deep_dive": policy.alternate_deep_dive.model,
            "refusal_fallback": policy.routes[ResearchRole.SYNTHESIZER].refusal_fallback}
        if moved := [f"{role} is {lineup[role]}, not {model}" for role, model in STUDY_MODELS.items()
                     if lineup[role] != model]:
            raise ValueError("the environment moves the study's models; unset or change its RESEARCH_*_MODEL "
                             "overrides: " + "; ".join(moved))
    policy.validate()
    return policy, config


class _JobRecorder:
    """The repository the loop writes to, noting each job it creates, so a failed case still names its job."""

    def __init__(self, repo: Any) -> None:
        self._repo = repo
        self.job_ids: list[UUID] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self._repo, name)

    async def create_job(self, **kwargs: Any) -> UUID:
        job_id = await self._repo.create_job(**kwargs)
        self.job_ids.append(job_id)
        return job_id


async def run_case(case: BenchmarkCaseSpec, *, paid: bool, policy: ModelPolicy, config: ResearchConfig,
                   settings: ResearchSettings, repo: Any, suite_name: str) -> dict[str, Any]:
    """One case's entry in the step record: its job, cost, and review reasons, or the error type."""
    loop_class = ResearchLoop if paid else SyntheticResearchLoop
    recorder = _JobRecorder(repo)
    loop = loop_class(policy, config, repository=recorder, settings=settings)
    started = datetime.now(UTC)
    entry: dict[str, Any] = {"case_id": case.case_id, "started_at": started.isoformat()}
    try:
        outcome = await loop.run(case.render_objective(), constraints=ResearchConstraints(
            blocked_urls=case.blocked_urls, benchmark_id=case.benchmark_id, benchmark_case_id=case.case_id,
            benchmark_suite=suite_name))
    except Exception as exc:  # noqa: BLE001 - one failed case must not stop the step; the type only, as bodies can leak
        # The job, if one was created, keeps the research spent before the failure for scripts/study_preflight.py.
        job = {"job_id": str(recorder.job_ids[-1])} if recorder.job_ids else {}
        return entry | {"status": "failed", "error": type(exc).__name__, **job,
                        "finished_at": datetime.now(UTC).isoformat()}
    return entry | {
        "status": "succeeded",
        "job_id": str(outcome.job_id),
        "cost_usd": None if outcome.cost_usd is None else float(outcome.cost_usd),
        "review_reasons": outcome.review_reasons,
        "finished_at": datetime.now(UTC).isoformat(),
    }


async def run(*, cases: list[BenchmarkCaseSpec], paid: bool, persist: bool, budget: float, reserve: float,
              settings: ResearchSettings, policy_name: str, cache_mode: str, step: str,
              output: Path, suite_name: str, budget_notes: tuple[ResearchRole, ...] = ()) -> dict[str, Any]:
    policy, config = build(paid, budget, reserve, settings, policy_name=policy_name, cache_mode=cache_mode,
                           budget_notes=budget_notes)
    configure_logfire(settings)
    record: dict[str, Any] = {
        "step": step,
        "suite": suite_name,
        "paid": paid,
        "started_at": datetime.now(UTC).isoformat(),
        "git": git_state(),
        "policy": policy.snapshot(),
        "config": asdict(config),
        "cache": {"mode": cache_mode, "dir": str(settings.benchmark_cache)},
        "persisted": persist,
        "runs": [],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    async with AsyncExitStack() as stack:
        if persist:
            # Refuses before any model call if migrations are pending or changed.
            pool = await open_migrated_pool(stack, settings.database_dsn)
            repo: Any = PostgresResearchRepository(pool, capture_transcripts=True)
        else:
            repo = InMemoryResearchRepository(capture_transcripts=True)
        for case in cases:
            record["runs"].append(await run_case(case, paid=paid, policy=policy, config=config,
                                                 settings=settings, repo=repo, suite_name=suite_name))
            # Written after every case, so an interrupted step still names the jobs it finished.
            output.write_text(json.dumps(record, indent=2, default=str) + "\n", encoding="utf-8")
    record["finished_at"] = datetime.now(UTC).isoformat()
    output.write_text(json.dumps(record, indent=2, default=str) + "\n", encoding="utf-8")
    return record


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the settings study's questions and record their jobs")
    parser.add_argument("--step", required=True, help="Study step this run belongs to, such as step1-pilot")
    parser.add_argument("--cases", nargs="+", metavar="CASE_ID",
                        help="Cases to run (default: every case not marked retired)")
    parser.add_argument("--paid", action="store_true", help="Call real models; otherwise run the synthetic policy")
    parser.add_argument("--policy", choices=PAID_POLICIES, default="value", help="Preset to run with --paid (default value)")
    # The pilot's planning, synthesis, and verification cost $0.30-0.60, so $1.00 is held back and research
    # gets $4.00, above a deep question's projected $3.50: the cap guards against a runaway run.
    parser.add_argument("--budget", type=float, default=5.0, help="Total USD cap per case (default 5.00)")
    parser.add_argument("--reserve", type=float, default=1.0,
                        help="USD of each case's budget held for synthesis and verification (default 1.00)")
    parser.add_argument("--cache-mode", choices=CACHE_MODES, default="record",
                        help="Acquisition cache mode (default record: store every call, read none)")
    parser.add_argument("--cache-dir", type=Path,
                        help="The study's cache directory (default: benchmark_outputs/settings_study/cache)")
    parser.add_argument("--persist", action="store_true",
                        help="Store synthetic runs in Postgres too, to try grading them (paid runs always are)")
    # Scouts and deep dives cannot see their limits, so every one in the pilots ran into them and was salvaged.
    parser.add_argument("--budget-notes", nargs="+", default=[],
                        choices=[ResearchRole.SCOUT.value, ResearchRole.DEEP_DIVE.value],
                        help="These tool loops end each request with the requests and tool calls they have left")
    parser.add_argument("--output", type=Path, help="Step record (default: benchmark_outputs/settings_study/<step>/<time>.json)")
    args = parser.parse_args(argv)
    settings = ResearchSettings.from_env()
    if args.policy != "value" and not args.paid:
        parser.error("--policy chooses the paid preset; add --paid")
    # Paid runs are only useful to the study stored with their transcripts.
    persist = args.paid or args.persist
    if persist and not settings.database_dsn:
        parser.error("study runs are stored in Postgres; set DATABASE_URL (see docs/setup.md#postgres)")
    manifest, specs = load_suite(SUITE)
    suite_name = manifest.name
    by_id = {spec.case_id: spec for spec in specs}
    if unknown := sorted(set(args.cases or ()) - set(by_id)):
        parser.error(f"not in {SUITE.name}: {', '.join(unknown)}")
    cases = [by_id[case_id] for case_id in args.cases] if args.cases else [
        spec for spec in specs if not spec.metadata.get("retired")]
    settings = settings.model_copy(update={
        "benchmark_cache": args.cache_dir or settings.benchmark_output / "settings_study" / "cache"})
    budget_notes = tuple(ResearchRole(role) for role in args.budget_notes)
    try:
        build(args.paid, args.budget, args.reserve, settings, policy_name=args.policy, cache_mode=args.cache_mode,
              budget_notes=budget_notes)
    except ValueError as exc:  # a budget or reserve no policy can run under
        parser.error(str(exc))
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output = args.output or settings.benchmark_output / "settings_study" / args.step / f"{stamp}.json"
    record = asyncio.run(run(cases=cases, paid=args.paid, persist=persist, budget=args.budget,
                             reserve=args.reserve, settings=settings, policy_name=args.policy,
                             cache_mode=args.cache_mode, step=args.step, output=output, suite_name=suite_name,
                             budget_notes=budget_notes))
    for entry in record["runs"]:
        detail = f"job_id={entry['job_id']} cost_usd={entry['cost_usd']}" if entry["status"] == "succeeded" \
            else f"failed ({entry['error']})"
        print(f"{entry['case_id']}: {detail}")
        for reason in entry.get("review_reasons", []):
            print(f"  needs review: {reason}")
    print(f"step record: {output}")
    if persist:
        print(f"grade it: research-grade {SUITE} --jobs-from {output}")


if __name__ == "__main__":
    main()

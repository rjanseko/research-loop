"""The Scout workflow: plan, research each question in parallel, and write a cited answer.

A normal run has three steps, each bounded:

1. The planner splits the question into one to `max_questions` research questions. If planning fails,
   the whole question is researched as one.
2. A scout researches each question with the web and scholarly tools, all at once up to
   `parallel_scouts`. Each scout is its own call with its own limits. When less than one request
   timeout remains, it is told to return and loses its tools, so it can write the claims it has.
   One that runs out, fails, or is still running at the research deadline leaves its question
   unanswered and keeps what it searched and read. Code checks every result against the tools'
   labeled output (evidence.py).
3. The synthesizer writes the report from the evidence ledger. If it cannot finish, the run returns the
   ledger's claims without a written answer.

Opt-in follow-up mode inserts a material-gap analysis after step 2 and at most one targeted deep dive,
then synthesizes the enlarged ledger. It has a separate dollar and time envelope.

Dollars are allocated before the run starts: the planner's share, the synthesizer's share, and the rest
split evenly across the scouts. Each call's share is its PydanticAI `cost_limit`, checked before every
request, so a call can pass its share by at most the one request that crossed it. Time is bounded by
`research_seconds` for the scouts and `deadline_seconds` for the whole run, and each model request by
`request_timeout_seconds`. A scout's provider client does not retry a request that hits that timeout.

Every call is recorded in the run store with its usage, cost, output, and messages, and the whole run is
one Logfire trace carrying the run ID.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
import time
from collections.abc import AsyncIterable, Awaitable, Callable, Sequence
from contextlib import AsyncExitStack, suppress
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4

import httpx
import logfire
from pydantic import BaseModel, Field
from pydantic_ai import Agent, UsageLimits, capture_run_messages
from pydantic_ai.exceptions import (
    ContentFilterError,
    ModelAPIError,
    UnexpectedModelBehavior,
    UsageLimitExceeded,
)
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import Model
from pydantic_ai.usage import RunUsage

from .acquisition import (
    FETCH_VERSION,
    AcquisitionCache,
    FetchMemo,
    SourcePolicy,
    public_fetch_client,
)
from .agents import (
    Assignment,
    GapRefs,
    LedgerRefs,
    PlanLimits,
    gap_agent,
    planner_agent,
    scout_agent,
    synthesizer_agent,
)
from .budget_notes import LoopBudget
from .config import ScoutLimits, Settings
from .evidence import (
    EvidenceLedger,
    Support,
    check_result,
    citation_problems,
    support_level,
)
from .models import (
    Role,
    build_model,
    effort_for,
    role_effort,
    role_model,
    sent_settings,
)
from .prices import price_per_million
from .prompts import prompt_fingerprint
from .rate_limit import RATE_LIMIT_POLICY_VERSION, ScoutRateLimitModel
from .schemas import (
    EVIDENCE_VERSION,
    FinalReport,
    GapAnalysis,
    MaterialGap,
    ResearchPlan,
    ResearchQuestion,
    ResearchResult,
    UnreachedSource,
)
from .scholar import ScholarClient
from .store import MemoryStore, RunStore
from .study_budget import (
    BUDGET_POLICY_VERSION,
    StudyBudget,
    StudyBudgetModel,
    StudyBudgetRefusal,
)
from .telemetry import run_span, trace_id
from .tools import TimedToolset, labeled_texts, research_toolset, tool_outcomes
from .web import WebAcquisition, WebSearch

WORKFLOW_VERSION = "scout-v1"
FOLLOWUP_VERSION = "scout-followup-v1"
RESCOUT_VERSION = "scout-research-v1"
Status = Literal["complete", "partial", "failed", "cancelled"]
# Failures a single call can end on without the run failing: a limit, a provider error the SDK's retries did
# not clear, a refusal, output that failed its checks twice, or a deadline.
_CALL_FAILURES = (UsageLimitExceeded, ModelAPIError, ContentFilterError, UnexpectedModelBehavior,
                  TimeoutError, StudyBudgetRefusal)
# How long recording a failed or cancelled call may take before it is given up.
_RECORD_SECONDS = 5
# Planning gets this long before the question is researched as one, so a slow plan cannot use up the scouts' time.
_PLAN_SECONDS = 90


@dataclass(frozen=True)
class StudyLabels:
    """Where a run sits in a study: which study, which arm of it, and which repetition."""

    study_id: str
    arm: str
    replicate: int = 1


def input_hash(question: str, notes: Sequence[str], blocked_urls: Sequence[str]) -> str:
    """A digest of what a run was asked, so runs given the same input can be paired."""
    canonical = json.dumps({"question": question, "notes": list(notes), "blocked_urls": list(blocked_urls)},
                           ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


class ConfigError(RuntimeError):
    """The configured models cannot run: a missing key, a disabled provider, or no price to cap cost with."""


class StatementCheck(BaseModel):
    statement: str
    claim_ids: list[str]
    support: Support


class RunChecks(BaseModel):
    """What code found in a finished run. Empty `review_reasons` means no check flagged a problem."""

    citation_problems: list[str] = Field(default_factory=list)
    statements: list[StatementCheck] = Field(default_factory=list)
    not_established: list[str] = Field(default_factory=list)
    unreached: list[UnreachedSource] = Field(default_factory=list)
    evidence_by_access: dict[str, int] = Field(default_factory=dict)
    quotes: int = 0
    quotes_verified: int = 0
    review_reasons: list[str] = Field(default_factory=list)
    gap_analysis: GapAnalysis | None = None
    follow_up_unresolved: bool = False
    study_budget_reserved_usd: Decimal | None = None


@dataclass
class ScoutRun:
    run_id: UUID
    question: str
    status: Status
    plan: ResearchPlan | None
    report: FinalReport | None
    ledger: EvidenceLedger
    checks: RunChecks
    cost_usd: Decimal | None
    seconds: float
    trace_id: str | None
    notes: list[str]
    config: dict[str, Any]
    workflow_version: str = WORKFLOW_VERSION

    def to_record(self) -> dict[str, Any]:
        """The run as JSON: what `research show` renders and evals read."""
        return {
            "run_id": str(self.run_id), "question": self.question, "status": self.status,
            "workflow_version": self.workflow_version,
            "plan": self.plan.model_dump(mode="json") if self.plan else None,
            "report": self.report.model_dump(mode="json") if self.report else None,
            "ledger": self.ledger.to_json(), "sources": self.ledger.source_table(),
            "checks": self.checks.model_dump(mode="json"),
            "cost_usd": float(self.cost_usd) if self.cost_usd is not None else None,
            "seconds": round(self.seconds, 1), "trace_id": self.trace_id, "notes": self.notes, "config": self.config,
        }


def check_config(settings: Settings) -> None:
    """Refuse, before any call, models that cannot run or whose cost cannot be capped."""
    problems = settings.route_problems()
    models = {settings.models.planner, settings.models.scout, settings.models.synthesizer, settings.models.fallback}
    problems += [f"{model} has no price, so its cost cannot be capped; add it to prices.toml"
                 for model in sorted(filter(None, models)) if price_per_million(model) is None]
    if problems:
        raise ConfigError("; ".join(problems))


def _git_commit() -> str | None:
    with suppress(OSError, subprocess.SubprocessError):
        done = subprocess.run(["git", "rev-parse", "HEAD"], cwd=Path(__file__).parent, capture_output=True,
                              text=True, timeout=2, check=False)
        if done.returncode == 0:
            return done.stdout.strip()
    return None


def run_config(settings: Settings, notes: Sequence[str], blocked_urls: Sequence[str],
               *, follow_up: bool = False) -> dict[str, Any]:
    """What a run records about how it was configured, so runs can be compared."""
    models = settings.models
    roles: dict[str, Any] = {role: {"model": model_id, "thinking": role_effort(role, model_id, settings),
                                    "sent": sent_settings(model_id, role, settings)}
                             for role, model_id in (("planner", models.planner), ("scout", models.scout),
                                                    ("synthesizer", models.synthesizer))}
    if follow_up:
        roles["gap_analyzer"] = roles["planner"]
        roles["deep_dive"] = roles["scout"]
    if models.fallback:
        # The fallback takes planner and synthesizer calls, each with that role's settings.
        roles["fallback"] = {"model": models.fallback, "thinking": effort_for(models.fallback),
                             "sent": {role: sent_settings(models.fallback, role, settings)
                                      for role in ("planner", "synthesizer")}}
    return {
        "models": roles,
        "limits": settings.limits.model_dump(),
        "prompt_fingerprint": prompt_fingerprint(follow_up=follow_up), "evidence_version": EVIDENCE_VERSION,
        "fetch_version": FETCH_VERSION, "cache_mode": settings.cache_mode, "git_commit": _git_commit(),
        "rate_limit_policy": RATE_LIMIT_POLICY_VERSION,
        "notes": list(notes), "blocked_urls": list(blocked_urls),
    }


async def _ignore_events(_: Any, events: AsyncIterable[Any]) -> None:
    """Consuming the event stream makes each model request a streamed one, so the read timeout applies
    between chunks instead of to the whole reply: a long report can take minutes to write."""
    async for _event in events:
        pass


async def _record(awaitable: Awaitable[Any]) -> None:
    """Record a failure without replacing it: bounded, shielded from cancellation, and never raising."""
    task = asyncio.ensure_future(awaitable)
    with suppress(Exception, asyncio.CancelledError):
        await asyncio.wait_for(asyncio.shield(task), _RECORD_SECONDS)


def _reason(exc: BaseException) -> str:
    if isinstance(exc, UsageLimitExceeded):
        return f"limit reached: {exc}"
    if isinstance(exc, StudyBudgetRefusal):
        return f"study budget refused: {exc}"
    if isinstance(exc, ModelAPIError):
        status = getattr(exc, "status_code", None)
        return f"provider error {type(exc).__name__}{f' {status}' if status else ''}"
    if isinstance(exc, ContentFilterError):
        return "the model refused"
    if isinstance(exc, UnexpectedModelBehavior):
        return "the model's output failed its checks twice"
    if isinstance(exc, TimeoutError | asyncio.CancelledError):
        return "the research deadline passed"
    return type(exc).__name__


def _cut_off(question: ResearchQuestion, messages: list[ModelMessage], reason: str) -> ResearchResult:
    """A question's result when its research stopped before returning one: what it searched and read, no claims."""
    outcomes = tool_outcomes(messages)
    return ResearchResult(
        question_id=question.id, question=question.question, confidence=0.0, cut_off=reason,
        conclusion=f"Research on this question stopped before it returned a result ({reason}).",
        searches=outcomes.searches, pages_read=outcomes.pages_read, unreached=outcomes.unreached)


@dataclass
class _Attempt:
    """One scout's question, and the messages its call has exchanged so far."""

    question: ResearchQuestion
    messages: list[ModelMessage] = field(default_factory=list)


class _Run:
    def __init__(self, question: str, settings: Settings, store: RunStore, notes: Sequence[str],
                 blocked_urls: Sequence[str], parent_run_id: UUID | None, study: StudyLabels | None,
                 follow_up: bool = False, budget: StudyBudget | None = None,
                 case_identity: dict[str, Any] | None = None) -> None:
        self.question, self.settings, self.store, self.study = question, settings, store, study
        self.limits: ScoutLimits = settings.limits
        self.follow_up, self.budget, self.case_identity = follow_up, budget, case_identity
        self.workflow_version = FOLLOWUP_VERSION if follow_up else WORKFLOW_VERSION
        self.notes_in, self.blocked_urls, self.parent_run_id = list(notes), list(blocked_urls), parent_run_id
        self.run_id = uuid4()
        self.cost = Decimal(0)
        self.unpriced = False
        self.notes: list[str] = []
        self.policy = SourcePolicy(tuple(blocked_urls))
        self.models: dict[Role, Model] = {}
        self.caches: list[AcquisitionCache] = []
        # Set when the research deadline, not a cancelled run, stopped the scouts still running.
        self.research_timed_out = False

    def _model(self, role: Role) -> Model:
        if role not in self.models:
            if self.budget is None:
                self.models[role] = role_model(role, self.settings)
            else:
                model_id = getattr(self.settings.models, role)
                guarded = StudyBudgetModel(build_model(model_id, role, self.settings, sdk_retries=0),
                                           model_id, self.budget)
                # The guard is inside the 429 wrapper, so every retry reserves a new request.
                self.models[role] = ScoutRateLimitModel(guarded) if role == "scout" else guarded
        return self.models[role]

    def _spend(self, usage: RunUsage, *, refused: bool = False) -> None:
        if not usage.requests:
            return
        if usage.cost is None:
            # PydanticAI counts a request before the budget guard refuses it; a refusal that is the
            # call's only request sent nothing and has no price to report.
            if not (refused and not usage.input_tokens and not usage.output_tokens):
                self.unpriced = True
        else:
            self.cost += usage.cost

    async def _call(self, *, role: Role, agent: Agent[Any, Any], prompt: str, deps: Any, limits: UsageLimits,
                    question_id: str | None = None, capabilities: list[Any] | None = None,
                    toolsets: list[Any] | None = None, stream: bool = False, attempt: _Attempt | None = None,
                    finish: Callable[[Any], Any] | None = None, finished: Callable[[Any], str] | None = None,
                    cancelled: Callable[[], str] | None = None, tool_seconds: Callable[[], float] | None = None,
                    call_role: str | None = None) -> Any:
        """Run one agent call as a recorded call; return its output, after `finish` when given.

        The call's row records why it stopped: `finished` names it for a result, `cancelled` for a
        cancellation (usually a deadline), and a failure is named by its exception.
        """
        model_id: str = getattr(self.settings.models, role)
        call_id = await self.store.start_call(self.run_id, role=call_role or role, model=model_id, question_id=question_id)
        usage = RunUsage()
        messages: list[ModelMessage] = []
        try:
            with capture_run_messages() as messages:
                if attempt is not None:
                    attempt.messages = messages
                output_cap = (16_000 if role == "planner" else self.limits.guarded_scout_max_output_tokens
                              if role == "scout" else self.limits.synthesis_max_output_tokens)
                result = await agent.run(prompt, model=self._model(role), deps=deps, usage_limits=limits, usage=usage,
                                         model_settings={"max_tokens": output_cap} if self.budget else None,
                                         capabilities=capabilities, toolsets=toolsets,
                                         event_stream_handler=_ignore_events if stream else None)
            output = finish(result) if finish else result.output
        except BaseException as exc:
            self._spend(usage, refused=isinstance(exc, StudyBudgetRefusal))
            if isinstance(exc, asyncio.CancelledError):
                status, reason = "cancelled", cancelled() if cancelled else "the run was cancelled"
            else:
                status, reason = "failed", _reason(exc)
            await _record(self.store.finish_call(call_id, status=status, usage=usage, cost_usd=usage.cost,
                                                 messages=list(messages), error=exc, stop_reason=reason,
                                                 tool_seconds=tool_seconds() if tool_seconds else None))
            raise
        self._spend(usage)
        self._note_fallback(role, result.all_messages())
        await self.store.finish_call(call_id, status="succeeded", usage=usage, cost_usd=usage.cost, output=output,
                                     messages=result.all_messages(),
                                     stop_reason=finished(result) if finished else "returned a result",
                                     tool_seconds=tool_seconds() if tool_seconds else None)
        return output

    def _note_fallback(self, role: Role, messages: list[ModelMessage]) -> None:
        fallback = self.settings.models.fallback
        primary: str = getattr(self.settings.models, role)
        if not fallback or fallback == primary or role == "scout":
            return
        names = {message.model_name for message in messages if isinstance(message, ModelResponse)}
        if fallback.partition(":")[2] in names:
            self.notes.append(f"the {role} ran on {fallback} after {primary} refused or failed")

    async def _plan(self, research_deadline: float) -> ResearchPlan:
        """The plan, within `_PLAN_SECONDS` of now and before `research_deadline`, whichever comes first."""
        prompt = json.dumps({"question": self.question, "notes": self.notes_in,
                             "max_questions": self.limits.max_questions}, ensure_ascii=False)
        deadline = asyncio.get_running_loop().time() + _PLAN_SECONDS
        timed_out = f"planning took longer than {_PLAN_SECONDS:g} seconds"
        if research_deadline <= deadline:
            deadline, timed_out = research_deadline, _reason(TimeoutError())
        try:
            with logfire.span("plan"):
                async with asyncio.timeout_at(deadline):
                    plan: ResearchPlan = await self._call(
                        role="planner", agent=planner_agent, prompt=prompt, deps=PlanLimits(self.limits.max_questions),
                        limits=UsageLimits(request_limit=2, total_tokens_limit=100_000,
                                           cost_limit=Decimal(str(self.limits.planner_usd))),
                        cancelled=lambda: timed_out)
        except _CALL_FAILURES as exc:
            reason = timed_out if isinstance(exc, TimeoutError) else _reason(exc)
            self.notes.append(f"planning failed ({reason}), so the question was researched as one")
            plan = ResearchPlan(questions=[ResearchQuestion(id="q1", question=self.question)])
        # Plan order names the questions, so claim IDs read q1/c1, q2/c1, ... whatever IDs the planner chose.
        return ResearchPlan(questions=[q.model_copy(update={"id": f"q{i}"}) for i, q in enumerate(plan.questions, 1)])

    async def _scout(self, attempt: _Attempt, semaphore: asyncio.Semaphore, share: Decimal,
                     toolset: TimedToolset, deadline: float, *, gap: str | None = None,
                     ledger: EvidenceLedger | None = None) -> ResearchResult:
        limits, question = self.limits, attempt.question
        # The budget note runs off the event loop, so the clock is taken here and only read later.
        clock = asyncio.get_running_loop()

        def time_left() -> float:
            return deadline - clock.time()

        deep = gap is not None
        requests = limits.deep_dive_requests if deep else limits.scout_requests
        productive = limits.deep_dive_productive_calls if deep else limits.scout_productive_calls
        misses = limits.deep_dive_misses if deep else limits.scout_misses
        budget = LoopBudget(requests, productive, misses, time_left=time_left,
                            return_within=limits.request_timeout_seconds)
        prompt_data: dict[str, Any] = {"question": question.model_dump(mode="json"), "notes": self.notes_in,
                                       "blocked_urls": self.blocked_urls}
        if deep:
            prompt_data.update({"material_gap": gap, "known_research": ledger.prompt_view() if ledger else {},
                                "instruction": "Investigate this gap. Cite only sources returned by your own tools."})
        prompt = json.dumps(prompt_data, ensure_ascii=False)
        tool_seconds_before = toolset.seconds(question.id)

        def checked(result: Any) -> ResearchResult:
            messages = result.all_messages()
            outcomes = tool_outcomes(messages)
            return check_result(result.output, labeled_texts(messages)).model_copy(update={
                "searches": outcomes.searches, "pages_read": outcomes.pages_read, "unreached": outcomes.unreached})

        async with semaphore:
            with logfire.span("deep dive {question_id}" if deep else "scout {question_id}", question_id=question.id):
                try:
                    return await self._call(
                        role="scout", call_role="deep_dive" if deep else None, agent=scout_agent,
                        prompt=prompt, question_id=question.id,
                        deps=Assignment(question, self.policy), toolsets=[toolset], capabilities=budget.capabilities(),
                        limits=UsageLimits(
                            request_limit=requests, total_tokens_limit=limits.scout_tokens, cost_limit=share,
                            tool_calls_limit=budget.tool_call_limit),
                        attempt=attempt, finish=checked,
                        finished=lambda result: budget.finish_reason(result.usage.requests, result.all_messages()),
                        cancelled=lambda: ("the deep-dive deadline passed" if deep else
                                           _reason(TimeoutError()) if self.research_timed_out else
                                           "the run was cancelled"),
                        tool_seconds=lambda: toolset.seconds(question.id) - tool_seconds_before)
                except _CALL_FAILURES as exc:
                    return _cut_off(question, attempt.messages, _reason(exc))

    async def _research(self, plan: ResearchPlan, deadline: float, toolset: TimedToolset) -> list[ResearchResult]:
        """Every question's result in plan order; questions still running at `deadline` are cut off."""
        attempts = [_Attempt(question) for question in plan.questions]
        share = Decimal(str(self.limits.followup_scout_usd(len(attempts)) if self.follow_up
                            else self.limits.scout_usd(len(attempts))))
        semaphore = asyncio.Semaphore(self.limits.parallel_scouts)
        tasks = [asyncio.create_task(self._scout(attempt, semaphore, share, toolset, deadline))
                 for attempt in attempts]
        try:
            _, running = await asyncio.wait(tasks, timeout=max(deadline - asyncio.get_running_loop().time(), 0))
            self.research_timed_out = bool(running)
        finally:
            # At the deadline, or when the run itself is cancelled, stop what is still running and let it record itself.
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        results = []
        for attempt, task in zip(attempts, tasks, strict=True):
            if task.cancelled():
                results.append(_cut_off(attempt.question, attempt.messages, _reason(TimeoutError())))
            elif (exc := task.exception()) is not None:
                raise exc  # _scout turns every expected failure into a result, so this is a bug
            else:
                results.append(task.result())
        return results

    async def _analyze_gap(self, plan: ResearchPlan, ledger: EvidenceLedger, deadline: float) -> GapAnalysis | None:
        prompt = json.dumps({"question": self.question, "notes": self.notes_in, "blocked_urls": self.blocked_urls,
                             "plan": plan.model_dump(mode="json"), "research": ledger.prompt_view(),
                             "not_established": _not_established(plan, ledger)}, ensure_ascii=False)
        try:
            with logfire.span("gap analysis"):
                async with asyncio.timeout_at(deadline):
                    return await self._call(
                        role="planner", call_role="gap_analyzer", agent=gap_agent, prompt=prompt,
                        deps=GapRefs(frozenset(q.id for q in plan.questions)),
                        limits=UsageLimits(request_limit=2, total_tokens_limit=100_000,
                                           cost_limit=Decimal(str(self.limits.gap_usd))),
                        cancelled=lambda: "the gap-analysis deadline passed")
        except _CALL_FAILURES as exc:
            self.notes.append(f"gap analysis did not finish ({_reason(exc)})")
            return None

    async def _deep_dive(self, gap: MaterialGap, plan: ResearchPlan, ledger: EvidenceLedger,
                         toolset: TimedToolset, deadline: float) -> ResearchResult:
        original = next(q for q in plan.questions if q.id == gap.question_id)
        question = original.model_copy(update={"question": gap.follow_up_question})
        attempt = _Attempt(question)
        try:
            async with asyncio.timeout_at(deadline):
                result = await self._scout(attempt, asyncio.Semaphore(1), Decimal(str(self.limits.deep_dive_usd)),
                                           toolset, deadline, gap=gap.reason, ledger=ledger)
        except TimeoutError:
            result = _cut_off(question, attempt.messages, "the deep-dive deadline passed")
        ledger.add(result)
        if result.cut_off:
            self.notes.append(f"deep dive for {gap.question_id} did not finish ({result.cut_off})")
        return result

    async def _synthesize(self, plan: ResearchPlan, ledger: EvidenceLedger, deadline: float) -> FinalReport | None:
        prompt = json.dumps({"question": self.question, "notes": self.notes_in, "research": ledger.prompt_view(),
                             "not_established": _not_established(plan, ledger)}, ensure_ascii=False)
        limits = self.limits
        try:
            with logfire.span("synthesize"):
                async with asyncio.timeout_at(deadline):
                    return await self._call(
                        role="synthesizer", agent=synthesizer_agent, prompt=prompt, stream=True,
                        deps=LedgerRefs(frozenset(ledger.claim_ids()), ledger.claim_source_ids()),
                        limits=UsageLimits(request_limit=2, total_tokens_limit=limits.synthesis_tokens,
                                           cost_limit=Decimal(str(limits.synthesis_usd))),
                        cancelled=lambda: "the run deadline passed")
        except _CALL_FAILURES as exc:
            self.notes.append(f"the synthesis did not finish ({_reason(exc).replace('research deadline', 'run deadline')})")
            return None

    async def execute(self) -> ScoutRun:
        config = run_config(self.settings, self.notes_in, self.blocked_urls, follow_up=self.follow_up)
        config["follow_up"] = self.follow_up
        if self.budget:
            config["study_budget"] = {"cap_usd": str(self.budget.cap_usd), "policy": BUDGET_POLICY_VERSION,
                                      "sdk_retries": 0, "fallback": False,
                                      "scout_max_output_tokens": self.limits.guarded_scout_max_output_tokens}
        if self.case_identity:
            config["case"] = self.case_identity
        await self.store.start_run(self.run_id, mode="scout", workflow_version=self.workflow_version,
                                   question=self.question, config=config, parent_run_id=self.parent_run_id,
                                   input_hash=input_hash(self.question, self.notes_in, self.blocked_urls),
                                   study_id=self.study.study_id if self.study else None,
                                   arm=self.study.arm if self.study else None,
                                   replicate=self.study.replicate if self.study else None)
        started, loop = time.monotonic(), asyncio.get_running_loop()
        research_deadline = loop.time() + self.limits.research_seconds
        deadline = loop.time() + (self.limits.followup_deadline_seconds if self.follow_up
                                  else self.limits.deadline_seconds)
        ledger, plan, report, span_trace = EvidenceLedger(), None, None, None
        analysis: GapAnalysis | None = None
        follow_up_unresolved = False
        try:
            async with AsyncExitStack() as stack:
                span = stack.enter_context(run_span(self.run_id, "scout", self.workflow_version))
                span_trace = trace_id(span)
                toolset = await self._toolset(stack)
                plan = await self._plan(research_deadline)
                for result in await self._research(plan, research_deadline, toolset):
                    ledger.add(result)
                if self.follow_up:
                    gap_deadline = min(loop.time() + self.limits.gap_seconds,
                                       deadline - self.limits.deep_dive_seconds - 90)
                    analysis = await self._analyze_gap(plan, ledger, gap_deadline)
                    if analysis and analysis.gaps:
                        gap = analysis.gaps[0]
                        dive_deadline = min(loop.time() + self.limits.deep_dive_seconds, deadline - 90)
                        deep_result = await self._deep_dive(gap, plan, ledger, toolset, dive_deadline)
                        follow_up_unresolved = bool(deep_result.cut_off or not deep_result.claims
                                                    or deep_result.unresolved)
                    elif analysis is None:
                        follow_up_unresolved = True
                if ledger.claims():
                    report = await self._synthesize(plan, ledger, deadline)
                checks = _checks(plan, ledger, report, self.unpriced)
                checks.gap_analysis = analysis
                checks.follow_up_unresolved = follow_up_unresolved
                checks.study_budget_reserved_usd = self.budget.reserved_usd if self.budget else None
                if follow_up_unresolved:
                    checks.review_reasons.append("the material gap follow-up did not establish a complete answer"
                                                 if analysis else "gap analysis did not finish")
                status = _status(plan, ledger, report, checks)
                span.set_attributes({"status": status, "cost_usd": float(self.cost)})
        except BaseException as exc:
            status = "cancelled" if isinstance(exc, asyncio.CancelledError | KeyboardInterrupt) else "failed"
            failure_checks = (RunChecks(study_budget_reserved_usd=self.budget.reserved_usd)
                              if self.budget else None)
            await _record(self.store.finish_run(self.run_id, status=status, plan=plan, ledger=ledger.to_json(),
                                                checks=failure_checks, cost_usd=self.cost, error=_error(exc),
                                                trace_id=span_trace, cache=self._cache_counts()))
            raise
        # A call without a price adds nothing, so the cost is then a lower bound; a review reason says so.
        await self.store.finish_run(self.run_id, status=status, plan=plan, report=report, ledger=ledger.to_json(),
                                    checks=checks, cost_usd=self.cost, trace_id=span_trace, cache=self._cache_counts())
        return ScoutRun(self.run_id, self.question, status, plan, report, ledger, checks, self.cost,
                        time.monotonic() - started, span_trace, self.notes, config, self.workflow_version)

    def _cache_counts(self) -> dict[str, Any]:
        """The run's cache mode and, by provider, the lookups its tools served, missed, and wrote."""
        counts: dict[str, dict[str, int]] = {}
        for cache in self.caches:
            for provider, tally in cache.counts.items():
                total = counts.setdefault(provider, dict.fromkeys(tally, 0))
                for outcome, n in tally.items():
                    total[outcome] += n
        return {"mode": self.settings.cache_mode, "by_provider": counts}

    async def _toolset(self, stack: AsyncExitStack) -> TimedToolset:
        """The run's research tools, sharing one fetch memo and one pair of HTTP clients across its scouts."""
        cache, settings = self.settings.cache_dir, self.settings
        memo = FetchMemo()
        pages_client = await stack.enter_async_context(public_fetch_client(timeout=15))
        metadata_client = await stack.enter_async_context(httpx.AsyncClient(follow_redirects=False, timeout=15))
        search = WebSearch(cache=AcquisitionCache(cache / "search", settings.cache_mode))
        pages = WebAcquisition(cache_root=cache / "web", cache_mode=settings.cache_mode, client=pages_client,
                               memo=memo, policy=self.policy)
        scholar = ScholarClient(cache=AcquisitionCache(cache / "scholarly", settings.cache_mode), client=metadata_client,
                                api_key=settings.openalex_api_key.get_secret_value() if settings.openalex_api_key else None,
                                contact_email=settings.crossref_mailto)
        self.caches = [cache for cache in (search.cache, pages.cache, scholar.cache) if cache is not None]
        return TimedToolset(research_toolset(search, pages, scholar))


def _error(exc: BaseException) -> dict[str, str]:
    return {"type": type(exc).__name__, "message": str(exc)[:1000]}


def _not_established(plan: ResearchPlan, ledger: EvidenceLedger) -> list[str]:
    answered = ledger.question_ids_with_claims()
    reasons = {result.question_id: result.cut_off for result in ledger.all() if result.cut_off}
    return [f"{q.id}: {q.question}" + (f" ({reasons[q.id]})" if q.id in reasons else " (no evidence found)")
            for q in plan.questions if q.id not in answered]


def _checks(plan: ResearchPlan, ledger: EvidenceLedger, report: FinalReport | None, unpriced: bool,
            *, synthesized: bool = True) -> RunChecks:
    claims = ledger.claims_by_id()
    evidence = [item for claim in claims.values() for item in claim.evidence]
    checks = RunChecks(
        citation_problems=citation_problems(report, ledger) if report else [],
        statements=[StatementCheck(statement=c.statement, claim_ids=c.claim_ids,
                                   support=support_level(claims[i] for i in c.claim_ids if i in claims))
                    for c in (report.claims if report else [])],
        not_established=_not_established(plan, ledger),
        unreached=[item for result in ledger.all() for item in result.unreached],
        quotes=sum(item.quote_check is not None for item in evidence),
        quotes_verified=sum(item.quote_check == "verified" for item in evidence),
    )
    for item in evidence:
        key = item.source_access or "not_returned"
        checks.evidence_by_access[key] = checks.evidence_by_access.get(key, 0) + 1
    reasons = checks.review_reasons
    if report is None and not claims:
        reasons.append("no research question returned evidence")
    elif report is None and synthesized:
        reasons.append("the synthesis did not finish, so this lists the research's claims without a written answer")
    if shallow := [s for s in checks.statements if s.support == "shallow"]:
        reasons.append(f"{len(shallow)} of {len(checks.statements)} statements rest only on search snippets, metadata, "
                       "or quotes and sources the tools did not return")
    if unsupported := [s for s in checks.statements if s.support == "unsupported"]:
        reasons.append(f"{len(unsupported)} of {len(checks.statements)} statements cite no supporting evidence")
    if checks.not_established:
        reasons.append(f"{len(checks.not_established)} of {len(plan.questions)} research questions returned no evidence")
    reasons += [f"citation problem: {problem}" for problem in checks.citation_problems]
    pages = sum(len(result.pages_read) for result in ledger.all())
    failed = sum(item.target.startswith("http") for item in checks.unreached)
    if failed >= 3 and failed > pages:
        reasons.append(f"{failed} of {failed + pages} page fetches failed")
    if unpriced:
        reasons.append("a model call had no price, so the cost shown is a lower bound")
    return checks


def _status(plan: ResearchPlan, ledger: EvidenceLedger, report: FinalReport | None, checks: RunChecks) -> Status:
    if report is None and not ledger.claims():
        return "failed"
    if report is not None and not checks.citation_problems and not checks.not_established and not checks.follow_up_unresolved:
        return "complete"
    return "partial"


async def scout(question: str, *, settings: Settings | None = None, store: RunStore | None = None,
                notes: Sequence[str] = (), blocked_urls: Sequence[str] = (),
                parent_run_id: UUID | None = None, study: StudyLabels | None = None,
                follow_up: bool = False, budget: StudyBudget | None = None,
                case_identity: dict[str, Any] | None = None) -> ScoutRun:
    """Research `question` and return a cited answer within the configured limits.

    `notes` are requirements every role follows, such as "prefer peer-reviewed sources". `blocked_urls` are
    sources no tool may fetch and no evidence may cite. Without a `store`, the run is kept in memory.
    `study` labels the run as one arm and repetition of a study, so its records can be paired.
    `follow_up` adds one material-gap analysis and at most one targeted research pass.
    `budget` reserves a conservative upper charge across all model requests before dispatch.
    Raises ConfigError, before any call, when the configured models cannot run or cannot be priced.
    """
    settings = settings or Settings()
    check_config(settings)
    run = _Run(question.strip(), settings, store or MemoryStore(), notes, blocked_urls, parent_run_id, study,
               follow_up=follow_up, budget=budget, case_identity=case_identity)
    return await run.execute()


async def synthesize_stored(source: dict[str, Any], *, settings: Settings, store: RunStore,
                            study: StudyLabels | None = None, budget: StudyBudget | None = None) -> ScoutRun:
    """Run production synthesis on an exact stored Scout ledger, without planning or retrieval.

    The new run points at its source and records a digest of the fixed ledger. Its synthesis call
    uses `_Run._synthesize`, so prompts, validation, fallback, usage, and call recording match Scout.
    """
    if (source.get("workflow_version") not in (WORKFLOW_VERSION, FOLLOWUP_VERSION, RESCOUT_VERSION)
            or not source.get("plan") or not source.get("ledger")):
        raise ValueError("source must have a Scout plan and ledger")
    plan = ResearchPlan.model_validate(source["plan"])
    ledger = EvidenceLedger.from_json(source["ledger"])
    if not ledger.claims():
        raise ValueError("source ledger has no claims to synthesize")
    notes = (source.get("config") or {}).get("notes") or []
    blocked = (source.get("config") or {}).get("blocked_urls") or []
    source_id = UUID(str(source["id"]))
    runner = _Run(source["question"], settings, store, notes, blocked, source_id, study)
    ledger_json = json.dumps(ledger.to_json(), ensure_ascii=False, sort_keys=True)
    ledger_sha = hashlib.sha256(ledger_json.encode()).hexdigest()
    config = run_config(settings, notes, blocked)
    config["fixed_ledger"] = {"source_run_id": str(source_id), "ledger_sha256": ledger_sha,
                              "source_prompt_fingerprint": (source.get("config") or {}).get("prompt_fingerprint")}
    # A frozen case's identity carries over, so `research grade` accepts the new report.
    if case := (source.get("config") or {}).get("case"):
        config["case"] = case
    if budget is not None:
        config["study_budget"] = {"cap_usd": str(budget.cap_usd), "policy": BUDGET_POLICY_VERSION,
                                  "sdk_retries": 0, "fallback": False}
        runner.models["synthesizer"] = StudyBudgetModel(
            build_model(settings.models.synthesizer, "synthesizer", settings, sdk_retries=0),
            settings.models.synthesizer, budget)
    await store.start_run(runner.run_id, mode="fixed-ledger", workflow_version="scout-synthesis-v1",
                          question=runner.question, config=config, parent_run_id=source_id,
                          input_hash=hashlib.sha256((runner.question + "\n" + ledger_sha).encode()).hexdigest(),
                          study_id=study.study_id if study else None, arm=study.arm if study else None,
                          replicate=study.replicate if study else None)
    started = time.monotonic()
    span_trace = None
    report = None
    try:
        with run_span(runner.run_id, "fixed-ledger", "scout-synthesis-v1") as span:
            span_trace = trace_id(span)
            # The production run reserves this much of its six-minute window after research.
            window = settings.limits.deadline_seconds - settings.limits.research_seconds
            deadline = asyncio.get_running_loop().time() + window
            report = await runner._synthesize(plan, ledger, deadline)
            checks = _checks(plan, ledger, report, runner.unpriced)
            status = _status(plan, ledger, report, checks)
            span.set_attributes({"status": status, "cost_usd": float(runner.cost)})
    except BaseException as exc:
        status = "cancelled" if isinstance(exc, asyncio.CancelledError | KeyboardInterrupt) else "failed"
        await _record(store.finish_run(runner.run_id, status=status, plan=plan, ledger=ledger.to_json(),
                                       cost_usd=runner.cost, error=_error(exc), trace_id=span_trace))
        raise
    await store.finish_run(runner.run_id, status=status, plan=plan, report=report, ledger=ledger.to_json(),
                           checks=checks, cost_usd=runner.cost, trace_id=span_trace)
    return ScoutRun(runner.run_id, runner.question, status, plan, report, ledger, checks, runner.cost,
                    time.monotonic() - started, span_trace, runner.notes, config)


async def rescout_stored(source: dict[str, Any], *, settings: Settings, store: RunStore,
                         study: StudyLabels | None = None, budget: StudyBudget | None = None) -> ScoutRun:
    """Research an exact stored Scout plan again, without planning or synthesis.

    The new run points at its source and records a digest of the fixed plan, so scout models can be compared on
    identical questions. Its scouts use `_Run._research`, so prompts, tools, evidence checks, shares, and call
    recording match Scout. They get the whole research window, which in Scout also covers planning, so compare
    rescouts with each other rather than with their source. `research synthesize` writes a report from the ledger.
    """
    if source.get("workflow_version") not in (WORKFLOW_VERSION, FOLLOWUP_VERSION, RESCOUT_VERSION) or not source.get("plan"):
        raise ValueError("source must have a Scout plan")
    plan = ResearchPlan.model_validate(source["plan"])
    source_config = source.get("config") or {}
    notes, blocked = source_config.get("notes") or [], source_config.get("blocked_urls") or []
    source_id = UUID(str(source["id"]))
    runner = _Run(source["question"], settings, store, notes, blocked, source_id, study, budget=budget)
    plan_json = json.dumps(plan.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
    plan_sha = hashlib.sha256(plan_json.encode()).hexdigest()
    config = run_config(settings, notes, blocked)
    config["fixed_plan"] = {"source_run_id": str(source_id), "plan_sha256": plan_sha,
                            "source_prompt_fingerprint": source_config.get("prompt_fingerprint")}
    if case := source_config.get("case"):
        config["case"] = case
    if budget is not None:
        config["study_budget"] = {"cap_usd": str(budget.cap_usd), "policy": BUDGET_POLICY_VERSION,
                                  "sdk_retries": 0, "fallback": False,
                                  "scout_max_output_tokens": settings.limits.guarded_scout_max_output_tokens}
    await store.start_run(runner.run_id, mode="fixed-plan", workflow_version=RESCOUT_VERSION,
                          question=runner.question, config=config, parent_run_id=source_id,
                          input_hash=hashlib.sha256((runner.question + "\n" + plan_sha).encode()).hexdigest(),
                          study_id=study.study_id if study else None, arm=study.arm if study else None,
                          replicate=study.replicate if study else None)
    started, ledger, span_trace = time.monotonic(), EvidenceLedger(), None
    try:
        async with AsyncExitStack() as stack:
            span = stack.enter_context(run_span(runner.run_id, "fixed-plan", RESCOUT_VERSION))
            span_trace = trace_id(span)
            toolset = await runner._toolset(stack)
            deadline = asyncio.get_running_loop().time() + settings.limits.research_seconds
            for result in await runner._research(plan, deadline, toolset):
                ledger.add(result)
            checks = _checks(plan, ledger, None, runner.unpriced, synthesized=False)
            checks.study_budget_reserved_usd = budget.reserved_usd if budget else None
            status: Status = ("failed" if not ledger.claims() else "partial" if checks.not_established
                              else "complete")
            span.set_attributes({"status": status, "cost_usd": float(runner.cost)})
    except BaseException as exc:
        status = "cancelled" if isinstance(exc, asyncio.CancelledError | KeyboardInterrupt) else "failed"
        failure_checks = RunChecks(study_budget_reserved_usd=budget.reserved_usd) if budget else None
        await _record(store.finish_run(runner.run_id, status=status, plan=plan, ledger=ledger.to_json(),
                                       checks=failure_checks, cost_usd=runner.cost, error=_error(exc),
                                       trace_id=span_trace, cache=runner._cache_counts()))
        raise
    await store.finish_run(runner.run_id, status=status, plan=plan, ledger=ledger.to_json(), checks=checks,
                           cost_usd=runner.cost, trace_id=span_trace, cache=runner._cache_counts())
    return ScoutRun(runner.run_id, runner.question, status, plan, None, ledger, checks, runner.cost,
                    time.monotonic() - started, span_trace, runner.notes, config, RESCOUT_VERSION)

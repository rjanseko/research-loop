"""The Scout workflow: plan, research each question in parallel, and write a cited answer.

One run is three steps, each bounded:

1. The planner splits the question into one to `max_questions` research questions. If planning fails,
   the whole question is researched as one.
2. A scout researches each question with the web and scholarly tools, all at once up to
   `parallel_scouts`. Each scout is its own call with its own limits; one that runs out, fails, or is
   still running at the research deadline leaves its question unanswered and keeps what it searched and
   read. Code checks every result against the tools' labeled output (evidence.py).
3. The synthesizer writes the report from the evidence ledger. If it cannot finish, the run returns the
   ledger's claims without a written answer.

Dollars are allocated before the run starts: the planner's share, the synthesizer's share, and the rest
split evenly across the scouts. Each call's share is its PydanticAI `cost_limit`, checked before every
request, so a call can pass its share by at most the one request that crossed it. Time is bounded by
`research_seconds` for the scouts and `deadline_seconds` for the whole run, and each model request by
`request_timeout_seconds`.

Every call is recorded in the run store with its usage, cost, output, and messages, and the whole run is
one Logfire trace carrying the run ID.
"""
from __future__ import annotations

import asyncio
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
    LedgerRefs,
    PlanLimits,
    planner_agent,
    scout_agent,
    synthesizer_agent,
)
from .budget_notes import TOOL_BATCH_SLACK, LoopBudget
from .config import ScoutLimits, Settings
from .evidence import (
    EvidenceLedger,
    Support,
    check_result,
    citation_problems,
    support_level,
)
from .models import Role, effort_for, role_model
from .prices import price_per_million
from .prompts import prompt_fingerprint
from .schemas import (
    EVIDENCE_VERSION,
    FinalReport,
    ResearchPlan,
    ResearchQuestion,
    ResearchResult,
    UnreachedSource,
)
from .scholar import ScholarClient
from .store import MemoryStore, RunStore
from .telemetry import run_span, trace_id
from .tools import labeled_texts, research_toolset, tool_outcomes
from .web import WebAcquisition, WebSearch

WORKFLOW_VERSION = "scout-v1"
Status = Literal["complete", "partial", "failed", "cancelled"]
# Failures a single call can end on without the run failing: a limit, a provider error the SDK's retries did
# not clear, a refusal, output that failed its checks twice, or a deadline.
_CALL_FAILURES = (UsageLimitExceeded, ModelAPIError, ContentFilterError, UnexpectedModelBehavior, TimeoutError)
# How long recording a failed or cancelled call may take before it is given up.
_RECORD_SECONDS = 5
# Planning gets this long before the question is researched as one, so a slow plan cannot use up the scouts' time.
_PLAN_SECONDS = 90


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

    def to_record(self) -> dict[str, Any]:
        """The run as JSON: what `research show` renders and evals read."""
        return {
            "run_id": str(self.run_id), "question": self.question, "status": self.status,
            "workflow_version": WORKFLOW_VERSION,
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


def run_config(settings: Settings, notes: Sequence[str], blocked_urls: Sequence[str]) -> dict[str, Any]:
    """What a run records about how it was configured, so runs can be compared."""
    models = settings.models
    return {
        "models": {role: {"model": model_id, "thinking": effort_for(model_id)} for role, model_id in (
            ("planner", models.planner), ("scout", models.scout), ("synthesizer", models.synthesizer),
            ("fallback", models.fallback)) if model_id},
        "limits": settings.limits.model_dump(),
        "prompt_fingerprint": prompt_fingerprint(), "evidence_version": EVIDENCE_VERSION,
        "fetch_version": FETCH_VERSION, "cache_mode": settings.cache_mode, "git_commit": _git_commit(),
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
                 blocked_urls: Sequence[str], parent_run_id: UUID | None) -> None:
        self.question, self.settings, self.store = question, settings, store
        self.limits: ScoutLimits = settings.limits
        self.notes_in, self.blocked_urls, self.parent_run_id = list(notes), list(blocked_urls), parent_run_id
        self.run_id = uuid4()
        self.cost = Decimal(0)
        self.unpriced = False
        self.notes: list[str] = []
        self.policy = SourcePolicy(tuple(blocked_urls))
        self.models: dict[Role, Model] = {}

    def _model(self, role: Role) -> Model:
        if role not in self.models:
            self.models[role] = role_model(role, self.settings)
        return self.models[role]

    def _spend(self, usage: RunUsage) -> None:
        if not usage.requests:
            return
        if usage.cost is None:
            self.unpriced = True
        else:
            self.cost += usage.cost

    async def _call(self, *, role: Role, agent: Agent[Any, Any], prompt: str, deps: Any, limits: UsageLimits,
                    question_id: str | None = None, capabilities: list[Any] | None = None,
                    toolsets: list[Any] | None = None, stream: bool = False, attempt: _Attempt | None = None,
                    finish: Callable[[Any], Any] | None = None) -> Any:
        """Run one agent call as a recorded call; return its output, after `finish` when given."""
        model_id: str = getattr(self.settings.models, role)
        call_id = await self.store.start_call(self.run_id, role=role, model=model_id, question_id=question_id)
        usage = RunUsage()
        messages: list[ModelMessage] = []
        try:
            with capture_run_messages() as messages:
                if attempt is not None:
                    attempt.messages = messages
                result = await agent.run(prompt, model=self._model(role), deps=deps, usage_limits=limits, usage=usage,
                                         capabilities=capabilities, toolsets=toolsets,
                                         event_stream_handler=_ignore_events if stream else None)
            output = finish(result) if finish else result.output
        except BaseException as exc:
            self._spend(usage)
            status = "cancelled" if isinstance(exc, asyncio.CancelledError) else "failed"
            await _record(self.store.finish_call(call_id, status=status, usage=usage, cost_usd=usage.cost,
                                                 messages=list(messages), error=exc))
            raise
        self._spend(usage)
        self._note_fallback(role, result.all_messages())
        await self.store.finish_call(call_id, status="succeeded", usage=usage, cost_usd=usage.cost, output=output,
                                     messages=result.all_messages())
        return output

    def _note_fallback(self, role: Role, messages: list[ModelMessage]) -> None:
        fallback = self.settings.models.fallback
        primary: str = getattr(self.settings.models, role)
        if not fallback or fallback == primary or role == "scout":
            return
        names = {message.model_name for message in messages if isinstance(message, ModelResponse)}
        if fallback.partition(":")[2] in names:
            self.notes.append(f"the {role} ran on {fallback} after {primary} refused or failed")

    async def _plan(self, deadline: float) -> ResearchPlan:
        prompt = json.dumps({"question": self.question, "notes": self.notes_in,
                             "max_questions": self.limits.max_questions}, ensure_ascii=False)
        try:
            with logfire.span("plan"):
                async with asyncio.timeout_at(deadline):
                    plan: ResearchPlan = await self._call(
                        role="planner", agent=planner_agent, prompt=prompt, deps=PlanLimits(self.limits.max_questions),
                        limits=UsageLimits(request_limit=2, total_tokens_limit=100_000,
                                           cost_limit=Decimal(str(self.limits.planner_usd))))
        except _CALL_FAILURES as exc:
            self.notes.append(f"planning failed ({_reason(exc)}), so the question was researched as one")
            plan = ResearchPlan(questions=[ResearchQuestion(id="q1", question=self.question)])
        # Plan order names the questions, so claim IDs read q1/c1, q2/c1, ... whatever IDs the planner chose.
        return ResearchPlan(questions=[q.model_copy(update={"id": f"q{i}"}) for i, q in enumerate(plan.questions, 1)])

    async def _scout(self, attempt: _Attempt, semaphore: asyncio.Semaphore, share: Decimal,
                     toolset: Any) -> ResearchResult:
        limits, question = self.limits, attempt.question
        budget = LoopBudget(limits.scout_requests, limits.scout_productive_calls, limits.scout_misses)
        prompt = json.dumps({"question": question.model_dump(mode="json"), "notes": self.notes_in,
                             "blocked_urls": self.blocked_urls}, ensure_ascii=False)

        def checked(result: Any) -> ResearchResult:
            messages = result.all_messages()
            outcomes = tool_outcomes(messages)
            return check_result(result.output, labeled_texts(messages)).model_copy(update={
                "searches": outcomes.searches, "pages_read": outcomes.pages_read, "unreached": outcomes.unreached})

        async with semaphore:
            with logfire.span("scout {question_id}", question_id=question.id):
                try:
                    return await self._call(
                        role="scout", agent=scout_agent, prompt=prompt, question_id=question.id,
                        deps=Assignment(question, self.policy), toolsets=[toolset], capabilities=budget.capabilities(),
                        limits=UsageLimits(
                            request_limit=limits.scout_requests, total_tokens_limit=limits.scout_tokens, cost_limit=share,
                            tool_calls_limit=limits.scout_productive_calls + limits.scout_misses + TOOL_BATCH_SLACK),
                        attempt=attempt, finish=checked)
                except _CALL_FAILURES as exc:
                    return _cut_off(question, attempt.messages, _reason(exc))

    async def _research(self, plan: ResearchPlan, deadline: float, toolset: Any) -> list[ResearchResult]:
        """Every question's result in plan order; questions still running at `deadline` are cut off."""
        attempts = [_Attempt(question) for question in plan.questions]
        share = Decimal(str(self.limits.scout_usd(len(attempts))))
        semaphore = asyncio.Semaphore(self.limits.parallel_scouts)
        tasks = [asyncio.create_task(self._scout(attempt, semaphore, share, toolset)) for attempt in attempts]
        try:
            await asyncio.wait(tasks, timeout=max(deadline - asyncio.get_running_loop().time(), 0))
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
                                           cost_limit=Decimal(str(limits.synthesis_usd))))
        except _CALL_FAILURES as exc:
            self.notes.append(f"the synthesis did not finish ({_reason(exc).replace('research deadline', 'run deadline')})")
            return None

    async def execute(self) -> ScoutRun:
        config = run_config(self.settings, self.notes_in, self.blocked_urls)
        await self.store.start_run(self.run_id, mode="scout", workflow_version=WORKFLOW_VERSION,
                                   question=self.question, config=config, parent_run_id=self.parent_run_id)
        started, loop = time.monotonic(), asyncio.get_running_loop()
        research_deadline = loop.time() + self.limits.research_seconds
        deadline = loop.time() + self.limits.deadline_seconds
        ledger, plan, report, span_trace = EvidenceLedger(), None, None, None
        try:
            async with AsyncExitStack() as stack:
                span = stack.enter_context(run_span(self.run_id, "scout", WORKFLOW_VERSION))
                span_trace = trace_id(span)
                toolset = await self._toolset(stack)
                plan = await self._plan(min(research_deadline, loop.time() + _PLAN_SECONDS))
                for result in await self._research(plan, research_deadline, toolset):
                    ledger.add(result)
                if ledger.claims():
                    report = await self._synthesize(plan, ledger, deadline)
                checks = _checks(plan, ledger, report, self.unpriced)
                status = _status(plan, ledger, report, checks)
                span.set_attributes({"status": status, "cost_usd": float(self.cost)})
        except BaseException as exc:
            status = "cancelled" if isinstance(exc, asyncio.CancelledError | KeyboardInterrupt) else "failed"
            await _record(self.store.finish_run(self.run_id, status=status, plan=plan, ledger=ledger.to_json(),
                                                cost_usd=self.cost, error=_error(exc), trace_id=span_trace))
            raise
        # A call without a price adds nothing, so the cost is then a lower bound; a review reason says so.
        await self.store.finish_run(self.run_id, status=status, plan=plan, report=report, ledger=ledger.to_json(),
                                    checks=checks, cost_usd=self.cost, trace_id=span_trace)
        return ScoutRun(self.run_id, self.question, status, plan, report, ledger, checks, self.cost,
                        time.monotonic() - started, span_trace, self.notes, config)

    async def _toolset(self, stack: AsyncExitStack) -> Any:
        """The run's research tools, sharing one fetch memo and one pair of HTTP clients across its scouts."""
        cache, settings = self.settings.cache_dir, self.settings
        memo = FetchMemo()
        pages_client = await stack.enter_async_context(public_fetch_client(timeout=15))
        metadata_client = await stack.enter_async_context(httpx.AsyncClient(follow_redirects=False, timeout=15))
        return research_toolset(
            WebSearch(cache=AcquisitionCache(cache / "search", settings.cache_mode)),
            WebAcquisition(cache_root=cache / "web", cache_mode=settings.cache_mode, client=pages_client,
                           memo=memo, policy=self.policy),
            ScholarClient(cache=AcquisitionCache(cache / "scholarly", settings.cache_mode), client=metadata_client,
                          api_key=settings.openalex_api_key.get_secret_value() if settings.openalex_api_key else None,
                          contact_email=settings.crossref_mailto),
        )


def _error(exc: BaseException) -> dict[str, str]:
    return {"type": type(exc).__name__, "message": str(exc)[:1000]}


def _not_established(plan: ResearchPlan, ledger: EvidenceLedger) -> list[str]:
    answered = ledger.question_ids_with_claims()
    reasons = {result.question_id: result.cut_off for result in ledger.all() if result.cut_off}
    return [f"{q.id}: {q.question}" + (f" ({reasons[q.id]})" if q.id in reasons else " (no evidence found)")
            for q in plan.questions if q.id not in answered]


def _checks(plan: ResearchPlan, ledger: EvidenceLedger, report: FinalReport | None, unpriced: bool) -> RunChecks:
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
    if report is None:
        reasons.append("the synthesis did not finish, so this lists the research's claims without a written answer"
                       if claims else "no research question returned evidence")
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
    if report is not None and not checks.citation_problems and not checks.not_established:
        return "complete"
    return "partial"


async def scout(question: str, *, settings: Settings | None = None, store: RunStore | None = None,
                notes: Sequence[str] = (), blocked_urls: Sequence[str] = (),
                parent_run_id: UUID | None = None) -> ScoutRun:
    """Research `question` and return a cited answer within the configured limits.

    `notes` are requirements every role follows, such as "prefer peer-reviewed sources". `blocked_urls` are
    sources no tool may fetch and no evidence may cite. Without a `store`, the run is kept in memory.
    Raises ConfigError, before any call, when the configured models cannot run or cannot be priced.
    """
    settings = settings or Settings()
    check_config(settings)
    run = _Run(question.strip(), settings, store or MemoryStore(), notes, blocked_urls, parent_run_id)
    return await run.execute()

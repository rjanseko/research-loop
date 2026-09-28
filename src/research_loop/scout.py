"""The Scout workflow: plan, research each question in parallel, and write a cited answer.

A normal run has three steps, each bounded:

1. The planner chooses a depth (quick, standard, or deep), whose limits the run then takes, and splits
   the question into at most that depth's number of research questions. If planning fails, the whole
   question is researched as one.
2. A scout researches each question with the web and scholarly tools, all at once up to
   `parallel_scouts`. Each scout is its own call with its own limits. When less than one request
   timeout remains, it is told to return and loses its tools, so it can write the claims it has.
   One that runs out, fails, or is still running at the research deadline leaves its question
   unanswered and keeps what it searched and read. Code checks every result against the tools'
   labeled output (evidence.py).
3. The synthesizer writes the report from the evidence ledger. If it cannot finish, the run returns the
   ledger's claims without a written answer.

Follow-up mode, opt-in or chosen by a deep plan, inserts a material-gap analysis after step 2 and up to
`max_gaps` targeted deep dives, run in parallel, then synthesizes the enlarged ledger. It has a separate
dollar and time envelope.

Dollars are allocated once the plan sets the depth: the planner's share, the synthesizer's share, and the rest
split evenly across the scouts. Each call's share is its PydanticAI `cost_limit`, checked before every
request, so a call can pass its share by at most the one request that crossed it. Time is bounded by
`research_seconds` for the scouts and `deadline_seconds` for the whole run, and each model request by
`request_timeout_seconds`. A scout's provider client does not retry a request that hits that timeout.

Every call is recorded in the run store with its usage, cost, output, and messages, and the whole run is
one Logfire trace carrying the run ID. `rescout_stored` and `synthesize_stored` repeat one step of a stored
run, and every kind of run is recorded through `_Run._recorded`.
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
from .config import ScoutLimits, Settings, split_model
from .evidence import (
    EvidenceLedger,
    Support,
    check_result,
    citation_problems,
    coverage_items,
    coverage_states,
    quote_is_short,
    support_level,
    uncited_sentences,
)
from .models import (
    Role,
    build_model,
    role_model,
    scout_model,
    sent_settings,
)
from .prices import price_per_million
from .prompts import prompt_fingerprint
from .rate_limit import RATE_LIMIT_POLICY_VERSION
from .reading import ExaContentsReader, ExternalSpend, FirecrawlReader, OpenAccessReader
from .schemas import (
    EVIDENCE_VERSION,
    CoverageItem,
    CoverageState,
    Depth,
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
from .telemetry import run_span, trace_http, trace_id
from .tools import TimedToolset, labeled_texts, research_toolset, tool_outcomes
from .web import WebAcquisition, WebSearch, exa_engine

# v2: scouts list a set from an overview before confirming its members, and the gap analysis treats a
# partial set as a gap.
# v3: larger scout budgets and research window, a planner that asks for whole sets, and prompts that no
# longer show a result's self-rated confidence.
# v4: the planner chooses a depth (quick, standard, deep) that sets the run's limits, and a deep run adds
# the gap follow-up, which now follows up to `max_gaps` gaps with parallel deep dives. Earlier runs stay
# valid sources for `synthesize_stored` and `rescout_stored`.
# v5: evidence v6. A quote counts as verified only in its cited source's text, and one found only in
# another source's text is misattributed; the synthesizer is told so. Status now says only whether the
# run did its work, and `answer_support` how well its answer is backed.
# v6: coverage items. The planner lists what a sufficient answer must address; scouts say which items
# their claims cover and name the members they could not establish as open items; the gap analysis
# targets open items; and the synthesizer addresses each item or says it was not established.
# v7: scouts cite the copy they read (a preprint rather than its published version), and a gap names the
# coverage item its deep dive targets, which the dive's untagged claims then count toward.
# v8: open items are short names only, at most five per result; caveats go in `unresolved`.
# v9: scouts quote the passage behind every piece of evidence, not only specific wording: two method-survey
# scouts of a v6 deep run quoted 1 of 34 items, and 11 of its 30 statements rested on their summaries alone.
# followup-v10: a deep run gets 20 minutes of research, 8-minute deep dives with a full scout's loop budget,
# and 48 productive calls a scout (config.py); a single depth setting no longer resets the rest of the depth.
# v10: a fetched page's text leaves a scout's view two responses after it was read (history.py), and
# re-reading it to quote uses no loop budget; followup-v11 also lets a deep run give every other scout and
# deep dive a second scout model (ScoutModels.scout_alt), with $3.00 for the follow-up envelope.
WORKFLOW_VERSION = "scout-v10"
FOLLOWUP_VERSION = "scout-followup-v11"
RESCOUT_VERSION = "scout-research-v10"
# v2: the synthesis prompt no longer shows result confidence. v3: it describes misattributed quotes.
# v4: it addresses coverage items.
SYNTHESIS_VERSION = "scout-synthesis-v4"
_SOURCE_VERSIONS = (WORKFLOW_VERSION, FOLLOWUP_VERSION, RESCOUT_VERSION,
                    *(f"scout-{kind}v{n}" for kind in ("", "followup-", "research-") for n in (1, 2, 3, 4, 5, 6, 7, 8)))
# Whether the run did its work: `complete` when the report was written and every step ran to its end,
# `partial` when a question was cut off, the synthesis did not finish, or the gap analysis failed.
# Whether the answer is backed is `RunChecks.answer_support`.
Status = Literal["complete", "partial", "failed", "cancelled"]
AnswerSupport = Literal["supported", "weak", "unsupported"]
# Failures a single call can end on without the run failing: a limit, a provider error the SDK's retries did
# not clear, a refusal, output that failed its checks twice, a deadline, or a network error the SDK let through.
# The OpenAI client raised a TLS `SSLError` (an OSError) from one scout's request unwrapped, which failed a
# run whose other three scouts had finished.
_CALL_FAILURES = (UsageLimitExceeded, ModelAPIError, ContentFilterError, UnexpectedModelBehavior,
                  TimeoutError, StudyBudgetRefusal, OSError)
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


class SourceRunError(ValueError):
    """A stored run cannot be the source of a rescout or a fixed-ledger synthesis."""


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
    # Found only in another source's text than the one cited (evidence v6).
    quotes_misattributed: int = 0
    # Verified quotes with under a quarter of their claim's words (evidence.quote_is_short): a diagnostic.
    quotes_short: int = 0
    # What paid web searches cost, which `cost_usd` includes; None when only free engines were searched.
    external_usd: Decimal | None = None
    # Pages read by the reading fallback after our fetch failed, by the reader that read them.
    pages_read_via: dict[str, int] = Field(default_factory=dict)
    # Whether the report's statements are backed, apart from whether the run finished (`_answer_support`).
    answer_support: AnswerSupport | None = None
    # Sentences of the answer, and those with no inline [sN] citation: a diagnostic, since some are framing.
    sentences: int = 0
    uncited_sentences: int = 0
    # Each coverage item the plan set or research named, and where it stands (evidence.coverage_states).
    coverage: list[CoverageState] = Field(default_factory=list)
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
    specs = {settings.models.planner, settings.models.scout, settings.models.scout_alt, settings.models.synthesizer,
             settings.models.fallback}
    models = {split_model(spec)[0] for spec in specs if spec}
    problems += [f"{model} has no price, so its cost cannot be capped; add it to prices.toml"
                 for model in sorted(models) if price_per_million(model) is None]
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
    roles: dict[str, Any] = {role: {"model": split_model(spec)[0], "thinking": split_model(spec)[1],
                                    "sent": sent_settings(spec, role, settings)}
                             for role, spec in (("planner", models.planner), ("scout", models.scout),
                                                ("synthesizer", models.synthesizer))}
    if follow_up:
        roles["gap_analyzer"] = roles["planner"]
        roles["deep_dive"] = roles["scout"]
    if models.fallback:
        # The fallback takes planner and synthesizer calls, each with that role's settings.
        roles["fallback"] = {"model": split_model(models.fallback)[0], "thinking": split_model(models.fallback)[1],
                             "sent": {role: sent_settings(models.fallback, role, settings)
                                      for role in ("planner", "synthesizer")}}
    return {
        "models": roles,
        "limits": settings.limits.model_dump(),
        "prompt_fingerprint": prompt_fingerprint(follow_up=follow_up), "evidence_version": EVIDENCE_VERSION,
        "fetch_version": FETCH_VERSION, "cache_mode": settings.cache_mode, "cache_dir": str(settings.cache_dir), "git_commit": _git_commit(),
        "rate_limit_policy": RATE_LIMIT_POLICY_VERSION, "search_engine": settings.search_engine,
        "read_fallback": list(settings.read_fallback),
        "tokens_per_minute": settings.tokens_per_minute.get(split_model(models.scout)[0]),
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
    if isinstance(exc, OSError):
        return f"network error {type(exc).__name__}"
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
                 case_identity: dict[str, Any] | None = None, depth: Depth | None = None) -> None:
        self.question, self.settings, self.store, self.study = question, settings, store, study
        # The standard limits until a plan sets the depth (`_set_depth`).
        self.limits: ScoutLimits = settings.limits
        self.depth_asked, self.depth = depth, depth
        self.follow_up, self.budget, self.case_identity = follow_up, budget, case_identity
        self.workflow_version = FOLLOWUP_VERSION if follow_up else WORKFLOW_VERSION
        self.notes_in, self.blocked_urls, self.parent_run_id = list(notes), list(blocked_urls), parent_run_id
        self.run_id = uuid4()
        self.cost = Decimal(0)
        self.unpriced = False
        self.notes: list[str] = []
        self.policy = SourcePolicy(tuple(blocked_urls))
        self.models: dict[tuple[Role, str], Model] = {}
        self.caches: list[AcquisitionCache] = []
        # Set when the research deadline, not a cancelled run, stopped the scouts still running.
        self.research_timed_out = False
        # What `_recorded` stores at the end, however the run ends; the body fills them in as it goes.
        self.config: dict[str, Any] = {}
        self.plan: ResearchPlan | None = None
        self.ledger = EvidenceLedger()
        self.report: FinalReport | None = None
        # Paid web searches, which the run's cost includes beside its model calls.
        self.external_spend = ExternalSpend()

    def _total_cost(self) -> Decimal:
        return self.cost + self.external_spend.usd

    def _model(self, role: Role, spec: str) -> Model:
        """The run's model for `role` on `spec`; scouts on one spec share its pacer and rate-limit pause."""
        if (role, spec) not in self.models:
            if self.budget is None:
                self.models[role, spec] = role_model(role, self.settings, spec)
            else:
                model_id = split_model(spec)[0]
                guarded = StudyBudgetModel(build_model(spec, role, self.settings, sdk_retries=0),
                                           model_id, self.budget)
                # The guard is inside the 429 wrapper, so every retry reserves a new request.
                self.models[role, spec] = (scout_model(guarded, model_id, self.settings)
                                           if role == "scout" else guarded)
        return self.models[role, spec]

    def _scout_spec(self, index: int) -> str:
        """The model of a deep run's `index`th scout or deep dive (from 0): the second scout model, when one
        is set, takes every other one, so the scouts draw on two providers' rate limits."""
        models = self.settings.models
        if models.scout_alt and self.depth == "deep" and index % 2 == 1:
            return models.scout_alt
        return models.scout

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
                    call_role: str | None = None, spec: str | None = None) -> Any:
        """Run one agent call as a recorded call; return its output, after `finish` when given.

        The call's row records why it stopped: `finished` names it for a result, `cancelled` for a
        cancellation (usually a deadline), and a failure is named by its exception.
        """
        spec = spec or getattr(self.settings.models, role)
        model_id = split_model(spec)[0]
        call_id = await self.store.start_call(self.run_id, role=call_role or role, model=model_id, question_id=question_id)
        # Labels the agent run's span in the trace; PydanticAI keeps it out of the model's requests.
        metadata = {"run_id": str(self.run_id), "role": call_role or role, "question_id": question_id,
                    "depth": self.depth}
        usage = RunUsage()
        messages: list[ModelMessage] = []
        try:
            with capture_run_messages() as messages:
                if attempt is not None:
                    attempt.messages = messages
                output_cap = (16_000 if role == "planner" else self.limits.guarded_scout_max_output_tokens
                              if role == "scout" else self.limits.synthesis_max_output_tokens)
                result = await agent.run(prompt, model=self._model(role, spec), deps=deps, usage_limits=limits, usage=usage,
                                         model_settings={"max_tokens": output_cap} if self.budget else None,
                                         capabilities=capabilities, toolsets=toolsets, metadata=metadata,
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
        if split_model(fallback)[0].partition(":")[2] in names:
            self.notes.append(f"the {role} ran on {fallback} after {primary} refused or failed")

    async def _plan(self, research_deadline: float) -> ResearchPlan:
        """The plan, within `_PLAN_SECONDS` of now and before `research_deadline`, whichever comes first."""
        caps = self.settings.limits.question_caps()
        asked = self.depth_asked
        prompt = json.dumps({"question": self.question, "notes": self.notes_in,
                             **({"depth": asked, "max_questions": {asked: caps[asked]}} if asked
                                else {"max_questions": caps})}, ensure_ascii=False)
        deadline = asyncio.get_running_loop().time() + _PLAN_SECONDS
        timed_out = f"planning took longer than {_PLAN_SECONDS:g} seconds"
        if research_deadline <= deadline:
            deadline, timed_out = research_deadline, _reason(TimeoutError())
        try:
            async with asyncio.timeout_at(deadline):
                plan: ResearchPlan = await self._call(
                    role="planner", agent=planner_agent, prompt=prompt, deps=PlanLimits(caps, asked),
                    limits=UsageLimits(request_limit=2, total_tokens_limit=100_000,
                                       cost_limit=Decimal(str(self.limits.planner_usd))),
                    cancelled=lambda: timed_out)
        except _CALL_FAILURES as exc:
            reason = timed_out if isinstance(exc, TimeoutError) else _reason(exc)
            self.notes.append(f"planning failed ({reason}), so the question was researched as one")
            plan = ResearchPlan(questions=[ResearchQuestion(id="q1", question=self.question)], depth=asked or "standard")
        # Plan order names the questions, so claim IDs read q1/c1, q2/c1, ... whatever IDs the planner chose.
        return plan.model_copy(update={"questions": [q.model_copy(update={"id": f"q{i}"})
                                                     for i, q in enumerate(plan.questions, 1)]})

    def _set_depth(self, depth: Depth) -> None:
        """Take the limits of `depth`; a depth that follows up turns the gap follow-up on."""
        self.depth = depth
        self.limits = self.settings.limits.for_depth(depth)
        if self.settings.limits.follows_up(depth) and not self.follow_up:
            self.follow_up, self.workflow_version = True, FOLLOWUP_VERSION

    def _config(self) -> dict[str, Any]:
        """What the run records about its configuration, with the limits of its depth once that is known."""
        settings = self.settings.model_copy(update={"limits": self.limits})
        config = run_config(settings, self.notes_in, self.blocked_urls, follow_up=self.follow_up)
        config["follow_up"], config["depth"] = self.follow_up, self.depth
        if (alt := self.settings.models.scout_alt) and self.depth == "deep":
            # Even-numbered scouts and deep dives (`_scout_spec`).
            config["models"]["scout_alt"] = {"model": split_model(alt)[0], "thinking": split_model(alt)[1],
                                             "sent": sent_settings(alt, "scout", self.settings)}
        if self.budget:
            config["study_budget"] = {"cap_usd": str(self.budget.cap_usd), "policy": BUDGET_POLICY_VERSION,
                                      "sdk_retries": 0, "fallback": False,
                                      "scout_max_output_tokens": self.limits.guarded_scout_max_output_tokens}
        if self.case_identity:
            config["case"] = self.case_identity
        return config

    async def _scout(self, attempt: _Attempt, semaphore: asyncio.Semaphore, share: Decimal,
                     toolset: TimedToolset, deadline: float, *, gap: str | None = None,
                     ledger: EvidenceLedger | None = None, coverage: Sequence[CoverageItem] = (),
                     spec: str | None = None) -> ResearchResult:
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
        if coverage:
            prompt_data["coverage"] = [item.model_dump(mode="json") for item in coverage]
        if deep:
            prompt_data.update({"material_gap": gap, "known_research": ledger.prompt_view() if ledger else {},
                                "instruction": "Investigate this gap. Cite only sources returned by your own tools."})
        prompt = json.dumps(prompt_data, ensure_ascii=False)
        tool_seconds_before = toolset.seconds(question.id)

        def checked(result: Any) -> ResearchResult:
            messages = result.all_messages()
            outcomes = tool_outcomes(messages)
            return check_result(result.output, labeled_texts(messages)).model_copy(update={
                "searches": outcomes.searches, "pages_read": outcomes.pages_read, "unreached": outcomes.unreached,
                "read_via": outcomes.read_via})

        async with semaphore:
            try:
                return await self._call(
                    role="scout", call_role="deep_dive" if deep else None, agent=scout_agent,
                    prompt=prompt, question_id=question.id,
                    deps=Assignment(question, self.policy, frozenset(item.id for item in coverage)),
                    toolsets=[toolset], capabilities=budget.capabilities(),
                    limits=UsageLimits(
                        request_limit=requests, total_tokens_limit=limits.scout_tokens, cost_limit=share,
                        tool_calls_limit=budget.tool_call_limit),
                    attempt=attempt, finish=checked, spec=spec,
                    finished=lambda result: budget.finish_reason(result.usage.requests, result.all_messages()),
                    cancelled=lambda: ("the deep-dive deadline passed" if deep else
                                       _reason(TimeoutError()) if self.research_timed_out else
                                       "the run was cancelled"),
                    tool_seconds=lambda: toolset.seconds(question.id) - tool_seconds_before)
            except _CALL_FAILURES as exc:
                return _cut_off(question, attempt.messages, _reason(exc))
            except Exception as exc:  # noqa: BLE001 - a bug in one scout must not discard the others' paid research
                # A UnicodeEncodeError from one PDF failed a run whose other three scouts had finished.
                logfire.exception("scout {question_id} stopped on an unexpected error", question_id=question.id)
                self.notes.append(f"research on {question.id} stopped on an unexpected error "
                                  f"({type(exc).__name__}); this is a bug")
                return _cut_off(question, attempt.messages, f"unexpected error {type(exc).__name__}")

    async def _research(self, plan: ResearchPlan, deadline: float, toolset: TimedToolset) -> list[ResearchResult]:
        """Every question's result in plan order; questions still running at `deadline` are cut off."""
        attempts = [_Attempt(question) for question in plan.questions]
        share = Decimal(str(self.limits.followup_scout_usd(len(attempts)) if self.follow_up
                            else self.limits.scout_usd(len(attempts))))
        semaphore = asyncio.Semaphore(self.limits.parallel_scouts)
        tasks = [asyncio.create_task(self._scout(attempt, semaphore, share, toolset, deadline,
                                                 coverage=_question_coverage(plan, attempt.question),
                                                 spec=self._scout_spec(index)))
                 for index, attempt in enumerate(attempts)]
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
                             "not_established": _not_established(plan, ledger),
                             "coverage": _coverage_view(plan, ledger),
                             "max_gaps": self.limits.max_gaps}, ensure_ascii=False)
        try:
            async with asyncio.timeout_at(deadline):
                return await self._call(
                    role="planner", call_role="gap_analyzer", agent=gap_agent, prompt=prompt,
                    deps=GapRefs(frozenset(q.id for q in plan.questions), self.limits.max_gaps,
                                 frozenset(state.id for state in coverage_states(plan, ledger))),
                    limits=UsageLimits(request_limit=2, total_tokens_limit=100_000,
                                       cost_limit=Decimal(str(self.limits.gap_usd))),
                    cancelled=lambda: "the gap-analysis deadline passed")
        except _CALL_FAILURES as exc:
            self.notes.append(f"gap analysis did not finish ({_reason(exc)})")
            return None

    async def _deep_dive(self, gap: MaterialGap, plan: ResearchPlan, ledger: EvidenceLedger,
                         toolset: TimedToolset, deadline: float, index: int = 0) -> ResearchResult:
        """One gap's research, filed under the planned question it names. Every deep dive of a run sees the
        same ledger, which the caller extends only after all of them finish."""
        original = next(q for q in plan.questions if q.id == gap.question_id)
        question = original.model_copy(update={"question": gap.follow_up_question})
        attempt = _Attempt(question)
        try:
            async with asyncio.timeout_at(deadline):
                # A deep dive may address any item still open, the ones research named included.
                open_items = [item for item, _ in coverage_items(plan, ledger)
                              if item.kind != "assumption" and item.id in _open_ids(plan, ledger)]
                result = await self._scout(attempt, asyncio.Semaphore(1), Decimal(str(self.limits.deep_dive_usd)),
                                           toolset, deadline, gap=gap.reason, ledger=ledger, coverage=open_items,
                                           spec=self._scout_spec(index))
        except TimeoutError:
            result = _cut_off(question, attempt.messages, "the deep-dive deadline passed")
        if result.cut_off:
            self.notes.append(f"deep dive for {gap.question_id} did not finish ({result.cut_off})")
        if gap.coverage_id:
            # A dive answers the item its gap targets, so its claims that name no item count toward it. The
            # live deep run established CSD without tagging it, and CSD stayed open.
            result = result.model_copy(update={"claims": [
                claim if claim.covers else claim.model_copy(update={"covers": [gap.coverage_id]})
                for claim in result.claims]})
        return result

    async def _synthesize(self, plan: ResearchPlan, ledger: EvidenceLedger, deadline: float) -> FinalReport | None:
        prompt = json.dumps({"question": self.question, "notes": self.notes_in, "research": ledger.prompt_view(),
                             "not_established": _not_established(plan, ledger),
                             **({"coverage": view} if (view := _coverage_view(plan, ledger)) else {}),
                             **({"assumptions": [item.requirement for item in plan.coverage
                                                 if item.kind == "assumption"]}
                                if any(item.kind == "assumption" for item in plan.coverage) else {})},
                            ensure_ascii=False)
        limits = self.limits
        try:
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

    async def _recorded(self, mode: str, input_hash: str,
                        body: Callable[[AsyncExitStack], Awaitable[tuple[Status, RunChecks]]]) -> ScoutRun:
        """Record the run's start, run `body` in its trace, and record how the run ended, a failure or a
        cancellation too. Every kind of run goes through here, so they all record the same fields."""
        study = self.study
        await self.store.start_run(self.run_id, mode=mode, workflow_version=self.workflow_version,
                                   question=self.question, config=self.config, parent_run_id=self.parent_run_id,
                                   input_hash=input_hash, study_id=study.study_id if study else None,
                                   arm=study.arm if study else None, replicate=study.replicate if study else None)
        started, span_trace = time.monotonic(), None
        try:
            async with AsyncExitStack() as stack:
                span = stack.enter_context(run_span(self.run_id, mode, self.workflow_version))
                span_trace = trace_id(span)
                status, checks = await body(stack)
                span.set_attributes({"status": status, "cost_usd": float(self.cost)})
        except BaseException as exc:
            failed = "cancelled" if isinstance(exc, asyncio.CancelledError | KeyboardInterrupt) else "failed"
            await _record(self.store.finish_run(
                self.run_id, status=failed, plan=self.plan, ledger=self.ledger.to_json(),
                checks=RunChecks(study_budget_reserved_usd=self.budget.reserved_usd,
                                 external_usd=self._external_usd()) if self.budget else None,
                cost_usd=self._total_cost(), error=_error(exc), trace_id=span_trace, cache=self._cache_counts(),
                config=self.config, workflow_version=self.workflow_version))
            raise
        checks.study_budget_reserved_usd = self.budget.reserved_usd if self.budget else None
        checks.external_usd = self._external_usd()
        # A call without a price adds nothing, so the cost is then a lower bound; a review reason says so.
        await self.store.finish_run(self.run_id, status=status, plan=self.plan, report=self.report,
                                    ledger=self.ledger.to_json(), checks=checks, cost_usd=self._total_cost(),
                                    trace_id=span_trace, cache=self._cache_counts(), config=self.config,
                                    workflow_version=self.workflow_version)
        return ScoutRun(self.run_id, self.question, status, self.plan, self.report, self.ledger, checks,
                        self._total_cost(),
                        time.monotonic() - started, span_trace, self.notes, self.config, self.workflow_version)

    async def execute(self) -> ScoutRun:
        if self.depth_asked:
            self._set_depth(self.depth_asked)
        self.config = self._config()
        loop = asyncio.get_running_loop()
        begun = loop.time()

        async def body(stack: AsyncExitStack) -> tuple[Status, RunChecks]:
            toolset = await self._toolset(stack)
            plan = self.plan = await self._plan(begun + self.limits.research_seconds)
            if plan.depth != self.depth:
                self._set_depth(plan.depth)
                self.config = self._config()
            # Both deadlines count from the start of the run, with the limits of the plan's depth.
            research_deadline = begun + self.limits.research_seconds
            deadline = begun + (self.limits.followup_deadline_seconds if self.follow_up
                                else self.limits.deadline_seconds)
            ledger = self.ledger
            for result in await self._research(plan, research_deadline, toolset):
                ledger.add(result)
            analysis: GapAnalysis | None = None
            follow_up_unresolved = False
            if self.follow_up:
                gap_deadline = min(loop.time() + self.limits.gap_seconds,
                                   deadline - self.limits.deep_dive_seconds - 90)
                analysis = await self._analyze_gap(plan, ledger, gap_deadline)
                if analysis and analysis.gaps:
                    dive_deadline = min(loop.time() + self.limits.deep_dive_seconds, deadline - 90)
                    dives = await asyncio.gather(*(self._deep_dive(gap, plan, ledger, toolset, dive_deadline, index)
                                                   for index, gap in enumerate(analysis.gaps)))
                    # Added in gap order, so claim IDs do not depend on which dive finished first.
                    for result in dives:
                        ledger.add(result)
                    follow_up_unresolved = any(result.cut_off or not result.claims or result.unresolved
                                               for result in dives)
                elif analysis is None:
                    follow_up_unresolved = True
            if ledger.claims():
                self.report = await self._synthesize(plan, ledger, deadline)
            checks = _checks(plan, ledger, self.report, self.unpriced)
            checks.gap_analysis = analysis
            checks.follow_up_unresolved = follow_up_unresolved
            if follow_up_unresolved:
                checks.review_reasons.append("the material gap follow-up did not establish a complete answer"
                                             if analysis else "gap analysis did not finish")
            checks.answer_support = _answer_support(self.report, checks)
            status = _status(ledger, self.report, gap_analysis_failed=self.follow_up and analysis is None)
            return status, checks

        return await self._recorded("scout", input_hash(self.question, self.notes_in, self.blocked_urls), body)

    def _external_usd(self) -> Decimal | None:
        """What paid searches and page reads cost, or None when the run used only free services."""
        spend = self.external_spend
        return spend.usd if spend.searches or spend.pages else None

    def _readers(self, client: httpx.AsyncClient, extract: Any, exa_key: str | None,
                 firecrawl_key: str | None) -> list[Any]:
        """The reading fallback the settings name, in order (reading.py)."""
        readers: list[Any] = []
        for name in self.settings.read_fallback:
            if name == "oa":
                readers.append(OpenAccessReader(client, extract))
            elif name == "exa" and exa_key:
                readers.append(ExaContentsReader(client, exa_key, self.external_spend, self.budget))
            elif name == "firecrawl" and firecrawl_key:
                readers.append(FirecrawlReader(client, firecrawl_key, self.external_spend, self.budget))
        return readers

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
        if settings.offline_world is not None:
            # The bug-finding harness: tools answer from a generated world, and nothing is cached.
            from .dryrun import World

            world = World(settings.offline_world, settings.offline_fault_rate)
            world.install(stack, self.policy)
            if settings.search_engine == "exa":
                # The world answers Exa's API too, so the paid search path runs with its faults and costs.
                exa_client = await stack.enter_async_context(world.client())
                search = WebSearch(engine=exa_engine(exa_client, "dry-exa-key", self.external_spend, self.budget),
                                   name="exa", retry_delays=(0.0, 0.0), policy=self.policy)
            else:
                search = WebSearch(engine=world.search, retry_delays=(0.0, 0.0), policy=self.policy)
            pages_client = await stack.enter_async_context(world.client())
            pages = WebAcquisition(cache_root=cache / "web", cache_mode="off", client=pages_client, memo=memo,
                                   policy=self.policy)
            # The world answers the open-access, Exa, and Firecrawl APIs too, with their faults.
            pages.fallbacks = self._readers(await stack.enter_async_context(world.client()), pages._extract,
                                            "dry-exa-key", "dry-firecrawl-key")
            scholar = ScholarClient(cache=AcquisitionCache(cache / "scholarly", "off"),
                                    client=await stack.enter_async_context(world.client()))
            self.caches = []
            return TimedToolset(research_toolset(search, pages, scholar))
        pages_client = await stack.enter_async_context(trace_http(public_fetch_client(timeout=15), settings))
        metadata_client = await stack.enter_async_context(
            trace_http(httpx.AsyncClient(follow_redirects=False, timeout=15), settings))
        search_cache = AcquisitionCache(cache / "search", settings.cache_mode)
        if settings.search_engine == "exa" and settings.exa_api_key is not None:
            exa_client = await stack.enter_async_context(
                trace_http(httpx.AsyncClient(follow_redirects=False, timeout=30), settings))
            search = WebSearch(cache=search_cache, name="exa", policy=self.policy,
                               engine=exa_engine(exa_client, settings.exa_api_key.get_secret_value(),
                                                 self.external_spend, self.budget))
        else:
            search = WebSearch(cache=search_cache, policy=self.policy)
        pages = WebAcquisition(cache_root=cache / "web", cache_mode=settings.cache_mode, client=pages_client,
                               memo=memo, policy=self.policy)
        if settings.read_fallback:
            reader_client = await stack.enter_async_context(
                trace_http(httpx.AsyncClient(follow_redirects=True, timeout=60), settings))
            pages.fallbacks = self._readers(
                reader_client, pages._extract,
                settings.exa_api_key.get_secret_value() if settings.exa_api_key else None,
                settings.firecrawl_api_key.get_secret_value() if settings.firecrawl_api_key else None)
        scholar = ScholarClient(cache=AcquisitionCache(cache / "scholarly", settings.cache_mode), client=metadata_client,
                                api_key=settings.openalex_api_key.get_secret_value() if settings.openalex_api_key else None,
                                contact_email=settings.crossref_mailto)
        self.caches = [cache for cache in (search.cache, pages.cache, scholar.cache) if cache is not None]
        return TimedToolset(research_toolset(search, pages, scholar))


def _error(exc: BaseException) -> dict[str, str]:
    return {"type": type(exc).__name__, "message": str(exc)[:1000]}


def _question_coverage(plan: ResearchPlan, question: ResearchQuestion) -> list[CoverageItem]:
    """The coverage items a scout works toward: those its question names, or every item when it names none."""
    items = [item for item in plan.coverage if item.kind != "assumption"]
    named = [item for item in items if item.id in question.covers]
    return named or items


def _open_ids(plan: ResearchPlan, ledger: EvidenceLedger) -> set[str]:
    return {state.id for state in coverage_states(plan, ledger) if state.status == "open"}


def _coverage_view(plan: ResearchPlan, ledger: EvidenceLedger) -> list[dict[str, Any]]:
    """Coverage items as a prompt shows them: what each requires, whether research covered it, and which
    claims do."""
    return [state.model_dump(mode="json", include={"id", "requirement", "status", "claim_ids"})
            for state in coverage_states(plan, ledger)]


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
        quotes_misattributed=sum(item.quote_check == "misattributed" for item in evidence),
        quotes_short=sum(quote_is_short(item, claim) for claim in claims.values() for item in claim.evidence),
    )
    if report is not None:
        checks.sentences, checks.uncited_sentences = uncited_sentences(report.answer)
    checks.coverage = coverage_states(plan, ledger, report)
    for item in evidence:
        key = item.source_access or "not_returned"
        checks.evidence_by_access[key] = checks.evidence_by_access.get(key, 0) + 1
    for result in ledger.all():
        for via in result.read_via.values():
            checks.pages_read_via[via] = checks.pages_read_via.get(via, 0) + 1
    reasons = checks.review_reasons
    if report is None and not claims:
        reasons.append("no research question returned evidence")
    elif report is None and synthesized:
        reasons.append("the synthesis did not finish, so this lists the research's claims without a written answer")
    if paraphrase := [s for s in checks.statements if s.support == "paraphrase"]:
        reasons.append(f"{len(paraphrase)} of {len(checks.statements)} statements rest only on the research's own "
                       "summaries of sources it read, with no quote checked against the source")
    if shallow := [s for s in checks.statements if s.support == "shallow"]:
        reasons.append(f"{len(shallow)} of {len(checks.statements)} statements rest only on search snippets, metadata, "
                       "or quotes and sources the tools did not return")
    if unsupported := [s for s in checks.statements if s.support == "unsupported"]:
        reasons.append(f"{len(unsupported)} of {len(checks.statements)} statements cite no supporting evidence")
    if checks.not_established:
        reasons.append(f"{len(checks.not_established)} of {len(plan.questions)} research questions returned no evidence")
    reasons += [f"citation problem: {problem}" for problem in checks.citation_problems]
    if open_items := [state for state in checks.coverage if state.status == "open"]:
        reasons.append(f"{len(open_items)} of {len(checks.coverage)} coverage items were not established by the "
                       "research: " + "; ".join(state.requirement for state in open_items[:8])
                       + ("; ..." if len(open_items) > 8 else ""))
    if missing := [state for state in checks.coverage if state.in_report == "missing"]:
        reasons.append(f"the report neither addresses nor lists as not established {len(missing)} coverage items: "
                       + ", ".join(state.id for state in missing))
    if checks.quotes_misattributed:
        reasons.append(f"{checks.quotes_misattributed} of {checks.quotes} quotes appear only in another source "
                       "than the one cited")
    pages = sum(len(result.pages_read) for result in ledger.all())
    failed = sum(item.target.startswith("http") for item in checks.unreached)
    if failed >= 3 and failed > pages:
        reasons.append(f"{failed} of {failed + pages} page fetches failed")
    if unpriced:
        reasons.append("a model call had no price, so the cost shown is a lower bound")
    return checks


def _status(ledger: EvidenceLedger, report: FinalReport | None, *, synthesized: bool = True,
            gap_analysis_failed: bool = False) -> Status:
    """Whether the run did its work, apart from how well its answer is backed."""
    if report is None and not ledger.claims():
        return "failed"
    cut_off = any(result.cut_off for result in ledger.all())
    return "partial" if cut_off or gap_analysis_failed or (synthesized and report is None) else "complete"


def _answer_support(report: FinalReport | None, checks: RunChecks) -> AnswerSupport | None:
    """How well the report's statements are backed: `unsupported` when a statement rests on no evidence or
    cites a claim that does not exist, `weak` when a statement rests only on thin evidence or on summaries
    with no checked quote (evidence v7), a question or follow-up gap was left open, or a coverage item is
    not addressed with cited claims, else `supported`. None without a report."""
    if report is None:
        return None
    support = [statement.support for statement in checks.statements]
    if not support or "unsupported" in support or checks.citation_problems:
        return "unsupported"
    if ("shallow" in support or "paraphrase" in support or checks.not_established or checks.follow_up_unresolved
            or any(state.in_report != "cited" for state in checks.coverage)):
        return "weak"
    return "supported"


async def scout(question: str, *, settings: Settings | None = None, store: RunStore | None = None,
                notes: Sequence[str] = (), blocked_urls: Sequence[str] = (),
                parent_run_id: UUID | None = None, study: StudyLabels | None = None,
                follow_up: bool = False, budget: StudyBudget | None = None,
                case_identity: dict[str, Any] | None = None, depth: Depth | None = None) -> ScoutRun:
    """Research `question` and return a cited answer within the configured limits.

    `notes` are requirements every role follows, such as "prefer peer-reviewed sources". `blocked_urls` are
    sources no tool may fetch and no evidence may cite. Without a `store`, the run is kept in memory.
    `study` labels the run as one arm and repetition of a study, so its records can be paired.
    `follow_up` adds one material-gap analysis and up to `max_gaps` targeted research passes in parallel.
    `depth` (quick, standard, or deep) fixes how much research the run gets; without it the planner chooses,
    and a deep run adds the follow-up.
    `budget` reserves a conservative upper charge across all model requests before dispatch.
    Raises ConfigError, before any call, when the configured models cannot run or cannot be priced.
    """
    settings = settings or Settings()
    check_config(settings)
    run = _Run(question.strip(), settings, store or MemoryStore(), notes, blocked_urls, parent_run_id, study,
               follow_up=follow_up, budget=budget, case_identity=case_identity, depth=depth)
    return await run.execute()


def _rerun(source: dict[str, Any], settings: Settings, store: RunStore, study: StudyLabels | None,
           budget: StudyBudget | None, workflow_version: str) -> _Run:
    """A run that repeats one step of `source` with the same question, notes, blocked sources, and frozen
    case, and with the limits of its plan's depth. A frozen case's identity carries over, so `research grade`
    accepts what the new run writes."""
    source_config = source.get("config") or {}
    runner = _Run(source["question"], settings, store, source_config.get("notes") or [],
                  source_config.get("blocked_urls") or [], UUID(str(source["id"])), study, budget=budget,
                  case_identity=source_config.get("case"))
    runner.plan = plan = ResearchPlan.model_validate(source["plan"])
    runner.depth, runner.limits = plan.depth, settings.limits.for_depth(plan.depth)
    runner.workflow_version = workflow_version
    runner.config = runner._config()
    return runner


def _plan_identity(plan: ResearchPlan) -> dict[str, Any]:
    """The plan as rescouts hash it. Fields added since v1 are left out while they hold their defaults
    (a standard depth, no coverage items), so a plan hashes as it did before they existed and rescouts
    of one stored plan pair across versions."""
    data = plan.model_dump(mode="json")
    if plan.depth == "standard":
        del data["depth"]
    if not plan.coverage:
        del data["coverage"]
    for question in data["questions"]:
        if not question["covers"]:
            del question["covers"]
    return data


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


async def synthesize_stored(source: dict[str, Any], *, settings: Settings, store: RunStore,
                            study: StudyLabels | None = None, budget: StudyBudget | None = None) -> ScoutRun:
    """Run production synthesis on an exact stored Scout ledger, without planning or retrieval.

    The new run points at its source and records a digest of the fixed ledger. Its synthesis call
    uses `_Run._synthesize`, so prompts, validation, fallback, usage, and call recording match Scout.
    """
    if (source.get("workflow_version") not in _SOURCE_VERSIONS
            or not source.get("plan") or not source.get("ledger")):
        raise SourceRunError("source must have a Scout plan and ledger")
    ledger = EvidenceLedger.from_json(source["ledger"])
    if not ledger.claims():
        raise SourceRunError("source ledger has no claims to synthesize")
    runner = _rerun(source, settings, store, study, budget, SYNTHESIS_VERSION)
    runner.ledger = ledger
    ledger_sha = _digest(ledger.to_json())
    runner.config["fixed_ledger"] = {"source_run_id": str(source["id"]), "ledger_sha256": ledger_sha,
                                     "source_prompt_fingerprint": (source.get("config") or {}).get("prompt_fingerprint")}

    async def body(stack: AsyncExitStack) -> tuple[Status, RunChecks]:
        plan = runner.plan
        assert plan is not None
        # Production synthesis gets what is left of the run after the research window.
        window = runner.limits.deadline_seconds - runner.limits.research_seconds
        runner.report = await runner._synthesize(plan, ledger, asyncio.get_running_loop().time() + window)
        checks = _checks(plan, ledger, runner.report, runner.unpriced)
        checks.answer_support = _answer_support(runner.report, checks)
        return _status(ledger, runner.report), checks

    return await runner._recorded("fixed-ledger", _digest_input(runner.question, ledger_sha), body)


async def rescout_stored(source: dict[str, Any], *, settings: Settings, store: RunStore,
                         study: StudyLabels | None = None, budget: StudyBudget | None = None) -> ScoutRun:
    """Research an exact stored Scout plan again, without planning or synthesis.

    The new run points at its source and records a digest of the fixed plan, so scout models can be compared on
    identical questions. Its scouts use `_Run._research`, so prompts, tools, evidence checks, shares, and call
    recording match Scout. They get the whole research window, which in Scout also covers planning, so compare
    rescouts with each other rather than with their source. `research synthesize` writes a report from the ledger.
    A rescout never follows up.
    """
    if source.get("workflow_version") not in _SOURCE_VERSIONS or not source.get("plan"):
        raise SourceRunError("source must have a Scout plan")
    runner = _rerun(source, settings, store, study, budget, RESCOUT_VERSION)
    plan = runner.plan
    assert plan is not None
    plan_sha = _digest(_plan_identity(plan))
    runner.config["fixed_plan"] = {"source_run_id": str(source["id"]), "plan_sha256": plan_sha,
                                   "source_prompt_fingerprint": (source.get("config") or {}).get("prompt_fingerprint")}

    async def body(stack: AsyncExitStack) -> tuple[Status, RunChecks]:
        toolset = await runner._toolset(stack)
        deadline = asyncio.get_running_loop().time() + runner.limits.research_seconds
        for result in await runner._research(plan, deadline, toolset):
            runner.ledger.add(result)
        checks = _checks(plan, runner.ledger, None, runner.unpriced, synthesized=False)
        return _status(runner.ledger, None, synthesized=False), checks

    return await runner._recorded("fixed-plan", _digest_input(runner.question, plan_sha), body)


def _digest_input(question: str, fixed_sha: str) -> str:
    """A rerun's input: its question and the digest of the plan or ledger it holds fixed."""
    return hashlib.sha256((question + "\n" + fixed_sha).encode()).hexdigest()

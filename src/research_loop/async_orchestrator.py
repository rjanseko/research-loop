from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator, Awaitable, Iterable, Iterator
from contextlib import asynccontextmanager, contextmanager, suppress
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import anyio
import httpx
from pydantic import BaseModel
from pydantic_ai import UsageLimits, capture_run_messages
from pydantic_ai.capabilities import ProcessHistory
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.usage import RunUsage

from .acquisition import (
    AcquisitionCache,
    FetchMemo,
    SourcePolicy,
    public_fetch_client,
    shared_ssl_context,
)
from .agents import (
    LedgerRefs,
    PlanLimits,
    ResearchAssignment,
    deep_dive_agent,
    gap_agent,
    planner_agent,
    scout_agent,
    synthesizer_agent,
    verifier_agent,
)
from .attachments import (
    AttachmentCorpus,
    AttachmentLimits,
    AttachmentMode,
    build_attachment_toolset,
    build_multimodal_prompt,
)
from .ledger import EvidenceLedger
from .observability import job_span
from .policy import ModelPolicy, ModelRoute, retry_token_budget
from .quotes import check_quotes, check_sources, tool_texts
from .repository import NullResearchRepository, ResearchRepository
from .schemas import (
    FinalReport,
    Gap,
    GapAnalysis,
    ResearchConstraints,
    ResearchPlan,
    ResearchQuestion,
    ResearchResult,
    ResearchRole,
    ToolEvent,
    VerificationReport,
)
from .scholar import ScholarClient, build_scholar_toolset
from .settings import ResearchSettings
from .telemetry import (
    SourceReach,
    error_snapshot,
    extract_tool_events,
    jsonable,
    safe_tool_args,
    usage_snapshot,
)
from .tool_history import ToolResultTrimmer
from .tools import ResearchToolMode, build_research_capabilities
from .web import WebAcquisition, build_web_toolset


@dataclass(frozen=True)
class ResearchConfig:
    max_parallel_scouts: int = 8
    max_parallel_deep_dives: int = 2
    max_deep_dives_per_round: int = 4
    max_verification_rounds: int = 2
    min_scout_confidence: float = 0.70
    tool_mode: ResearchToolMode = ResearchToolMode.ADAPTIVE
    attachment_mode: AttachmentMode = AttachmentMode.NORMALIZED
    attachment_limits: AttachmentLimits = field(default_factory=AttachmentLimits)
    attachment_strict: bool = True
    scholarly_tools: bool = True
    scholarly_cache_mode: str = "live"
    # When a scout or deep dive exhausts its budget, summarize what it gathered in one
    # tool-free call (or return an empty result) instead of failing the job.
    salvage_exhausted_research: bool = False
    # Wall-clock limit for one run; past it the run is cancelled and recorded failed (TimeoutError).
    max_run_seconds: float | None = None
    # Experimental, off by default: a scout or deep dive sends only its most recent N tool results
    # whole and a short stub for older ones it has read (tool_history.py). This changes what the
    # model is sent, so compare runs with it on against runs without before relying on it.
    keep_recent_tool_results: int | None = None

    def __post_init__(self) -> None:
        # Zero deep dives or verification rounds disables that step; zero parallel slots would hang.
        for name, minimum in (("max_parallel_scouts", 1), ("max_parallel_deep_dives", 1),
                              ("max_deep_dives_per_round", 0), ("max_verification_rounds", 0)):
            if getattr(self, name) < minimum:
                raise ValueError(f"{name} must be at least {minimum}")
        if not 0.0 <= self.min_scout_confidence <= 1.0:
            raise ValueError("min_scout_confidence must be between 0 and 1")
        if self.max_run_seconds is not None and not self.max_run_seconds > 0:
            raise ValueError("max_run_seconds must be positive when set")
        if self.keep_recent_tool_results is not None and self.keep_recent_tool_results < 0:
            raise ValueError("keep_recent_tool_results must be at least 0 when set")


# How long recording a failed or cancelled task or job may take before the run gives up on it.
_FAILURE_WRITE_SECONDS = 10.0


@contextmanager
def _recording_failure(exc: BaseException) -> Iterator[None]:
    """Persist a failure even while the run is being cancelled, without hiding the failure.

    Pydantic Graph cancels branches through anyio, which re-raises cancellation at every await,
    so the writes run shielded, for at most _FAILURE_WRITE_SECONDS. If a write fails or times
    out, the original exception is still the one raised, with a note saying so.
    """
    with anyio.move_on_after(_FAILURE_WRITE_SECONDS, shield=True) as scope:
        try:
            yield
        except Exception as write_error:  # noqa: BLE001 - a failed write must never replace the primary error
            exc.add_note(f"Recording this failure also failed ({type(write_error).__name__}).")
    if scope.cancelled_caught:
        exc.add_note("Recording this failure timed out.")


async def _gather_or_cancel(awaitables: Iterable[Awaitable[Any]]) -> list[Any]:
    """Like asyncio.gather, but a failure first cancels the other tasks and waits for them to end.

    The failure that propagates is the original one, as with gather; the cancelled siblings get to
    record themselves instead of running on after their job has failed.
    """
    tasks = [asyncio.ensure_future(awaitable) for awaitable in awaitables]
    try:
        return await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


# Tool output replayed to a salvage call: a total bound, and the least each kept result gets.
# The bound leaves room for one validation retry within the salvage route's 80k tokens even at
# 2.5 characters per token with a 6k-token answer; tests/test_budget.py holds it to that.
_SALVAGE_EVIDENCE_CHARS = 64_000
_SALVAGE_MIN_RESULT_CHARS = 800


def _dead_result(result: Any) -> bool:
    """A tool result with nothing to cite: a fetch error, or a scholarly call that found nothing."""
    if isinstance(result, str) and result[:1] == "{":
        try:
            result = json.loads(result)
        except ValueError:
            return False
    if not isinstance(result, dict):
        return False
    if result.get("error"):
        return True
    return bool(result.get("provider_errors")) and not result.get("works") and not result.get("text")


def _gathered_evidence(messages: list[Any]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Tool results from an exhausted run for its salvage call, within `_SALVAGE_EVIDENCE_CHARS`.

    Unanswered calls, errors, and repeated calls are skipped. Every other result is kept, in call
    order: short ones whole, long ones cut to one shared allowance, the largest the bound allows,
    so late results (in a deep dive, usually the most targeted fetches) are not crowded out by
    early searches. Only when even `_SALVAGE_MIN_RESULT_CHARS` each would not fit are the oldest
    left out. Returns the kept items and counts of what was skipped, cut, or left out.
    """
    kept: list[tuple[Any, str]] = []
    seen: set[str] = set()
    counts = {"unanswered": 0, "errors": 0, "repeated": 0}
    for event in extract_tool_events(messages):
        if event.result is None:
            counts["unanswered"] += 1
            continue
        if _dead_result(event.result):
            counts["errors"] += 1
            continue
        call = json.dumps([event.tool_name, event.args], sort_keys=True, default=str)
        if call in seen:
            counts["repeated"] += 1
            continue
        seen.add(call)
        kept.append((event, json.dumps(event.result, ensure_ascii=False, default=str)))
    left_out = 0
    while len(kept) * _SALVAGE_MIN_RESULT_CHARS > _SALVAGE_EVIDENCE_CHARS:
        kept.pop(0)
        left_out += 1
    # The allowance: the largest per-result cut at which everything kept fits the bound.
    lengths = sorted(len(text) for _, text in kept)
    budget, allowance = _SALVAGE_EVIDENCE_CHARS, _SALVAGE_EVIDENCE_CHARS
    for index, length in enumerate(lengths):
        share = budget // (len(lengths) - index)
        if length > share:
            allowance = share
            break
        budget -= length
    gathered = [
        {"tool": event.tool_name, "args": event.args, "result": text[:allowance]}
        for event, text in kept
    ]
    counts |= {"kept": len(gathered), "cut": sum(len(text) > allowance for _, text in kept), "left_out": left_out}
    return gathered, counts


def _budget_exhausted_result(question: ResearchQuestion) -> ResearchResult:
    return ResearchResult(
        question_id=question.id,
        question=question.question,
        conclusion="No evidence summarized: the research budget ran out before a structured result.",
        unresolved_questions=[question.question],
        confidence=0.0,
    )


def review_reasons(
    report: FinalReport,
    verification: VerificationReport,
    ledger: EvidenceLedger,
    reach: SourceReach | None = None,
) -> list[str]:
    """What a finished run left unresolved, as short reasons; empty when nothing needs review.

    Execution status says whether a run finished. This says whether its result holds up:
    a report without evidence, one the verifier never checked, one it did not fully support,
    or one gathered while most web and scholarly tool calls could not reach their sources.
    """
    reasons = []
    if reach and (unreached := reach.review_reason()):
        reasons.append(unreached)
    if not ledger.claim_count():
        reasons.append("no evidence claims were gathered")
    elif not report.claim_ids_used:
        reasons.append("the report cites no evidence claims")
    checks = verification.checks
    if report.claims and not checks:
        reasons.append("the verifier checked none of the report's statements")
    if unsupported := sum(not check.supported for check in checks):
        reasons.append(f"{unsupported} of {len(checks)} verifier checks {'is' if unsupported == 1 else 'are'} unsupported")
    if major := sum(check.severity == "major" for check in checks):
        reasons.append(f"{major} verifier check{' is' if major == 1 else 's are'} rated major")
    if verification.needs_research:
        reasons.append("the verifier still asked for more research")
    return reasons


@dataclass
class ResearchOutcome:
    job_id: UUID
    plan: ResearchPlan
    report: FinalReport
    verification: VerificationReport
    ledger: EvidenceLedger
    attachments: AttachmentCorpus | None = None
    cost_usd: Decimal | None = None
    reach: SourceReach | None = None

    @property
    def review_reasons(self) -> list[str]:
        """Why this run, though it finished, needs review; empty when nothing is unresolved."""
        return review_reasons(self.report, self.verification, self.ledger, self.reach)


@dataclass
class AgentJobOutcome:
    job_id: UUID
    output: Any
    cost_usd: Decimal | None = None


class _JobHttp:
    """A job's HTTP clients, opened on first use: building one loads TLS certificates."""

    def __init__(self) -> None:
        self._metadata: httpx.AsyncClient | None = None
        self._fetch: httpx.AsyncClient | None = None

    @property
    def metadata(self) -> httpx.AsyncClient:
        if self._metadata is None:
            self._metadata = httpx.AsyncClient(follow_redirects=False, verify=shared_ssl_context())
        return self._metadata

    @property
    def fetch(self) -> httpx.AsyncClient:
        if self._fetch is None:
            self._fetch = public_fetch_client(timeout=15)
        return self._fetch

    async def aclose(self) -> None:
        for client in (self._metadata, self._fetch):
            if client is not None:
                await client.aclose()


@dataclass
class _BudgetHold:
    """A running call's share of the job cap, returned once when the call ends."""

    amount: float
    released: bool = False


class JobBudgetExceeded(RuntimeError):
    """The policy's per-job cost cap is spent, or spend could not be priced."""


class PromptExceedsRetryBudget(RuntimeError):
    """A finishing prompt cannot fit one validation retry inside its token limit."""


class AsyncResearchLoop:
    """Legacy v4-style plain-async orchestration kept for parity/regression."""

    def __init__(
        self,
        policy: ModelPolicy,
        config: ResearchConfig | None = None,
        repository: ResearchRepository | None = None,
        *,
        settings: ResearchSettings | None = None,
    ) -> None:
        policy.validate()
        self.policy = policy
        # Applications pass the settings they resolved; only a bare library call reads the environment.
        self.settings = settings or ResearchSettings.from_env()
        self.config = config or ResearchConfig(scholarly_cache_mode=self.settings.scholarly_cache_mode)
        self.repository: ResearchRepository = repository or NullResearchRepository()
        # Per-job USD spend; None once any billed call could not be priced.
        self._job_spend: dict[UUID, Decimal | None] = {}
        # Per-job cost allowances held by running calls (total, count), and the event that
        # wakes calls waiting for one to be released.
        self._job_holds: dict[UUID, tuple[float, int]] = {}
        self._budget_released: dict[UUID, asyncio.Event] = {}
        # Per-job fetched documents, shared by the job's agents and dropped when it ends.
        self._fetch_memos: dict[UUID, FetchMemo] = {}
        # Per-job HTTP clients shared by the job's research tools, so calls reuse connections:
        # one for scholarly metadata APIs and one for page downloads that connects only to
        # public addresses.
        self._http_clients: dict[UUID, _JobHttp] = {}
        # Per-job blocked sources, enforced by the fetch tools and the research output check.
        self._source_policies: dict[UUID, SourcePolicy] = {}
        # Per-job counts of web and scholarly tool calls that reached no source, for review reasons.
        self._source_reach: dict[UUID, SourceReach] = {}

    async def _create_job(
        self,
        objective: str,
        constraints: ResearchConstraints,
        *,
        session_id: UUID | None,
        root_run_id: UUID | None,
        kind: str,
        graph_version: str | None = None,
    ) -> UUID:
        """Persist a research job with its effective configuration; no local paths or file contents."""
        self.policy.validate()
        return await self.repository.create_job(
            session_id=session_id or uuid4(),
            root_run_id=root_run_id or uuid4(),
            objective=objective,
            policy_name=self.policy.name,
            config={
                "orchestrator": {"kind": kind, "graph_version": graph_version},
                "loop": jsonable(asdict(self.config)),
                "policy": self.policy.snapshot(),
                "constraints": {
                    "blocked_urls": constraints.blocked_urls,
                    "benchmark_id": constraints.benchmark_id,
                    "benchmark_case_id": constraints.benchmark_case_id,
                    "benchmark_suite": constraints.benchmark_suite,
                    "notes": constraints.notes,
                    "attachment_count": len(constraints.attachment_paths),
                },
            },
        )

    @asynccontextmanager
    async def _job_scope(self, job_id: UUID, constraints: ResearchConstraints | None = None,
                         ledger: EvidenceLedger | None = None) -> AsyncIterator[None]:
        """Hold the job's spend and fetch memo while it runs; record the job failed if the body raises.

        Cancellation, which is how Ctrl-C reaches the run, is recorded too, so an interrupted
        run does not stay "running", and so is ResearchConfig.max_run_seconds running out
        (TimeoutError). A failed job keeps the evidence `ledger` gathered so far.
        """
        self._job_spend[job_id] = Decimal(0)
        self._fetch_memos[job_id] = FetchMemo()
        self._http_clients[job_id] = _JobHttp()
        self._source_policies[job_id] = SourcePolicy(tuple(constraints.blocked_urls) if constraints else ())
        self._source_reach[job_id] = SourceReach()
        try:
            with job_span(job_id, self.policy.name):
                async with asyncio.timeout(self.config.max_run_seconds):
                    yield
        except (Exception, asyncio.CancelledError) as exc:
            with _recording_failure(exc):
                await self.repository.finish_job(
                    job_id,
                    status="failed",
                    final_report=None,
                    verification=None,
                    error=error_snapshot(exc),
                    evidence_ledger=ledger.to_json() if ledger and ledger.results else None,
                )
            raise
        finally:
            self._job_spend.pop(job_id, None)
            self._job_holds.pop(job_id, None)
            self._budget_released.pop(job_id, None)
            self._fetch_memos.pop(job_id, None)
            self._source_policies.pop(job_id, None)
            self._source_reach.pop(job_id, None)
            if http := self._http_clients.pop(job_id, None):
                # Shielded and bounded like failure records, so a cancelled run still closes them.
                with anyio.move_on_after(_FAILURE_WRITE_SECONDS, shield=True):
                    await http.aclose()

    async def _load_attachments(
        self, job_id: UUID, constraints: ResearchConstraints
    ) -> AttachmentCorpus | None:
        if not constraints.attachment_paths:
            return None
        # Extraction parses whole documents; a worker thread keeps it from stalling other tasks.
        attachments = await asyncio.to_thread(
            AttachmentCorpus.from_paths,
            constraints.attachment_paths,
            limits=self.config.attachment_limits,
            strict=self.config.attachment_strict,
        )
        await self.repository.save_attachments(job_id, attachments.manifest())
        return attachments

    async def _finish(
        self,
        job_id: UUID,
        plan: ResearchPlan,
        report: FinalReport,
        verification: VerificationReport,
        ledger: EvidenceLedger,
        attachments: AttachmentCorpus | None,
    ) -> ResearchOutcome:
        outcome = ResearchOutcome(
            job_id, plan, report, verification, ledger, attachments,
            cost_usd=self._job_spend.get(job_id), reach=self._source_reach.get(job_id),
        )
        # The ledger is stored whole: its unique claim IDs are the ones the report and verification cite.
        await self.repository.finish_job(
            job_id,
            status="succeeded",
            final_report=report.model_dump(mode="json"),
            verification=verification.model_dump(mode="json"),
            evidence_ledger=ledger.to_json(),
            review_reasons=outcome.review_reasons,
        )
        return outcome

    @staticmethod
    def _limits(route: ModelRoute, remaining_budget: float | None = None) -> UsageLimits:
        caps = [cap for cap in (route.cost_limit, remaining_budget) if cap is not None]
        return UsageLimits(
            request_limit=route.max_requests,
            tool_calls_limit=route.max_tool_calls,
            total_tokens_limit=route.total_tokens_limit,
            cost_limit=min(caps) if caps else None,
        )

    def _available(self, job_id: UUID, reserve: float) -> float | None:
        """Job cap less spend, allowances held by running calls, and `reserve`; None when uncapped."""
        limit = self.policy.job_cost_limit
        if limit is None or job_id not in self._job_spend:
            return None
        spent = self._job_spend[job_id]
        if spent is None:
            raise JobBudgetExceeded("job cost cap cannot be enforced: a model call had no pricing data")
        held, _ = self._job_holds.get(job_id, (0.0, 0))
        return limit - float(spent) - held - reserve

    async def _admit(self, job_id: UUID, route: ModelRoute, reserve: float) -> _BudgetHold | None:
        """Hold a call's cost allowance under the job cap until _release; None when uncapped.

        A call holds its route cost_limit, or everything left when the route has none. When that
        does not fit beside the allowances running calls hold, it waits for one of them to finish
        instead of counting on the same money; with none running it takes what is left, or is
        refused when nothing is. Parallel calls therefore run only as many at once as their full
        allowances fit, and a route without a cost_limit runs alone under a job cap.
        """
        while True:
            available = self._available(job_id, reserve)
            if available is None:
                return None
            held, holders = self._job_holds.get(job_id, (0.0, 0))
            wanted = route.cost_limit
            if wanted is not None and available >= wanted:
                amount = wanted
            elif holders:
                await self._budget_released.setdefault(job_id, asyncio.Event()).wait()
                continue
            elif available <= 0:
                limit = self.policy.job_cost_limit
                raise JobBudgetExceeded(f"job cost cap of ${limit:.2f} reached (${reserve:.2f} held in reserve)")
            else:
                amount = available
            self._job_holds[job_id] = (held + amount, holders + 1)
            return _BudgetHold(amount)

    def _release(self, job_id: UUID, hold: _BudgetHold | None) -> None:
        """Return a call's allowance and wake calls waiting for one; later calls do nothing."""
        if hold is None or hold.released:
            return
        hold.released = True
        held, holders = self._job_holds.get(job_id, (0.0, 0))
        if holders > 1:
            self._job_holds[job_id] = (held - hold.amount, holders - 1)
        else:
            self._job_holds.pop(job_id, None)
        if released := self._budget_released.pop(job_id, None):
            released.set()

    def _record_reach(self, job_id: UUID, events: list[ToolEvent]) -> None:
        if (reach := self._source_reach.get(job_id)) is not None:
            reach.add(events)

    def _record_spend(self, job_id: UUID, usage: RunUsage) -> None:
        spent = self._job_spend.get(job_id)
        if spent is None or not usage.requests:
            return
        self._job_spend[job_id] = None if usage.cost is None else spent + usage.cost

    @staticmethod
    def _constraints_payload(
        constraints: ResearchConstraints,
        attachments: AttachmentCorpus | None,
    ) -> dict[str, Any]:
        """Model-visible constraints without local filesystem paths."""
        return {
            "blocked_urls": constraints.blocked_urls,
            "benchmark_id": constraints.benchmark_id,
            "notes": constraints.notes,
            "attachments": attachments.prompt_manifest() if attachments else [],
        }

    def _assignment(self, job_id: UUID, question: ResearchQuestion,
                    attachments: AttachmentCorpus | None) -> ResearchAssignment:
        return ResearchAssignment(
            question=question,
            attachment_ids=frozenset(record.attachment_id for record in attachments.records) if attachments else frozenset(),
            source_policy=self._source_policies.get(job_id, SourcePolicy()),
        )

    @staticmethod
    def _ledger_refs(ledger: EvidenceLedger) -> LedgerRefs:
        return LedgerRefs(claim_ids=frozenset(ledger.claim_ids()), question_ids=frozenset(ledger.results))

    async def _run_agent(
        self,
        *,
        job_id: UUID,
        agent: Any,
        role: ResearchRole,
        route: ModelRoute,
        prompt: str,
        question_id: str | None = None,
        parent_task_id: UUID | None = None,
        attempt: int = 0,
        research_tools: bool = False,
        attachment_corpus: AttachmentCorpus | None = None,
        attachment_tools: bool = False,
        multimodal_inputs: bool = False,
        deps: Any = None,
        salvage: bool = False,
        persisted_prompt: str | None = None,
        captured: list[Any] | None = None,
        task_ids: list[UUID] | None = None,
        quote_texts: list[str] | None = None,
        require_retry_room: bool = False,
    ) -> Any:
        """Run one agent as a persisted task.

        `task_ids` receives the task's ID once it is persisted; on failure, `captured` receives
        the run's messages. A ResearchResult's quotes and cited sources are checked against this
        run's tool output plus `quote_texts` (a salvage call passes the output of the run it summarizes).
        `require_retry_room` refuses the call, before any model request, when one validation retry
        would not fit the route's token limit.
        """
        effective_config = route.snapshot() | {
            "tool_mode": self.config.tool_mode.value,
            "attachment_mode": self.config.attachment_mode.value,
            "attachment_count": len(attachment_corpus.records) if attachment_corpus else 0,
            "scholarly_cache_mode": self.config.scholarly_cache_mode,
        } | ({"salvage": True} if salvage else {})
        keep_recent = self.config.keep_recent_tool_results if research_tools else None
        if keep_recent is not None:
            effective_config["keep_recent_tool_results"] = keep_recent
        hold = await self._admit(job_id, route, self.policy.job_reserve_for(role, salvage=salvage))
        remaining_budget = hold.amount if hold else None
        try:
            task_id = await self.repository.start_task(
                job_id=job_id,
                parent_task_id=parent_task_id,
                role=role,
                question_id=question_id,
                prompt=prompt if persisted_prompt is None else persisted_prompt,
                model_id=route.model,
                effective_config=effective_config,
                attempt=attempt,
            )
            if task_ids is not None:
                task_ids.append(task_id)

            # A caller-owned RunUsage keeps counting billed requests even when the run raises.
            usage = RunUsage()
            run_messages: list[Any] = []
            trimmer = ToolResultTrimmer(keep_recent) if keep_recent is not None else None
            try:
                if require_retry_room:
                    needed = retry_token_budget(prompt, route, role)
                    if needed > route.total_tokens_limit:
                        raise PromptExceedsRetryBudget(
                            f"{role.value} needs about {needed} tokens for one retry, "
                            f"above its {route.total_tokens_limit} token limit"
                        )
                capabilities = (
                    build_research_capabilities(self.config.tool_mode) if research_tools else None
                )
                if trimmer is not None and capabilities is not None:
                    capabilities.append(ProcessHistory(trimmer))
                toolsets = []
                memo = self._fetch_memos.get(job_id)
                policy = self._source_policies.get(job_id)
                http = self._http_clients.get(job_id)
                if research_tools and self.config.tool_mode is ResearchToolMode.NORMALIZED:
                    toolsets.append(build_web_toolset(WebAcquisition(
                        cache_root=self.settings.benchmark_cache / "web",
                        cache_mode=self.config.scholarly_cache_mode,
                        client=http.fetch if http else None,
                        memo=memo,
                        policy=policy,
                    )))
                if research_tools and self.config.scholarly_tools:
                    scholar_client = ScholarClient(
                        cache=AcquisitionCache(
                            self.settings.benchmark_cache / "scholarly",
                            mode=self.config.scholarly_cache_mode,
                        ),
                        api_key=self.settings.openalex_api_key.get_secret_value() if self.settings.openalex_api_key else None,
                        contact_email=self.settings.crossref_mailto,
                        grobid_url=self.settings.grobid_url,
                        memo=memo,
                        policy=policy,
                        client=http.metadata if http else None,
                        fetch_client=http.fetch if http else None,
                    )
                    toolsets.append(build_scholar_toolset(scholar_client))
                if attachment_corpus and attachment_tools:
                    toolsets.append(build_attachment_toolset(attachment_corpus))

                user_prompt: Any = prompt
                if (
                    attachment_corpus
                    and multimodal_inputs
                    and self.config.attachment_mode is AttachmentMode.MULTIMODAL
                ):
                    user_prompt = build_multimodal_prompt(prompt, attachment_corpus)

                try:
                    with capture_run_messages() as run_messages:
                        result = await agent.run(
                            user_prompt,
                            model=route.model,
                            model_settings=route.model_settings(),
                            usage_limits=self._limits(route, remaining_budget),
                            usage=usage,
                            deps=deps,
                            capabilities=capabilities,
                            toolsets=toolsets or None,
                        )
                finally:
                    # Spend and the released allowance change together, so no call sees both or neither.
                    self._record_spend(job_id, usage)
                    self._release(job_id, hold)
                # Tool telemetry and the quote and source checks read what the tools returned.
                new_messages = trimmer.restore(result.new_messages()) if trimmer else result.new_messages()
                events = extract_tool_events(new_messages)
                self._record_reach(job_id, events)
                await self.repository.record_tool_events(task_id, events)
                output = result.output
                if isinstance(output, ResearchResult):
                    texts = [*tool_texts(events), *(quote_texts or ())]
                    output = check_sources(check_quotes(output, texts), texts)
                await self.repository.finish_task(
                    task_id,
                    status="succeeded",
                    output=(
                        output.model_dump(mode="json")
                        if isinstance(output, BaseModel)
                        else jsonable(output)
                    ),
                    usage=usage_snapshot(result.usage),
                    agent_run_id=result.run_id,
                    conversation_id=result.conversation_id,
                )
                return output
            except (Exception, asyncio.CancelledError) as exc:
                # Cancelled tasks are recorded too: a cancelled run, or a sibling branch the graph
                # cancelled because another failed.
                if trimmer is not None:
                    run_messages = trimmer.restore(run_messages)
                if captured is not None:
                    captured.extend(run_messages)
                with _recording_failure(exc):
                    if run_messages:
                        # Keep the original failure; the task row still records it.
                        with suppress(Exception):
                            events = extract_tool_events(run_messages)
                            self._record_reach(job_id, events)
                            await self.repository.record_tool_events(task_id, events)
                    await self.repository.finish_task(
                        task_id,
                        status="failed",
                        output=None,
                        usage=usage_snapshot(usage) if usage.requests else None,
                        agent_run_id=None,
                        conversation_id=None,
                        error=error_snapshot(exc),
                    )
                raise
        finally:
            self._release(job_id, hold)

    async def _run_research(self, question: ResearchQuestion, **kwargs: Any) -> ResearchResult:
        """Run a scout or deep dive; with salvage enabled, budget exhaustion degrades, not fails."""
        if not self.config.salvage_exhausted_research:
            return await self._run_agent(**kwargs)
        messages: list[Any] = []
        exhausted_ids: list[UUID] = []
        try:
            return await self._run_agent(**kwargs, captured=messages, task_ids=exhausted_ids)
        except JobBudgetExceeded:
            return _budget_exhausted_result(question)  # refused before any spend
        except UsageLimitExceeded:
            gathered, gathered_counts = _gathered_evidence(messages)
        if not gathered:
            return _budget_exhausted_result(question)
        request = json.loads(kwargs["prompt"]) | {
            "budget_exhausted": True,
            "instruction": (
                "Your research budget ran out. Do not call tools. Return the ResearchResult for this "
                "question using only gathered_evidence: your earlier tool calls with truncated results. "
                "Cite only sources that appear there, keep their IDs and publication status, record what "
                "remains unresolved, and lower confidence where the evidence is thin."
            ),
            # How gathered_evidence was built: calls skipped as errors, unanswered, or repeated;
            # results kept, cut to fit, or left out as oldest.
            "gathered_counts": gathered_counts,
        }
        # Postgres keeps hashes of replayed tool output, as it does for tool telemetry.
        stored = [
            {"tool": item["tool"], "args": safe_tool_args(item["args"]),
             "result_sha256": hashlib.sha256(item["result"].encode()).hexdigest(), "result_chars": len(item["result"])}
            for item in gathered
        ]
        try:
            return await self._run_agent(
                job_id=kwargs["job_id"],
                agent=kwargs["agent"],
                role=kwargs["role"],
                route=kwargs["route"].salvage(),
                prompt=json.dumps(request | {"gathered_evidence": gathered}, ensure_ascii=False),
                persisted_prompt=json.dumps(request | {"gathered_evidence": stored}, ensure_ascii=False),
                question_id=question.id,
                attempt=kwargs.get("attempt", 0),
                parent_task_id=exhausted_ids[0] if exhausted_ids else None,
                deps=kwargs.get("deps"),
                salvage=True,
                quote_texts=tool_texts(extract_tool_events(messages)),
            )
        except (JobBudgetExceeded, UsageLimitExceeded):
            return _budget_exhausted_result(question)

    async def _plan(
        self,
        job_id: UUID,
        objective: str,
        constraints: ResearchConstraints,
        attachments: AttachmentCorpus | None,
    ) -> ResearchPlan:
        qmin, qmax = self.policy.planner_question_range
        plan: ResearchPlan = await self._run_agent(
            job_id=job_id,
            agent=planner_agent,
            role=ResearchRole.PLANNER,
            route=self.policy.for_role(ResearchRole.PLANNER),
            prompt=json.dumps(
                {
                    "objective": objective,
                    "constraints": self._constraints_payload(constraints, attachments),
                    "planning_guidance": (
                        f"Aim for {qmin}-{qmax} non-overlapping research questions when the objective "
                        "is broad enough. Use fewer when additional questions would be artificial or redundant."
                    ),
                },
                ensure_ascii=False,
            ),
            attachment_corpus=attachments,
            attachment_tools=bool(attachments),
            multimodal_inputs=bool(attachments),
            deps=PlanLimits(max_questions=qmax),
        )
        await self.repository.save_plan(job_id, plan.model_dump(mode="json"))
        return plan

    async def _run_scout(
        self,
        job_id: UUID,
        q: ResearchQuestion,
        sem: asyncio.Semaphore,
        constraints: ResearchConstraints,
        attachments: AttachmentCorpus | None,
    ) -> ResearchResult:
        route = self.policy.scout_for(q)
        async with sem:
            return await self._run_research(
                q,
                job_id=job_id,
                agent=scout_agent,
                role=ResearchRole.SCOUT,
                route=route,
                prompt=json.dumps(
                    {
                        "question": q.model_dump(mode="json"),
                        "constraints": self._constraints_payload(constraints, attachments),
                    },
                    ensure_ascii=False,
                ),
                question_id=q.id,
                deps=self._assignment(job_id, q, attachments),
                research_tools=True,
                attachment_corpus=attachments,
                attachment_tools=bool(attachments),
                multimodal_inputs=q.requires_multimodal,
            )

    async def _run_gap(
        self,
        job_id: UUID,
        gap: Gap,
        question: ResearchQuestion,
        sem: asyncio.Semaphore,
        constraints: ResearchConstraints,
        attachments: AttachmentCorpus | None,
        *,
        attempt: int,
        parent_task_id: UUID | None = None,
    ) -> ResearchResult:
        """Deep-dive one gap; `parent_task_id` is the gap-analysis or verifier task that asked for it."""
        route = self.policy.escalation_for(question, attempt=attempt)
        prompt = json.dumps(
            {
                "question": question.model_dump(mode="json"),
                "gap": gap.model_dump(mode="json"),
                "constraints": self._constraints_payload(constraints, attachments),
            },
            ensure_ascii=False,
        )
        async with sem:
            return await self._run_research(
                question,
                job_id=job_id,
                agent=deep_dive_agent,
                role=ResearchRole.DEEP_DIVE,
                route=route,
                prompt=prompt,
                question_id=question.id,
                attempt=attempt,
                parent_task_id=parent_task_id,
                deps=self._assignment(job_id, question, attachments),
                research_tools=True,
                attachment_corpus=attachments,
                attachment_tools=bool(attachments),
                multimodal_inputs=question.requires_multimodal,
            )

    async def _analyze_gaps(
        self,
        job_id: UUID,
        objective: str,
        plan: ResearchPlan,
        ledger: EvidenceLedger,
        constraints: ResearchConstraints,
        attachments: AttachmentCorpus | None,
    ) -> tuple[list[Gap], UUID | None]:
        """The analyst's gaps plus low-confidence ones, at most one per question, and the analyst's task ID."""
        task_ids: list[UUID] = []
        analysis: GapAnalysis = await self._run_agent(
            job_id=job_id,
            agent=gap_agent,
            role=ResearchRole.GAP_ANALYST,
            route=self.policy.for_role(ResearchRole.GAP_ANALYST),
            prompt=json.dumps(
                {
                    "objective": objective,
                    "plan": plan.model_dump(mode="json"),
                    **ledger.prompt_view("results", include_search=True),
                    "constraints": self._constraints_payload(constraints, attachments),
                },
                ensure_ascii=False,
            ),
            task_ids=task_ids,
            deps=self._ledger_refs(ledger),
            require_retry_room=True,
        )
        gaps = self._dedupe_gaps(analysis.gaps + self._confidence_gaps(plan, ledger))
        return gaps, (task_ids[0] if task_ids else None)

    async def _synthesize(
        self,
        job_id: UUID,
        objective: str,
        ledger: EvidenceLedger,
        constraints: ResearchConstraints,
        attachments: AttachmentCorpus | None,
    ) -> FinalReport:
        route = self.policy.for_role(ResearchRole.SYNTHESIZER)
        return await self._run_agent(
            job_id=job_id,
            agent=synthesizer_agent,
            role=ResearchRole.SYNTHESIZER,
            route=route,
            deps=self._ledger_refs(ledger),
            require_retry_room=True,
            prompt=json.dumps(
                {
                    "objective": objective,
                    **ledger.prompt_view("evidence"),
                    "constraints": self._constraints_payload(constraints, attachments),
                },
                ensure_ascii=False,
            ),
        )

    async def _verify(
        self,
        job_id: UUID,
        objective: str,
        report: FinalReport,
        ledger: EvidenceLedger,
        constraints: ResearchConstraints,
        attachments: AttachmentCorpus | None,
        *,
        task_ids: list[UUID] | None = None,
    ) -> VerificationReport:
        route = self.policy.for_role(ResearchRole.VERIFIER)
        return await self._run_agent(
            job_id=job_id,
            agent=verifier_agent,
            role=ResearchRole.VERIFIER,
            route=route,
            deps=self._ledger_refs(ledger),
            prompt=json.dumps(
                {
                    "objective": objective,
                    "report": report.model_dump(mode="json"),
                    **ledger.prompt_view("evidence", claim_ids=report.claim_ids_used),
                    "constraints": self._constraints_payload(constraints, attachments),
                },
                ensure_ascii=False,
            ),
            task_ids=task_ids,
            require_retry_room=True,
        )

    def _select_gaps(
        self,
        gaps: list[Gap],
        questions: dict[str, ResearchQuestion],
    ) -> list[Gap]:
        """Select valid highest-priority gap work using the same limits in every orchestrator."""
        valid = [g for g in gaps if g.question_id in questions]
        valid.sort(key=lambda g: g.severity, reverse=True)
        return valid[: self.config.max_deep_dives_per_round]

    async def _resolve_gaps(
        self,
        job_id: UUID,
        gaps: list[Gap],
        questions: dict[str, ResearchQuestion],
        ledger: EvidenceLedger,
        constraints: ResearchConstraints,
        attachments: AttachmentCorpus | None,
        *,
        attempt: int,
        parent_task_id: UUID | None = None,
    ) -> None:
        valid = self._select_gaps(gaps, questions)
        if not valid:
            return

        sem = asyncio.Semaphore(self.config.max_parallel_deep_dives)
        deep_results = await _gather_or_cancel(
            
                self._run_gap(
                    job_id,
                    g,
                    questions[g.question_id],
                    sem,
                    constraints,
                    attachments,
                    attempt=attempt,
                    parent_task_id=parent_task_id,
                )
                for g in valid
            
        )
        for result in deep_results:
            ledger.add(result)

    def _confidence_gaps(self, plan: ResearchPlan, ledger: EvidenceLedger) -> list[Gap]:
        gaps: list[Gap] = []
        for q in plan.questions:
            attempts = ledger.for_question(q.id)
            if not attempts:
                gaps.append(
                    Gap(
                        question_id=q.id,
                        reason="missing_evidence",
                        followup=f"No scout result exists for: {q.question}",
                        severity=q.priority,
                    )
                )
                continue
            best = max(r.confidence for r in attempts)
            if best < self.config.min_scout_confidence:
                gaps.append(
                    Gap(
                        question_id=q.id,
                        reason="low_confidence",
                        followup=(
                            f"Raise confidence above {self.config.min_scout_confidence:.2f} for: "
                            f"{q.question}"
                        ),
                        severity=max(2, q.priority),
                    )
                )
        return gaps

    @staticmethod
    def _dedupe_gaps(gaps: list[Gap]) -> list[Gap]:
        """Keep at most one highest-severity escalation per question per round."""
        best: dict[str, Gap] = {}
        for gap in gaps:
            current = best.get(gap.question_id)
            if current is None or gap.severity > current.severity:
                best[gap.question_id] = gap
        return list(best.values())

    async def run_agent_job(
        self,
        objective: str,
        *,
        agent: Any,
        role: ResearchRole,
        route: ModelRoute,
        prompt: str,
        deps: Any = None,
        config: dict[str, Any] | None = None,
    ) -> AgentJobOutcome:
        """Run one agent as its own job with the same persistence and job cost cap as run()."""
        self.policy.validate()
        job_id = await self.repository.create_job(
            session_id=uuid4(),
            root_run_id=uuid4(),
            objective=objective,
            policy_name=self.policy.name,
            config={
                "orchestrator": {"kind": "single-agent", "graph_version": None},
                "policy": self.policy.snapshot(),
                **(config or {}),
            },
        )
        async with self._job_scope(job_id):
            output = await self._run_agent(
                job_id=job_id, agent=agent, role=role, route=route, prompt=prompt, deps=deps
            )
            await self.repository.finish_job(
                job_id,
                status="succeeded",
                final_report=output.model_dump(mode="json") if isinstance(output, BaseModel) else jsonable(output),
                verification=None,
            )
            return AgentJobOutcome(job_id, output, cost_usd=self._job_spend.get(job_id))

    async def run(
        self,
        objective: str,
        *,
        session_id: UUID | None = None,
        root_run_id: UUID | None = None,
        constraints: ResearchConstraints | None = None,
    ) -> ResearchOutcome:
        constraints = constraints or ResearchConstraints()
        job_id = await self._create_job(
            objective, constraints, session_id=session_id, root_run_id=root_run_id, kind="async-legacy"
        )
        ledger = EvidenceLedger()
        async with self._job_scope(job_id, constraints, ledger):
            attachments = await self._load_attachments(job_id, constraints)
            plan = await self._plan(job_id, objective, constraints, attachments)
            questions = {q.id: q for q in plan.questions}

            scout_sem = asyncio.Semaphore(self.config.max_parallel_scouts)
            scout_results = await _gather_or_cancel(
                self._run_scout(job_id, q, scout_sem, constraints, attachments) for q in plan.questions
            )
            for result in scout_results:
                ledger.add(result)

            gaps, gap_task_id = await self._analyze_gaps(
                job_id, objective, plan, ledger, constraints, attachments
            )
            await self._resolve_gaps(
                job_id, gaps, questions, ledger, constraints, attachments, attempt=0,
                parent_task_id=gap_task_id,
            )

            report = await self._synthesize(job_id, objective, ledger, constraints, attachments)
            verify_task_ids: list[UUID] = []
            verification = await self._verify(
                job_id, objective, report, ledger, constraints, attachments, task_ids=verify_task_ids
            )

            for round_index in range(self.config.max_verification_rounds):
                if not verification.needs_research or not verification.followups:
                    break
                await self._resolve_gaps(
                    job_id,
                    self._dedupe_gaps(verification.followups),
                    questions,
                    ledger,
                    constraints,
                    attachments,
                    attempt=round_index + 1,
                    parent_task_id=verify_task_ids[-1] if verify_task_ids else None,
                )
                report = await self._synthesize(job_id, objective, ledger, constraints, attachments)
                verification = await self._verify(
                    job_id, objective, report, ledger, constraints, attachments, task_ids=verify_task_ids
                )

            return await self._finish(job_id, plan, report, verification, ledger, attachments)

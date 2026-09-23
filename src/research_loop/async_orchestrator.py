from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import anyio
from pydantic import BaseModel
from pydantic_ai import UsageLimits, capture_run_messages
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.usage import RunUsage

from .acquisition import AcquisitionCache, FetchMemo
from .agents import (
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
from .policy import ModelPolicy, ModelRoute
from .quotes import check_quotes, tool_texts
from .repository import NullResearchRepository, ResearchRepository
from .scholar import ScholarClient, build_scholar_toolset
from .settings import ResearchSettings
from .schemas import (
    FinalReport,
    Gap,
    GapAnalysis,
    ResearchConstraints,
    ResearchPlan,
    ResearchQuestion,
    ResearchResult,
    ResearchRole,
    VerificationReport,
)
from .telemetry import error_snapshot, extract_tool_events, jsonable, safe_tool_args, usage_snapshot
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

    def __post_init__(self) -> None:
        # Zero deep dives or verification rounds disables that step; zero parallel slots would hang.
        for name, minimum in (("max_parallel_scouts", 1), ("max_parallel_deep_dives", 1),
                              ("max_deep_dives_per_round", 0), ("max_verification_rounds", 0)):
            if getattr(self, name) < minimum:
                raise ValueError(f"{name} must be at least {minimum}")
        if not 0.0 <= self.min_scout_confidence <= 1.0:
            raise ValueError("min_scout_confidence must be between 0 and 1")


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
        except Exception as write_error:
            exc.add_note(f"Recording this failure also failed ({type(write_error).__name__}).")
    if scope.cancelled_caught:
        exc.add_note("Recording this failure timed out.")


# Bounds on tool output replayed to a salvage call.
_SALVAGE_EVIDENCE_CHARS = 48_000
_SALVAGE_RESULT_CHARS = 4_000


def _gathered_evidence(messages: list[Any]) -> list[dict[str, Any]]:
    """Tool calls and truncated results from an interrupted run, oldest first, within a size bound."""
    gathered: list[dict[str, Any]] = []
    used = 0
    for event in extract_tool_events(messages):
        if event.result is None:
            continue
        result = json.dumps(event.result, ensure_ascii=False, default=str)[:_SALVAGE_RESULT_CHARS]
        if used + len(result) > _SALVAGE_EVIDENCE_CHARS:
            break
        gathered.append({"tool": event.tool_name, "args": event.args, "result": result})
        used += len(result)
    return gathered


def _budget_exhausted_result(question: ResearchQuestion) -> ResearchResult:
    return ResearchResult(
        question_id=question.id,
        question=question.question,
        conclusion="No evidence summarized: the research budget ran out before a structured result.",
        unresolved_questions=[question.question],
        confidence=0.0,
    )


@dataclass
class ResearchOutcome:
    job_id: UUID
    plan: ResearchPlan
    report: FinalReport
    verification: VerificationReport
    ledger: EvidenceLedger
    attachments: AttachmentCorpus | None = None
    cost_usd: Decimal | None = None


@dataclass
class AgentJobOutcome:
    job_id: UUID
    output: Any
    cost_usd: Decimal | None = None


class JobBudgetExceeded(RuntimeError):
    """The policy's per-job cost cap is spent, or spend could not be priced."""


class AsyncResearchLoop:
    """Legacy v4-style plain-async orchestration kept for parity/regression."""

    def __init__(
        self,
        policy: ModelPolicy,
        config: ResearchConfig | None = None,
        repository: ResearchRepository | None = None,
    ) -> None:
        self.policy = policy
        self.settings = ResearchSettings.from_env()
        self.config = config or ResearchConfig(scholarly_cache_mode=self.settings.scholarly_cache_mode)
        self.repository: ResearchRepository = repository or NullResearchRepository()
        # Per-job USD spend; None once any billed call could not be priced.
        self._job_spend: dict[UUID, Decimal | None] = {}
        # Per-job fetched documents, shared by the job's agents and dropped when it ends.
        self._fetch_memos: dict[UUID, FetchMemo] = {}

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
    async def _job_scope(self, job_id: UUID) -> AsyncIterator[None]:
        """Hold the job's spend and fetch memo while it runs; record the job failed if the body raises.

        Cancellation, which is how Ctrl-C reaches the run, is recorded too, so an interrupted
        run does not stay "running".
        """
        self._job_spend[job_id] = Decimal(0)
        self._fetch_memos[job_id] = FetchMemo()
        try:
            yield
        except (Exception, asyncio.CancelledError) as exc:
            with _recording_failure(exc):
                await self.repository.finish_job(
                    job_id,
                    status="failed",
                    final_report=None,
                    verification=None,
                    error=error_snapshot(exc),
                )
            raise
        finally:
            self._job_spend.pop(job_id, None)
            self._fetch_memos.pop(job_id, None)

    async def _load_attachments(
        self, job_id: UUID, constraints: ResearchConstraints
    ) -> AttachmentCorpus | None:
        if not constraints.attachment_paths:
            return None
        attachments = AttachmentCorpus.from_paths(
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
        await self.repository.finish_job(
            job_id,
            status="succeeded",
            final_report=report.model_dump(mode="json"),
            verification=verification.model_dump(mode="json"),
        )
        return ResearchOutcome(
            job_id, plan, report, verification, ledger, attachments,
            cost_usd=self._job_spend.get(job_id),
        )

    @staticmethod
    def _limits(route: ModelRoute, remaining_budget: float | None = None) -> UsageLimits:
        caps = [cap for cap in (route.cost_limit, remaining_budget) if cap is not None]
        return UsageLimits(
            request_limit=route.max_requests,
            tool_calls_limit=route.max_tool_calls,
            total_tokens_limit=route.total_tokens_limit,
            cost_limit=min(caps) if caps else None,
        )

    def _remaining_budget(self, job_id: UUID, reserve: float = 0.0) -> float | None:
        """Spend left under the job cap after `reserve`; raises before a call that cannot fit."""
        limit = self.policy.job_cost_limit
        if limit is None or job_id not in self._job_spend:
            return None
        spent = self._job_spend[job_id]
        if spent is None:
            raise JobBudgetExceeded("job cost cap cannot be enforced: a model call had no pricing data")
        remaining = limit - float(spent) - reserve
        if remaining <= 0:
            raise JobBudgetExceeded(f"job cost cap of ${limit:.2f} reached (${reserve:.2f} held in reserve)")
        return remaining

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
    ) -> Any:
        """Run one agent as a persisted task.

        `task_ids` receives the task's ID once it is persisted; on failure, `captured` receives
        the run's messages. A ResearchResult's quotes are checked against this run's tool output
        plus `quote_texts` (a salvage call passes the output of the run it summarizes).
        """
        effective_config = route.snapshot() | {
            "tool_mode": self.config.tool_mode.value,
            "attachment_mode": self.config.attachment_mode.value,
            "attachment_count": len(attachment_corpus.records) if attachment_corpus else 0,
            "scholarly_cache_mode": self.config.scholarly_cache_mode,
        } | ({"salvage": True} if salvage else {})
        remaining_budget = self._remaining_budget(
            job_id, self.policy.job_reserve_for(role, salvage=salvage)
        )
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
        try:
            capabilities = (
                build_research_capabilities(self.config.tool_mode) if research_tools else None
            )
            toolsets = []
            memo = self._fetch_memos.get(job_id)
            if research_tools and self.config.tool_mode is ResearchToolMode.NORMALIZED:
                toolsets.append(build_web_toolset(WebAcquisition(
                    cache_root=self.settings.benchmark_cache / "web",
                    cache_mode=self.config.scholarly_cache_mode,
                    memo=memo,
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
                self._record_spend(job_id, usage)
            events = extract_tool_events(result.new_messages())
            await self.repository.record_tool_events(task_id, events)
            output = result.output
            if isinstance(output, ResearchResult):
                output = check_quotes(output, [*tool_texts(events), *(quote_texts or ())])
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
            if captured is not None:
                captured.extend(run_messages)
            with _recording_failure(exc):
                if run_messages:
                    try:
                        await self.repository.record_tool_events(task_id, extract_tool_events(run_messages))
                    except Exception:
                        pass  # keep the original failure; the task row still records it
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
            gathered = _gathered_evidence(messages)
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
                    "results": [r.model_dump(mode="json") for r in ledger.all()],
                    "constraints": self._constraints_payload(constraints, attachments),
                },
                ensure_ascii=False,
            ),
            task_ids=task_ids,
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
            prompt=json.dumps(
                {
                    "objective": objective,
                    "evidence": [r.model_dump(mode="json") for r in ledger.all()],
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
            prompt=json.dumps(
                {
                    "objective": objective,
                    "report": report.model_dump(mode="json"),
                    "evidence": [r.model_dump(mode="json") for r in ledger.all()],
                    "constraints": self._constraints_payload(constraints, attachments),
                },
                ensure_ascii=False,
            ),
            task_ids=task_ids,
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
        deep_results = await asyncio.gather(
            *(
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
        async with self._job_scope(job_id):
            attachments = await self._load_attachments(job_id, constraints)
            plan = await self._plan(job_id, objective, constraints, attachments)
            questions = {q.id: q for q in plan.questions}

            ledger = EvidenceLedger()
            scout_sem = asyncio.Semaphore(self.config.max_parallel_scouts)
            scout_results = await asyncio.gather(
                *(
                    self._run_scout(job_id, q, scout_sem, constraints, attachments)
                    for q in plan.questions
                )
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

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass, field
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel
from pydantic_ai import UsageLimits

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
    VerificationReport,
)
from .telemetry import extract_tool_events, jsonable, usage_snapshot
from .tools import ResearchToolMode, build_research_capabilities


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


@dataclass
class ResearchOutcome:
    job_id: UUID
    plan: ResearchPlan
    report: FinalReport
    verification: VerificationReport
    ledger: EvidenceLedger
    attachments: AttachmentCorpus | None = None


class AsyncResearchLoop:
    """Legacy v4-style plain-async orchestration kept for parity/regression."""

    def __init__(
        self,
        policy: ModelPolicy,
        config: ResearchConfig | None = None,
        repository: ResearchRepository | None = None,
    ) -> None:
        self.policy = policy
        self.config = config or ResearchConfig()
        self.repository: ResearchRepository = repository or NullResearchRepository()
        # Exposed to the graph orchestrator so agent definitions remain centralized here.
        self._planner_agent = planner_agent
        self._gap_agent = gap_agent

    @staticmethod
    def _limits(route: ModelRoute) -> UsageLimits:
        return UsageLimits(
            request_limit=route.max_requests,
            tool_calls_limit=route.max_tool_calls,
            total_tokens_limit=route.total_tokens_limit,
            cost_limit=route.cost_limit,
        )

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
    ) -> Any:
        effective_config = route.snapshot() | {
            "tool_mode": self.config.tool_mode.value,
            "attachment_mode": self.config.attachment_mode.value,
            "attachment_count": len(attachment_corpus.records) if attachment_corpus else 0,
        }
        task_id = await self.repository.start_task(
            job_id=job_id,
            parent_task_id=parent_task_id,
            role=role,
            question_id=question_id,
            prompt=prompt,
            model_id=route.model,
            effective_config=effective_config,
            attempt=attempt,
        )

        try:
            capabilities = (
                build_research_capabilities(self.config.tool_mode) if research_tools else None
            )
            toolsets = None
            if attachment_corpus and attachment_tools:
                toolsets = [build_attachment_toolset(attachment_corpus)]

            user_prompt: Any = prompt
            if (
                attachment_corpus
                and multimodal_inputs
                and self.config.attachment_mode is AttachmentMode.MULTIMODAL
            ):
                user_prompt = build_multimodal_prompt(prompt, attachment_corpus)

            result = await agent.run(
                user_prompt,
                model=route.model,
                model_settings=route.model_settings(),
                usage_limits=self._limits(route),
                capabilities=capabilities,
                toolsets=toolsets,
            )
            events = extract_tool_events(result.new_messages())
            await self.repository.record_tool_events(task_id, events)
            output = result.output
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
        except Exception as exc:
            await self.repository.finish_task(
                task_id,
                status="failed",
                output=None,
                usage=None,
                agent_run_id=None,
                conversation_id=None,
                error={"type": type(exc).__name__, "message": str(exc)},
            )
            raise

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
            return await self._run_agent(
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
    ) -> ResearchResult:
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
            return await self._run_agent(
                job_id=job_id,
                agent=deep_dive_agent,
                role=ResearchRole.DEEP_DIVE,
                route=route,
                prompt=prompt,
                question_id=question.id,
                attempt=attempt,
                research_tools=True,
                attachment_corpus=attachments,
                attachment_tools=bool(attachments),
                multimodal_inputs=question.requires_multimodal,
            )

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

    async def run(
        self,
        objective: str,
        *,
        session_id: UUID | None = None,
        root_run_id: UUID | None = None,
        constraints: ResearchConstraints | None = None,
    ) -> ResearchOutcome:
        session_id = session_id or uuid4()
        root_run_id = root_run_id or uuid4()
        constraints = constraints or ResearchConstraints()

        job_id = await self.repository.create_job(
            session_id=session_id,
            root_run_id=root_run_id,
            objective=objective,
            policy_name=self.policy.name,
            config={
                "orchestrator": {"kind": "async-legacy", "graph_version": None},
                "loop": jsonable(asdict(self.config)),
                "policy": self.policy.snapshot(),
                "constraints": {
                    "blocked_urls": constraints.blocked_urls,
                    "benchmark_id": constraints.benchmark_id,
                    "notes": constraints.notes,
                    "attachment_count": len(constraints.attachment_paths),
                },
            },
        )

        try:
            attachments = None
            if constraints.attachment_paths:
                attachments = AttachmentCorpus.from_paths(
                    constraints.attachment_paths,
                    limits=self.config.attachment_limits,
                    strict=self.config.attachment_strict,
                )
                await self.repository.save_attachments(job_id, attachments.manifest())

            planner_route = self.policy.for_role(ResearchRole.PLANNER)
            qmin, qmax = self.policy.planner_question_range
            planner_prompt = json.dumps(
                {
                    "objective": objective,
                    "constraints": self._constraints_payload(constraints, attachments),
                    "planning_guidance": (
                        f"Aim for {qmin}-{qmax} non-overlapping research questions when the objective "
                        "is broad enough. Use fewer when additional questions would be artificial or redundant."
                    ),
                },
                ensure_ascii=False,
            )
            plan: ResearchPlan = await self._run_agent(
                job_id=job_id,
                agent=planner_agent,
                role=ResearchRole.PLANNER,
                route=planner_route,
                prompt=planner_prompt,
                attachment_corpus=attachments,
                attachment_tools=bool(attachments),
                multimodal_inputs=bool(attachments),
            )
            await self.repository.save_plan(job_id, plan.model_dump(mode="json"))
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

            gap_route = self.policy.for_role(ResearchRole.GAP_ANALYST)
            gap_analysis: GapAnalysis = await self._run_agent(
                job_id=job_id,
                agent=gap_agent,
                role=ResearchRole.GAP_ANALYST,
                route=gap_route,
                prompt=json.dumps(
                    {
                        "objective": objective,
                        "plan": plan.model_dump(mode="json"),
                        "results": [r.model_dump(mode="json") for r in ledger.all()],
                        "constraints": self._constraints_payload(constraints, attachments),
                    },
                    ensure_ascii=False,
                ),
            )
            gaps = self._dedupe_gaps(gap_analysis.gaps + self._confidence_gaps(plan, ledger))
            await self._resolve_gaps(
                job_id, gaps, questions, ledger, constraints, attachments, attempt=0
            )

            report = await self._synthesize(job_id, objective, ledger, constraints, attachments)
            verification = await self._verify(
                job_id, objective, report, ledger, constraints, attachments
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
                )
                report = await self._synthesize(job_id, objective, ledger, constraints, attachments)
                verification = await self._verify(
                    job_id, objective, report, ledger, constraints, attachments
                )

            await self.repository.finish_job(
                job_id,
                status="succeeded",
                final_report=report.model_dump(mode="json"),
                verification=verification.model_dump(mode="json"),
            )
            return ResearchOutcome(job_id, plan, report, verification, ledger, attachments)
        except Exception as exc:
            await self.repository.finish_job(
                job_id,
                status="failed",
                final_report=None,
                verification=None,
                error={"type": type(exc).__name__, "message": str(exc)},
            )
            raise

from __future__ import annotations

import asyncio
from dataclasses import asdict
from decimal import Decimal
from uuid import UUID, uuid4

from .async_orchestrator import AsyncResearchLoop, ResearchConfig, ResearchOutcome
from .attachments import AttachmentCorpus
from .graph import (
    RESEARCH_GRAPH_VERSION,
    ResearchGraphDeps,
    ResearchGraphInput,
    ResearchGraphState,
    get_research_graph,
    render_research_graph,
)
from .ledger import EvidenceLedger
from .policy import ModelPolicy
from .repository import NullResearchRepository, ResearchRepository
from .schemas import ResearchConstraints
from .telemetry import error_snapshot, jsonable


class ResearchLoop(AsyncResearchLoop):
    """Graph-backed research-loop facade.

    Public behavior is intentionally compatible with the v4 async orchestrator.
    Pydantic Graph owns workflow topology; PydanticAI agents still own model/tool
    semantics, and Postgres remains the durable system of record.
    """

    graph_version = RESEARCH_GRAPH_VERSION

    def __init__(
        self,
        policy: ModelPolicy,
        config: ResearchConfig | None = None,
        repository: ResearchRepository | None = None,
    ) -> None:
        super().__init__(policy, config, repository or NullResearchRepository())
        self.graph = get_research_graph()

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
                "orchestrator": {
                    "kind": "pydantic-graph",
                    "graph_version": self.graph_version,
                },
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

        self._job_spend[job_id] = Decimal(0)
        try:
            attachments: AttachmentCorpus | None = None
            if constraints.attachment_paths:
                attachments = AttachmentCorpus.from_paths(
                    constraints.attachment_paths,
                    limits=self.config.attachment_limits,
                    strict=self.config.attachment_strict,
                )
                await self.repository.save_attachments(job_id, attachments.manifest())

            ledger = EvidenceLedger()
            state = ResearchGraphState(
                job_id=job_id,
                objective=objective,
                policy_name=self.policy.name,
                max_verification_rounds=self.config.max_verification_rounds,
            )
            deps = ResearchGraphDeps(
                loop=self,
                job_id=job_id,
                constraints=constraints,
                attachments=attachments,
                ledger=ledger,
                scout_semaphore=asyncio.Semaphore(self.config.max_parallel_scouts),
                deep_dive_semaphore=asyncio.Semaphore(
                    self.config.max_parallel_deep_dives
                ),
            )
            graph_result = await self.graph.run(
                state=state,
                deps=deps,
                inputs=ResearchGraphInput(objective=objective),
            )

            await self.repository.finish_job(
                job_id,
                status="succeeded",
                final_report=graph_result.report.model_dump(mode="json"),
                verification=graph_result.verification.model_dump(mode="json"),
            )
            return ResearchOutcome(
                job_id=job_id,
                plan=graph_result.plan,
                report=graph_result.report,
                verification=graph_result.verification,
                ledger=ledger,
                attachments=attachments,
                cost_usd=self._job_spend.get(job_id),
            )
        except Exception as exc:
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

    @classmethod
    def render_graph(cls, *, direction: str = "LR") -> str:
        return render_research_graph(direction=direction)


# Explicit baseline for parity/regression experiments.
LegacyResearchLoop = AsyncResearchLoop

__all__ = [
    "LegacyResearchLoop",
    "RESEARCH_GRAPH_VERSION",
    "ResearchConfig",
    "ResearchLoop",
    "ResearchOutcome",
    "render_research_graph",
]

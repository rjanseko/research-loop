from __future__ import annotations

import asyncio
from uuid import UUID

from .async_orchestrator import AsyncResearchLoop, ResearchConfig, ResearchOutcome
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
from .repository import ResearchRepository
from .schemas import ResearchConstraints
from .settings import ResearchSettings


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
        *,
        settings: ResearchSettings | None = None,
    ) -> None:
        super().__init__(policy, config, repository, settings=settings)
        self.graph = get_research_graph()

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
            objective, constraints, session_id=session_id, root_run_id=root_run_id,
            kind="pydantic-graph", graph_version=self.graph_version,
        )
        ledger = EvidenceLedger()
        async with self._job_scope(job_id, constraints, ledger):
            attachments = await self._load_attachments(job_id, constraints)
            result = await self.graph.run(
                state=ResearchGraphState(
                    job_id=job_id,
                    objective=objective,
                    policy_name=self.policy.name,
                    max_verification_rounds=self.config.max_verification_rounds,
                ),
                deps=ResearchGraphDeps(
                    loop=self,
                    job_id=job_id,
                    constraints=constraints,
                    attachments=attachments,
                    ledger=ledger,
                    scout_semaphore=asyncio.Semaphore(self.config.max_parallel_scouts),
                    deep_dive_semaphore=asyncio.Semaphore(self.config.max_parallel_deep_dives),
                ),
                inputs=ResearchGraphInput(objective=objective),
            )
            return await self._finish(
                job_id, result.plan, result.report, result.verification, ledger, attachments
            )

    @classmethod
    def render_graph(cls, *, direction: str = "LR") -> str:
        return render_research_graph(direction=direction)


# Explicit baseline for parity/regression experiments.
LegacyResearchLoop = AsyncResearchLoop

__all__ = [
    "RESEARCH_GRAPH_VERSION",
    "LegacyResearchLoop",
    "ResearchConfig",
    "ResearchLoop",
    "ResearchOutcome",
    "render_research_graph",
]

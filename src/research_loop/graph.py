from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from pydantic_graph import GraphBuilder, StepContext, reduce_list_append

from .attachments import AttachmentCorpus
from .async_orchestrator import AsyncResearchLoop
from .ledger import EvidenceLedger
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

RESEARCH_GRAPH_VERSION = "research-graph-v1"


@dataclass(frozen=True)
class ResearchGraphInput:
    objective: str


@dataclass
class ResearchGraphState:
    """Small control-plane state shared by graph nodes.

    Evidence is intentionally not stored here. Parallel workers return immutable
    typed values; serial record steps append them to deps.ledger.
    """

    job_id: UUID
    objective: str
    policy_name: str
    max_verification_rounds: int
    verification_round: int = 0
    phase: str = "created"
    plan: ResearchPlan | None = None


@dataclass
class ResearchGraphDeps:
    loop: AsyncResearchLoop
    job_id: UUID
    constraints: ResearchConstraints
    attachments: AttachmentCorpus | None
    ledger: EvidenceLedger
    scout_semaphore: asyncio.Semaphore
    deep_dive_semaphore: asyncio.Semaphore


@dataclass(frozen=True)
class InitialResearchNeeded:
    gaps: list[Gap]
    parent_task_id: UUID | None = None  # the gap-analysis task


@dataclass(frozen=True)
class ReadyForSynthesis:
    reason: str = "initial research complete"


@dataclass(frozen=True)
class ScoutWork:
    order: int
    question: ResearchQuestion


@dataclass(frozen=True)
class OrderedResearchResult:
    order: int
    result: ResearchResult


@dataclass(frozen=True)
class DeepDiveWork:
    order: int
    gap: Gap
    question: ResearchQuestion
    attempt: int
    parent_task_id: UUID | None = None  # the task that asked for this deep dive


@dataclass(frozen=True)
class VerificationBundle:
    report: FinalReport
    verification: VerificationReport
    task_id: UUID | None = None  # the verifier task


@dataclass(frozen=True)
class VerificationResearchNeeded:
    followups: list[Gap]
    parent_task_id: UUID | None = None  # the verifier task


@dataclass(frozen=True)
class VerificationComplete:
    report: FinalReport
    verification: VerificationReport


@dataclass(frozen=True)
class ResearchGraphResult:
    plan: ResearchPlan
    report: FinalReport
    verification: VerificationReport


def _questions(state: ResearchGraphState) -> dict[str, ResearchQuestion]:
    if state.plan is None:
        raise RuntimeError("research plan is not available")
    return {q.id: q for q in state.plan.questions}


def build_research_graph():
    """Build the graph-backed research algorithm.

    The topology is deliberately independent of model routing. ModelPolicy remains
    owned by the ResearchLoop/AsyncResearchLoop instance in deps.loop.
    """

    g = GraphBuilder(
        name=RESEARCH_GRAPH_VERSION,
        state_type=ResearchGraphState,
        deps_type=ResearchGraphDeps,
        input_type=ResearchGraphInput,
        output_type=ResearchGraphResult,
    )

    @g.step(label="Plan research")
    async def plan(
        ctx: StepContext[ResearchGraphState, ResearchGraphDeps, ResearchGraphInput],
    ) -> list[ScoutWork]:
        ctx.state.phase = "planning"
        objective = ctx.inputs.objective
        route = ctx.deps.loop.policy.for_role(ResearchRole.PLANNER)
        qmin, qmax = ctx.deps.loop.policy.planner_question_range
        prompt = json.dumps(
            {
                "objective": objective,
                "constraints": ctx.deps.loop._constraints_payload(
                    ctx.deps.constraints, ctx.deps.attachments
                ),
                "planning_guidance": (
                    f"Aim for {qmin}-{qmax} non-overlapping research questions when the objective "
                    "is broad enough. Use fewer when additional questions would be artificial or redundant."
                ),
            },
            ensure_ascii=False,
        )
        research_plan: ResearchPlan = await ctx.deps.loop._run_agent(
            job_id=ctx.deps.job_id,
            agent=ctx.deps.loop._planner_agent,
            role=ResearchRole.PLANNER,
            route=route,
            prompt=prompt,
            attachment_corpus=ctx.deps.attachments,
            attachment_tools=bool(ctx.deps.attachments),
            multimodal_inputs=bool(ctx.deps.attachments),
        )
        ctx.state.plan = research_plan
        ctx.state.phase = "scouting"
        await ctx.deps.loop.repository.save_plan(
            ctx.deps.job_id, research_plan.model_dump(mode="json")
        )
        return [ScoutWork(order=i, question=q) for i, q in enumerate(research_plan.questions)]

    @g.step(label="Scout question")
    async def scout(
        ctx: StepContext[ResearchGraphState, ResearchGraphDeps, ScoutWork],
    ) -> OrderedResearchResult:
        result = await ctx.deps.loop._run_scout(
            ctx.deps.job_id,
            ctx.inputs.question,
            ctx.deps.scout_semaphore,
            ctx.deps.constraints,
            ctx.deps.attachments,
        )
        return OrderedResearchResult(order=ctx.inputs.order, result=result)

    scout_join = g.join(reduce_list_append, initial_factory=list[OrderedResearchResult])

    @g.step(label="Record scout evidence")
    async def record_scouts(
        ctx: StepContext[ResearchGraphState, ResearchGraphDeps, list[OrderedResearchResult]],
    ) -> None:
        for item in sorted(ctx.inputs, key=lambda item: item.order):
            ctx.deps.ledger.add(item.result)
        return None

    @g.step(label="Analyze evidence gaps")
    async def analyze_gaps(
        ctx: StepContext[ResearchGraphState, ResearchGraphDeps, None],
    ) -> InitialResearchNeeded | ReadyForSynthesis:
        ctx.state.phase = "gap_analysis"
        if ctx.state.plan is None:
            raise RuntimeError("gap analysis requires a research plan")
        route = ctx.deps.loop.policy.for_role(ResearchRole.GAP_ANALYST)
        gap_task_ids: list[UUID] = []
        gap_analysis: GapAnalysis = await ctx.deps.loop._run_agent(
            job_id=ctx.deps.job_id,
            agent=ctx.deps.loop._gap_agent,
            role=ResearchRole.GAP_ANALYST,
            route=route,
            prompt=json.dumps(
                {
                    "objective": ctx.state.objective,
                    "plan": ctx.state.plan.model_dump(mode="json"),
                    "results": [
                        r.model_dump(mode="json") for r in ctx.deps.ledger.all()
                    ],
                    "constraints": ctx.deps.loop._constraints_payload(
                        ctx.deps.constraints, ctx.deps.attachments
                    ),
                },
                ensure_ascii=False,
            ),
            task_ids=gap_task_ids,
        )
        gaps = ctx.deps.loop._dedupe_gaps(
            gap_analysis.gaps
            + ctx.deps.loop._confidence_gaps(ctx.state.plan, ctx.deps.ledger)
        )
        selected = ctx.deps.loop._select_gaps(gaps, _questions(ctx.state))
        if selected:
            return InitialResearchNeeded(selected, parent_task_id=gap_task_ids[0] if gap_task_ids else None)
        return ReadyForSynthesis()

    @g.step(label="Prepare initial deep dives")
    async def prepare_initial_deep_dives(
        ctx: StepContext[ResearchGraphState, ResearchGraphDeps, InitialResearchNeeded],
    ) -> list[DeepDiveWork]:
        ctx.state.phase = "initial_deep_dive"
        questions = _questions(ctx.state)
        return [
            DeepDiveWork(order=i, gap=gap, question=questions[gap.question_id], attempt=0,
                         parent_task_id=ctx.inputs.parent_task_id)
            for i, gap in enumerate(ctx.inputs.gaps)
            if gap.question_id in questions
        ]

    @g.step(label="Initial deep dive")
    async def initial_deep_dive(
        ctx: StepContext[ResearchGraphState, ResearchGraphDeps, DeepDiveWork],
    ) -> OrderedResearchResult:
        work = ctx.inputs
        result = await ctx.deps.loop._run_gap(
            ctx.deps.job_id,
            work.gap,
            work.question,
            ctx.deps.deep_dive_semaphore,
            ctx.deps.constraints,
            ctx.deps.attachments,
            attempt=work.attempt,
            parent_task_id=work.parent_task_id,
        )
        return OrderedResearchResult(order=work.order, result=result)

    initial_deep_join = g.join(
        reduce_list_append, initial_factory=list[OrderedResearchResult]
    )

    @g.step(label="Record initial deep-dive evidence")
    async def record_initial_deep_dives(
        ctx: StepContext[ResearchGraphState, ResearchGraphDeps, list[OrderedResearchResult]],
    ) -> None:
        for item in sorted(ctx.inputs, key=lambda item: item.order):
            ctx.deps.ledger.add(item.result)
        return None

    @g.step(label="Synthesize report")
    async def synthesize(
        ctx: StepContext[ResearchGraphState, ResearchGraphDeps, Any],
    ) -> FinalReport:
        ctx.state.phase = "synthesis"
        return await ctx.deps.loop._synthesize(
            ctx.deps.job_id,
            ctx.state.objective,
            ctx.deps.ledger,
            ctx.deps.constraints,
            ctx.deps.attachments,
        )

    @g.step(label="Verify report")
    async def verify(
        ctx: StepContext[ResearchGraphState, ResearchGraphDeps, FinalReport],
    ) -> VerificationBundle:
        ctx.state.phase = "verification"
        verify_task_ids: list[UUID] = []
        verification = await ctx.deps.loop._verify(
            ctx.deps.job_id,
            ctx.state.objective,
            ctx.inputs,
            ctx.deps.ledger,
            ctx.deps.constraints,
            ctx.deps.attachments,
            task_ids=verify_task_ids,
        )
        return VerificationBundle(report=ctx.inputs, verification=verification,
                                  task_id=verify_task_ids[0] if verify_task_ids else None)

    @g.step(label="Route verification result")
    async def route_verification(
        ctx: StepContext[ResearchGraphState, ResearchGraphDeps, VerificationBundle],
    ) -> VerificationResearchNeeded | VerificationComplete:
        bundle = ctx.inputs
        if (
            not bundle.verification.needs_research
            or not bundle.verification.followups
            or ctx.state.verification_round >= ctx.state.max_verification_rounds
        ):
            return VerificationComplete(bundle.report, bundle.verification)
        return VerificationResearchNeeded(bundle.verification.followups, parent_task_id=bundle.task_id)

    @g.step(label="Prepare verification deep dives")
    async def prepare_verification_deep_dives(
        ctx: StepContext[
            ResearchGraphState, ResearchGraphDeps, VerificationResearchNeeded
        ],
    ) -> list[DeepDiveWork]:
        ctx.state.phase = "verification_research"
        ctx.state.verification_round += 1
        questions = _questions(ctx.state)
        gaps = ctx.deps.loop._dedupe_gaps(ctx.inputs.followups)
        selected = ctx.deps.loop._select_gaps(gaps, questions)
        return [
            DeepDiveWork(
                order=i,
                gap=gap,
                question=questions[gap.question_id],
                attempt=ctx.state.verification_round,
                parent_task_id=ctx.inputs.parent_task_id,
            )
            for i, gap in enumerate(selected)
            if gap.question_id in questions
        ]

    @g.step(label="Verification deep dive")
    async def verification_deep_dive(
        ctx: StepContext[ResearchGraphState, ResearchGraphDeps, DeepDiveWork],
    ) -> OrderedResearchResult:
        work = ctx.inputs
        result = await ctx.deps.loop._run_gap(
            ctx.deps.job_id,
            work.gap,
            work.question,
            ctx.deps.deep_dive_semaphore,
            ctx.deps.constraints,
            ctx.deps.attachments,
            attempt=work.attempt,
            parent_task_id=work.parent_task_id,
        )
        return OrderedResearchResult(order=work.order, result=result)

    verification_deep_join = g.join(
        reduce_list_append, initial_factory=list[OrderedResearchResult]
    )

    @g.step(label="Record verification evidence")
    async def record_verification_deep_dives(
        ctx: StepContext[ResearchGraphState, ResearchGraphDeps, list[OrderedResearchResult]],
    ) -> None:
        for item in sorted(ctx.inputs, key=lambda item: item.order):
            ctx.deps.ledger.add(item.result)
        return None

    @g.step(label="Finalize research")
    async def finalize(
        ctx: StepContext[ResearchGraphState, ResearchGraphDeps, VerificationComplete],
    ) -> ResearchGraphResult:
        ctx.state.phase = "complete"
        if ctx.state.plan is None:
            raise RuntimeError("cannot finalize without a research plan")
        return ResearchGraphResult(
            plan=ctx.state.plan,
            report=ctx.inputs.report,
            verification=ctx.inputs.verification,
        )

    initial_decision = (
        g.decision(note="Resolve material gaps before synthesis", node_id="initial_gap_decision")
        .branch(
            g.match(InitialResearchNeeded)
            .label("material gaps")
            .to(prepare_initial_deep_dives)
        )
        .branch(
            g.match(ReadyForSynthesis)
            .label("evidence sufficient")
            .to(synthesize)
        )
    )

    verification_decision = (
        g.decision(note="Finish or research verifier follow-ups", node_id="verification_decision")
        .branch(
            g.match(VerificationResearchNeeded)
            .label("research follow-ups")
            .to(prepare_verification_deep_dives)
        )
        .branch(
            g.match(VerificationComplete)
            .label("complete")
            .to(finalize)
        )
    )

    g.add(g.edge_from(g.start_node).to(plan))
    g.add_mapping_edge(plan, scout, downstream_join_id=scout_join.id)
    g.add(
        g.edge_from(scout).to(scout_join),
        g.edge_from(scout_join).to(record_scouts),
        g.edge_from(record_scouts).to(analyze_gaps),
        g.edge_from(analyze_gaps).to(initial_decision),
    )

    g.add_mapping_edge(
        prepare_initial_deep_dives,
        initial_deep_dive,
        downstream_join_id=initial_deep_join.id,
    )
    g.add(
        g.edge_from(initial_deep_dive).to(initial_deep_join),
        g.edge_from(initial_deep_join).to(record_initial_deep_dives),
        g.edge_from(record_initial_deep_dives).to(synthesize),
        g.edge_from(synthesize).to(verify),
        g.edge_from(verify).to(route_verification),
        g.edge_from(route_verification).to(verification_decision),
    )

    g.add_mapping_edge(
        prepare_verification_deep_dives,
        verification_deep_dive,
        downstream_join_id=verification_deep_join.id,
    )
    g.add(
        g.edge_from(verification_deep_dive).to(verification_deep_join),
        g.edge_from(verification_deep_join).to(record_verification_deep_dives),
        g.edge_from(record_verification_deep_dives).to(synthesize),
        g.edge_from(finalize).to(g.end_node),
    )

    return g.build()


_RESEARCH_GRAPH = None


def get_research_graph():
    global _RESEARCH_GRAPH
    if _RESEARCH_GRAPH is None:
        _RESEARCH_GRAPH = build_research_graph()
    return _RESEARCH_GRAPH


def render_research_graph(*, direction: str = "LR") -> str:
    return get_research_graph().render(
        title=f"Research Loop ({RESEARCH_GRAPH_VERSION})", direction=direction
    )

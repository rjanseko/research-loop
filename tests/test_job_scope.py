"""Job-scoped wiring through the real agent runner: task lineage and the shared fetch memo."""
from __future__ import annotations

from contextlib import ExitStack
from typing import Any

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from research_loop import agents
from research_loop.async_orchestrator import AsyncResearchLoop, ResearchConfig
from research_loop.orchestrator import ResearchLoop
from research_loop.policy import ModelPolicy, ModelRoute
from research_loop.repository import InMemoryResearchRepository
from research_loop.schemas import ResearchRole
from research_loop.tools import ResearchToolMode

SOURCE = {"url": "https://example.org/paper", "title": "Paper", "source_type": "paper"}


def _result(confidence: float) -> dict[str, Any]:
    return {"question_id": "q1", "question": "What is measured?", "conclusion": "Measured",
            "claims": [{"id": "c1", "statement": "It is measured", "confidence": confidence,
                        "evidence": [{"source": SOURCE, "excerpt": "measured", "confidence": confidence}]}],
            "confidence": confidence}


def _outputs() -> dict[str, Any]:
    """One output per role; the verifier asks for one follow-up, then passes."""
    verifications = iter([
        {"checks": [], "needs_research": True,
         "followups": [{"question_id": "q1", "reason": "missing_evidence", "followup": "Check again", "severity": 4}]},
        {"checks": []},
    ])
    return {
        "planner": lambda: {"objective": "o", "questions": [{"id": "q1", "question": "What is measured?"}]},
        "scout": lambda: _result(0.3),  # below min_scout_confidence: an initial deep dive follows
        "gap": lambda: {"gaps": []},
        "deep_dive": lambda: _result(0.9),
        "synthesizer": lambda: {"answer": "Measured", "claims": [{"statement": "It is measured", "claim_ids": ["q1/c1"]}]},
        "verifier": lambda: next(verifications),
    }


def _override_agents(stack: ExitStack) -> None:
    outputs = _outputs()
    for name, agent in (("planner", agents.planner_agent), ("scout", agents.scout_agent),
                        ("gap", agents.gap_agent), ("deep_dive", agents.deep_dive_agent),
                        ("synthesizer", agents.synthesizer_agent), ("verifier", agents.verifier_agent)):
        def respond(_messages, info: AgentInfo, _name: str = name) -> ModelResponse:
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, outputs[_name]())])

        stack.enter_context(agent.override(model=FunctionModel(respond)))


def _loop(loop_class: type[AsyncResearchLoop]) -> AsyncResearchLoop:
    route = ModelRoute("test", 5, 5, 50_000)
    return loop_class(
        ModelPolicy("job-scope", {role: route for role in ResearchRole}, planner_question_range=(1, 1)),
        ResearchConfig(tool_mode=ResearchToolMode.NORMALIZED, scholarly_cache_mode="off", max_verification_rounds=1),
        repository=InMemoryResearchRepository(),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("loop_class", [ResearchLoop, AsyncResearchLoop])
async def test_deep_dives_record_the_task_that_asked_for_them(loop_class) -> None:
    loop = _loop(loop_class)
    with ExitStack() as stack:
        _override_agents(stack)
        await loop.run("objective")
    tasks = list(loop.repository.tasks.values())

    def only(role: ResearchRole, attempt: int = 0) -> dict[str, Any]:
        (task,) = [t for t in tasks if t["role"] is role and t["attempt"] == attempt]
        return task

    first_verifier = min((t for t in tasks if t["role"] is ResearchRole.VERIFIER), key=lambda t: t["started_at"])
    assert only(ResearchRole.DEEP_DIVE, attempt=0)["parent_task_id"] == only(ResearchRole.GAP_ANALYST)["id"]
    assert only(ResearchRole.DEEP_DIVE, attempt=1)["parent_task_id"] == first_verifier["id"]
    assert only(ResearchRole.SCOUT)["parent_task_id"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("loop_class", [ResearchLoop, AsyncResearchLoop])
async def test_research_agents_in_one_job_share_one_fetch_memo(monkeypatch, loop_class) -> None:
    import research_loop.async_orchestrator as orchestrator

    memos: list[Any] = []

    def recording(cls):
        class Recording(cls):
            def __init__(self, **kwargs: Any) -> None:
                super().__init__(**kwargs)
                memos.append(self.memo)
        return Recording

    monkeypatch.setattr(orchestrator, "WebAcquisition", recording(orchestrator.WebAcquisition))
    monkeypatch.setattr(orchestrator, "ScholarClient", recording(orchestrator.ScholarClient))
    loop = _loop(loop_class)
    with ExitStack() as stack:
        _override_agents(stack)
        await loop.run("first objective")
    first_job = list(memos)
    memos.clear()
    with ExitStack() as stack:
        _override_agents(stack)
        await loop.run("second objective")

    # The scout and both deep dives each build a web fetcher and a scholar client.
    assert len(first_job) == 6
    assert all(memo is first_job[0] for memo in first_job)
    assert all(memo is memos[0] for memo in memos) and memos[0] is not first_job[0]
    assert loop._fetch_memos == {}

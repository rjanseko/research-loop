"""scripts/prompt_trial.py: both prompts reach the model through the orchestrator's own role calls."""
from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from research_loop.agents import INSTRUCTIONS, planner_agent
from research_loop.policy import ModelPolicy, ModelRoute
from research_loop.schemas import (
    ClaimCheck,
    FinalReport,
    ReportClaim,
    ResearchRole,
    VerificationReport,
)
from research_loop.settings import ResearchSettings

_SPEC = importlib.util.spec_from_file_location("prompt_trial", Path(__file__).parents[1] / "scripts" / "prompt_trial.py")
prompt_trial = importlib.util.module_from_spec(_SPEC)
sys.modules["prompt_trial"] = prompt_trial
_SPEC.loader.exec_module(prompt_trial)


def test_candidates_add_to_the_current_instructions() -> None:
    for role, candidate in prompt_trial.CANDIDATES.items():
        assert candidate.startswith(INSTRUCTIONS[role]) and len(candidate) > len(INSTRUCTIONS[role])


def test_each_case_is_planned_with_both_prompts() -> None:
    seen: list[str] = []

    def respond(messages, info: AgentInfo) -> ModelResponse:
        instructions = next(m.instructions for m in messages if isinstance(m, ModelRequest))
        seen.append(instructions)
        # The candidate gets one question per subject; the current prompt, one for all.
        count = 3 if prompt_trial.PLANNER_ADDITION.strip() in instructions else 1
        plan = {"objective": "o", "questions": [{"id": f"Q{i}", "question": f"q{i}", "priority": 3}
                                                for i in range(1, count + 1)]}
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, plan)])

    route = ModelRoute("test", 2, 0, 100_000)
    policy = ModelPolicy("p", {role: route for role in ResearchRole})
    loop = prompt_trial._loop(policy, ResearchSettings.from_env({}))
    cases = [SimpleNamespace(case_id=f"case{i}", blocked_urls=[], benchmark_id=None,
                             render_objective=lambda: "objective") for i in range(2)]
    with planner_agent.override(model=FunctionModel(respond)):
        rows = asyncio.run(prompt_trial.trial_plans(loop, prompt_trial.TrialBudget(1.0), cases,
                                                    prompt_trial.CANDIDATES["planner"]))

    assert sorted((row["case_id"], row["variant"], len(row["questions"])) for row in rows) == [
        ("case0", "candidate", 3), ("case0", "current", 1), ("case1", "candidate", 3), ("case1", "current", 1)]
    assert sorted(seen) == sorted([INSTRUCTIONS["planner"]] * 2 + [prompt_trial.CANDIDATES["planner"]] * 2)
    assert "case0" in prompt_trial.render_plans(rows)


def test_synthesis_rows_count_the_verifiers_findings() -> None:
    report = FinalReport(answer="a", claims=[ReportClaim(statement="s1"), ReportClaim(statement="s2")])
    verification = VerificationReport(checks=[
        ClaimCheck(statement="s1", supported=False, severity="major", explanation="e"),
        ClaimCheck(statement="s2", supported=True, severity="none", explanation="e")])
    counts = prompt_trial._counts(verification, report)
    assert counts == {"statements": 2, "checked": 2, "unsupported": 1, "major": 1, "follow_ups": 0}
    row = {"job_id": "3bccbe6d-x", "variant": "candidate", "synthesis_usd": 0.5, "verification_usd": 0.2, "requests": 2,
           "synthesis_requests": 1, "error": None, **counts}
    table = prompt_trial.render_synthesis([row], {"3bccbe6d-x": counts})
    assert "candidate" in table and "stored" in table


def test_a_failed_unit_is_recorded_and_the_others_finish() -> None:
    def respond(messages, info: AgentInfo) -> ModelResponse:
        if "refuse me" in str(messages[0].parts[0].content):
            raise RuntimeError("refused")
        plan = {"objective": "o", "questions": [{"id": "Q1", "question": "q", "priority": 3}]}
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, plan)])

    route = ModelRoute("test", 2, 0, 100_000)
    loop = prompt_trial._loop(ModelPolicy("p", {role: route for role in ResearchRole}), ResearchSettings.from_env({}))
    cases = [SimpleNamespace(case_id=case_id, blocked_urls=[], benchmark_id=None, render_objective=lambda o=objective: o)
             for case_id, objective in (("ok", "fine"), ("bad", "refuse me"))]
    with planner_agent.override(model=FunctionModel(respond)):
        rows = asyncio.run(prompt_trial.trial_plans(loop, prompt_trial.TrialBudget(1.0), cases,
                                                    prompt_trial.CANDIDATES["planner"]))
    assert sorted((row["case_id"], row["error"], len(row["questions"])) for row in rows) == [
        ("bad", "RuntimeError", 0), ("bad", "RuntimeError", 0), ("ok", None, 1), ("ok", None, 1)]
    assert "RuntimeError" in prompt_trial.render_plans(rows)


def test_no_unit_starts_once_the_cap_is_spent() -> None:
    calls: list[int] = []

    def respond(messages, info: AgentInfo) -> ModelResponse:
        calls.append(1)
        plan = {"objective": "o", "questions": [{"id": "Q1", "question": "q", "priority": 3}]}
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, plan)])

    route = ModelRoute("test", 2, 0, 100_000)
    loop = prompt_trial._loop(ModelPolicy("p", {role: route for role in ResearchRole}), ResearchSettings.from_env({}))
    cases = [SimpleNamespace(case_id=f"case{i}", blocked_urls=[], benchmark_id=None, render_objective=lambda: "o")
             for i in range(3)]
    budget = prompt_trial.TrialBudget(0.01, concurrency=1)
    budget.spent = 0.01  # as if earlier units had spent the cap
    with planner_agent.override(model=FunctionModel(respond)):
        rows = asyncio.run(prompt_trial.trial_plans(loop, budget, cases, prompt_trial.CANDIDATES["planner"]))
    assert not calls
    assert {row["error"] for row in rows} == {prompt_trial.CAP_REACHED}


def test_a_unit_records_its_requests() -> None:
    def respond(messages, info: AgentInfo) -> ModelResponse:
        plan = {"objective": "o", "questions": [{"id": "Q1", "question": "q", "priority": 3}]}
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, plan)])

    route = ModelRoute("test", 2, 0, 100_000)
    loop = prompt_trial._loop(ModelPolicy("p", {role: route for role in ResearchRole}), ResearchSettings.from_env({}))
    cases = [SimpleNamespace(case_id="c", blocked_urls=[], benchmark_id=None, render_objective=lambda: "o")]
    with planner_agent.override(model=FunctionModel(respond)):
        rows = asyncio.run(prompt_trial.trial_plans(loop, prompt_trial.TrialBudget(1.0), cases,
                                                    prompt_trial.CANDIDATES["planner"]))
    assert [row["requests"] for row in rows] == [1, 1]


def test_each_unit_is_a_job_marked_as_the_trial_and_arm_that_made_it() -> None:
    def respond(messages, info: AgentInfo) -> ModelResponse:
        if "refuse me" in str(messages[0].parts[0].content):
            raise RuntimeError("refused")
        plan = {"objective": "o", "questions": [{"id": "Q1", "question": "q", "priority": 3}]}
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, plan)])

    route = ModelRoute("test", 2, 0, 100_000)
    loop = prompt_trial._loop(ModelPolicy("p", {role: route for role in ResearchRole}), ResearchSettings.from_env({}))
    cases = [SimpleNamespace(case_id=case_id, blocked_urls=[], benchmark_id=None, render_objective=lambda o=objective: o)
             for case_id, objective in (("ok", "fine"), ("bad", "refuse me"))]
    with planner_agent.override(model=FunctionModel(respond)):
        rows = asyncio.run(prompt_trial.trial_plans(loop, prompt_trial.TrialBudget(1.0), cases,
                                                    prompt_trial.CANDIDATES["planner"]))
    jobs = loop.repository.jobs
    assert {row["job_id"] for row in rows} == set(jobs)
    for row in rows:
        job = jobs[row["job_id"]]
        assert job["config"]["trial"] == {"name": "prompt", "mode": "planner", "variant": row["variant"],
                                          "case_id": row["case_id"]}
        assert job["config"]["orchestrator"]["kind"] == "trial"
        assert job["status"] == ("failed" if row["case_id"] == "bad" else "succeeded")

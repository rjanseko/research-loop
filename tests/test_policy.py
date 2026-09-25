import asyncio
from dataclasses import replace
from uuid import uuid4

import pytest
from pydantic_ai import Agent
from pydantic_ai.exceptions import ContentFilterError, ModelAPIError
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from research_loop.async_orchestrator import AsyncResearchLoop
from research_loop.policy import ModelPolicy, ModelRoute, get_policy, retry_token_budget
from research_loop.repository import InMemoryResearchRepository
from research_loop.schemas import ResearchQuestion, ResearchRole
from research_loop.settings import ResearchSettings


def test_quality_policy_routes_easy_question_to_cheap_scout():
    policy = get_policy("quality")
    question = ResearchQuestion(id="q1", question="easy", expected_difficulty="low")
    assert policy.scout_for(question).model == policy.cheap_scout.model


def test_primary_source_question_uses_main_scout():
    policy = get_policy("quality")
    question = ResearchQuestion(
        id="q1",
        question="harder",
        expected_difficulty="low",
        requires_primary_sources=True,
    )
    assert policy.scout_for(question).model == policy.for_role(ResearchRole.SCOUT).model


def test_structured_output_roles_raise_default_output_cap():
    # Anthropic sends max_tokens=4096 unless set, shared by adaptive thinking and the output.
    for name in ("quality", "breadth", "glm-heavy", "value"):
        policy = get_policy(name, model_overrides={"RESEARCH_SYNTH_MODEL": "openai:override"})
        for role in (ResearchRole.PLANNER, ResearchRole.SYNTHESIZER):
            assert policy.for_role(role).model_settings()["max_tokens"] >= 16_000


def test_policy_uses_current_nonempty_override(monkeypatch) -> None:
    monkeypatch.setenv("RESEARCH_SCOUT_MODEL", "openai:configured-scout")
    assert get_policy("quality").for_role(ResearchRole.SCOUT).model == "openai:configured-scout"
    monkeypatch.setenv("RESEARCH_SCOUT_MODEL", "")
    assert get_policy("quality").for_role(ResearchRole.SCOUT).model != ""


def test_policy_applies_typed_settings_override() -> None:
    settings = ResearchSettings.from_env({"RESEARCH_SCOUT_MODEL": "openai:typed-scout"})
    policy = get_policy("quality", model_overrides=settings.model_overrides)
    assert policy.for_role(ResearchRole.SCOUT).model == "openai:typed-scout"


def test_env_example_lists_every_default_model() -> None:
    import re
    from pathlib import Path

    from research_loop.policy import DEFAULT_MODELS
    from research_loop.settings import MODEL_OVERRIDE_ENV

    example = (Path(__file__).parents[1] / ".env.example").read_text()
    listed = dict(re.findall(r"^(RESEARCH_\w+_MODEL)=(.*)$", example, re.MULTILINE))
    assert listed == DEFAULT_MODELS
    assert set(DEFAULT_MODELS) == set(MODEL_OVERRIDE_ENV)


def test_routes_fall_back_to_default_models(monkeypatch) -> None:
    from research_loop.policy import DEFAULT_MODELS

    for name in DEFAULT_MODELS:
        monkeypatch.delenv(name, raising=False)  # a test may have loaded the local .env
    quality, breadth, glm, value = (get_policy(name) for name in ("quality", "breadth", "glm-heavy", "value"))
    used = {
        "RESEARCH_PLANNER_MODEL": quality.for_role(ResearchRole.PLANNER).model,
        "RESEARCH_SCOUT_MODEL": quality.for_role(ResearchRole.SCOUT).model,
        "RESEARCH_CHEAP_SCOUT_MODEL": quality.cheap_scout.model,
        "RESEARCH_GAP_MODEL": quality.for_role(ResearchRole.GAP_ANALYST).model,
        "RESEARCH_DEEP_MODEL": quality.for_role(ResearchRole.DEEP_DIVE).model,
        "RESEARCH_SYNTH_MODEL": quality.for_role(ResearchRole.SYNTHESIZER).model,
        "RESEARCH_VERIFY_MODEL": quality.for_role(ResearchRole.VERIFIER).model,
        "RESEARCH_MULTIMODAL_MODEL": quality.multimodal_scout.model,
        "RESEARCH_ALT_DEEP_MODEL": quality.alternate_deep_dive.model,
        "RESEARCH_BREADTH_SCOUT_MODEL": breadth.for_role(ResearchRole.SCOUT).model,
        "RESEARCH_GLM_GAP_MODEL": glm.for_role(ResearchRole.GAP_ANALYST).model,
        "RESEARCH_GLM_CHEAP_MODEL": glm.cheap_scout.model,
        "RESEARCH_VALUE_PLANNER_MODEL": value.for_role(ResearchRole.PLANNER).model,
        "RESEARCH_VALUE_GAP_MODEL": value.for_role(ResearchRole.GAP_ANALYST).model,
        "RESEARCH_VALUE_DEEP_MODEL": value.for_role(ResearchRole.DEEP_DIVE).model,
        "RESEARCH_VALUE_SYNTH_MODEL": value.for_role(ResearchRole.SYNTHESIZER).model,
        "RESEARCH_VALUE_VERIFY_MODEL": value.for_role(ResearchRole.VERIFIER).model,
    }
    assert used == DEFAULT_MODELS


def test_retry_budget_refuses_the_rerun_finishing_prompts_and_allows_the_first_pilot() -> None:
    # Character counts are the stored prompts (PROMPT_SIZES.md), not their text.
    synthesis = ModelRoute(
        "anthropic:claude-opus-5", 8, 4, 120_000, settings={"max_tokens": 32_000},
    )
    verifier = ModelRoute("openai:gpt-5.6-sol", 8, 8, 100_000)
    gap = ModelRoute("openai:gpt-5.6-sol", 6, 4, 70_000)
    assert retry_token_budget("x" * 113_964, synthesis, ResearchRole.SYNTHESIZER) > 120_000
    assert retry_token_budget("x" * 99_254, synthesis, ResearchRole.SYNTHESIZER) <= 120_000
    assert retry_token_budget("x" * 142_376, verifier, ResearchRole.VERIFIER) > 100_000
    assert retry_token_budget("x" * 127_067, verifier, ResearchRole.VERIFIER) <= 100_000
    assert retry_token_budget("x" * 75_426, gap, ResearchRole.GAP_ANALYST) <= 70_000
    # The trimmed projection of the same rerun (PROMPT_SIZES.md) fits one retry.
    assert retry_token_budget("x" * 94_390, synthesis, ResearchRole.SYNTHESIZER) <= 120_000
    assert retry_token_budget("x" * 123_055, verifier, ResearchRole.VERIFIER) <= 100_000
    assert retry_token_budget("x" * 63_475, gap, ResearchRole.GAP_ANALYST) <= 70_000
    # An unknown provider is counted at the Anthropic ratio, which charges more tokens.
    unknown = ModelRoute("test", 8, 4, 120_000)
    assert retry_token_budget("x" * 113_964, unknown, ResearchRole.SYNTHESIZER) == (
        retry_token_budget("x" * 113_964, synthesis, ResearchRole.SYNTHESIZER)
    )
    # max_tokens below the allowance is the output the retry has to cover.
    capped = ModelRoute("anthropic:claude-opus-5", 8, 4, 120_000, settings={"max_tokens": 4_000})
    assert retry_token_budget("x" * 113_964, capped, ResearchRole.SYNTHESIZER) <= 120_000
    # An explicit allowance replaces the role table. Long-horizon synthesis uses its output cap.
    spec = ModelRoute("anthropic:claude-opus-5", 3, 1, 400_000, settings={"max_tokens": 48_000})
    assert retry_token_budget(
        "x" * 360_000, spec, ResearchRole.SYNTHESIZER, output_allowance=48_000,
    ) > 400_000
    assert retry_token_budget(
        "x" * 360_000, spec, ResearchRole.SYNTHESIZER, output_allowance=36_000,
    ) <= 400_000


def test_quality_finishing_routes_fit_a_retry_of_prompts_nearly_twice_p01() -> None:
    # The trimmed p01 rerun prompts (PROMPT_SIZES.md) at 1.8x: a deeper question still gets its retry.
    for name in ("quality", "value"):
        policy = get_policy(name)
        for role, chars in ((ResearchRole.SYNTHESIZER, 94_390), (ResearchRole.VERIFIER, 123_055)):
            route = policy.for_role(role)
            assert retry_token_budget("x" * int(chars * 1.8), route, role) <= route.total_tokens_limit


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"model": " "}, "needs a model"),
        ({"max_requests": 0}, "max_requests"),
        ({"max_tool_calls": -1}, "max_tool_calls"),
        ({"max_misses": -1}, "max_misses"),
        ({"total_tokens_limit": 0}, "total_tokens_limit"),
        ({"total_tokens_limit": 1.5}, "total_tokens_limit"),
        ({"cost_limit": 0.0}, "cost_limit"),
        ({"cost_limit": float("nan")}, "cost_limit"),
        ({"cost_limit": float("inf")}, "cost_limit"),
        ({"thinking": "extreme"}, "thinking"),
        ({"settings": {"max_tokens": 0}}, "max_tokens"),
    ],
)
def test_route_rejects_limits_no_call_could_run_under(fields, message) -> None:
    route = ModelRoute("test", 5, 5, 10_000)
    with pytest.raises(ValueError, match=message):
        replace(route, **fields)


def test_tool_free_route_and_every_preset_are_valid() -> None:
    assert ModelRoute("test", 1, 0, 1).max_tool_calls == 0
    for name in ("quality", "breadth", "glm-heavy", "value", "synthetic"):
        policy = get_policy(name)
        policy.validate()
        for route in policy.routes.values():
            route.salvage()


def _policy(**kwargs) -> ModelPolicy:
    route = ModelRoute("test", 5, 5, 10_000)
    return ModelPolicy("p", {role: route for role in ResearchRole}, **kwargs)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"planner_question_range": (0, 3)}, "planner_question_range"),
        ({"planner_question_range": (5, 4)}, "planner_question_range"),
        ({"job_cost_limit": 0.0}, "job_cost_limit"),
        ({"job_cost_limit": float("nan")}, "job_cost_limit"),
        ({"job_reserve_usd": -1.0}, "job_reserve_usd"),
        ({"job_cost_limit": 2.0, "job_reserve_usd": 2.0}, "below job_cost_limit"),
    ],
)
def test_loop_refuses_a_policy_no_job_could_run_under(kwargs, message) -> None:
    with pytest.raises(ValueError, match=message):
        AsyncResearchLoop(_policy(**kwargs))


def test_policy_needs_a_route_for_every_role() -> None:
    policy = _policy()
    del policy.routes[ResearchRole.VERIFIER]
    with pytest.raises(ValueError, match="no route for verifier"):
        policy.validate()


@pytest.mark.asyncio
async def test_job_creation_rechecks_a_policy_changed_after_construction() -> None:
    loop = AsyncResearchLoop(_policy(job_cost_limit=2.0))
    loop.policy.job_reserve_usd = 3.0
    with pytest.raises(ValueError, match="job_reserve_usd"):
        await loop.run("objective")


def test_prompt_cache_asks_each_provider_in_its_own_terms() -> None:
    anthropic = ModelRoute("anthropic:claude-opus-5-5", 5, 5, 10_000, prompt_cache=True)
    openai = replace(anthropic, model="openai:gpt-6-sol")
    zai = replace(anthropic, model="zai:glm-5.3")
    assert anthropic.model_settings() == {"anthropic_cache": True}
    assert openai.model_settings() == {"openai_prompt_cache_key": "research-loop:openai:gpt-6-sol"}
    assert zai.model_settings() is None
    # A setting the route gives explicitly wins, and salvage keeps caching.
    assert replace(anthropic, settings={"anthropic_cache": "1h"}).model_settings() == {"anthropic_cache": "1h"}
    assert anthropic.salvage().prompt_cache


def test_prompt_cache_is_in_the_snapshot_only_when_set() -> None:
    # Presets without caching keep the configuration fingerprint of earlier manifests.
    assert "prompt_cache" not in ModelRoute("test", 5, 5, 10_000).snapshot()
    assert ModelRoute("test", 5, 5, 10_000, prompt_cache=True).snapshot()["prompt_cache"] is True
    quality = get_policy("quality").snapshot()
    assert all("prompt_cache" not in route for route in quality["routes"].values())


def test_value_preset_caches_every_route_and_thinks_less_on_opus(monkeypatch) -> None:
    from research_loop.policy import DEFAULT_MODELS

    for name in DEFAULT_MODELS:
        monkeypatch.delenv(name, raising=False)
    value, quality = get_policy("value"), get_policy("quality")
    routes = [*value.routes.values(), value.cheap_scout, value.multimodal_scout, value.alternate_deep_dive]
    assert all(route.prompt_cache for route in routes)
    # Opus 5.5 synthesizes at medium; the planner is on gpt-6-sol at quality's effort.
    assert (value.for_role(ResearchRole.SYNTHESIZER).model, value.for_role(ResearchRole.SYNTHESIZER).thinking) == (
        "anthropic:claude-opus-5-5", "medium")
    assert (value.for_role(ResearchRole.PLANNER).model, value.for_role(ResearchRole.PLANNER).thinking) == (
        "openai:gpt-6-sol", "high")
    for role in ResearchRole:
        # Same limits as quality, so a comparison changes models, effort, and caching only.
        mine, theirs = value.for_role(role), quality.for_role(role)
        assert (mine.max_requests, mine.max_tool_calls, mine.total_tokens_limit, mine.cost_limit) == (
            theirs.max_requests, theirs.max_tool_calls, theirs.total_tokens_limit, theirs.cost_limit)


def test_value_overrides_leave_quality_alone_and_the_reverse() -> None:
    value = get_policy("value", model_overrides={
        "RESEARCH_DEEP_MODEL": "openai:quality-deep",
        "RESEARCH_VALUE_SYNTH_MODEL": "zai:glm-5.3",
        "RESEARCH_GLM_CHEAP_MODEL": "zai:cheap",
    })
    assert value.for_role(ResearchRole.DEEP_DIVE).model != "openai:quality-deep"
    assert value.for_role(ResearchRole.SYNTHESIZER).model == "zai:glm-5.3"
    assert value.for_role(ResearchRole.SYNTHESIZER).prompt_cache
    # Off Anthropic, the planner and synthesizer go back to quality's effort.
    assert value.for_role(ResearchRole.SYNTHESIZER).thinking == "high"
    planner = get_policy("value", model_overrides={"RESEARCH_VALUE_PLANNER_MODEL": "anthropic:claude-opus-5-5"})
    assert planner.for_role(ResearchRole.PLANNER).thinking == "medium"
    assert value.cheap_scout.model == "zai:cheap"
    quality = get_policy("quality", model_overrides={"RESEARCH_VALUE_SYNTH_MODEL": "zai:glm-5.3"})
    assert quality.for_role(ResearchRole.SYNTHESIZER).model != "zai:glm-5.3"


def test_value_effort_follows_models_set_in_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("RESEARCH_VALUE_SYNTH_MODEL", "zai:glm-5.3")
    monkeypatch.setenv("RESEARCH_VALUE_PLANNER_MODEL", "anthropic:claude-opus-5-5")
    value = get_policy("value")
    assert value.for_role(ResearchRole.SYNTHESIZER).thinking == "high"
    assert value.for_role(ResearchRole.PLANNER).thinking == "medium"


def _refusing_then_answering(calls: list[int], *, provider_error: bool = False):
    """A model whose first call is refused by the content filter, or fails at the provider, and whose later ones answer."""
    def respond(messages, info):
        calls.append(1)
        if len(calls) == 1:
            if provider_error:
                raise ModelAPIError("primary", "Request timed out.")
            return ModelResponse(parts=[], finish_reason="content_filter")
        return ModelResponse(parts=[TextPart("answer")])
    return FunctionModel(respond)


async def _run_refused(route: ModelRoute, *, provider_error: bool = False, notes: list[str] | None = None):
    loop = AsyncResearchLoop(ModelPolicy("p", {role: route for role in ResearchRole}),
                             repository=InMemoryResearchRepository())
    agent = Agent(output_type=str)
    job_id = uuid4()
    with agent.override(model=_refusing_then_answering([], provider_error=provider_error)):
        output = await loop._run_agent(job_id=job_id, agent=agent, role=ResearchRole.SYNTHESIZER, route=route,
                                       prompt="plan")
    if notes is not None:
        notes += loop._notes.get(job_id, [])
    return output, list(loop.repository.tasks.values())


def test_a_refused_call_runs_again_on_the_routes_refusal_fallback() -> None:
    route = ModelRoute("test:primary", 2, 0, 10_000, refusal_fallback="test:fallback")
    output, tasks = asyncio.run(_run_refused(route))
    assert output == "answer"
    assert [(t["model_id"], t["status"]) for t in tasks] == [("test:primary", "failed"), ("test:fallback", "succeeded")]
    assert tasks[0]["error"]["type"] == "ContentFilterError"
    assert "refusal_fallback" not in tasks[1]["effective_config"]


def test_a_refusal_without_a_fallback_fails_the_call() -> None:
    with pytest.raises(ContentFilterError):
        asyncio.run(_run_refused(ModelRoute("test:primary", 2, 0, 10_000)))


def test_a_provider_error_runs_again_on_the_fallback_and_leaves_a_note() -> None:
    # As pilot 8's synthesis did, timing out at Z.ai: the job keeps its research and gets a report.
    route = ModelRoute("test:primary", 2, 0, 10_000, refusal_fallback="test:fallback")
    notes: list[str] = []
    output, tasks = asyncio.run(_run_refused(route, provider_error=True, notes=notes))
    assert output == "answer"
    assert [(t["model_id"], t["status"]) for t in tasks] == [("test:primary", "failed"), ("test:fallback", "succeeded")]
    assert tasks[0]["error"]["type"] == "ModelAPIError"
    assert notes == [("the synthesizer on test:primary ended on a provider error (ModelAPIError) "
                      "and ran again on test:fallback")]


def test_a_provider_error_without_a_fallback_fails_the_call() -> None:
    with pytest.raises(ModelAPIError):
        asyncio.run(_run_refused(ModelRoute("test:primary", 2, 0, 10_000), provider_error=True))


def test_anthropic_planners_and_synthesizers_fall_back_to_the_gap_model() -> None:
    for name in ("quality", "value"):
        policy = get_policy(name)
        for role in (ResearchRole.PLANNER, ResearchRole.SYNTHESIZER):
            route = policy.routes[role]
            assert route.refusal_fallback == policy.routes[ResearchRole.GAP_ANALYST].model
            assert route.snapshot()["refusal_fallback"] == route.refusal_fallback
    with pytest.raises(ValueError, match="refusal_fallback"):
        ModelRoute("m", 1, 0, 1, refusal_fallback=" ")


def test_every_flash_route_thinks_at_max_and_a_route_moved_off_flash_keeps_its_preset_effort() -> None:
    for name in ("glm-heavy", "value"):
        assert get_policy(name).cheap_scout.thinking == "xhigh"
    flash = get_policy("quality", model_overrides={"RESEARCH_SCOUT_MODEL": "zai:glm-5.3-flash"})
    assert flash.for_role(ResearchRole.SCOUT).thinking == "xhigh"
    assert get_policy("quality").for_role(ResearchRole.SCOUT).thinking == "high"
    moved = get_policy("value", model_overrides={"RESEARCH_GLM_CHEAP_MODEL": "zai:glm-5.3"})
    assert moved.cheap_scout.thinking == "low"


def test_the_default_preset_plans_and_synthesizes_on_opus_5_5_at_medium() -> None:
    for name in ("quality", "breadth", "glm-heavy"):
        policy = get_policy(name)
        for role in (ResearchRole.PLANNER, ResearchRole.SYNTHESIZER):
            assert (policy.for_role(role).model, policy.for_role(role).thinking) == ("anthropic:claude-opus-5-5", "medium")
    # Moved off Opus 5.5, a route keeps the preset's effort.
    opus_5 = get_policy("quality", model_overrides={"RESEARCH_SYNTH_MODEL": "anthropic:claude-opus-5"})
    assert opus_5.for_role(ResearchRole.SYNTHESIZER).thinking == "high"


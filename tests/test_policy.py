from research_loop.policy import ModelRoute, get_policy, retry_token_budget
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
    for name in ("quality", "breadth", "glm-heavy"):
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
    listed = dict(re.findall(r"^(RESEARCH_\w+_MODEL)=(.*)$", example, re.M))
    assert listed == DEFAULT_MODELS
    assert set(DEFAULT_MODELS) == set(MODEL_OVERRIDE_ENV)


def test_routes_fall_back_to_default_models(monkeypatch) -> None:
    from research_loop.policy import DEFAULT_MODELS

    for name in DEFAULT_MODELS:
        monkeypatch.delenv(name, raising=False)  # a test may have loaded the local .env
    quality, breadth, glm = (get_policy(name) for name in ("quality", "breadth", "glm-heavy"))
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
    # An explicit allowance replaces the role table. Campaign synthesis uses its output cap.
    campaign = ModelRoute("anthropic:claude-opus-5", 3, 1, 400_000, settings={"max_tokens": 48_000})
    assert retry_token_budget(
        "x" * 360_000, campaign, ResearchRole.SYNTHESIZER, output_allowance=48_000,
    ) > 400_000
    assert retry_token_budget(
        "x" * 360_000, campaign, ResearchRole.SYNTHESIZER, output_allowance=36_000,
    ) <= 400_000


def test_quality_finishing_routes_fit_a_retry_of_prompts_nearly_twice_p01() -> None:
    # The trimmed p01 rerun prompts (PROMPT_SIZES.md) at 1.8x: a deeper question still gets its retry.
    policy = get_policy("quality")
    for role, chars in ((ResearchRole.SYNTHESIZER, 94_390), (ResearchRole.VERIFIER, 123_055)):
        route = policy.for_role(role)
        assert retry_token_budget("x" * int(chars * 1.8), route, role) <= route.total_tokens_limit

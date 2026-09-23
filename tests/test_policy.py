from research_loop.policy import get_policy
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
    settings = ResearchSettings.from_env({"RESEARCH_SCOUT_MODEL": "openrouter:openai/typed-scout"})
    policy = get_policy("quality", model_overrides=settings.model_overrides)
    assert policy.for_role(ResearchRole.SCOUT).model == "openrouter:openai/typed-scout"


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

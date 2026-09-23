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
    settings = ResearchSettings.from_env({"RESEARCH_SCOUT_MODEL": "openai:typed-scout"})
    policy = get_policy("quality", model_overrides=settings.model_overrides)
    assert policy.for_role(ResearchRole.SCOUT).model == "openai:typed-scout"

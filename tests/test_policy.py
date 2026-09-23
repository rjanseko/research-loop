from research_loop.policy import get_policy
from research_loop.schemas import ResearchQuestion, ResearchRole


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

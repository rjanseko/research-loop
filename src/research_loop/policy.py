from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Literal

from .schemas import ResearchQuestion, ResearchRole

ThinkingEffort = Literal["minimal", "low", "medium", "high", "xhigh"] | bool | None


@dataclass(frozen=True)
class ModelRoute:
    model: str
    max_requests: int
    max_tool_calls: int
    total_tokens_limit: int
    cost_limit: float | None = None
    thinking: ThinkingEffort = None
    settings: dict[str, Any] = field(default_factory=dict)

    def model_settings(self) -> dict[str, Any] | None:
        result = dict(self.settings)
        if self.thinking is not None:
            result["thinking"] = self.thinking
        return result or None

    def snapshot(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "max_requests": self.max_requests,
            "max_tool_calls": self.max_tool_calls,
            "total_tokens_limit": self.total_tokens_limit,
            "cost_limit": self.cost_limit,
            "thinking": self.thinking,
            "settings": self.settings,
        }


class ModelPolicy:
    """Config-driven role routing; orchestration never branches on vendor names."""

    def __init__(
        self,
        name: str,
        routes: dict[ResearchRole, ModelRoute],
        *,
        cheap_scout: ModelRoute | None = None,
        multimodal_scout: ModelRoute | None = None,
        alternate_deep_dive: ModelRoute | None = None,
        planner_question_range: tuple[int, int] = (6, 10),
    ) -> None:
        self.name = name
        self.routes = routes
        self.cheap_scout = cheap_scout
        self.multimodal_scout = multimodal_scout
        self.alternate_deep_dive = alternate_deep_dive
        self.planner_question_range = planner_question_range

    def for_role(self, role: ResearchRole) -> ModelRoute:
        return self.routes[role]

    def scout_for(self, question: ResearchQuestion) -> ModelRoute:
        if question.requires_multimodal and self.multimodal_scout:
            return self.multimodal_scout
        if (
            question.expected_difficulty == "low"
            and not question.requires_primary_sources
            and self.cheap_scout
        ):
            return self.cheap_scout
        return self.routes[ResearchRole.SCOUT]

    def escalation_for(self, question: ResearchQuestion, *, attempt: int = 0) -> ModelRoute:
        if attempt > 0 and self.alternate_deep_dive:
            return self.alternate_deep_dive
        return self.routes[ResearchRole.DEEP_DIVE]

    def snapshot(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "routes": {role.value: route.snapshot() for role, route in self.routes.items()},
            "cheap_scout": self.cheap_scout.snapshot() if self.cheap_scout else None,
            "multimodal_scout": self.multimodal_scout.snapshot() if self.multimodal_scout else None,
            "alternate_deep_dive": (
                self.alternate_deep_dive.snapshot() if self.alternate_deep_dive else None
            ),
            "planner_question_range": list(self.planner_question_range),
        }


def _env(name: str, default: str) -> str:
    return os.getenv(name, default)


def _quality_policy() -> ModelPolicy:
    return ModelPolicy(
        "quality",
        {
            ResearchRole.PLANNER: ModelRoute(
                _env("RESEARCH_PLANNER_MODEL", "anthropic:claude-opus-5"),
                6, 4, 70_000, 2.50, "high",
            ),
            ResearchRole.SCOUT: ModelRoute(
                _env("RESEARCH_SCOUT_MODEL", "zai:glm-5.3"),
                12, 24, 100_000, 0.80, "high",
            ),
            ResearchRole.GAP_ANALYST: ModelRoute(
                _env("RESEARCH_GAP_MODEL", "openai:gpt-5.6-sol"),
                6, 4, 70_000, 1.25, "medium",
            ),
            ResearchRole.DEEP_DIVE: ModelRoute(
                _env("RESEARCH_DEEP_MODEL", "openai:gpt-6-astra"),
                20, 40, 180_000, 5.00, "high",
            ),
            ResearchRole.SYNTHESIZER: ModelRoute(
                _env("RESEARCH_SYNTH_MODEL", "anthropic:claude-opus-5"),
                8, 4, 120_000, 3.50, "high",
            ),
            ResearchRole.VERIFIER: ModelRoute(
                _env("RESEARCH_VERIFY_MODEL", "openai:gpt-5.6-sol"),
                8, 8, 100_000, 2.50, "high",
            ),
        },
        cheap_scout=ModelRoute(
            _env("RESEARCH_CHEAP_SCOUT_MODEL", "openai:gpt-5.6-luna"),
            10, 20, 80_000, 0.25, "low",
        ),
        multimodal_scout=ModelRoute(
            _env("RESEARCH_MULTIMODAL_MODEL", "google:gemini-3.8-flash"),
            12, 20, 100_000, 0.75, "medium",
        ),
        alternate_deep_dive=ModelRoute(
            _env("RESEARCH_ALT_DEEP_MODEL", "xai:grok-4.5-latest"),
            20, 40, 180_000, 5.00, "high",
        ),
        planner_question_range=(6, 10),
    )


def _breadth_policy() -> ModelPolicy:
    p = _quality_policy()
    routes = dict(p.routes)
    routes[ResearchRole.SCOUT] = ModelRoute(
        _env("RESEARCH_BREADTH_SCOUT_MODEL", "openai:gpt-5.6-luna"),
        10, 20, 80_000, 0.25, "low",
    )
    return ModelPolicy(
        "breadth",
        routes,
        cheap_scout=routes[ResearchRole.SCOUT],
        multimodal_scout=p.multimodal_scout,
        alternate_deep_dive=p.alternate_deep_dive,
        planner_question_range=(16, 24),
    )


def _glm_heavy_policy() -> ModelPolicy:
    p = _quality_policy()
    routes = dict(p.routes)
    routes[ResearchRole.GAP_ANALYST] = ModelRoute(
        _env("RESEARCH_GLM_GAP_MODEL", "zai:glm-5.3"), 8, 12, 100_000, 1.00, "high"
    )
    return ModelPolicy(
        "glm-heavy",
        routes,
        cheap_scout=ModelRoute(
            _env("RESEARCH_GLM_CHEAP_MODEL", "zai:glm-5.3-flash"),
            10, 20, 80_000, 0.30, "low",
        ),
        multimodal_scout=p.multimodal_scout,
        alternate_deep_dive=p.alternate_deep_dive,
        planner_question_range=(8, 12),
    )


POLICY_PRESETS: dict[str, ModelPolicy] = {
    "quality": _quality_policy(),
    "breadth": _breadth_policy(),
    "glm-heavy": _glm_heavy_policy(),
}


def get_policy(name: str) -> ModelPolicy:
    try:
        return POLICY_PRESETS[name]
    except KeyError as exc:
        raise ValueError(f"unknown policy {name!r}; choose from {sorted(POLICY_PRESETS)}") from exc

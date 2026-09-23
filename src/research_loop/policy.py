from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from typing import Any, Literal, Mapping

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

    def salvage(self) -> ModelRoute:
        """Tool-free wrap-up call after this route's research budget ran out."""
        return replace(
            self,
            max_requests=2,
            total_tokens_limit=80_000,
            cost_limit=None if self.cost_limit is None else round(self.cost_limit * 0.5, 4),
        )


# Steps after research that the job reserve pays for; salvage calls may also draw on it.
_FINISHING_ROLES = frozenset({ResearchRole.GAP_ANALYST, ResearchRole.SYNTHESIZER, ResearchRole.VERIFIER})


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
        job_cost_limit: float | None = None,
        job_reserve_usd: float = 0.0,
    ) -> None:
        self.name = name
        self.routes = routes
        self.cheap_scout = cheap_scout
        self.multimodal_scout = multimodal_scout
        self.alternate_deep_dive = alternate_deep_dive
        self.planner_question_range = planner_question_range
        # Soft USD cap across all agent calls in one job; route cost_limit still applies per call.
        self.job_cost_limit = job_cost_limit
        # Part of job_cost_limit that research calls leave for gap analysis, synthesis,
        # verification, and salvage.
        self.job_reserve_usd = job_reserve_usd

    def for_role(self, role: ResearchRole) -> ModelRoute:
        return self.routes[role]

    def job_reserve_for(self, role: ResearchRole, *, salvage: bool = False) -> float:
        """USD a call in this role must leave unspent under job_cost_limit."""
        return 0.0 if salvage or role in _FINISHING_ROLES else self.job_reserve_usd

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
            "job_cost_limit": self.job_cost_limit,
            "job_reserve_usd": self.job_reserve_usd,
        }


def _env(name: str, default: str) -> str:
    return os.getenv(name, "").strip() or default


def _quality_policy() -> ModelPolicy:
    return ModelPolicy(
        "quality",
        {
            # Anthropic defaults max_tokens to 4096, shared by adaptive thinking and output.
            ResearchRole.PLANNER: ModelRoute(
                _env("RESEARCH_PLANNER_MODEL", "anthropic:claude-opus-5"),
                6, 4, 70_000, 2.50, "high", {"max_tokens": 32_000},
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
                8, 4, 120_000, 3.50, "high", {"max_tokens": 32_000},
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


def _synthetic_policy() -> ModelPolicy:
    route = ModelRoute("synthetic:fake", 5, 5, 5_000)
    return ModelPolicy("synthetic", {role: route for role in ResearchRole}, planner_question_range=(1, 1))


_POLICY_FACTORIES = {
    "quality": _quality_policy,
    "breadth": _breadth_policy,
    "glm-heavy": _glm_heavy_policy,
    "synthetic": _synthetic_policy,
}
# Built at import from the environment at that time; get_policy() builds fresh routes.
POLICY_PRESETS: dict[str, ModelPolicy] = {name: factory() for name, factory in _POLICY_FACTORIES.items()}


def get_policy(name: str, *, model_overrides: Mapping[str, str] | None = None) -> ModelPolicy:
    """Build fresh routes, optionally applying validated settings overrides."""
    try:
        policy = _POLICY_FACTORIES[name]()
    except KeyError as exc:
        raise ValueError(f"unknown policy {name!r}; choose from {sorted(_POLICY_FACTORIES)}") from exc
    if not model_overrides or name == "synthetic":
        return policy

    route_names = {
        ResearchRole.PLANNER: "RESEARCH_PLANNER_MODEL",
        ResearchRole.SCOUT: "RESEARCH_BREADTH_SCOUT_MODEL" if name == "breadth" else "RESEARCH_SCOUT_MODEL",
        ResearchRole.GAP_ANALYST: "RESEARCH_GLM_GAP_MODEL" if name == "glm-heavy" else "RESEARCH_GAP_MODEL",
        ResearchRole.DEEP_DIVE: "RESEARCH_DEEP_MODEL",
        ResearchRole.SYNTHESIZER: "RESEARCH_SYNTH_MODEL",
        ResearchRole.VERIFIER: "RESEARCH_VERIFY_MODEL",
    }
    for role, env_name in route_names.items():
        if model := model_overrides.get(env_name):
            policy.routes[role] = replace(policy.routes[role], model=model)
    if name == "breadth":
        policy.cheap_scout = policy.routes[ResearchRole.SCOUT]
    elif policy.cheap_scout:
        env_name = "RESEARCH_GLM_CHEAP_MODEL" if name == "glm-heavy" else "RESEARCH_CHEAP_SCOUT_MODEL"
        if model := model_overrides.get(env_name):
            policy.cheap_scout = replace(policy.cheap_scout, model=model)
    if policy.multimodal_scout and (model := model_overrides.get("RESEARCH_MULTIMODAL_MODEL")):
        policy.multimodal_scout = replace(policy.multimodal_scout, model=model)
    if policy.alternate_deep_dive and (model := model_overrides.get("RESEARCH_ALT_DEEP_MODEL")):
        policy.alternate_deep_dive = replace(policy.alternate_deep_dive, model=model)
    return policy

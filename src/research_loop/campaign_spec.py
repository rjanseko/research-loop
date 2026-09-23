"""The campaign specification (campaign.toml), validated in full when it is loaded.

Every field a campaign run reads is checked here, so `research-campaign --dry-run` catches a
missing, mistyped, or unsupported setting before any paid call. Unknown keys are rejected: a
misspelled setting would otherwise be silently ignored.
"""
from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeFloat,
    NonNegativeInt,
    PositiveFloat,
    PositiveInt,
    field_validator,
    model_validator,
)


SYNTHESIS_DIR = "campaign"
# Question IDs name folders under the output directory, next to these.
RESERVED_QUESTION_IDS = (".", "..", SYNTHESIS_DIR, "manifests")


class _Spec(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Question(_Spec):
    id: str = Field(min_length=1)
    text: str = Field(min_length=1)


class Execution(_Spec):
    # Campaigns run the normalized tool stack only; these two record that in the spec.
    normalized_web: Literal[True] = True
    scholarly_cache_mode: Literal["off", "live", "record", "replay"] = "record"
    # Notes for the operator; nothing reads them.
    model_budget_gate: str | None = None
    first_run: str | None = None

    max_failed_questions: PositiveInt = 2
    question_timeout_seconds: PositiveInt | PositiveFloat | None = None  # an int stays an int in fingerprints
    max_parallel_scouts: PositiveInt
    planner_question_min: PositiveInt
    planner_question_max: PositiveInt
    max_deep_dives_per_round: NonNegativeInt
    max_parallel_deep_dives: PositiveInt = 2
    max_verification_rounds: NonNegativeInt
    deep_dive_cost_limit_usd: PositiveFloat
    question_cost_limit_usd: PositiveFloat
    question_reserve_usd: NonNegativeFloat = 0.0
    scout_max_requests: PositiveInt | None = None
    scout_max_tool_calls: PositiveInt | None = None
    scout_total_tokens_limit: PositiveInt | None = None
    research_notes: list[str] = Field(default_factory=list)

    @field_validator("research_notes")
    @classmethod
    def _notes_have_text(cls, notes: list[str]) -> list[str]:
        if not all(note.strip() for note in notes):
            raise ValueError("research_notes must be nonempty strings")
        return notes

    @model_validator(mode="after")
    def _limits_are_consistent(self) -> Execution:
        if self.planner_question_min > self.planner_question_max:
            raise ValueError("planner_question_min must not exceed planner_question_max")
        if self.question_reserve_usd >= self.question_cost_limit_usd:
            raise ValueError("question_reserve_usd must be below question_cost_limit_usd")
        return self


class Synthesis(_Spec):
    cost_limit_usd: PositiveFloat
    total_tokens_limit: PositiveInt
    max_requests: PositiveInt
    max_output_tokens: PositiveInt
    max_prompt_chars: PositiveInt


class Outputs(_Spec):
    question_files: list[str]
    campaign_files: list[str]
    findings_sections: list[str]
    hypothesis_fields: list[str]


class CampaignSpec(_Spec):
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    status: str
    graph_version: Literal["research-graph-v1"]
    period_start: str
    period_end: str
    as_of: str
    execution: Execution
    # Rendered into every question's objective in this order, so its order is part of the objective hash.
    source_policy: dict[str, str]
    synthesis: Synthesis
    outputs: Outputs
    questions: list[Question] = Field(min_length=1)

    @field_validator("questions")
    @classmethod
    def _questions_name_their_own_folders(cls, questions: list[Question]) -> list[Question]:
        ids = [question.id for question in questions]
        if len(ids) != len(set(ids)) or any("/" in question_id for question_id in ids):
            raise ValueError("campaign needs unique question IDs without '/'")
        if reserved := sorted(set(ids) & set(RESERVED_QUESTION_IDS)):
            raise ValueError(f"campaign question IDs cannot be {', '.join(map(repr, RESERVED_QUESTION_IDS))}; "
                             f"found {', '.join(map(repr, reserved))}")
        return questions


def load_campaign(path: Path) -> dict[str, Any]:
    """Load and validate a campaign spec; returns it as a plain dict, with defaults filled in."""
    return CampaignSpec.model_validate(tomllib.loads(path.read_text(encoding="utf-8"))).model_dump()

"""Versioned, source-grounded assessment of stored Scout reports.

This lane is separate from the historical point-rubric grade. Packets contain independently
reviewed source summaries; the run's own ledger is shown for traceability, never treated as
proof that a claim is true. Model judgments remain reviewable, not ground truth.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal
from importlib.resources import files
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, model_validator
from pydantic_ai import Agent, ModelRetry, capture_run_messages
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models import Model
from pydantic_ai.usage import RunUsage
from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import Evaluator, EvaluatorContext

from .config import Settings
from .evidence import EvidenceLedger
from .models import build_model
from .schemas import FinalReport
from .store import error_record, transcript, usage_record
from .study_budget import BUDGET_POLICY_VERSION, StudyBudget, StudyBudgetModel

QUALITY_VERSION = 1
QUALITY_MODEL = "openai:gpt-6-sol"
QUALITY_THINKING = "high"
DIMENSIONS = ("direct_answer", "coverage", "evidence_use", "calibration", "reader_utility")
INSTRUCTIONS = """Evaluate a research report for the reader and decision in the packet.
Give each quality dimension an anchored level: 0 absent or misleading, 1 major gaps,
2 useful with material reservations, 3 strong. Ground reasons in a specific report passage.
For each fact target, compare the report with the independent source packet. Mark correct,
incorrect, omitted, or unresolved. Do not infer truth from the report's source titles or its
research ledger. Name packet source IDs supporting each factual verdict. If the packet does not
settle a claim, say unresolved. Audit up to five additional decision-critical claims from the
report against the packet, including any claim whose error would change the conclusion.
Distinguish a source's own claim from an established general fact, and note material omissions.
A polished report with a contradicted decisive claim cannot receive a strong overall rating.
"""


class PacketSource(BaseModel):
    id: str
    url: str
    title: str
    passage: str = Field(min_length=20, description="Reviewed paraphrase of a source passage, not a model-generated run claim")


class FactTarget(BaseModel):
    id: str
    proposition: str
    source_ids: list[str] = Field(min_length=1)


class QualityPacket(BaseModel):
    case_id: str
    version: str
    question: str
    reader_goal: str
    as_of: str
    sources: list[PacketSource] = Field(min_length=1)
    fact_targets: list[FactTarget] = Field(min_length=1)
    min_critical_claims: int = Field(0, ge=0, le=5)

    @model_validator(mode="after")
    def valid_references(self) -> QualityPacket:
        source_ids = [source.id for source in self.sources]
        target_ids = [target.id for target in self.fact_targets]
        if len(set(source_ids)) != len(source_ids) or len(set(target_ids)) != len(target_ids):
            raise ValueError("packet source and target IDs must be unique")
        if unknown := {sid for target in self.fact_targets for sid in target.source_ids} - set(source_ids):
            raise ValueError(f"fact targets name unknown packet sources: {sorted(unknown)}")
        return self

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


def quality_packets() -> dict[str, QualityPacket]:
    text = files("research_loop").joinpath("quality_packets.jsonl").read_text(encoding="utf-8")
    packets = [QualityPacket.model_validate_json(line) for line in text.splitlines() if line.strip()]
    if len({packet.case_id for packet in packets}) != len(packets):
        raise ValueError("duplicate quality packet case ID")
    return {packet.case_id: packet for packet in packets}


def find_quality_packet(key: str) -> QualityPacket:
    matches = [packet for case_id, packet in quality_packets().items()
               if case_id == key or case_id.split("-")[0] == key]
    if len(matches) != 1:
        raise KeyError(f"no single quality packet {key!r}")
    return matches[0]


class DimensionVerdict(BaseModel):
    dimension: Literal["direct_answer", "coverage", "evidence_use", "calibration", "reader_utility"]
    level: int = Field(ge=0, le=3)
    report_anchor: str
    reason: str


class FactVerdict(BaseModel):
    target_id: str
    finding: Literal["correct", "incorrect", "omitted", "unresolved"]
    report_anchor: str = ""
    source_ids: list[str] = Field(default_factory=list)
    reason: str


class CriticalClaimVerdict(BaseModel):
    claim: str
    finding: Literal["supported", "contradicted", "unresolved"]
    source_ids: list[str] = Field(default_factory=list)
    reason: str


class QualityJudgment(BaseModel):
    overall_level: int = Field(ge=0, le=3)
    overall_reason: str
    dimensions: list[DimensionVerdict]
    fact_checks: list[FactVerdict]
    critical_claims: list[CriticalClaimVerdict] = Field(default_factory=list, max_length=5)
    material_omissions: list[str] = Field(default_factory=list)


def assessment_input(report: FinalReport, ledger: EvidenceLedger, packet: QualityPacket,
                     checks: dict[str, Any] | None = None) -> str:
    return json.dumps({"packet": packet.model_dump(mode="json"), "report": report.model_dump(mode="json"),
                       "run_evidence": ledger.prompt_view(), "integrity_checks": checks or {}}, ensure_ascii=False)


@dataclass
class QualityRecord:
    run_id: UUID
    packet: QualityPacket
    status: str
    judgment: QualityJudgment | None = None
    usage: RunUsage | None = None
    messages: list[ModelMessage] = field(default_factory=list)
    error: BaseException | None = None
    id: UUID = field(default_factory=uuid4)
    budget_cap_usd: Decimal | None = None
    reserved_usd: Decimal | None = None

    @property
    def cost_usd(self) -> Decimal | None:
        return self.usage.cost if self.usage else None


def _validate(judgment: QualityJudgment, packet: QualityPacket) -> QualityJudgment:
    dimensions = [item.dimension for item in judgment.dimensions]
    expected = {target.id for target in packet.fact_targets}
    target_sources = {target.id: set(target.source_ids) for target in packet.fact_targets}
    targets = [item.target_id for item in judgment.fact_checks]
    sources = {source.id for source in packet.sources}
    problems = []
    if len(dimensions) != len(DIMENSIONS) or set(dimensions) != set(DIMENSIONS):
        problems.append("return each quality dimension exactly once")
    if len(targets) != len(expected) or set(targets) != expected:
        problems.append("return each fact target exactly once")
    if unknown := {sid for item in (*judgment.fact_checks, *judgment.critical_claims)
                   for sid in item.source_ids} - sources:
        problems.append(f"unknown packet source IDs: {sorted(unknown)}")
    if any(not item.report_anchor.strip() or not item.reason.strip() for item in judgment.dimensions):
        problems.append("each dimension needs a report passage and reason")
    if any(not item.reason.strip() for item in judgment.fact_checks):
        problems.append("each fact check needs a reason")
    if any(item.finding in ("correct", "incorrect")
           and not set(item.source_ids) & target_sources.get(item.target_id, set())
           for item in judgment.fact_checks):
        problems.append("correct and incorrect fact checks need a relevant packet source ID")
    if len(judgment.critical_claims) < packet.min_critical_claims:
        problems.append(f"audit at least {packet.min_critical_claims} additional critical claims")
    if any(not item.claim.strip() or not item.reason.strip() for item in judgment.critical_claims):
        problems.append("each critical claim needs its text and a reason")
    if judgment.overall_level == 3 and (any(item.finding == "incorrect" for item in judgment.fact_checks)
                                         or any(item.finding == "contradicted" for item in judgment.critical_claims)):
        problems.append("a contradicted decisive claim rules out overall level 3")
    if problems:
        raise ModelRetry("; ".join(problems))
    return judgment


async def judge_quality(report: FinalReport, ledger: EvidenceLedger, packet: QualityPacket, run_id: UUID,
                        settings: Settings, *, checks: dict[str, Any] | None = None,
                        model: Model | None = None, budget: StudyBudget | None = None) -> QualityRecord:
    """One paid model assessment; failures retain usage and messages for study accounting."""
    agent = Agent(output_type=QualityJudgment, instructions=INSTRUCTIONS)

    @agent.output_validator
    def complete(judgment: QualityJudgment) -> QualityJudgment:
        return _validate(judgment, packet)

    usage = RunUsage()
    messages: list[ModelMessage] = []
    chosen = model or build_model(QUALITY_MODEL, "scout", settings, sdk_retries=0)
    if budget is not None:
        chosen = StudyBudgetModel(chosen, QUALITY_MODEL, budget)
    try:
        with capture_run_messages() as messages:
            result = await agent.run(assessment_input(report, ledger, packet, checks),
                                     model=chosen, usage=usage,
                                     model_settings={"thinking": QUALITY_THINKING, "timeout": 180,
                                                     "max_tokens": 5000})
    except Exception as exc:  # noqa: BLE001 - retain the judge's partial usage and failure
        return QualityRecord(run_id, packet, "failed", usage=usage, messages=list(messages), error=exc,
                             budget_cap_usd=budget.cap_usd if budget else None,
                             reserved_usd=budget.reserved_usd if budget else None)
    return QualityRecord(run_id, packet, "succeeded", result.output, usage, result.all_messages(),
                         budget_cap_usd=budget.cap_usd if budget else None,
                         reserved_usd=budget.reserved_usd if budget else None)


def quality_row(record: QualityRecord) -> dict[str, Any]:
    return {"id": record.id, "run_id": record.run_id, "case_id": record.packet.case_id,
            "packet_version": record.packet.version, "packet_sha256": record.packet.digest,
            "evaluator_version": QUALITY_VERSION, "judge_model": QUALITY_MODEL,
            "judge_thinking": QUALITY_THINKING, "status": record.status,
            "judgment": record.judgment.model_dump(mode="json") if record.judgment else None,
            "usage": usage_record(record.usage) if record.usage else None, "cost_usd": record.cost_usd,
            "messages": transcript(record.messages) if record.messages else None,
            "error": error_record(record.error) if record.error else None,
            "budget_cap_usd": record.budget_cap_usd, "reserved_usd": record.reserved_usd,
            "budget_policy": BUDGET_POLICY_VERSION if record.budget_cap_usd is not None else None}


@dataclass
class StoredQualityReport:
    run_id: UUID
    report: FinalReport
    ledger: EvidenceLedger
    packet: QualityPacket
    checks: dict[str, Any] = field(default_factory=dict)


@dataclass
class QualityEvaluator(Evaluator[StoredQualityReport, StoredQualityReport]):
    settings: Settings
    model: Model | None = None
    records: list[QualityRecord] = field(default_factory=list)
    budget: StudyBudget | None = None

    def get_default_evaluation_name(self) -> str:
        return "research_quality"

    async def evaluate(self, ctx: EvaluatorContext[StoredQualityReport, StoredQualityReport]) -> dict[str, Any]:
        item = ctx.output
        record = await judge_quality(item.report, item.ledger, item.packet, item.run_id, self.settings,
                                     checks=item.checks, model=self.model, budget=self.budget)
        self.records.append(record)
        if record.judgment is None:
            return {}
        return {"overall_quality": record.judgment.overall_level / 3,
                **{f"quality:{part.dimension}": part.level / 3 for part in record.judgment.dimensions},
                "fact_correct": sum(item.finding == "correct" for item in record.judgment.fact_checks),
                "fact_incorrect": sum(item.finding == "incorrect" for item in record.judgment.fact_checks)}


async def assess_reports(reports: list[StoredQualityReport], settings: Settings,
                         model: Model | None = None, budget: StudyBudget | None = None) -> tuple[Any, list[QualityRecord]]:
    evaluator = QualityEvaluator(settings, model, budget=budget)
    dataset = Dataset[StoredQualityReport, StoredQualityReport, None](
        name=f"scout-quality-v{QUALITY_VERSION}",
        cases=[Case(name=f"{r.packet.case_id}:{r.run_id}", inputs=r) for r in reports],
        evaluators=[evaluator])

    async def stored(item: StoredQualityReport) -> StoredQualityReport:
        return item

    result = await dataset.evaluate(stored, max_concurrency=1, progress=False)
    return result, evaluator.records

"""Offline checks for the source-grounded quality evaluator."""
from __future__ import annotations

import json
from uuid import uuid4

import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from research_loop.config import Settings
from research_loop.evidence import EvidenceLedger
from research_loop.quality import (
    DIMENSIONS,
    QUALITY_VERSION,
    StoredQualityReport,
    assess_reports,
    find_quality_packet,
    quality_packets,
    quality_row,
)
from research_loop.schemas import FinalReport

pytestmark = pytest.mark.filterwarnings("ignore::pydantic_ai.exceptions.CostNotFoundWarning")


def _judgment(packet, *, omit_fact=False):
    return {
        "overall_level": 2,
        "overall_reason": "The answer names the false premise and relevant task.",
        "dimensions": [{"dimension": name, "level": 2, "report_anchor": "No captioning track ran.",
                        "reason": "The report addresses the question."} for name in DIMENSIONS],
        "fact_checks": [{"target_id": target.id, "finding": "correct", "report_anchor": "No captioning track ran.",
                         "source_ids": target.source_ids, "reason": "The official task list supports this."}
                        for target in packet.fact_targets[:1 if omit_fact else None]],
        "critical_claims": [], "material_omissions": [],
    }


def _model(replies, prompts):
    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        first = messages[0]
        assert isinstance(first, ModelRequest)
        prompts.append(json.loads(next(p.content for p in first.parts if isinstance(p, UserPromptPart))))
        attempt = sum(isinstance(m, ModelResponse) for m in messages)
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, replies[attempt])])

    return FunctionModel(respond)


def test_packets_cover_a_short_and_a_broad_case_with_independent_sources() -> None:
    packets = quality_packets()
    assert set(packets) == {"st04-ilsvrc-captioning", "st07-swebench-trust"}
    assert find_quality_packet("st04").version == "1"
    for packet in packets.values():
        assert packet.digest and all(source.url.startswith("https://") for source in packet.sources)


async def test_quality_assessment_requires_every_fact_and_dimension(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    packet = find_quality_packet("st04")
    report = FinalReport(title="No captioning track", executive_summary="No captioning track ran.",
                         answer="No ILSVRC 2016 video captioning track existed. The video task was detection.")
    prompts = []
    run_id = uuid4()
    result, records = await assess_reports(
        [StoredQualityReport(run_id, report, EvidenceLedger(), packet)], Settings(),
        model=_model([_judgment(packet, omit_fact=True), _judgment(packet)], prompts))
    (record,) = records
    assert record.status == "succeeded" and record.usage.requests == 2
    assert record.judgment.overall_level == 2
    assert len(record.judgment.fact_checks) == len(packet.fact_targets)
    assert prompts[0]["packet"]["sources"][0]["id"] == "p1"
    assert prompts[0]["report"]["executive_summary"] == "No captioning track ran."
    assert result.cases[0].scores["overall_quality"].value == pytest.approx(2 / 3)
    row = quality_row(record)
    assert row["packet_sha256"] == packet.digest and row["evaluator_version"] == QUALITY_VERSION
    assert row["messages"] and row["error"] is None


async def test_failed_quality_assessment_retains_usage(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    packet = find_quality_packet("st04")
    report = FinalReport(title="t", executive_summary="s", answer="a")
    _, (record,) = await assess_reports([StoredQualityReport(uuid4(), report, EvidenceLedger(), packet)], Settings(),
                                        model=_model([_judgment(packet, omit_fact=True)] * 3, []))
    assert record.status == "failed" and record.judgment is None and record.usage.requests == 2
    assert quality_row(record)["error"]["type"] == "UnexpectedModelBehavior"

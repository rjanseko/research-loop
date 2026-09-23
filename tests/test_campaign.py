from __future__ import annotations

import json
from pathlib import Path

import pytest

from research_loop.campaign import CAMPAIGN_FILE, load_campaign, render_objective


def test_campaign_spec_has_unique_questions_and_reproducible_scope() -> None:
    campaign = load_campaign(CAMPAIGN_FILE)
    assert campaign["graph_version"] == "research-graph-v1"
    assert len(campaign["questions"]) == 11
    assert len({item["id"] for item in campaign["questions"]}) == 11
    objective = render_objective(campaign, campaign["questions"][0])
    assert "preprints" in objective
    assert "Question q01" in objective
    assert "benchmark_claims" in objective


def test_campaign_rejects_duplicate_question_ids(tmp_path: Path) -> None:
    spec = tmp_path / "bad.toml"
    spec.write_text('graph_version = "research-graph-v1"\n[[questions]]\nid = "q1"\ntext = "first"\n[[questions]]\nid = "q1"\ntext = "second"\n')
    with pytest.raises(ValueError, match="unique"):
        load_campaign(spec)


@pytest.mark.asyncio
async def test_campaign_run_exports_evidence_without_model_calls(monkeypatch, tmp_path: Path) -> None:
    from decimal import Decimal
    from types import SimpleNamespace
    from uuid import uuid4

    from research_loop.campaign import run_campaign
    from research_loop.ledger import EvidenceLedger
    from research_loop.schemas import Claim, Evidence, FinalReport, ResearchResult, SourceRef, VerificationReport
    from research_loop.settings import ResearchSettings

    source = SourceRef(url="https://example.org/paper", title="Paper", source_type="paper", publication_status="preprint")
    ledger = EvidenceLedger()
    ledger.add(ResearchResult(
        question_id="q01", question="definitions", conclusion="A finding",
        claims=[Claim(id="c1", statement="A finding", evidence=[Evidence(source=source, excerpt="Short evidence", confidence=0.8)], confidence=0.8)],
        confidence=0.8,
    ))

    class FakeLoop:
        def __init__(self, *_args, **_kwargs):
            pass

        async def run(self, _objective, **_kwargs):
            return SimpleNamespace(job_id=uuid4(), report=FinalReport(answer="Draft report"),
                                   ledger=ledger, verification=VerificationReport(),
                                   cost_usd=Decimal("1.25"))

    monkeypatch.setattr("research_loop.campaign.ResearchLoop", FakeLoop)
    output = tmp_path / "results"
    manifest_path = await run_campaign(
        CAMPAIGN_FILE, question_ids=["q01"], policy_name="quality",
        settings=ResearchSettings.from_env({}), output_dir=output, persist=False,
    )
    assert "Draft report" in (output / "q01" / "report.md").read_text()
    assert "preprint" in (output / "q01" / "bibliography.json").read_text()
    manifest = json.loads(manifest_path.read_text())
    assert manifest["status"] == "completed"
    assert manifest["run_limits"]["question_cost_limit_usd"] == 5.0
    assert manifest["questions"][0]["cost_usd"] == "1.25"


def test_campaign_requires_question_cost_cap(tmp_path: Path) -> None:
    spec = tmp_path / "uncapped.toml"
    spec.write_text('graph_version = "research-graph-v1"\n[execution]\nmax_parallel_scouts = 1\n[[questions]]\nid = "q1"\ntext = "first"\n')
    with pytest.raises(ValueError, match="question_cost_limit_usd"):
        load_campaign(spec)


@pytest.mark.asyncio
async def test_campaign_manifest_records_failed_question(monkeypatch, tmp_path: Path) -> None:
    from research_loop.async_orchestrator import JobBudgetExceeded
    from research_loop.campaign import run_campaign
    from research_loop.settings import ResearchSettings

    class OverBudgetLoop:
        def __init__(self, *_args, **_kwargs):
            pass

        async def run(self, _objective, **_kwargs):
            raise JobBudgetExceeded("job cost cap reached")

    monkeypatch.setattr("research_loop.campaign.ResearchLoop", OverBudgetLoop)
    with pytest.raises(JobBudgetExceeded):
        await run_campaign(
            CAMPAIGN_FILE, question_ids=["q01"], policy_name="quality",
            settings=ResearchSettings.from_env({}), output_dir=tmp_path, persist=False,
        )
    manifest = json.loads((tmp_path / "campaign_manifest.json").read_text())
    assert manifest["status"] == "failed"
    assert manifest["error"] == "JobBudgetExceeded"
    assert manifest["questions"] == [{"id": "q01", "status": "failed", "error": "JobBudgetExceeded"}]

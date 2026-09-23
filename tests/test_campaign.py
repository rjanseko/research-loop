from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from research_loop.campaign import CAMPAIGN_FILE, load_campaign, render_objective
from research_loop.ledger import EvidenceLedger
from research_loop.schemas import (
    CampaignFindings,
    Claim,
    Contradiction,
    Evidence,
    FinalReport,
    Hypothesis,
    ResearchResult,
    SourceRef,
    VerificationReport,
)
from research_loop.settings import ResearchSettings


def test_campaign_spec_has_unique_questions_and_reproducible_scope() -> None:
    campaign = load_campaign(CAMPAIGN_FILE)
    assert campaign["graph_version"] == "research-graph-v1"
    assert len(campaign["questions"]) == 11
    assert len({item["id"] for item in campaign["questions"]}) == 11
    objective = render_objective(campaign, campaign["questions"][0])
    assert "preprints" in objective
    assert "Question q01" in objective
    assert "benchmark_claims" in objective


def test_campaign_spec_matches_synthesis_schema() -> None:
    outputs = load_campaign(CAMPAIGN_FILE)["outputs"]
    assert outputs["findings_sections"] == list(CampaignFindings.model_fields)
    assert set(outputs["hypothesis_fields"]) <= set(Hypothesis.model_fields)


def test_campaign_rejects_duplicate_question_ids(tmp_path: Path) -> None:
    spec = tmp_path / "bad.toml"
    spec.write_text('graph_version = "research-graph-v1"\n[[questions]]\nid = "q1"\ntext = "first"\n[[questions]]\nid = "q1"\ntext = "second"\n')
    with pytest.raises(ValueError, match="unique"):
        load_campaign(spec)


@pytest.mark.parametrize("question_id", [".", "..", "campaign", "manifests"])
def test_campaign_rejects_question_ids_that_are_not_their_own_folder(tmp_path: Path, question_id: str) -> None:
    spec = tmp_path / "unsafe.toml"
    spec.write_text(f'graph_version = "research-graph-v1"\n[[questions]]\nid = "{question_id}"\ntext = "first"\n')
    with pytest.raises(ValueError, match="cannot be"):
        load_campaign(spec)


def test_campaign_requires_question_cost_cap(tmp_path: Path) -> None:
    spec = tmp_path / "uncapped.toml"
    spec.write_text('graph_version = "research-graph-v1"\n[execution]\nmax_parallel_scouts = 1\n[[questions]]\nid = "q1"\ntext = "first"\n')
    with pytest.raises(ValueError, match="question_cost_limit_usd"):
        load_campaign(spec)


def _ledger(question_id: str) -> EvidenceLedger:
    preprint = SourceRef(url="https://example.org/paper", title="Paper", source_type="paper", publication_status="preprint")
    journal = SourceRef(url="https://example.org/journal", title="Paper", source_type="paper", publication_status="journal")
    ledger = EvidenceLedger()
    ledger.add(ResearchResult(
        question_id="q1", question="definitions", conclusion="A finding",
        claims=[Claim(id="c1", statement=f"{question_id} scout finding",
                      evidence=[Evidence(source=preprint, excerpt="Short evidence", confidence=0.8)], confidence=0.8)],
        contradictions=[Contradiction(description="Sources disagree", claim_ids=["c1"])],
        unresolved_questions=["What remains open"],
        confidence=0.8,
    ))
    # A deep dive may reuse a claim ID; campaign refs must still be unique.
    ledger.add(ResearchResult(
        question_id="q1", question="definitions", conclusion="Deeper finding",
        claims=[Claim(id="c1", statement=f"{question_id} deep-dive finding",
                      evidence=[Evidence(source=journal, excerpt="Published evidence", confidence=0.9)], confidence=0.9)],
        confidence=0.9,
    ))
    return ledger


async def _complete_questions(monkeypatch, output: Path, question_ids: list[str],
                              verification: VerificationReport | None = None) -> Path:
    from research_loop.campaign import run_campaign

    class FakeLoop:
        def __init__(self, *_args, **_kwargs):
            pass

        async def run(self, objective, **_kwargs):
            question_id = next(qid for qid in question_ids if f"Question {qid}:" in objective)
            return SimpleNamespace(job_id=uuid4(), report=FinalReport(answer=f"Draft report {question_id}", caveats=["Thin"]),
                                   ledger=_ledger(question_id), verification=verification or VerificationReport(),
                                   cost_usd=Decimal("1.25"))

    monkeypatch.setattr("research_loop.campaign.ResearchLoop", FakeLoop)
    return await run_campaign(
        CAMPAIGN_FILE, question_ids=question_ids, policy_name="quality",
        settings=ResearchSettings.from_env({}), output_dir=output, persist=False,
    )


@pytest.mark.asyncio
async def test_campaign_run_exports_spec_question_files(monkeypatch, tmp_path: Path) -> None:
    manifest_path = await _complete_questions(monkeypatch, tmp_path, ["q01"])
    folder = tmp_path / "q01"
    assert {item.name for item in folder.iterdir()} == set(load_campaign(CAMPAIGN_FILE)["outputs"]["question_files"])
    assert "Draft report q01" in (folder / "report.md").read_text()
    assert json.loads((folder / "report.json").read_text())["caveats"] == ["Thin"]
    assert "preprint" in (folder / "bibliography.json").read_text()
    run = json.loads((folder / "run.json").read_text())
    manifest = json.loads(manifest_path.read_text())
    assert manifest_path.parent == tmp_path / "manifests"
    assert manifest["status"] == "completed"
    assert manifest["run_limits"]["question_cost_limit_usd"] == 5.0
    assert manifest["acquisition"]["fetch_version"] == 2
    assert manifest["evidence_version"] == 2
    assert manifest["questions"][0]["cost_usd"] == "1.25"
    assert run["status"] == "completed"
    assert run["experiment_id"] == manifest["experiment_id"]
    assert run["config_fingerprint"] == manifest["config_fingerprint"]


@pytest.mark.asyncio
async def test_campaign_manifest_records_failed_question(monkeypatch, tmp_path: Path) -> None:
    from research_loop.async_orchestrator import JobBudgetExceeded
    from research_loop.campaign import run_campaign

    class OverBudgetLoop:
        def __init__(self, *_args, **_kwargs):
            pass

        async def run(self, _objective, **_kwargs):
            raise JobBudgetExceeded("job cost cap reached")

    monkeypatch.setattr("research_loop.campaign.ResearchLoop", OverBudgetLoop)
    manifest_path = await run_campaign(
        CAMPAIGN_FILE, question_ids=["q01"], policy_name="quality",
        settings=ResearchSettings.from_env({}), output_dir=tmp_path, persist=False,
    )
    manifest = json.loads(manifest_path.read_text())
    assert manifest["status"] == "failed"
    assert "error" not in manifest  # reserved for failures of the batch itself
    assert manifest["questions"] == [{"id": "q01", "status": "failed", "error": "JobBudgetExceeded"}]
    assert not (tmp_path / "q01").exists()


def _loop_failing_on(failing: set[str]):
    from research_loop.async_orchestrator import JobBudgetExceeded

    class PartlyFailingLoop:
        def __init__(self, *_args, **_kwargs):
            pass

        async def run(self, objective, **_kwargs):
            question_id = objective.split("Question ", 1)[1].split(":", 1)[0]
            if question_id in failing:
                raise JobBudgetExceeded("job cost cap reached")
            return SimpleNamespace(job_id=uuid4(), report=FinalReport(answer=f"Draft {question_id}"),
                                   ledger=_ledger(question_id), verification=VerificationReport(), cost_usd=None)

    return PartlyFailingLoop


@pytest.mark.asyncio
async def test_campaign_continues_past_a_failed_question(monkeypatch, tmp_path: Path) -> None:
    from research_loop.campaign import run_campaign

    monkeypatch.setattr("research_loop.campaign.ResearchLoop", _loop_failing_on({"q01"}))
    manifest_path = await run_campaign(
        CAMPAIGN_FILE, question_ids=["q01", "q02"], policy_name="quality",
        settings=ResearchSettings.from_env({}), output_dir=tmp_path, persist=False,
    )
    manifest = json.loads(manifest_path.read_text())
    assert manifest["status"] == "completed_with_failures"
    assert [(item["id"], item["status"]) for item in manifest["questions"]] == [("q01", "failed"), ("q02", "completed")]
    assert "not_run" not in manifest
    assert (tmp_path / "q02" / "run.json").exists() and not (tmp_path / "q01").exists()


@pytest.mark.asyncio
async def test_campaign_stops_once_failures_suggest_a_shared_cause(monkeypatch, tmp_path: Path) -> None:
    from research_loop.campaign import run_campaign

    assert load_campaign(CAMPAIGN_FILE)["execution"]["max_failed_questions"] == 2
    monkeypatch.setattr("research_loop.campaign.ResearchLoop", _loop_failing_on({"q01", "q02", "q03"}))
    manifest_path = await run_campaign(
        CAMPAIGN_FILE, question_ids=["q01", "q02", "q03", "q04"], policy_name="quality",
        settings=ResearchSettings.from_env({}), output_dir=tmp_path, persist=False,
    )
    manifest = json.loads(manifest_path.read_text())
    assert manifest["status"] == "failed"
    assert [item["id"] for item in manifest["questions"]] == ["q01", "q02"]
    assert manifest["not_run"] == ["q03", "q04"]


def test_campaign_cli_exits_nonzero_and_names_failed_questions(monkeypatch, tmp_path: Path, capsys) -> None:
    import sys

    import research_loop.campaign as campaign

    settings = ResearchSettings.from_env({})
    monkeypatch.setattr(campaign, "ResearchSettings", SimpleNamespace(from_env=lambda: settings))
    monkeypatch.setattr(campaign, "_paid_preflight", lambda *_args: None)
    monkeypatch.setattr(campaign, "ResearchLoop", _loop_failing_on({"q01"}))
    monkeypatch.setattr(sys, "argv", ["research-campaign", "--question", "q01", "--paid", "--output", str(tmp_path)])
    with pytest.raises(SystemExit) as exc:
        campaign.main()
    assert exc.value.code == 1
    assert "q01 (JobBudgetExceeded)" in capsys.readouterr().err


def test_campaign_rejects_a_nonpositive_failure_limit(tmp_path: Path) -> None:
    spec = tmp_path / "limit.toml"
    spec.write_text(CAMPAIGN_FILE.read_text().replace("max_failed_questions = 2", "max_failed_questions = 0"))
    with pytest.raises(ValueError, match="max_failed_questions"):
        load_campaign(spec)


@pytest.mark.asyncio
async def test_aggregate_assigns_unique_refs_and_keeps_source_versions(monkeypatch, tmp_path: Path) -> None:
    from research_loop.campaign import aggregate_campaign

    await _complete_questions(monkeypatch, tmp_path, ["q01", "q02"])
    evidence = aggregate_campaign(load_campaign(CAMPAIGN_FILE), tmp_path)
    assert [item.question["id"] for item in evidence.completed] == ["q01", "q02"]
    assert evidence.missing == [f"q{index:02d}" for index in range(3, 12)]
    assert evidence.refs == {"q01/q1/c1", "q01/q1/c1~2", "q02/q1/c1", "q02/q1/c1~2"}
    assert evidence.contradictions["q01"] == [{"description": "Sources disagree", "claim_refs": ["q01/q1/c1"]}]
    # Preprint and journal records stay distinct; each lists every question that cited it.
    assert sorted(item["publication_status"] for item in evidence.bibliography) == ["journal", "preprint"]
    assert all(item["question_ids"] == ["q01", "q02"] for item in evidence.bibliography)


@pytest.mark.asyncio
async def test_synthesis_prompt_carries_verifier_findings(monkeypatch, tmp_path: Path) -> None:
    from research_loop.campaign import aggregate_campaign, synthesis_prompt
    from research_loop.schemas import ClaimCheck

    verification = VerificationReport(needs_research=True, checks=[
        ClaimCheck(statement="Scout finding holds", claim_ids=["q1/c1"], supported=True, severity="none",
                   explanation="ok"),
        ClaimCheck(statement="Deep-dive finding holds", claim_ids=["q1/c1~2", "q9/c9"], supported=False,
                   severity="major", explanation="excerpt does not say this"),
    ])
    await _complete_questions(monkeypatch, tmp_path, ["q01"], verification)
    campaign = load_campaign(CAMPAIGN_FILE)
    payload = json.loads(synthesis_prompt(campaign, aggregate_campaign(campaign, tmp_path)))
    # Only flagged checks reach the prompt, with ledger IDs mapped to campaign refs.
    assert payload["questions"][0]["verification"] == {
        "checked": 2, "not_supported": 1, "major": 1, "needs_research": True,
        "findings": [{"statement": "Deep-dive finding holds", "supported": False, "severity": "major",
                      "explanation": "excerpt does not say this", "claim_refs": ["q01/q1/c1~2"]}],
    }


@pytest.mark.asyncio
async def test_aggregate_skips_outputs_that_answered_a_different_objective(monkeypatch, tmp_path: Path) -> None:
    from research_loop.campaign import aggregate_campaign

    await _complete_questions(monkeypatch, tmp_path, ["q01", "q02"])
    campaign = load_campaign(CAMPAIGN_FILE)
    campaign["questions"][1]["text"] = "A reworded question the stored evidence never answered"
    evidence = aggregate_campaign(campaign, tmp_path)
    assert [item.question["id"] for item in evidence.completed] == ["q01"]
    assert evidence.stale == ["q02"]
    assert "q02" in evidence.missing

    # A run recorded without an objective hash cannot show what it answered.
    run_path = tmp_path / "q01" / "run.json"
    run = json.loads(run_path.read_text())
    del run["objective_sha256"]
    run_path.write_text(json.dumps(run))
    assert aggregate_campaign(load_campaign(CAMPAIGN_FILE), tmp_path).stale == ["q01"]


@pytest.mark.asyncio
async def test_synthesis_refuses_partial_campaign_and_oversized_prompt(monkeypatch, tmp_path: Path) -> None:
    from research_loop.campaign import prepare_synthesis

    with pytest.raises(ValueError, match="no completed"):
        prepare_synthesis(CAMPAIGN_FILE, tmp_path, allow_partial=True)
    await _complete_questions(monkeypatch, tmp_path, ["q01"])
    with pytest.raises(ValueError, match="--allow-partial"):
        prepare_synthesis(CAMPAIGN_FILE, tmp_path, allow_partial=False)
    tiny = tmp_path / "tiny.toml"
    tiny.write_text(CAMPAIGN_FILE.read_text().replace("max_prompt_chars = 360_000", "max_prompt_chars = 100"))
    with pytest.raises(ValueError, match="max_prompt_chars"):
        prepare_synthesis(tiny, tmp_path, allow_partial=True)


def _synthesis_body(refs: list[str]) -> dict:
    return {
        "summary": "Evidence is thin but consistent.",
        "findings": {"well_supported": [{"statement": "Agents drift on long tasks", "claim_refs": refs}],
                     "unknowns": [{"statement": "Cost-matched comparisons are missing"}]},
        "benchmark_catalog": [{"name": "Bench", "versions": ["v1"], "scope": "repos", "evaluation_method": "hidden tests",
                               "limits": ["small"], "claim_refs": refs}],
        "hypotheses": [{"id": "H1", "statement": "Replanning beats more reasoning effort", "supporting_evidence": refs,
                        "confidence": 0.4, "proposed_experiment": "A/B on long tasks",
                        "expected_metric": "task completion", "estimated_cost": "$50"}],
    }


@pytest.mark.asyncio
@pytest.mark.filterwarnings("ignore:A `cost_limit` is set but cannot be enforced")
async def test_synthesis_retries_unknown_refs_and_writes_campaign_files(monkeypatch, tmp_path: Path) -> None:
    from pydantic_ai.messages import ModelResponse, ToolCallPart
    from pydantic_ai.models.function import AgentInfo, FunctionModel

    from research_loop.agents import campaign_synthesizer_agent
    from research_loop.campaign import SYNTHESIS_DIR, synthesize_campaign

    await _complete_questions(monkeypatch, tmp_path, ["q01", "q02"])
    monkeypatch.undo()  # the synthesis job uses the real ResearchLoop
    prompts: list[str] = []

    def respond(messages, info: AgentInfo) -> ModelResponse:
        prompts.append(str(messages[-1].parts[-1].content))
        refs = ["q01/q1/c1", "q07/c9"] if len(prompts) == 1 else ["q01/q1/c1", "q02/q1/c1~2"]
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, _synthesis_body(refs))])

    with campaign_synthesizer_agent.override(model=FunctionModel(respond)):
        manifest_path = await synthesize_campaign(
            CAMPAIGN_FILE, policy_name="quality", settings=ResearchSettings.from_env({}),
            output_dir=tmp_path, persist=False, allow_partial=True,
        )

    assert len(prompts) == 2
    assert '"ref": "q02/q1/c1~2"' in prompts[0]
    assert "q07/c9" in prompts[1]  # the retry names the invented ref
    campaign_dir = tmp_path / SYNTHESIS_DIR
    assert {item.name for item in campaign_dir.iterdir()} == set(load_campaign(CAMPAIGN_FILE)["outputs"]["campaign_files"])
    hypotheses = json.loads((campaign_dir / "hypotheses.json").read_text())
    assert hypotheses[0]["supporting_evidence"] == ["q01/q1/c1", "q02/q1/c1~2"]
    report = (campaign_dir / "report.md").read_text()
    assert "Partial synthesis" in report and "q01/q1/c1, q02/q1/c1~2" in report
    ledger = json.loads((campaign_dir / "evidence_ledger.json").read_text())
    assert {item["ref"] for item in ledger["claims"]} == {"q01/q1/c1", "q01/q1/c1~2", "q02/q1/c1", "q02/q1/c1~2"}
    manifest = json.loads(manifest_path.read_text())
    assert manifest["kind"] == "synthesis"
    assert manifest["status"] == "completed"
    assert manifest["partial"] is True
    assert [item["id"] for item in manifest["inputs"]] == ["q01", "q02"]
    assert manifest["mixed_question_configs"] is False
    assert manifest["synthesis_route"]["settings"]["max_tokens"] == 48_000
    assert manifest["hypothesis_count"] == 1


def test_pilot_spec_matches_campaign_except_identity_and_questions() -> None:
    campaign = load_campaign(CAMPAIGN_FILE)
    pilot = load_campaign(CAMPAIGN_FILE.with_name("pilot.toml"))
    identity = {"id", "title", "status", "questions"}
    assert {key: value for key, value in pilot.items() if key not in identity} == {
        key: value for key, value in campaign.items() if key not in identity
    }
    assert [item["id"] for item in pilot["questions"]] == ["p01"]


@pytest.mark.asyncio
async def test_campaign_applies_reserve_scout_tokens_salvage_and_notes(monkeypatch, tmp_path: Path) -> None:
    from research_loop.campaign import run_campaign
    from research_loop.schemas import ResearchRole

    seen = {}

    class RecordingLoop:
        def __init__(self, policy, config, **_kwargs):
            seen["policy"], seen["config"] = policy, config

        async def run(self, _objective, *, constraints):
            seen["notes"] = constraints.notes
            return SimpleNamespace(job_id=uuid4(), report=FinalReport(answer="Draft"), ledger=_ledger("q01"),
                                   verification=VerificationReport(), cost_usd=None)

    monkeypatch.setattr("research_loop.campaign.ResearchLoop", RecordingLoop)
    await run_campaign(CAMPAIGN_FILE, question_ids=["q01"], policy_name="quality",
                       settings=ResearchSettings.from_env({}), output_dir=tmp_path, persist=False)
    execution = load_campaign(CAMPAIGN_FILE)["execution"]
    policy = seen["policy"]
    assert seen["config"].salvage_exhausted_research is True
    assert policy.job_reserve_usd == execution["question_reserve_usd"]
    assert policy.for_role(ResearchRole.SCOUT).total_tokens_limit == execution["scout_total_tokens_limit"]
    assert policy.cheap_scout.total_tokens_limit == execution["scout_total_tokens_limit"]
    scout = policy.for_role(ResearchRole.SCOUT)
    assert (scout.max_requests, scout.max_tool_calls) == (execution["scout_max_requests"], execution["scout_max_tool_calls"])
    assert policy.for_role(ResearchRole.DEEP_DIVE).cost_limit == execution["deep_dive_cost_limit_usd"]
    assert seen["config"].max_deep_dives_per_round == execution["max_deep_dives_per_round"]
    assert seen["config"].max_parallel_deep_dives == execution["max_parallel_deep_dives"]
    assert seen["notes"] == execution["research_notes"]


def test_question_report_carries_caveats_and_verifier_findings() -> None:
    from research_loop.campaign import render_question_report
    from research_loop.schemas import ClaimCheck

    report = FinalReport(answer="## Summary\n\nSWE-bench has 2,294 tasks.", caveats=["Dataset card was not version-pinned."])
    verification = VerificationReport(needs_research=True, checks=[
        ClaimCheck(statement="Task count", claim_ids=["q1/c1"], supported=True, severity="none", explanation="ok"),
        ClaimCheck(statement="Lite excluded repo", claim_ids=[], supported=False, severity="minor", explanation="unconfirmed"),
        ClaimCheck(statement="OpenAI audit figures", claim_ids=["q3/c4"], supported=False, severity="major",
                   explanation="secondary sources only"),
    ])
    text = render_question_report(report, verification)
    assert text.startswith("## Summary")
    assert "## Caveats\n\n- Dataset card was not version-pinned." in text
    assert "checked 3 statements: 1 supported, 2 not supported (1 major)" in text
    assert "unresolved" in text
    assert text.index("OpenAI audit figures") < text.index("Lite excluded repo")  # major first
    assert "**[major, not supported]** OpenAI audit figures (claims: q3/c4): secondary sources only" in text
    assert "Task count" not in text  # supported, non-major checks stay in verification.json


def _quoted_ledger() -> EvidenceLedger:
    source = SourceRef(url="https://example.org/paper", title="Paper", source_type="paper")
    ledger = EvidenceLedger()
    ledger.add(ResearchResult(question_id="q1", question="size", conclusion="c", confidence=0.8, claims=[
        Claim(id="c1", statement="Verified size", confidence=0.8, evidence=[
            Evidence(source=source, excerpt="size", quote="2,294 tasks", quote_check="verified", confidence=0.8)]),
        Claim(id="c2", statement="Invented size", confidence=0.8, evidence=[
            Evidence(source=source, excerpt="size", quote="4,000 tasks", quote_check="not_found", confidence=0.8),
            Evidence(source=source, excerpt="paraphrase only", confidence=0.8)]),
    ]))
    return ledger


def test_question_report_counts_quotes_not_found_in_tool_output() -> None:
    from research_loop.campaign import render_question_report

    text = render_question_report(FinalReport(answer="Answer"), VerificationReport(), _quoted_ledger())
    assert "1 of 2 quoted passages was not found in any text the research tools returned (claims: q1/c2)." in text
    assert "quoted passages" not in render_question_report(FinalReport(answer="Answer"), VerificationReport())


@pytest.mark.asyncio
async def test_synthesis_prompt_marks_quotes_not_found(monkeypatch, tmp_path: Path) -> None:
    from research_loop.campaign import aggregate_campaign, run_campaign, synthesis_prompt

    class QuotingLoop:
        def __init__(self, *_args, **_kwargs):
            pass

        async def run(self, _objective, **_kwargs):
            return SimpleNamespace(job_id=uuid4(), report=FinalReport(answer="Draft"), ledger=_quoted_ledger(),
                                   verification=VerificationReport(), cost_usd=None)

    monkeypatch.setattr("research_loop.campaign.ResearchLoop", QuotingLoop)
    await run_campaign(CAMPAIGN_FILE, question_ids=["q01"], policy_name="quality",
                       settings=ResearchSettings.from_env({}), output_dir=tmp_path, persist=False)
    campaign = load_campaign(CAMPAIGN_FILE)
    evidence = {item["ref"]: item["evidence"] for item in
                json.loads(synthesis_prompt(campaign, aggregate_campaign(campaign, tmp_path)))["evidence"]}
    assert [item.get("quote_check") for item in evidence["q01/q1/c2"]] == ["not_found", None]
    assert evidence["q01/q1/c1"][0]["quote_check"] == "verified"


@pytest.mark.asyncio
async def test_cancelled_campaign_run_marks_its_manifest_failed(monkeypatch, tmp_path: Path) -> None:
    import asyncio

    from research_loop.campaign import run_campaign

    started = asyncio.Event()

    class HangingLoop:
        def __init__(self, *_args, **_kwargs):
            pass

        async def run(self, _objective, **_kwargs):
            started.set()
            await asyncio.Event().wait()

    monkeypatch.setattr("research_loop.campaign.ResearchLoop", HangingLoop)
    running = asyncio.create_task(run_campaign(
        CAMPAIGN_FILE, question_ids=["q01"], policy_name="quality",
        settings=ResearchSettings.from_env({}), output_dir=tmp_path, persist=False,
    ))
    await asyncio.wait_for(started.wait(), timeout=15)
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    (manifest_path,) = (tmp_path / "manifests").iterdir()
    manifest = json.loads(manifest_path.read_text())
    assert (manifest["status"], manifest["error"]) == ("failed", "CancelledError")
    assert manifest["finished_at"]

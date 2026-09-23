from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from research_loop.ledger import EvidenceLedger
from research_loop.long_horizon import SPEC_FILE, load_spec, render_objective
from research_loop.schemas import (
    Claim,
    Contradiction,
    Evidence,
    FinalReport,
    Hypothesis,
    LongHorizonFindings,
    ReportClaim,
    ResearchResult,
    SourceRef,
    VerificationReport,
)
from research_loop.settings import ResearchSettings


def test_long_horizon_spec_has_unique_questions_and_reproducible_scope() -> None:
    spec = load_spec(SPEC_FILE)
    assert spec["graph_version"] == "research-graph-v1"
    assert len(spec["questions"]) == 11
    assert len({item["id"] for item in spec["questions"]}) == 11
    objective = render_objective(spec, spec["questions"][0])
    assert "preprints" in objective
    assert "Question q01" in objective
    assert "benchmark_claims" in objective


def test_long_horizon_spec_matches_synthesis_schema() -> None:
    outputs = load_spec(SPEC_FILE)["outputs"]
    assert outputs["findings_sections"] == list(LongHorizonFindings.model_fields)
    assert set(outputs["hypothesis_fields"]) <= set(Hypothesis.model_fields)


def test_long_horizon_rejects_duplicate_question_ids(tmp_path: Path) -> None:
    spec = tmp_path / "bad.toml"
    spec.write_text('graph_version = "research-graph-v1"\n[[questions]]\nid = "q1"\ntext = "first"\n[[questions]]\nid = "q1"\ntext = "second"\n')
    with pytest.raises(ValueError, match="unique"):
        load_spec(spec)


@pytest.mark.parametrize("question_id", [".", "..", "synthesis", "manifests"])
def test_long_horizon_rejects_question_ids_that_are_not_their_own_folder(tmp_path: Path, question_id: str) -> None:
    spec = tmp_path / "unsafe.toml"
    spec.write_text(f'graph_version = "research-graph-v1"\n[[questions]]\nid = "{question_id}"\ntext = "first"\n')
    with pytest.raises(ValueError, match="cannot be"):
        load_spec(spec)


def test_long_horizon_loads_synthesis_limits_that_cannot_fit_a_retry(tmp_path: Path) -> None:
    # Only --synthesize checks a retry, against its actual prompt, so questions still run.
    spec = tmp_path / "wide.toml"
    spec.write_text(SPEC_FILE.read_text().replace("max_output_tokens = 36_000", "max_output_tokens = 480_000"))
    assert load_spec(spec)["synthesis"]["max_output_tokens"] == 480_000


def test_long_horizon_requires_question_cost_cap(tmp_path: Path) -> None:
    spec = tmp_path / "uncapped.toml"
    spec.write_text('graph_version = "research-graph-v1"\n[execution]\nmax_parallel_scouts = 1\n[[questions]]\nid = "q1"\ntext = "first"\n')
    with pytest.raises(ValueError, match="question_cost_limit_usd"):
        load_spec(spec)


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
    # A deep dive may reuse a claim ID; long-horizon refs must still be unique.
    ledger.add(ResearchResult(
        question_id="q1", question="definitions", conclusion="Deeper finding",
        claims=[Claim(id="c1", statement=f"{question_id} deep-dive finding",
                      evidence=[Evidence(source=journal, excerpt="Published evidence", confidence=0.9)], confidence=0.9)],
        confidence=0.9,
    ))
    return ledger


async def _complete_questions(monkeypatch, output: Path, question_ids: list[str],
                              verification: VerificationReport | None = None, report_for=None) -> Path:
    from research_loop.long_horizon import run_long_horizon

    class FakeLoop:
        def __init__(self, *_args, **_kwargs):
            pass

        async def run(self, objective, **_kwargs):
            question_id = next(qid for qid in question_ids if f"Question {qid}:" in objective)
            report = report_for(question_id) if report_for else FinalReport(
                answer=f"Draft report {question_id}", caveats=["Thin"])
            return SimpleNamespace(job_id=uuid4(), report=report,
                                   ledger=_ledger(question_id), verification=verification or VerificationReport(),
                                   cost_usd=Decimal("1.25"))

    monkeypatch.setattr("research_loop.long_horizon.ResearchLoop", FakeLoop)
    return await run_long_horizon(
        SPEC_FILE, question_ids=question_ids, policy_name="quality",
        settings=ResearchSettings.from_env({}), output_dir=output, persist=False,
    )


@pytest.mark.asyncio
async def test_long_horizon_run_exports_spec_question_files(monkeypatch, tmp_path: Path) -> None:
    manifest_path = await _complete_questions(monkeypatch, tmp_path, ["q01"])
    folder = tmp_path / "q01"
    assert {item.name for item in folder.iterdir()} == set(load_spec(SPEC_FILE)["outputs"]["question_files"])
    assert "Draft report q01" in (folder / "report.md").read_text()
    assert json.loads((folder / "report.json").read_text())["caveats"] == ["Thin"]
    assert "preprint" in (folder / "bibliography.json").read_text()
    run = json.loads((folder / "run.json").read_text())
    manifest = json.loads(manifest_path.read_text())
    assert manifest_path.parent == tmp_path / "manifests"
    assert manifest["status"] == "completed"
    assert manifest["run_limits"]["question_cost_limit_usd"] == 5.0
    assert manifest["acquisition"]["fetch_version"] == 3
    assert manifest["evidence_version"] == 4
    assert manifest["questions"][0]["cost_usd"] == "1.25"
    assert run["status"] == "completed"
    assert run["review_reasons"] == manifest["questions"][0]["review_reasons"] == ["the report cites no evidence claims"]
    assert run["experiment_id"] == manifest["experiment_id"]
    assert run["config_fingerprint"] == manifest["config_fingerprint"]


@pytest.mark.asyncio
async def test_long_horizon_manifest_records_failed_question(monkeypatch, tmp_path: Path) -> None:
    from research_loop.async_orchestrator import JobBudgetExceeded
    from research_loop.long_horizon import run_long_horizon

    class OverBudgetLoop:
        def __init__(self, *_args, **_kwargs):
            pass

        async def run(self, _objective, **_kwargs):
            raise JobBudgetExceeded("job cost cap reached")

    monkeypatch.setattr("research_loop.long_horizon.ResearchLoop", OverBudgetLoop)
    manifest_path = await run_long_horizon(
        SPEC_FILE, question_ids=["q01"], policy_name="quality",
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
async def test_long_horizon_continues_past_a_failed_question(monkeypatch, tmp_path: Path) -> None:
    from research_loop.long_horizon import run_long_horizon

    monkeypatch.setattr("research_loop.long_horizon.ResearchLoop", _loop_failing_on({"q01"}))
    manifest_path = await run_long_horizon(
        SPEC_FILE, question_ids=["q01", "q02"], policy_name="quality",
        settings=ResearchSettings.from_env({}), output_dir=tmp_path, persist=False,
    )
    manifest = json.loads(manifest_path.read_text())
    assert manifest["status"] == "completed_with_failures"
    assert [(item["id"], item["status"]) for item in manifest["questions"]] == [("q01", "failed"), ("q02", "completed")]
    assert "not_run" not in manifest
    assert (tmp_path / "q02" / "run.json").exists() and not (tmp_path / "q01").exists()


@pytest.mark.asyncio
async def test_long_horizon_stops_once_failures_suggest_a_shared_cause(monkeypatch, tmp_path: Path) -> None:
    from research_loop.long_horizon import run_long_horizon

    assert load_spec(SPEC_FILE)["execution"]["max_failed_questions"] == 2
    monkeypatch.setattr("research_loop.long_horizon.ResearchLoop", _loop_failing_on({"q01", "q02", "q03"}))
    manifest_path = await run_long_horizon(
        SPEC_FILE, question_ids=["q01", "q02", "q03", "q04"], policy_name="quality",
        settings=ResearchSettings.from_env({}), output_dir=tmp_path, persist=False,
    )
    manifest = json.loads(manifest_path.read_text())
    assert manifest["status"] == "failed"
    assert [item["id"] for item in manifest["questions"]] == ["q01", "q02"]
    assert manifest["not_run"] == ["q03", "q04"]


def test_long_horizon_cli_exits_nonzero_and_names_failed_questions(monkeypatch, tmp_path: Path, capsys) -> None:
    import sys

    from research_loop import long_horizon

    settings = ResearchSettings.from_env({})
    monkeypatch.setattr(long_horizon, "ResearchSettings", SimpleNamespace(from_env=lambda: settings))
    monkeypatch.setattr(long_horizon, "_paid_preflight", lambda *_args: None)
    monkeypatch.setattr(long_horizon, "ResearchLoop", _loop_failing_on({"q01"}))
    monkeypatch.setattr(sys, "argv", ["research-long-horizon", "--question", "q01", "--paid", "--output", str(tmp_path)])
    with pytest.raises(SystemExit) as exc:
        long_horizon.main()
    assert exc.value.code == 1
    assert "q01 (JobBudgetExceeded)" in capsys.readouterr().err


def test_long_horizon_rejects_a_nonpositive_failure_limit(tmp_path: Path) -> None:
    spec = tmp_path / "limit.toml"
    spec.write_text(SPEC_FILE.read_text().replace("max_failed_questions = 2", "max_failed_questions = 0"))
    with pytest.raises(ValueError, match="max_failed_questions"):
        load_spec(spec)


@pytest.mark.asyncio
async def test_aggregate_assigns_unique_refs_and_keeps_source_versions(monkeypatch, tmp_path: Path) -> None:
    from research_loop.long_horizon import aggregate_long_horizon

    await _complete_questions(monkeypatch, tmp_path, ["q01", "q02"])
    evidence = aggregate_long_horizon(load_spec(SPEC_FILE), tmp_path)
    assert [item.question["id"] for item in evidence.completed] == ["q01", "q02"]
    assert evidence.missing == [f"q{index:02d}" for index in range(3, 12)]
    assert evidence.refs == {"q01/q1/c1", "q01/q1/c1~2", "q02/q1/c1", "q02/q1/c1~2"}
    assert evidence.contradictions["q01"] == [{"description": "Sources disagree", "claim_refs": ["q01/q1/c1"]}]
    # Preprint and journal records stay distinct; each lists every question that cited it.
    assert sorted(item["publication_status"] for item in evidence.bibliography) == ["journal", "preprint"]
    assert all(item["question_ids"] == ["q01", "q02"] for item in evidence.bibliography)


@pytest.mark.asyncio
async def test_synthesis_prompt_carries_verifier_findings(monkeypatch, tmp_path: Path) -> None:
    from research_loop.long_horizon import aggregate_long_horizon, synthesis_prompt
    from research_loop.schemas import ClaimCheck

    verification = VerificationReport(needs_research=True, checks=[
        ClaimCheck(statement="Scout finding holds", claim_ids=["q1/c1"], supported=True, severity="none",
                   explanation="ok"),
        ClaimCheck(statement="Deep-dive finding holds", claim_ids=["q1/c1~2", "q9/c9"], supported=False,
                   severity="major", explanation="excerpt does not say this"),
    ])
    await _complete_questions(monkeypatch, tmp_path, ["q01"], verification)
    spec = load_spec(SPEC_FILE)
    payload = json.loads(synthesis_prompt(spec, aggregate_long_horizon(spec, tmp_path)))
    # Only flagged checks reach the prompt, with ledger IDs mapped to long-horizon refs.
    question = payload["questions"][0]
    assert question["verification"] == {
        "checked": 2, "not_supported": 1, "major": 1, "needs_research": True,
        "findings": [{"statement": "Deep-dive finding holds", "supported": False, "severity": "major",
                      "explanation": "excerpt does not say this", "claim_refs": ["q01/q1/c1~2"]}],
    }
    assert question["caveats"] == ["Thin"]
    assert question["contradictions"] == [{"description": "Sources disagree", "claim_refs": ["q01/q1/c1"]}]
    assert "answer" not in question
    # Each distinct unresolved question from the ledger, once.
    assert question["unresolved_questions"] == ["What remains open"]
    assert "report" not in question


@pytest.mark.asyncio
async def test_aggregate_skips_outputs_that_answered_a_different_objective(monkeypatch, tmp_path: Path) -> None:
    from research_loop.long_horizon import aggregate_long_horizon

    await _complete_questions(monkeypatch, tmp_path, ["q01", "q02"])
    spec = load_spec(SPEC_FILE)
    spec["questions"][1]["text"] = "A reworded question the stored evidence never answered"
    evidence = aggregate_long_horizon(spec, tmp_path)
    assert [item.question["id"] for item in evidence.completed] == ["q01"]
    assert evidence.stale == ["q02"]
    assert "q02" in evidence.missing

    # A run recorded without an objective hash cannot show what it answered.
    run_path = tmp_path / "q01" / "run.json"
    run = json.loads(run_path.read_text())
    del run["objective_sha256"]
    run_path.write_text(json.dumps(run))
    assert aggregate_long_horizon(load_spec(SPEC_FILE), tmp_path).stale == ["q01"]


@pytest.mark.asyncio
async def test_synthesis_refuses_partial_long_horizon_and_oversized_prompt(monkeypatch, tmp_path: Path) -> None:
    from research_loop.long_horizon import prepare_synthesis

    with pytest.raises(ValueError, match="no completed"):
        prepare_synthesis(SPEC_FILE, tmp_path, allow_partial=True)
    await _complete_questions(monkeypatch, tmp_path, ["q01"])
    with pytest.raises(ValueError, match="--allow-partial"):
        prepare_synthesis(SPEC_FILE, tmp_path, allow_partial=False)
    tiny = tmp_path / "tiny.toml"
    tiny.write_text(SPEC_FILE.read_text().replace("max_prompt_chars = 600_000", "max_prompt_chars = 100"))
    with pytest.raises(ValueError, match="max_prompt_chars"):
        prepare_synthesis(tiny, tmp_path, allow_partial=True)
    from research_loop.policy import ModelRoute

    # A prompt under the character cap is still refused when the route cannot fit one retry.
    tight = ModelRoute("anthropic:claude-opus-5", 3, 1, 1_000, settings={"max_tokens": 36_000})
    with pytest.raises(ValueError, match="one retry"):
        prepare_synthesis(SPEC_FILE, tmp_path, allow_partial=True, route=tight)


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
async def test_synthesis_retries_unknown_refs_and_writes_synthesis_files(monkeypatch, tmp_path: Path) -> None:
    from pydantic_ai.messages import ModelResponse, ToolCallPart
    from pydantic_ai.models.function import AgentInfo, FunctionModel

    from research_loop.agents import long_horizon_synthesizer_agent
    from research_loop.long_horizon import SYNTHESIS_DIR, synthesize_long_horizon

    def report_for(question_id: str) -> FinalReport:
        return FinalReport(
            answer=f"Draft report {question_id}", caveats=["Thin"],
            claims=[
                ReportClaim(statement=f"{question_id} scout finding", claim_ids=["q1/c1"]),
                ReportClaim(statement=f"{question_id} deep-dive finding", claim_ids=["q1/c1~2"]),
            ],
        )

    await _complete_questions(monkeypatch, tmp_path, ["q01", "q02"], report_for=report_for)
    monkeypatch.undo()  # the synthesis job uses the real ResearchLoop
    prompts: list[str] = []

    def respond(messages, info: AgentInfo) -> ModelResponse:
        prompts.append(str(messages[-1].parts[-1].content))
        refs = ["q01/q1/c1", "q07/c9"] if len(prompts) == 1 else ["q01/q1/c1", "q02/q1/c1~2"]
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, _synthesis_body(refs))])

    with long_horizon_synthesizer_agent.override(model=FunctionModel(respond)):
        manifest_path = await synthesize_long_horizon(
            SPEC_FILE, policy_name="quality", settings=ResearchSettings.from_env({}),
            output_dir=tmp_path, persist=False, allow_partial=True,
        )

    assert len(prompts) == 2
    assert "q02/q1/c1~2" in prompts[0]
    assert "excerpt" not in prompts[0]
    assert "q07/c9" in prompts[1]  # the retry names the invented ref
    long_horizon_dir = tmp_path / SYNTHESIS_DIR
    assert {item.name for item in long_horizon_dir.iterdir()} == set(load_spec(SPEC_FILE)["outputs"]["synthesis_files"])
    hypotheses = json.loads((long_horizon_dir / "hypotheses.json").read_text())
    assert hypotheses[0]["supporting_evidence"] == ["q01/q1/c1", "q02/q1/c1~2"]
    report = (long_horizon_dir / "report.md").read_text()
    assert "Partial synthesis" in report and "q01/q1/c1, q02/q1/c1~2" in report
    ledger = json.loads((long_horizon_dir / "evidence_ledger.json").read_text())
    assert {item["ref"] for item in ledger["claims"]} == {"q01/q1/c1", "q01/q1/c1~2", "q02/q1/c1", "q02/q1/c1~2"}
    manifest = json.loads(manifest_path.read_text())
    assert manifest["kind"] == "synthesis"
    assert manifest["status"] == "completed"
    assert manifest["partial"] is True
    assert [item["id"] for item in manifest["inputs"]] == ["q01", "q02"]
    assert manifest["mixed_question_configs"] is False
    assert manifest["uncited_question_ids"] == []
    assert manifest["synthesis_route"]["settings"]["max_tokens"] == 36_000
    assert manifest["hypothesis_count"] == 1


def test_pilot_spec_matches_long_horizon_except_identity_and_questions() -> None:
    spec = load_spec(SPEC_FILE)
    pilot = load_spec(SPEC_FILE.with_name("pilot.toml"))
    identity = {"id", "title", "status", "questions"}
    assert {key: value for key, value in pilot.items() if key not in identity} == {
        key: value for key, value in spec.items() if key not in identity
    }
    assert [item["id"] for item in pilot["questions"]] == ["p01"]


@pytest.mark.asyncio
async def test_long_horizon_applies_reserve_scout_tokens_salvage_and_notes(monkeypatch, tmp_path: Path) -> None:
    from research_loop.long_horizon import run_long_horizon
    from research_loop.schemas import ResearchRole

    seen = {}

    class RecordingLoop:
        def __init__(self, policy, config, **kwargs):
            seen["policy"], seen["config"], seen["settings"] = policy, config, kwargs.get("settings")

        async def run(self, _objective, *, constraints):
            seen["notes"] = constraints.notes
            return SimpleNamespace(job_id=uuid4(), report=FinalReport(answer="Draft"), ledger=_ledger("q01"),
                                   verification=VerificationReport(), cost_usd=None)

    monkeypatch.setattr("research_loop.long_horizon.ResearchLoop", RecordingLoop)
    settings = ResearchSettings.from_env({})
    await run_long_horizon(SPEC_FILE, question_ids=["q01"], policy_name="quality",
                       settings=settings, output_dir=tmp_path, persist=False)
    assert seen["settings"] is settings  # the loop uses the study's settings, not a fresh environment read
    execution = load_spec(SPEC_FILE)["execution"]
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
    assert seen["config"].max_run_seconds == execution["question_timeout_seconds"] == 3600
    assert seen["notes"] == execution["research_notes"]


def test_question_report_carries_caveats_and_verifier_findings() -> None:
    from research_loop.long_horizon import render_question_report
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
            Evidence(source=source, excerpt="size", quote="2,294 tasks", quote_check="verified",
                     source_check="observed", confidence=0.8)]),
        Claim(id="c2", statement="Invented size", confidence=0.8, evidence=[
            Evidence(source=source, excerpt="size", quote="4,000 tasks", quote_check="not_found",
                     source_check="not_found", confidence=0.8),
            Evidence(source=source, excerpt="paraphrase only", confidence=0.8)]),
    ]))
    return ledger


def test_question_report_counts_quotes_not_found_in_tool_output() -> None:
    from research_loop.long_horizon import render_question_report

    text = render_question_report(FinalReport(answer="Answer"), VerificationReport(), _quoted_ledger())
    assert "1 of 2 quoted passages was not found in any text the research tools returned (claims: q1/c2)." in text
    assert "1 of 2 cited sources was not found in any text the research tools returned (claims: q1/c2)." in text
    assert "quoted passages" not in render_question_report(FinalReport(answer="Answer"), VerificationReport())


@pytest.mark.asyncio
async def test_synthesis_prompt_marks_quotes_not_found(monkeypatch, tmp_path: Path) -> None:
    from research_loop.long_horizon import (
        aggregate_long_horizon,
        run_long_horizon,
        synthesis_prompt,
    )

    class QuotingLoop:
        def __init__(self, *_args, **_kwargs):
            pass

        async def run(self, _objective, **_kwargs):
            return SimpleNamespace(
                job_id=uuid4(),
                report=FinalReport(answer="Draft", claims=[
                    ReportClaim(statement="Verified size", claim_ids=["q1/c1"]),
                    ReportClaim(statement="Invented size", claim_ids=["q1/c2"]),
                ]),
                ledger=_quoted_ledger(), verification=VerificationReport(), cost_usd=None,
            )

    monkeypatch.setattr("research_loop.long_horizon.ResearchLoop", QuotingLoop)
    await run_long_horizon(SPEC_FILE, question_ids=["q01"], policy_name="quality",
                       settings=ResearchSettings.from_env({}), output_dir=tmp_path, persist=False)
    spec = load_spec(SPEC_FILE)
    claims = json.loads(synthesis_prompt(spec, aggregate_long_horizon(spec, tmp_path)))["questions"][0]["claims"]
    by_ref = {ref: item for item in claims for ref in item["claim_refs"]}
    assert by_ref["q01/q1/c2"]["quote_check"] == "not_found"
    assert by_ref["q01/q1/c2"]["source_check"] == "not_found"
    assert "quote_check" not in by_ref["q01/q1/c1"]
    assert "source_check" not in by_ref["q01/q1/c1"]
    assert "excerpt" not in json.dumps(claims)
    assert by_ref["q01/q1/c1"]["source_count"] == 1
    assert by_ref["q01/q1/c2"]["source_count"] == 1


def test_prompt_claims_count_distinct_sources_and_flag_the_whole_claim() -> None:
    from research_loop.long_horizon import _prompt_claims

    paper = {"url": "https://example.org/a", "title": "A", "source_type": "paper", "publication_status": "preprint"}
    same_type = {"url": "https://example.org/b", "title": "B", "source_type": "paper", "publication_status": "unknown"}
    retracted = {
        "url": "https://example.org/c", "title": "C", "source_type": "paper",
        "publication_status": "journal", "is_retracted": True,
    }
    claims = [
        {"question_id": "q01", "ref": "q01/q1/c1", "claim": {"id": "c1", "evidence": [
            {"source": paper}, {"source": same_type},
        ]}},
        {"question_id": "q01", "ref": "q01/q1/c2", "claim": {"id": "c2", "evidence": [
            {"source": paper, "quote_check": "verified"},
            {"source": retracted, "quote_check": "not_found"},
        ]}},
    ]
    report = FinalReport(answer="Report prose stays out of the prompt", claims=[
        ReportClaim(statement="Two papers", claim_ids=["c1"]),
        ReportClaim(statement="Mixed evidence", claim_ids=["c2"]),
    ])
    rows = _prompt_claims("q01", report, claims)
    assert rows[0]["source_count"] == 2
    assert rows[0]["source_types"] == ["paper"]
    assert rows[0]["publication_statuses"] == ["preprint"]
    assert "quote_check" not in rows[0]
    assert "retracted" not in rows[0]
    assert rows[1]["source_count"] == 2
    assert rows[1]["quote_check"] == "not_found"
    assert rows[1]["retracted"] is True
    assert rows[1]["publication_statuses"] == ["journal", "preprint"]
    assert "answer" not in rows[0]


def test_prompt_claims_count_works_that_support_the_claim() -> None:
    from research_loop.long_horizon import _prompt_claims

    paper = {"url": "https://example.org/a", "title": "A", "source_type": "paper", "publication_status": "journal"}
    claims = [{"question_id": "q01", "ref": "q01/q1/c1", "claim": {"id": "c1", "evidence": [
        # One work: another page, another provider and fetch time, a DOI link, a reworded title.
        {"source": paper | {"locator": "p. 3", "doi": "10.1/A", "provider": "openalex"}},
        {"source": paper | {"locator": "p. 7", "accessed_at": "2026-09-02", "title": "A (journal)"}},
        {"source": {"url": "https://doi.org/10.1/a", "title": "A", "provider": "crossref"}},
        # The query names the work, so these are two more.
        {"source": {"url": "https://openreview.net/forum?id=X", "title": "X", "publication_status": "preprint"},
         "supports": False},
        {"source": {"url": "https://openreview.net/forum?id=Y", "title": "Y", "source_type": "secondary"},
         "supports": False},
    ]}}]
    report = FinalReport(answer="Report", claims=[ReportClaim(statement="Disputed", claim_ids=["c1"])])
    (row,) = _prompt_claims("q01", report, claims)
    assert row["source_count"] == 1
    assert row["source_ids"] == ["s1"]
    assert row["contradicting_source_count"] == 2
    assert row["contradicting_source_ids"] == ["s2", "s3"]
    # Types and statuses describe the supporting sources only.
    assert row["source_types"] == ["paper", "unknown"]
    assert row["publication_statuses"] == ["journal"]

    supported = FinalReport(answer="Report", claims=[ReportClaim(statement="Clean", claim_ids=["c1"])])
    clean = [{**claims[0], "claim": {"id": "c1", "evidence": claims[0]["claim"]["evidence"][:1]}}]
    assert "contradicting_source_count" not in _prompt_claims("q01", supported, clean)[0]


def test_prompt_source_table_lists_each_work_once_across_questions() -> None:
    from research_loop.long_horizon import _prompt_claims, _Works

    paper = {"url": "https://example.org/a", "title": "A", "published_at": "2026-03-01"}
    claims = [
        {"question_id": "q01", "ref": "q01/q1/c1", "claim": {"id": "c1", "confidence": 0.8, "evidence": [
            {"source": paper | {"locator": "p. 3"}},
        ]}},
        {"question_id": "q01", "ref": "q01/q1/c2", "claim": {"id": "c2", "confidence": 0.4, "evidence": [
            {"source": {"url": "https://arxiv.org/abs/2601.00001", "title": "A preprint"}},
        ]}},
        # A later question cites the same work by URL, and links it to the arXiv id above.
        {"question_id": "q02", "ref": "q02/q1/c1", "claim": {"id": "c1", "confidence": 0.9, "evidence": [
            {"source": paper | {"arxiv_id": "2601.00001"}},
        ]}},
    ]
    works = _Works(claims)
    (first,) = _prompt_claims("q01", FinalReport(answer="", claims=[
        ReportClaim(statement="Both", claim_ids=["c1", "c2"])]), claims, works)
    (second,) = _prompt_claims("q02", FinalReport(answer="", claims=[
        ReportClaim(statement="Again", claim_ids=["c1"])]), claims, works)
    assert first["source_ids"] == second["source_ids"] == ["s1"]
    assert first["source_count"] == 1
    assert first["min_confidence"] == 0.4
    assert works.rows == [{"id": "s1", "title": "A", "url": "https://example.org/a", "published_at": "2026-03-01"}]


@pytest.mark.asyncio
async def test_a_report_citing_no_claims_is_named_in_the_synthesis_inputs(monkeypatch, tmp_path: Path) -> None:
    from research_loop.long_horizon import (
        aggregate_long_horizon,
        render_long_horizon_report,
    )
    from research_loop.schemas import LongHorizonSynthesis

    def report_for(question_id: str) -> FinalReport:
        claims = [] if question_id == "q02" else [ReportClaim(statement="cited", claim_ids=["q1/c1"])]
        return FinalReport(answer=f"Draft report {question_id}", claims=claims)

    await _complete_questions(monkeypatch, tmp_path, ["q01", "q02"], report_for=report_for)
    spec = load_spec(SPEC_FILE)
    evidence = aggregate_long_horizon(spec, tmp_path)
    assert evidence.uncited == ["q02"]
    synthesis = LongHorizonSynthesis(summary="s", findings=LongHorizonFindings())
    assert "contributed none: q02." in render_long_horizon_report(spec, evidence, synthesis)


def test_long_horizon_instruction_omits_unknown_statuses_from_a_present_list() -> None:
    from research_loop.agents import INSTRUCTIONS

    text = INSTRUCTIONS["long_horizon_synthesizer"]
    assert "Unknown statuses are left out of a list that is present" in text
    assert "a listed status is not the status of every source" in text


@pytest.mark.asyncio
async def test_repeated_stored_claim_id_keeps_every_copy(monkeypatch, tmp_path: Path) -> None:
    from research_loop.experiment import file_sha256
    from research_loop.long_horizon import aggregate_long_horizon, synthesis_prompt
    from research_loop.schemas import ClaimCheck

    await _complete_questions(monkeypatch, tmp_path, ["q01"])
    folder = tmp_path / "q01"
    tainted = SourceRef(url="https://example.org/tainted", title="Tainted", source_type="paper", publication_status="preprint")
    clean = SourceRef(url="https://example.org/clean", title="Clean", source_type="paper", publication_status="unknown")
    shared = "q1/c1"
    ledger = {
        "q1": [
            ResearchResult(
                question_id="q1", question="definitions", conclusion="tainted", confidence=0.4,
                claims=[Claim(id=shared, statement="tainted copy", confidence=0.4, evidence=[
                    Evidence(source=tainted, excerpt="bad", quote_check="not_found", confidence=0.4),
                ])],
                contradictions=[Contradiction(description="both copies", claim_ids=[shared])],
            ).model_dump(mode="json"),
            ResearchResult(
                question_id="q1", question="definitions", conclusion="clean", confidence=0.8,
                claims=[Claim(id=shared, statement="clean copy", confidence=0.8, evidence=[
                    Evidence(source=clean, excerpt="ok", confidence=0.8),
                ])],
            ).model_dump(mode="json"),
        ],
    }
    (folder / "evidence_ledger.json").write_text(json.dumps(ledger) + "\n", encoding="utf-8")
    report = FinalReport(answer="Report prose stays in report.json", caveats=["Thin"], claims=[
        ReportClaim(statement="Uses both copies", claim_ids=[shared]),
    ])
    (folder / "report.json").write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    verification = VerificationReport(checks=[
        ClaimCheck(statement="The shared id is tainted", claim_ids=[shared], supported=False,
                   severity="major", explanation="first copy was not found"),
    ])
    (folder / "verification.json").write_text(verification.model_dump_json(indent=2) + "\n", encoding="utf-8")
    run = json.loads((folder / "run.json").read_text(encoding="utf-8"))
    run["files"] = {name: file_sha256(folder / name) for name in run["files"]}
    (folder / "run.json").write_text(json.dumps(run) + "\n", encoding="utf-8")

    spec = load_spec(SPEC_FILE)
    evidence = aggregate_long_horizon(spec, tmp_path)
    both = ["q01/q1/c1", "q01/q1/c1~2"]
    assert evidence.contradictions["q01"] == [{"description": "both copies", "claim_refs": both}]
    assert evidence.verification["q01"]["findings"][0]["claim_refs"] == both
    row = json.loads(synthesis_prompt(spec, evidence))["questions"][0]["claims"][0]
    assert row["claim_refs"] == both
    assert row["source_count"] == 2
    assert row["quote_check"] == "not_found"
    assert row["publication_statuses"] == ["preprint"]


@pytest.mark.asyncio
async def test_cancelled_long_horizon_run_marks_its_manifest_failed(monkeypatch, tmp_path: Path) -> None:
    import asyncio

    from research_loop.long_horizon import run_long_horizon

    started = asyncio.Event()

    class HangingLoop:
        def __init__(self, *_args, **_kwargs):
            pass

        async def run(self, _objective, **_kwargs):
            started.set()
            await asyncio.Event().wait()

    monkeypatch.setattr("research_loop.long_horizon.ResearchLoop", HangingLoop)
    running = asyncio.create_task(run_long_horizon(
        SPEC_FILE, question_ids=["q01"], policy_name="quality",
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


@pytest.mark.asyncio
async def test_interrupted_rerun_leaves_the_previous_outputs_intact(monkeypatch, tmp_path: Path) -> None:
    from research_loop import long_horizon

    await _complete_questions(monkeypatch, tmp_path, ["q01"])
    folder = tmp_path / "q01"
    before = {path.name: path.read_bytes() for path in folder.iterdir()}

    real_write_json = long_horizon._write_json

    def failing_write_json(path: Path, value) -> None:
        if path.name == "bibliography.json":
            raise OSError("disk full")
        real_write_json(path, value)

    class RerunLoop:
        def __init__(self, *_args, **_kwargs):
            pass

        async def run(self, _objective, **_kwargs):
            return SimpleNamespace(job_id=uuid4(), report=FinalReport(answer="Rerun report q01"),
                                   ledger=_ledger("q01"), verification=VerificationReport(), cost_usd=None)

    monkeypatch.setattr(long_horizon, "ResearchLoop", RerunLoop)
    monkeypatch.setattr(long_horizon, "_write_json", failing_write_json)
    with pytest.raises(OSError, match="disk full"):  # after report.md and report.json were written
        await long_horizon.run_long_horizon(SPEC_FILE, question_ids=["q01"], policy_name="quality",
                                    settings=ResearchSettings.from_env({}), output_dir=tmp_path, persist=False)

    assert {path.name: path.read_bytes() for path in folder.iterdir()} == before
    assert sorted(path.name for path in tmp_path.iterdir()) == ["manifests", "q01"]  # no staging left behind


@pytest.mark.asyncio
async def test_aggregate_rejects_outputs_changed_after_publication(monkeypatch, tmp_path: Path) -> None:
    from research_loop.long_horizon import aggregate_long_horizon

    await _complete_questions(monkeypatch, tmp_path, ["q01", "q02"])
    run = json.loads((tmp_path / "q01" / "run.json").read_text())
    assert set(run["files"]) == {"report.md", "report.json", "evidence_ledger.json", "bibliography.json", "verification.json"}
    report = tmp_path / "q01" / "report.json"
    report.write_text(report.read_text().replace("Draft report q01", "Edited by hand"))

    evidence = aggregate_long_horizon(load_spec(SPEC_FILE), tmp_path)
    assert evidence.invalid == ["q01"]
    assert "q01" in evidence.missing
    assert [item.question["id"] for item in evidence.completed] == ["q02"]


@pytest.mark.parametrize(("edit", "problem"), [
    (("planner_question_max = 3\n", ""), "planner_question_max"),                                    # missing
    (("max_parallel_scouts = 2\n", "max_paralel_scouts = 2\n"), "max_paralel_scouts"),               # a typo
    (("normalized_web = true\n", "normalized_web = false\n"), "normalized_web"),                    # unsupported
    (("planner_question_min = 1\n", "planner_question_min = 5\n"), "planner_question_min"),         # above the max
], ids=["missing-field", "unknown-key", "provider-native-web", "inverted-planner-range"])
def test_long_horizon_spec_problems_fail_at_load_not_mid_run(tmp_path: Path, edit, problem) -> None:
    old, new = edit
    text = SPEC_FILE.read_text()
    assert old in text
    spec = tmp_path / "spec.toml"
    spec.write_text(text.replace(old, new, 1))
    with pytest.raises(ValueError, match=problem):
        load_spec(spec)

"""The bug-finding harness itself: its guards, its oracles, and a fixed fuzz sweep CI runs."""
from __future__ import annotations

import asyncio

import pytest

from research_loop.config import ScoutModels, Settings
from research_loop.dryrun import (
    World,
    check_record,
    fuzz,
    fuzz_one,
    fuzz_report,
    fuzz_settings,
)

FAKE = "fake:fuzz@high"


def test_fake_models_and_the_offline_world_only_run_together(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    fake_only = Settings().model_copy(update={"models": ScoutModels(planner=FAKE, scout=FAKE, synthesizer=FAKE,
                                                                    fallback=None, judge=FAKE)})
    assert any("only in the offline world" in problem for problem in fake_only.route_problems())
    real_in_world = Settings().model_copy(update={"offline_world": 1})
    assert any("runs only fake models" in problem for problem in real_in_world.route_problems())
    assert fuzz_settings(1, 0.2).route_problems() == []


async def test_the_world_answers_offline_and_fails_on_purpose() -> None:
    world = World(seed=7, fault_rate=1.0)
    async with world.client() as client:
        statuses = {page.fault: (await client.get(page.url)).status_code
                    for page in world.pages.values() if page.fault in ("403", "404", "500", "redirect_loop")}
    assert statuses.get("404", 404) == 404 and statuses.get("500", 500) == 500
    healthy = World(seed=7, fault_rate=0.0)
    # Only the one PDF whose text extracts with surrogates is faulty in a fault-free world.
    assert [page.fault for page in healthy.pages.values() if page.fault] == ["surrogate_pdf"]
    results = await healthy.search("materials database")
    assert all(result["href"] in healthy.pages or not result["href"] for result in results)


def _record(**changes) -> dict:
    from research_loop.evidence import EvidenceLedger
    from research_loop.schemas import Claim, ResearchResult

    ledger = EvidenceLedger()
    ledger.add(ResearchResult(question_id="q1", question="Q?", conclusion="c", confidence=0.5,
                              claims=[Claim(id="c1", statement="s", confidence=0.5)]))
    record = {"status": "complete", "plan": {"questions": [{"id": "q1", "question": "Q?"}]}, "report": None,
              "ledger": ledger.to_json(), "checks": {}, "cost_usd": 0.01, "workflow_version": "scout-v6",
              "config": {}, "run_id": "r", "question": "Q?"}
    return record | changes


def test_the_research_oracle_wants_one_stored_result_per_finished_call() -> None:
    from research_loop.dryrun import check_research

    call = {"id": "c1", "role": "scout", "question_id": "q1", "status": "succeeded"}
    stored = {"call_id": "c1", "question_id": "q1", "status": "returned"}
    assert check_research(_record(), [call], [stored]) == []
    assert any("0 stored results" in p for p in check_research(_record(), [call], []))
    assert any("2 stored results" in p for p in check_research(_record(), [call], [stored, stored]))
    assert any("no stored result: q1" in p for p in check_research(_record(), [], []))
    failed = {**call, "status": "failed"}
    assert any("has a failed call" in p for p in check_research(_record(), [failed], [stored]))
    # A cancelled run's ledger may hold only what finished.
    assert check_research(_record(status="cancelled"), [], []) == []


def test_the_oracles_catch_broken_records() -> None:
    assert check_record(_record(), [{"role": "scout", "status": "succeeded", "stop_reason": "done",
                                     "cost_usd": 0.01}]) == []
    assert any("not an end state" in p for p in check_record(_record(status="running"), []))
    assert any("left running" in p for p in check_record(_record(), [{"role": "scout", "status": "running"}]))
    assert any("no stop reason" in p for p in check_record(_record(), [{"role": "scout", "status": "failed"}]))
    assert any("is not its calls'" in p for p in check_record(_record(cost_usd=0.5), [
        {"role": "scout", "status": "succeeded", "stop_reason": "done", "cost_usd": 0.01}]))
    repeated = _record(plan={"questions": [{"id": "q1", "question": "Q?"}, {"id": "q1", "question": "Q2?"}]})
    assert any("question IDs repeat" in p for p in check_record(repeated, []))
    assert any("one retry would have cleared" in p for p in check_record(_record(), [
        {"role": "scout", "question_id": "q1", "status": "failed", "stop_reason": "network error FuzzTransientNetworkError",
         "cost_usd": 0.01}]))


def test_scouts_retry_the_tls_faults_the_fuzzer_found_ending_calls() -> None:
    # Seeds 75 and 122 ended scouts on a one-time TLS fault before rate policy scout-429-v3 retried it.
    findings = [finding for seed in (75, 122) for finding in asyncio.run(fuzz_one(seed))]
    assert not findings, fuzz_report(findings, 2, 0.2)


def test_a_fixed_fuzz_sweep_keeps_every_invariant() -> None:
    findings = asyncio.run(fuzz(20, seed=3))
    assert not findings, fuzz_report(findings, 20, 0.2)

"""The study runner, offline: a fake `research` writes each run's record instead of running it."""
from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path

import pytest
from pydantic import ValidationError

from research_loop.study import StudySpec, load_spec, run_study, schedule, summary

SPEC = """
study = "deep-vs-standard"
cases = ["drb2-task8"]
replicates = 2
cap_usd = 3.00
estimate_usd = 0.40
ceiling_usd = 2.00
grade = true
grade_estimate_usd = 0.05

[[arms]]
name = "standard"
args = ["--depth", "standard"]

[[arms]]
name = "deep"
args = ["--depth", "deep"]
ref = "main"
env = { RESEARCH_MODELS__SCOUT = "openai:gpt-6-luna@high" }
"""


@pytest.fixture
def spec(tmp_path: Path) -> StudySpec:
    path = tmp_path / "study.toml"
    path.write_text(SPEC)
    return load_spec(path)


def _record(run_id: str, cost: float) -> dict:
    return {"run_id": run_id, "status": "complete", "cost_usd": cost, "seconds": 300.0, "report": {"answer": "a"},
            "config": {"case": {"id": "drb2-task8"}},
            "checks": {"answer_support": "weak", "quotes": 10, "quotes_verified": 8, "quotes_misattributed": 1,
                       "quotes_short": 2, "statements": [{"support": "read"}, {"support": "read"},
                                                         {"support": "paraphrase"}, {"support": "shallow"}]},
            "ledger": {"q4": [{"conclusion": "Materials Project and OQMD", "claims": [],
                               "unresolved": ["ICSD was named but not checked"]}]}}


@contextmanager
def _no_worktrees(spec: StudySpec):
    yield {"main": Path("/tmp/tree")}


def test_arms_alternate_order_on_every_other_replicate(spec: StudySpec) -> None:
    order = [(run.arm.name, run.replicate) for run in schedule(spec)]
    assert order == [("standard", 1), ("deep", 1), ("deep", 2), ("standard", 2)]


def test_specs_must_name_what_their_kind_needs() -> None:
    base = {"study": "s", "cap_usd": 1, "estimate_usd": 0.1, "ceiling_usd": 1, "arms": [{"name": "a"}]}
    with pytest.raises(ValidationError, match="lists `cases`"):
        StudySpec.model_validate({**base, "sources": ["run"]})
    with pytest.raises(ValidationError, match="gives --model"):
        StudySpec.model_validate({**base, "kind": "rescout", "sources": ["run"]})
    with pytest.raises(ValidationError):
        StudySpec.model_validate({**base, "cases": ["c"], "arms": [{"name": "../x"}]})


def test_a_study_over_its_ceiling_is_refused_before_any_run(spec: StudySpec, tmp_path: Path) -> None:
    calls: list[list[str]] = []
    over = spec.model_copy(update={"ceiling_usd": 1.00})  # 4 runs x ($0.40 + $0.05) = $1.80
    with pytest.raises(ValueError, match="over the \\$1.00 ceiling"):
        run_study(over, tmp_path, invoke=lambda args, env: (calls.append(args) or 0, ""), worktrees=_no_worktrees)
    assert not calls


def test_runs_are_labeled_capped_and_summarized(spec: StudySpec, tmp_path: Path) -> None:
    calls: list[tuple[list[str], dict[str, str]]] = []

    def invoke(args: list[str], env: dict[str, str]) -> int:
        calls.append((args, env))
        out = Path(args[args.index("--out") + 1])
        out.mkdir(parents=True)
        (out / "run.json").write_text(json.dumps(_record(f"{len(calls):08d}-run", 0.30)))
        return 0, ""

    graded: list[str] = []

    def grade(run_id: str, case: str, env: dict[str, str], cap: float) -> dict:
        graded.append(run_id)
        return {"met": 20, "points": 52, "score": 0.385, "cost_usd": 0.04}

    outcomes = run_study(spec, tmp_path, invoke=invoke, grade=grade, worktrees=_no_worktrees)
    first_args, first_env = calls[0]
    assert first_args[:3] == ["scout", "--case", "drb2-task8"]
    assert first_args[first_args.index("--study") + 1] == "deep-vs-standard"
    assert first_args[first_args.index("--max-usd") + 1] == "3.00" and first_args[-2:] == ["--depth", "standard"]
    deep_env = calls[1][1]
    assert deep_env["PYTHONPATH"] == "/tmp/tree/src" and deep_env["RESEARCH_MODELS__SCOUT"].endswith("@high")
    assert "PYTHONPATH" not in first_env
    assert len(graded) == 4 and sum(o.cost_usd for o in outcomes) == pytest.approx(4 * 0.34)
    table = summary(spec, outcomes)
    assert "| drb2-task8 | standard | 1 | 00000001 | complete | weak | $0.340 | 300 s | 8 / 1 / 1 (2) | 2 / 1 / 1 |  | 2/7 (1) | 20/52 |" in table


def test_an_audited_study_runs_the_audit_with_the_modes_model_and_counts_its_cost(spec: StudySpec, tmp_path: Path) -> None:
    from research_loop.study import _audit_with, mode_env

    audited = spec.model_copy(update={"audit": True, "replicates": 1, "ceiling_usd": 3.00})
    assert audited.worst_case_usd() == pytest.approx(2 * (0.40 + 0.05 + 0.04))
    asked: list[tuple[str, str, float]] = []

    def invoke(args: list[str], env: dict[str, str]) -> int:
        out = Path(args[args.index("--out") + 1])
        out.mkdir(parents=True)
        (out / "run.json").write_text(json.dumps(_record(f"{len(asked):08d}-run", 0.30)))
        return 0, ""

    def audit(run_id: str, model: str, env: dict[str, str], cap: float) -> dict:
        asked.append((run_id, model, cap))
        return {"counts": {"supported": 5, "partial": 2, "no_quote": 1}, "cost_usd": 0.02}

    outcomes = run_study(audited, tmp_path, invoke=invoke, grade=lambda *a: None, audit=audit,
                         worktrees=_no_worktrees)
    assert [model for _, model, _ in asked] == ["zai:glm-5.3@high"] * 2 and asked[0][2] == 0.30
    assert sum(o.cost_usd for o in outcomes) == pytest.approx(2 * 0.32)
    assert "| 5 / 2 / 0 / 1 |" in summary(audited, outcomes)
    # Dry and cheap studies never send the audit to the real auditor.
    assert "RESEARCH_MODELS__JUDGE" in mode_env("dry", schedule(audited)[0])

    # The line `research audit` prints, parsed back.
    line = "7a7fc5b5: 4 partial, 8 supported; $0.0191. Recorded as 09b98c5d.\n"
    parsed = _audit_with(lambda args, env: (0, line))("r", "m", {}, 0.3)
    assert parsed == {"counts": {"partial": 4, "supported": 8}, "cost_usd": 0.0191}
    assert _audit_with(lambda args, env: (0, "odd"))("r", "m", {}, 0.3) == {"unreadable": "odd"}
    assert _audit_with(lambda args, env: (1, ""))("r", "m", {}, 0.3) is None


def test_the_ceiling_stops_runs_once_actual_spend_nears_it(spec: StudySpec, tmp_path: Path) -> None:
    def expensive(args: list[str], env: dict[str, str]) -> int:
        out = Path(args[args.index("--out") + 1])
        out.mkdir(parents=True)
        (out / "run.json").write_text(json.dumps(_record("run", 0.90)))  # far above the $0.40 estimate
        return 0, ""

    outcomes = run_study(spec, tmp_path, invoke=expensive, grade=lambda *a: None, worktrees=_no_worktrees)
    assert [o.exit_code for o in outcomes] == [0, 0, -1, -1]
    assert "another run could pass the $2.00 ceiling" in summary(spec, outcomes)


def test_the_plan_command_lists_runs_without_running_them(tmp_path: Path, monkeypatch, capsys) -> None:
    from research_loop.cli import main

    monkeypatch.setenv("DATABASE_URL", "postgresql://127.0.0.1:1/none")
    path = tmp_path / "study.toml"
    path.write_text(SPEC)
    with pytest.raises(SystemExit) as exit_info:
        main(["study", "plan", str(path)])
    assert exit_info.value.code == 0
    captured = capsys.readouterr()
    assert "4 scout runs over 2 arms, worst case $1.80" in captured.err
    assert "drb2-task8-deep-1: drb2-task8 deep replicate 1 at main" in captured.out


def test_the_grade_line_is_read_as_research_grade_prints_it() -> None:
    from research_loop.study import _grade_with

    line = ("drb2-task8: 20 of 52 points (0.385), $0.0403. Unmet: info_recall 3, analysis 2. "
            "Recorded as 214fb3be-136f-4cd7-89d7-f902ee470660.\n")
    grade = _grade_with(lambda args, env: (0, line))
    assert grade("run", "drb2-task8", {}, 1.0) == {"met": 20, "points": 52, "score": 0.385, "cost_usd": 0.0403}

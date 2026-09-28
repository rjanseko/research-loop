"""The study runner, offline: a fake `research` writes each run's record instead of running it."""
from __future__ import annotations

import json
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from research_loop.config import Settings
from research_loop.study import (
    StudySpec,
    load_spec,
    run_study,
    schedule,
    summary,
    tool_counts,
)
from research_loop.study import (
    _preflight as real_preflight,  # before the autouse fixture replaces it
)

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


@pytest.fixture(autouse=True)
def _arms_can_start(monkeypatch) -> None:
    """The runner's configuration check runs the real one in a subprocess; these tests are about what follows."""
    monkeypatch.setattr("research_loop.study._preflight", lambda env: None)


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


def test_three_arms_on_three_stored_plans_each_go_first_once() -> None:
    # One replicate per plan would otherwise run the same arm first every time, warming the shared
    # search cache for the others.
    spec = StudySpec(study="s", kind="rescout", sources=["p1", "p2", "p3"], cap_usd=1, estimate_usd=0.1,
                     ceiling_usd=5, arms=[{"name": n, "args": ["--model", "m"]} for n in ("luna", "flash", "pro")])
    firsts = [run.arm.name for run in schedule(spec)][::3]
    assert firsts == ["luna", "flash", "pro"] and len(schedule(spec)) == 9


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
    # The spec's $3 run cap is lowered to the $2 ceiling, less the room kept for the run's $0.05 grade.
    assert first_args[first_args.index("--max-usd") + 1] == "1.95" and first_args[-2:] == ["--depth", "standard"]
    deep_env = calls[1][1]
    assert deep_env["PYTHONPATH"] == "/tmp/tree/src" and deep_env["RESEARCH_MODELS__SCOUT"].endswith("@high")
    assert "PYTHONPATH" not in first_env
    assert len(graded) == 4 and sum(o.cost_usd for o in outcomes) == pytest.approx(4 * 0.34)
    table = summary(spec, outcomes)
    assert "| drb2-task8 | standard | 1 | 00000001 | complete | weak | $0.340 | 300 s |  | 8 / 1 / 1 (2) | 2 / 1 / 1 |  | 2/7 (1) | 20/52 |" in table


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

    graded = {"met": 0, "points": 1, "score": 0.0, "cost_usd": 0.0}
    outcomes = run_study(spec, tmp_path, invoke=expensive, grade=lambda *a: graded, worktrees=_no_worktrees)
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


def test_a_cheap_study_caps_each_run_at_the_cheap_ceiling(spec: StudySpec) -> None:
    from research_loop.study import CHEAP_CEILING_USD, for_mode

    cheap = for_mode(spec, "cheap")
    # Paid searches are not cheap; the spec's $3 run cap would let one cheap Exa run spend dollars.
    assert cheap.cap_usd == CHEAP_CEILING_USD == cheap.ceiling_usd


def _costing(costs: list[float | None], seen: list[str]):
    """A fake `research` that spends what the next of `costs` says, up to the cap it was given, as the
    budget guard would; None writes no record, as a crashed run does."""
    def invoke(args: list[str], env: dict[str, str]) -> tuple[int, str]:
        cap = args[args.index("--max-usd") + 1]
        seen.append(cap)
        cost = costs[len(seen) - 1]
        if cost is None:
            return 1, "killed"
        out = Path(args[args.index("--out") + 1])
        out.mkdir(parents=True)
        (out / "run.json").write_text(json.dumps(_record(f"run-{len(seen)}", min(cost, float(cap)))))
        return 0, ""
    return invoke


def test_low_estimates_cannot_carry_a_study_past_its_ceiling(tmp_path: Path) -> None:
    # The audit's case (docs/architectural-audit-2026-09-27.md, F05): two runs that each cost $0.75 under
    # their own $1 caps, admitted by $0.10 estimates, spent $1.50 of a $1 ceiling.
    spec = StudySpec(study="s", cases=["c"], arms=[{"name": "a"}], replicates=2, cap_usd=1, estimate_usd=0.1,
                     ceiling_usd=1)
    seen: list[str] = []
    outcomes = run_study(spec, tmp_path, invoke=_costing([0.745, 0.745], seen), worktrees=_no_worktrees)
    # $0.255 remains after the first run; the cap passed is rounded down, never up to $0.26.
    assert seen == ["1.00", "0.25"]
    assert sum(o.charged_usd for o in outcomes) <= 1


def test_a_step_whose_cost_is_unknown_counts_its_whole_cap(tmp_path: Path) -> None:
    spec = StudySpec(study="s", cases=["drb2-task8"], arms=[{"name": "a"}], replicates=3, cap_usd=0.5,
                     estimate_usd=0.2, ceiling_usd=1.2, grade=True, grade_estimate_usd=0.05, grade_cap_usd=0.1)
    seen: list[str] = []
    outcomes = run_study(spec, tmp_path, invoke=_costing([None, 0.30, 0.30], seen), grade=lambda *a: None,
                         worktrees=_no_worktrees)
    # The crashed run may have spent its $0.50 cap, and the second run's failed grade its $0.10 cap, so the
    # third run gets $1.20 - $0.50 - $0.30 - $0.10, less $0.05 kept for its grade, and not $0.50.
    assert seen == ["0.50", "0.50", "0.25"]
    assert outcomes[0].charged_usd == Decimal("0.50") and outcomes[1].charged_usd == Decimal("0.40")
    # The third run's grade fails too, under the $0.05 that is left.
    assert "plus up to $0.65 from steps whose cost could not be read" in summary(spec, outcomes)


def test_a_grade_and_audit_are_capped_by_what_the_ceiling_leaves(tmp_path: Path) -> None:
    spec = StudySpec(study="s", cases=["drb2-task8"], arms=[{"name": "a"}], cap_usd=1, estimate_usd=0.5,
                     ceiling_usd=1, grade=True, grade_estimate_usd=0.1, grade_cap_usd=1, audit=True,
                     audit_estimate_usd=0.1, audit_cap_usd=1)
    caps: dict[str, float] = {}

    def grade(run_id: str, case: str, env: dict[str, str], cap: float) -> dict:
        caps["grade"] = cap
        return {"met": 1, "points": 2, "score": 0.5, "cost_usd": 0.12}

    def audit(run_id: str, model: str, env: dict[str, str], cap: float) -> dict:
        caps["audit"] = cap
        return {"counts": {}, "cost_usd": 0.05}

    seen: list[str] = []
    run_study(spec, tmp_path, invoke=_costing([0.70], seen), grade=grade, audit=audit, worktrees=_no_worktrees)
    # The run keeps $0.20 for its grade and audit; after it spends $0.70 the grade may use $0.30 less the
    # audit's $0.10, and the audit what the grade left.
    assert seen == ["0.80"] and caps == {"grade": 0.2, "audit": 0.18}


def test_an_arm_that_cannot_start_stops_the_study_before_any_run(spec: StudySpec, tmp_path: Path) -> None:
    # The reading-fallback cheap checks (docs/study-log.md, 27 September 2026): the own arm ran and was paid for
    # before the fallback arm refused to start without EXA_API_KEY and FIRECRAWL_API_KEY.
    from research_loop.study import StudyConfigError

    calls: list[list[str]] = []
    checked: list[dict[str, str]] = []

    def preflight(env: dict[str, str]) -> str | None:
        checked.append(env)
        return "reading fallback: exa needs EXA_API_KEY" if env.get("RESEARCH_MODELS__SCOUT") else None

    with pytest.raises(StudyConfigError, match="arm deep: reading fallback: exa needs EXA_API_KEY"):
        run_study(spec, tmp_path, invoke=lambda args, env: (calls.append(args) or 0, ""), worktrees=_no_worktrees,
                  preflight=preflight)
    assert not calls
    # Each arm is checked once, with its own environment and its git ref's code.
    assert len(checked) == 2 and {env.get("PYTHONPATH") for env in checked} == {None, "/tmp/tree/src"}


def test_a_rescout_arm_is_checked_with_its_own_model() -> None:
    spec = StudySpec(study="s", kind="rescout", sources=["r"], cap_usd=1, estimate_usd=0.1, ceiling_usd=1,
                     arms=[{"name": "a", "args": ["--model", "openai:gpt-6-sol@high"]}])
    checked: list[dict[str, str]] = []
    with pytest.raises(Exception, match="stop"):
        run_study(spec, Path("/nonexistent"), invoke=lambda *a: (0, ""), worktrees=_no_worktrees,
            preflight=lambda env: checked.append(env) or "stop")
    assert checked[0]["RESEARCH_MODELS__SCOUT"] == "openai:gpt-6-sol@high"


def test_the_configuration_check_reports_what_the_command_would_refuse() -> None:
    from research_loop.study import mode_env

    # A fake model outside the offline world is refused whatever keys the machine has.
    assert "offline world" in real_preflight({"RESEARCH_MODELS__SCOUT": Settings().models.dry})
    run = schedule(StudySpec(study="s", cases=["c"], cap_usd=1, estimate_usd=0.1, ceiling_usd=1, arms=[{"name": "a"}]))[0]
    assert real_preflight(mode_env("dry", run)) is None


def test_dry_and_cheap_studies_replace_a_second_scout_model_only_when_one_is_set(spec: StudySpec, monkeypatch) -> None:
    from research_loop.study import mode_env

    monkeypatch.delenv("RESEARCH_MODELS__SCOUT_ALT", raising=False)
    monkeypatch.setattr("research_loop.config.Settings.model_config", {**Settings.model_config, "env_file": None})
    glm = spec.model_copy(update={"arms": [arm.model_copy(update={"env": {"RESEARCH_MODELS__SCOUT_ALT": "zai:glm-5.3@xhigh"}})
                                           for arm in spec.arms]})
    # A cheap check must never send the real GLM-5.3@xhigh scouts a study's arm names.
    assert mode_env("cheap", schedule(glm)[0])["RESEARCH_MODELS__SCOUT_ALT"] == Settings().models.cheap
    assert mode_env("dry", schedule(glm)[0])["RESEARCH_MODELS__SCOUT_ALT"] == Settings().models.dry
    # Without one, the check runs one scout model like the real run; "" leaves the setting off.
    assert mode_env("cheap", schedule(spec)[0])["RESEARCH_MODELS__SCOUT_ALT"] == ""
    monkeypatch.setenv("RESEARCH_MODELS__SCOUT_ALT", "zai:glm-5.3@xhigh")
    assert mode_env("cheap", schedule(spec)[0])["RESEARCH_MODELS__SCOUT_ALT"] == Settings().models.cheap
    assert mode_env("real", schedule(glm)[0]) == {}


def test_a_diagnosed_study_diagnoses_each_run_with_the_current_code_and_sums_them_up(spec: StudySpec,
                                                                                     tmp_path: Path) -> None:
    from research_loop.study import _diagnose_with, study_diagnosis

    diagnosed = spec.model_copy(update={"diagnose": True, "replicates": 1, "ceiling_usd": 3.00})
    assert diagnosed.worst_case_usd() == pytest.approx(2 * (0.40 + 0.05 + 0.15))
    asked: list[tuple[str, str, dict, float]] = []

    def invoke(args: list[str], env: dict[str, str]) -> int:
        out = Path(args[args.index("--out") + 1])
        out.mkdir(parents=True)
        (out / "run.json").write_text(json.dumps(_record(f"{len(asked):08d}-run", 0.30)))
        return 0, ""

    def diagnose(run_id: str, model: str, env: dict[str, str], cap: float) -> dict:
        asked.append((run_id, model, env, cap))
        return {"cost_usd": 0.12}

    outcomes = run_study(diagnosed, tmp_path, invoke=invoke, grade=lambda *a: None, diagnose=diagnose,
                         worktrees=_no_worktrees)
    assert [model for _, model, _, _ in asked] == ["zai:glm-5.3@high"] * 2 and asked[0][3] == 0.75
    # The deep arm runs at git ref "main", whose code may have no diagnosis; the current code diagnoses it.
    assert all("PYTHONPATH" not in env for _, _, env, _ in asked)
    assert sum(o.cost_usd for o in outcomes) == pytest.approx(2 * 0.42)

    # At the end, one free call over every diagnosed run gives the study's tables.
    calls: list[list[str]] = []

    def invoke_output(args: list[str], env: dict[str, str]) -> tuple[int, str]:
        calls.append(args)
        return 0, "00000000-run: diagnosed with m, 0 new grade(s) and 3 reused; $0.0000.\n\n## drb2-task8: tables\n"

    assert study_diagnosis(diagnosed, outcomes, invoke_output=invoke_output) == "## drb2-task8: tables\n"
    assert calls == [["diagnose", "00000000-run", "00000001-run", "--model", "zai:glm-5.3@high", "--free"]]
    assert study_diagnosis(spec, outcomes, invoke_output=invoke_output) == ""  # a spec without diagnose

    # The line `research diagnose` prints, parsed back; a cheap check never reaches the real judge.
    line = "7a7fc5b5-aaaa: diagnosed with zai:glm-5.3@high, 2 new grade(s) and 1 reused; $0.0812.\n"
    assert _diagnose_with(lambda args, env: (0, line))("r", "m", {}, 0.5) == {"cost_usd": 0.0812}
    assert _diagnose_with(lambda args, env: (0, "odd"))("r", "m", {}, 0.5) == {"unreadable": "odd"}
    assert _diagnose_with(lambda args, env: (1, ""))("r", "m", {}, 0.5) is None
    # A diagnosis with a failed grade still reports what its other grades cost.
    failed = line.replace("$0.0812.", "$0.0812. Failed: research (UnexpectedModelBehavior).")
    assert _diagnose_with(lambda args, env: (1, failed))("r", "m", {}, 0.5) == {"cost_usd": 0.0812, "incomplete": True}
    cheap_calls: list[list[str]] = []
    study_diagnosis(diagnosed, outcomes, mode="cheap",
                    invoke_output=lambda args, env: (cheap_calls.append(args), (0, ""))[1])
    assert cheap_calls[0][cheap_calls[0].index("--model") + 1] == "openai:gpt-6-luna@low"


def test_evaluation_models_follow_the_environment_and_arm_overrides(spec: StudySpec, tmp_path: Path,
                                                                             monkeypatch) -> None:
    from research_loop.study import command, mode_env, study_diagnosis

    monkeypatch.setenv("RESEARCH_MODELS__AUDIT", "openai:gpt-6-sol@medium")
    monkeypatch.setenv("RESEARCH_MODELS__DIAGNOSE", "openai:gpt-6-sol@low")
    monkeypatch.setenv("RESEARCH_MODELS__CHEAP", "openai:gpt-6-sol@low")
    arms = [spec.arms[0], spec.arms[1].model_copy(update={"env": {
        **spec.arms[1].env, "RESEARCH_MODELS__AUDIT": "zai:glm-5.3@xhigh",
        "RESEARCH_MODELS__DIAGNOSE": "zai:glm-5.3@high"}})]
    configured = spec.model_copy(update={"arms": arms, "replicates": 1, "grade": False, "audit": True,
                                         "diagnose": True, "ceiling_usd": 3.0})
    audits: list[str] = []
    diagnoses: list[str] = []
    checked: list[dict[str, str]] = []
    count = 0

    def invoke(args: list[str], env: dict[str, str]) -> tuple[int, str]:
        nonlocal count
        count += 1
        out = Path(args[args.index("--out") + 1])
        out.mkdir(parents=True)
        (out / "run.json").write_text(json.dumps(_record(f"{count:08d}-run", 0.20)))
        return 0, ""

    outcomes = run_study(configured, tmp_path, invoke=invoke, worktrees=_no_worktrees,
                         preflight=lambda env: checked.append(env) or None,
                         audit=lambda run_id, model, env, cap: audits.append(model) or {"cost_usd": 0.01},
                         diagnose=lambda run_id, model, env, cap: diagnoses.append(model) or {"cost_usd": 0.01})
    assert audits == ["openai:gpt-6-sol@medium", "zai:glm-5.3@xhigh"]
    assert diagnoses == ["openai:gpt-6-sol@low", "zai:glm-5.3@high"]
    assert {env["RESEARCH_MODELS__JUDGE"] for env in checked} >= set(audits + diagnoses)
    free_calls: list[list[str]] = []
    study_diagnosis(configured, outcomes, invoke_output=lambda args, env: (free_calls.append(args) or 0, ""))
    assert [args[args.index("--model") + 1] for args in free_calls] == diagnoses
    assert mode_env("cheap", schedule(configured)[0])["RESEARCH_MODELS__SCOUT"] == "openai:gpt-6-sol@low"
    rerun = configured.model_copy(update={"kind": "rescout", "cases": [], "sources": ["source"]})
    rerun = rerun.model_copy(update={"arms": [arm.model_copy(update={"args": ["--model", "zai:glm-5.3@high"]})
                                                   for arm in rerun.arms]})
    args = command(rerun, schedule(rerun)[0], tmp_path, mode="cheap")
    assert args[args.index("--model") + 1] == "openai:gpt-6-sol@low"


def test_configured_audit_model_is_preflighted_before_any_paid_run(spec: StudySpec, tmp_path: Path,
                                                                     monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("ZAI_API_KEY", "")
    audited = spec.model_copy(update={"audit": True, "replicates": 1, "ceiling_usd": 3.0})
    calls: list[list[str]] = []
    with pytest.raises(ValueError, match="audit: judge: zai:glm-5.3 needs ZAI_API_KEY"):
        run_study(audited, tmp_path, invoke=lambda args, env: (calls.append(args) or 0, ""),
                  preflight=real_preflight, worktrees=_no_worktrees)
    assert not calls


def test_study_freezes_model_settings_before_running_arms(spec: StudySpec, tmp_path: Path, monkeypatch) -> None:
    frozen = spec.model_copy(update={"arms": spec.arms[:1], "grade": False, "audit": True,
                                     "ceiling_usd": 3.0})
    monkeypatch.setenv("RESEARCH_MODELS__AUDIT", "zai:glm-5.3@high")
    monkeypatch.setenv("RESEARCH_MODEL_CALLS__RUBRIC_TIMEOUT_SECONDS", "240")
    seen: list[dict[str, str]] = []
    audited: list[str] = []

    def invoke(args: list[str], env: dict[str, str]) -> tuple[int, str]:
        seen.append(env)
        out = Path(args[args.index("--out") + 1])
        out.mkdir(parents=True)
        (out / "run.json").write_text(json.dumps(_record(f"{len(seen):08d}-run", 0.10)))
        if len(seen) == 1:
            monkeypatch.setenv("RESEARCH_MODELS__SCOUT", "openai:gpt-6-sol@medium")
            monkeypatch.setenv("RESEARCH_MODELS__AUDIT", "openai:gpt-6-sol@medium")
            monkeypatch.setenv("RESEARCH_MODEL_CALLS__RUBRIC_TIMEOUT_SECONDS", "360")
        return 0, ""

    run_study(frozen, tmp_path, invoke=invoke, worktrees=_no_worktrees, preflight=lambda env: None,
              audit=lambda run_id, model, env, cap: audited.append(model) or {"cost_usd": 0.01})
    assert [env["RESEARCH_MODELS__SCOUT"] for env in seen] == ["openai:gpt-6-luna@high"] * 2
    assert audited == ["zai:glm-5.3@high"] * 2
    assert [env["RESEARCH_MODEL_CALLS__RUBRIC_TIMEOUT_SECONDS"] for env in seen] == ["240.0"] * 2


def _returned(tool: str, content: dict) -> dict:
    return {"parts": [{"part_kind": "tool-return", "tool_name": tool, "content": content}]}


def test_a_studys_runs_count_searches_found_empty_and_failed_and_pages_read(spec: StudySpec, tmp_path: Path) -> None:
    # A search engine comparison turns on how often searches come back empty or fail, which the run record lacks.
    stored = [{"messages": [
        _returned("web_search", {"results": [{"url": "https://a.example"}]}),
        _returned("web_search", {"results": [], "hint": "No results for this query."}),
        _returned("web_search", {"error": "SearchUnavailable (TimeoutException)"}),
        _returned("fetch", {"url": "https://a.example", "text": "page", "start": 0}),
        _returned("fetch", {"url": "https://a.example", "text": "more", "start": 40000}),
        _returned("fetch", {"url": "https://b.example", "error": "HTTPStatusError", "status": 403}),
        {"parts": [{"part_kind": "tool-call", "tool_name": "web_search", "args": {"query": "q"}}]}]}]
    assert tool_counts(stored) == {"searches_found": 1, "searches_empty": 1, "searches_failed": 1,
                                   "pages_read": 1, "pages_failed": 1}

    def invoke(args: list[str], env: dict[str, str]) -> tuple[int, str]:
        out = Path(args[args.index("--out") + 1])
        out.mkdir(parents=True)
        (out / "run.json").write_text(json.dumps(_record("00000001-run", 0.30)))
        return 0, ""

    one = spec.model_copy(update={"replicates": 1, "arms": spec.arms[:1], "grade": False})
    outcomes = run_study(one, tmp_path, invoke=invoke, worktrees=_no_worktrees, dsn="postgresql://unused",
                         calls=lambda dsn, run_id: stored)
    assert "| 300 s | 1 / 1 / 1 · 1 / 1 |" in summary(one, outcomes)

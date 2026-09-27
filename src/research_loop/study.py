"""Run a whole study from a spec: every arm, case, and replicate, then one summary table.

    research study run studies/deep-vs-standard.toml

A study compares arms, such as two depths, two scout models, or two commits, on the same frozen cases
or stored runs. The runner does what the screens did by hand. It runs each planned run through the
`research` command, one at a time, reversing the arm order on every other replicate so that neither arm
always goes first against the study's shared cache (ABBA). It labels every run with the study, arm, and
replicate, and passes each run's hard cap. An arm at another git ref runs from a temporary worktree of
that ref, so it uses that commit's code. Before starting, it refuses a spec whose worst case exceeds the
study's ceiling, and it stops before any run that could take actual spend past the ceiling. At the end
it prints and saves a table of status, answer support, cost, time, quote checks, coverage, and grades.

A spec is TOML:

    study = "deep-vs-standard"      # the study label; also names its cache and output folder
    kind = "scout"                  # scout (frozen cases), rescout or synthesize (stored runs)
    cases = ["drb2-task8"]          # for scout; `sources = [run IDs]` for rescout and synthesize
    replicates = 2
    cap_usd = 3.00                  # each run's hard pre-dispatch cap (--max-usd)
    estimate_usd = 0.45             # the most a comparable run has cost, for the ceiling check
    ceiling_usd = 2.50              # the whole study, grades included
    grade = true                    # grade each report against its case's rubric
    grade_estimate_usd = 0.06
    grade_cap_usd = 1.00            # each grade's hard cap

    [[arms]]
    name = "standard"
    args = ["--depth", "standard"]  # extra arguments for the command
    [[arms]]
    name = "deep"
    args = ["--depth", "deep"]
    ref = "main"                    # optional: run this arm at a git ref
    env = { RESEARCH_MODELS__SCOUT = "openai:gpt-6-luna@high" }   # optional overrides

For rescout and synthesize, each arm must give `--model` in `args`.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from .coverage import EXPECTED, coverage

REPO = Path(__file__).resolve().parents[2]
_GRADE_LINE = re.compile(r": (\d+) of (\d+) points \(([\d.]+)\), \$([\d.]+)")


class Arm(BaseModel):
    name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    args: list[str] = Field(default_factory=list)
    ref: str | None = None
    env: dict[str, str] = Field(default_factory=dict)


class StudySpec(BaseModel):
    study: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    kind: Literal["scout", "rescout", "synthesize"] = "scout"
    cases: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    arms: list[Arm] = Field(min_length=1)
    replicates: int = Field(1, ge=1)
    cap_usd: float = Field(gt=0)
    estimate_usd: float = Field(gt=0)
    ceiling_usd: float = Field(gt=0)
    grade: bool = False
    grade_estimate_usd: float = Field(0.06, ge=0)
    grade_cap_usd: float = Field(1.00, gt=0)

    @model_validator(mode="after")
    def _targets(self) -> StudySpec:
        if self.kind == "scout" and (not self.cases or self.sources):
            raise ValueError("a scout study lists `cases` and no `sources`")
        if self.kind != "scout" and (not self.sources or self.cases):
            raise ValueError(f"a {self.kind} study lists `sources` (stored run IDs) and no `cases`")
        if self.kind != "scout" and any("--model" not in arm.args for arm in self.arms):
            raise ValueError(f"every arm of a {self.kind} study gives --model in its args")
        if len({arm.name for arm in self.arms}) != len(self.arms):
            raise ValueError("arm names must be unique")
        return self

    @property
    def targets(self) -> list[str]:
        return self.cases or self.sources

    def worst_case_usd(self) -> float:
        """What the planned runs could cost by their estimates, grades included."""
        per_run = self.estimate_usd + (self.grade_estimate_usd if self.grade else 0)
        return round(len(schedule(self)) * per_run, 4)


def load_spec(path: Path) -> StudySpec:
    return StudySpec.model_validate(tomllib.loads(path.read_text(encoding="utf-8")))


@dataclass(frozen=True)
class Planned:
    target: str
    arm: Arm
    replicate: int

    @property
    def label(self) -> str:
        return f"{_slug(self.target)}-{self.arm.name}-{self.replicate}"


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", text)[:40]


def schedule(spec: StudySpec) -> list[Planned]:
    """Every run in order: for each target and replicate, the arms forward, then reversed on the next
    replicate, so that over a pair of replicates each arm goes first once (ABBA)."""
    planned = []
    for target in spec.targets:
        for replicate in range(1, spec.replicates + 1):
            arms = spec.arms if replicate % 2 else list(reversed(spec.arms))
            planned += [Planned(target, arm, replicate) for arm in arms]
    return planned


def command(spec: StudySpec, run: Planned, out: Path) -> list[str]:
    """The `research` arguments for one planned run."""
    labels = ["--study", spec.study, "--arm", run.arm.name, "--replicate", str(run.replicate),
              "--max-usd", f"{spec.cap_usd:.2f}", "--out", str(out)]
    if spec.kind == "scout":
        return ["scout", "--case", run.target, *labels, *run.arm.args]
    return [spec.kind, run.target, *labels, *run.arm.args]


@dataclass
class Outcome:
    run: Planned
    exit_code: int
    record: dict[str, Any] | None = None
    grade: dict[str, Any] | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def cost_usd(self) -> float:
        run_cost = float((self.record or {}).get("cost_usd") or 0)
        return run_cost + float((self.grade or {}).get("cost_usd") or 0)


# Runs `research` with these arguments and extra environment; returns the exit code. Tests replace it.
Invoke = Callable[[list[str], dict[str, str]], int]


def _invoke(args: list[str], env: dict[str, str]) -> int:
    executable = Path(sys.executable).with_name("research")
    return subprocess.run([str(executable), *args], cwd=REPO, env={**os.environ, **env}, check=False).returncode


def _grade_with(invoke_output: Callable[[list[str], dict[str, str]], tuple[int, str]]) -> Callable[..., dict | None]:
    def grade(run_id: str, case: str, env: dict[str, str], cap: float) -> dict[str, Any] | None:
        code, output = invoke_output(["grade", run_id, "--case", case, "--max-usd", f"{cap:.2f}"], env)
        if code != 0 or not (match := _GRADE_LINE.search(output)):
            return None
        return {"met": int(match.group(1)), "points": int(match.group(2)), "score": float(match.group(3)),
                "cost_usd": float(match.group(4))}
    return grade


def _invoke_output(args: list[str], env: dict[str, str]) -> tuple[int, str]:
    executable = Path(sys.executable).with_name("research")
    done = subprocess.run([str(executable), *args], cwd=REPO, env={**os.environ, **env}, check=False,
                          capture_output=True, text=True)
    sys.stderr.write(done.stderr)
    return done.returncode, done.stdout


@contextmanager
def _worktrees(spec: StudySpec) -> Iterator[dict[str, Path]]:
    """A temporary worktree for each arm's git ref, removed afterwards."""
    trees: dict[str, Path] = {}
    try:
        for ref in sorted({arm.ref for arm in spec.arms if arm.ref}):
            path = Path(tempfile.mkdtemp(prefix="study-")) / "tree"
            subprocess.run(["git", "worktree", "add", "--detach", str(path), ref], cwd=REPO, check=True,
                           capture_output=True)
            trees[ref] = path
        yield trees
    finally:
        for path in trees.values():
            subprocess.run(["git", "worktree", "remove", "--force", str(path)], cwd=REPO, check=False,
                           capture_output=True)
            shutil.rmtree(path.parent, ignore_errors=True)


def run_study(spec: StudySpec, out_root: Path, *, invoke: Invoke = _invoke,
              grade: Callable[..., dict | None] | None = None,
              worktrees: Callable[[StudySpec], Any] = _worktrees) -> list[Outcome]:
    """Run every planned run of `spec` in order, stopping before one that could pass the ceiling."""
    grade = grade or _grade_with(_invoke_output)
    if (worst := spec.worst_case_usd()) > spec.ceiling_usd:
        raise ValueError(f"the planned runs could cost ${worst:.2f} by their estimates, over the "
                         f"${spec.ceiling_usd:.2f} ceiling; raise the ceiling or plan fewer runs")
    outcomes: list[Outcome] = []
    spent = 0.0
    per_run = spec.estimate_usd + (spec.grade_estimate_usd if spec.grade else 0)
    with worktrees(spec) as trees:
        for run in schedule(spec):
            if spent + per_run > spec.ceiling_usd:
                note = f"not run: ${spent:.2f} spent, and another run could pass the ${spec.ceiling_usd:.2f} ceiling"
                outcomes.append(Outcome(run, -1, notes=[note]))
                continue
            out = out_root / run.label
            env = dict(run.arm.env)
            if run.arm.ref:
                env["PYTHONPATH"] = str(trees[run.arm.ref] / "src")
            outcome = Outcome(run, invoke(command(spec, run, out), env))
            if (out / "run.json").exists():
                outcome.record = json.loads((out / "run.json").read_text(encoding="utf-8"))
            case_id = _case_id(outcome.record)
            if spec.grade and outcome.record and outcome.record.get("report") and case_id:
                outcome.grade = grade(outcome.record["run_id"], case_id, env, spec.grade_cap_usd)
                if outcome.grade is None:
                    outcome.notes.append("grading failed; see the output above")
            spent += outcome.cost_usd
            outcomes.append(outcome)
    return outcomes


def _case_id(record: dict[str, Any] | None) -> str | None:
    return (((record or {}).get("config") or {}).get("case") or {}).get("id")


def summary(spec: StudySpec, outcomes: list[Outcome]) -> str:
    """A Markdown table of every planned run, the ones not run included."""
    header = ("| Target | Arm | Rep | Run | Status | Answer | Cost | Time | Quotes verified / misattributed / not found "
              "| Coverage found (named) | Grade |")
    lines = [f"# Study {spec.study}", "", header, "|---|---|---|---|---|---|---|---|---|---|---|"]
    for outcome in outcomes:
        record, run = outcome.record or {}, outcome.run
        checks = record.get("checks") or {}
        quotes = checks.get("quotes") or 0
        verified, wrong = checks.get("quotes_verified") or 0, checks.get("quotes_misattributed") or 0
        cover = ""
        if _case_id(record) in EXPECTED:
            found = coverage(record)
            cover = f"{len(found['found'])}/{found['expected']} ({len(found['named_only'])})"
        grade = outcome.grade
        cells = [run.target[:24], run.arm.name, str(run.replicate), str(record.get("run_id", ""))[:8],
                 str(record.get("status") or ("not run" if outcome.exit_code == -1 else f"exit {outcome.exit_code}")),
                 str(checks.get("answer_support") or ""),
                 f"${outcome.cost_usd:.3f}" if record else "",
                 f"{float(record['seconds']):.0f} s" if record.get("seconds") is not None else "",
                 f"{verified} / {wrong} / {quotes - verified - wrong}" if quotes else "",
                 cover, f"{grade['met']}/{grade['points']}" if grade else ""]
        lines.append("| " + " | ".join(cells) + " |")
    spent = sum(outcome.cost_usd for outcome in outcomes)
    lines += ["", f"Total ${spent:.2f} of a ${spec.ceiling_usd:.2f} ceiling."]
    lines += [f"- {o.run.label}: {note}" for o in outcomes for note in o.notes]
    return "\n".join(lines) + "\n"

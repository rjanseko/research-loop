"""Run a whole study from a spec: every arm, case, and replicate, then one summary table.

    research study run studies/deep-vs-standard.toml

A study compares arms, such as two depths, two scout models, or two commits, on the same frozen cases
or stored runs. The runner does what the screens did by hand. It runs each planned run through the
`research` command, one at a time, rotating the arm order on every replicate and target so that no arm
always goes first against the study's shared cache (ABBA). It labels every run with the study, arm, and
replicate. An arm at another git ref runs from a temporary worktree of that ref, so it uses that
commit's code. Before starting, it refuses a spec whose worst case by the estimates exceeds the study's
ceiling, and one with an arm whose runs would refuse to start, such as for a missing API key, so an
earlier arm does not spend first. The estimates only plan; the ceiling is enforced by hard caps. Each run, grade, and audit gets a
`--max-usd` cap no larger than what remains of the ceiling, rounded down to the cent, and a run's cap
leaves room for its grade and audit by their estimates. What a step cost comes from its record; a step
whose cost cannot be read, because it wrote no record or its output could not be parsed, counts its whole
cap against the ceiling, since it may have spent it. It skips a run when what remains could not cover the
run's estimates. At the end it prints and saves a table of status, answer support, cost, time, quote checks, support audits, coverage,
and grades.

A spec is TOML:

    study = "deep-vs-standard"      # the study label; also names its cache and output folder
    kind = "scout"                  # scout (frozen cases), rescout or synthesize (stored runs)
    cases = ["drb2-task8"]          # for scout; `sources = [run IDs]` for rescout and synthesize
    replicates = 2
    cap_usd = 3.00                  # each run's hard cap (--max-usd), lowered to what remains of the ceiling
    estimate_usd = 0.45             # the most a comparable run has cost, for the ceiling check
    ceiling_usd = 2.50              # the whole study, grades included
    grade = true                    # grade each report against its case's rubric
    grade_estimate_usd = 0.06
    grade_cap_usd = 1.00            # each grade's hard cap, lowered the same way
    audit = true                    # audit each report's statements against their quotes (audit.py)
    audit_model = "zai:glm-5.3@high"
    audit_estimate_usd = 0.04
    audit_cap_usd = 0.30            # each audit's hard cap, lowered the same way
    diagnose = true                 # grade each run's claims and research too, to find where points were lost;
                                    # a rescout study's only grade, since a rescout writes no report
    diagnose_model = "zai:glm-5.3@high"
    diagnose_estimate_usd = 0.15
    diagnose_cap_usd = 0.75         # each diagnosis's hard cap, lowered the same way

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

import hashlib
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
from decimal import ROUND_DOWN, Decimal
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from .config import ModelCallLimits, ScoutModels, Settings
from .coverage import EXPECTED, coverage

REPO = Path(__file__).resolve().parents[2]
# `research grade` ends its line with "... (0.385), $0.0403. Unmet: ...", so the cost stops at the digits.
_GRADE_LINE = re.compile(r": (\d+) of (\d+) points \((\d+\.\d+)\), \$(\d+\.\d+)")


class StudyCeilingError(ValueError):
    """The planned runs could cost more than the study's ceiling."""


class StudyConfigError(ValueError):
    """An arm's runs would refuse to start, such as for a missing API key."""


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
    audit: bool = False
    audit_model: str | None = None
    # The costliest audit of a stored report was $0.036 (audit v1, GLM-5.3 at high effort).
    audit_estimate_usd: float = Field(0.04, ge=0)
    audit_cap_usd: float = Field(0.30, gt=0)
    # Grades a run's report, claims, and research with one judge (diagnose.py). GLM-5.3 at high effort cost
    # $0.03 to $0.05 a grade on drb2-task8 reports.
    diagnose: bool = False
    diagnose_model: str | None = None
    diagnose_estimate_usd: float = Field(0.15, ge=0)
    diagnose_cap_usd: float = Field(0.75, gt=0)

    @model_validator(mode="after")
    def _targets(self) -> StudySpec:
        if self.kind == "scout" and (not self.cases or self.sources):
            raise ValueError("a scout study lists `cases` and no `sources`")
        if self.kind != "scout" and (not self.sources or self.cases):
            raise ValueError(f"a {self.kind} study lists `sources` (stored run IDs) and no `cases`")
        if self.kind != "scout" and any("--model" not in arm.args for arm in self.arms):
            raise ValueError(f"every arm of a {self.kind} study gives --model in its args")
        if self.kind == "rescout" and (self.grade or self.audit):
            raise ValueError("a rescout writes no report to grade or audit; use `diagnose` to grade its claims")
        if len({arm.name for arm in self.arms}) != len(self.arms):
            raise ValueError("arm names must be unique")
        return self

    @property
    def targets(self) -> list[str]:
        return self.cases or self.sources

    def worst_case_usd(self) -> float:
        """What the planned runs could cost by their estimates, grades included."""
        return round(len(schedule(self)) * self.per_run_usd(), 4)

    def per_run_usd(self) -> float:
        """What one planned run could cost by the estimates, its grade, audit, and diagnosis included."""
        return (self.estimate_usd + (self.grade_estimate_usd if self.grade else 0)
                + (self.audit_estimate_usd if self.audit else 0)
                + (self.diagnose_estimate_usd if self.diagnose else 0))


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
    """Every run in order: for each target and replicate, the arms rotated one place further than the last,
    so that each arm goes first equally often against the study's shared cache. Two arms alternate (ABBA);
    three arms on three stored plans each go first once."""
    planned = []
    for index, target in enumerate(spec.targets):
        for replicate in range(1, spec.replicates + 1):
            turn = (index * spec.replicates + replicate - 1) % len(spec.arms)
            planned += [Planned(target, arm, replicate) for arm in spec.arms[turn:] + spec.arms[:turn]]
    return planned


# How a study runs: for real; dry, with fake models, the offline world, and the dry database, for free; or
# cheap, with every role on a cheap real model, for cents. Dry and cheap runs exist to find bugs before
# a real study pays for them (dryrun.py); their research is never scored.
Mode = Literal["real", "dry", "cheap"]
def configured_model(role: Literal["audit", "diagnose", "cheap", "dry"], env: dict[str, str],
                     models: ScoutModels | None = None) -> str:
    """A study model from the arm's environment, then the study's model snapshot."""
    key = f"RESEARCH_MODELS__{role.upper()}"
    return env[key] if key in env else getattr(models or Settings().models, role)


def mode_model(model: str | None, mode: Mode, role: Literal["audit", "diagnose"],
               env: dict[str, str], models: ScoutModels | None = None) -> str:
    """Resolve a study model after the arm environment is chosen; dry and cheap replace it."""
    if mode == "dry":
        return configured_model("dry", env, models)
    if mode == "cheap":
        return configured_model("cheap", env, models)
    return configured_model(role, env, models) if model is None else model
CHEAP_CEILING_USD = 0.25
_ROLES = ("PLANNER", "SCOUT", "SYNTHESIZER", "FALLBACK", "JUDGE")
_SCOUT_ALT = "RESEARCH_MODELS__SCOUT_ALT"
# Short limits for dry runs; fake calls are fast, so these only bound faults that stall.
_DRY_LIMITS = {"RESEARCH_SECONDS": "20", "DEADLINE_SECONDS": "60", "FOLLOWUP_DEADLINE_SECONDS": "130",
               "GAP_SECONDS": "5", "DEEP_DIVE_SECONDS": "8", "REQUEST_TIMEOUT_SECONDS": "1",
               "QUICK__MAX_QUESTIONS": "2", "QUICK__COST_USD": "0.30", "QUICK__SYNTHESIS_USD": "0.12",
               "QUICK__RESEARCH_SECONDS": "3", "QUICK__DEADLINE_SECONDS": "30",
               "DEEP__RESEARCH_SECONDS": "20", "DEEP__DEADLINE_SECONDS": "60",
               "DEEP__FOLLOWUP_DEADLINE_SECONDS": "130", "DEEP__DEEP_DIVE_SECONDS": "8"}


def for_mode(spec: StudySpec, mode: Mode, seeds: int = 1) -> StudySpec:
    """The spec as a dry or cheap study runs it: its own study name, and for a cheap study one replicate of
    the first target under a low ceiling."""
    if mode == "dry":
        # Dry money is fictional; the ceiling scales with the seeds, so the ceiling logic still runs.
        return spec.model_copy(update={"study": f"{spec.study}-dry", "replicates": seeds,
                                       "ceiling_usd": spec.ceiling_usd * max(1.0, seeds / spec.replicates)})
    if mode == "cheap":
        # Cheap runs have cost $0.003 to $0.03; $0.05 lets three arms and their diagnoses plan under the ceiling.
        # Each run's hard cap is the cheap ceiling too: cheap models cost cents, but paid web searches do not,
        # and a cheap drb2-task8 run on Exa could make a hundred of them under the spec's own cap.
        return spec.model_copy(update={"study": f"{spec.study}-cheap", "replicates": 1, "cases": spec.cases[:1],
                                       "cap_usd": min(spec.cap_usd, CHEAP_CEILING_USD),
                                       "sources": spec.sources[:1], "estimate_usd": 0.05, "grade_estimate_usd": 0.01,
                                       "audit_estimate_usd": 0.01, "diagnose_estimate_usd": 0.03,
                                       "ceiling_usd": min(spec.ceiling_usd, CHEAP_CEILING_USD)})
    return spec


def dry_database_url(dsn: str) -> str:
    """The dry database beside `dsn`: the same server, database `research_dry` (`make dry-db`)."""
    return os.environ.get("RESEARCH_DRY_DATABASE_URL") or re.sub(r"/[^/?]+(\?|$)", r"/research_dry\1", dsn, count=1)


def copy_sources_to_dry(dsn: str, sources: list[str]) -> list[str]:
    """Copy each source run's record from the main database into the dry one, so a dry rescout or synthesis
    study has the plans and ledgers it reruns; returns the sources the main database does not hold. Only the
    `runs` row is copied, without its parent link, and a source already there is left as it is."""
    import psycopg

    missing = []
    with psycopg.connect(dsn) as main, psycopg.connect(dry_database_url(dsn)) as dry:
        for source in sources:
            row = main.execute("select row_to_json(runs) from runs where id::text = %s", (source,)).fetchone()
            if row is None:
                missing.append(source)
                continue
            dry.execute("insert into runs select * from json_populate_record(null::runs, %s::json) "
                        "on conflict (id) do nothing", (json.dumps(row[0] | {"parent_run_id": None}),))
    return missing


def mode_env(mode: Mode, run: Planned, dsn: str | None = None, models: ScoutModels | None = None) -> dict[str, str]:
    """What a dry or cheap run's environment overrides, applied after the arm's own, so no arm reaches a
    real or expensive model in these modes."""
    if mode == "real":
        return {}
    models = models or Settings().models
    model = mode_model(None, mode, "audit", run.arm.env, models)
    env = {f"RESEARCH_MODELS__{role}": model for role in _ROLES}
    if mode == "cheap":
        # The synthesizer must be Claude, whose citations its report is built from.
        env["RESEARCH_MODELS__SYNTHESIZER"] = _cheap_synthesizer(run.arm.env, models)
    # A second scout model, from the arm or the environment, is replaced like the rest; without one the check
    # keeps one scout model, as the real run does.
    alt = run.arm.env.get(_SCOUT_ALT) if _SCOUT_ALT in run.arm.env else models.scout_alt
    env[_SCOUT_ALT] = model if alt else ""
    if mode == "dry":
        env |= {"RESEARCH_OFFLINE_WORLD": str(_stable_seed(run)), "RESEARCH_CACHE_MODE": "off",
                "RESEARCH_TOKENS_PER_MINUTE": "{}", "RESEARCH_LOGFIRE": "false", "PYDANTIC_AI_NO_BANNER": "1"}
        env |= {f"RESEARCH_LIMITS__{name}": value for name, value in _DRY_LIMITS.items()}
        if dsn:
            env["DATABASE_URL"] = dry_database_url(dsn)
    return env


def _cheap_synthesizer(env: dict[str, str], models: ScoutModels | None) -> str:
    key = "RESEARCH_MODELS__CHEAP_SYNTHESIZER"
    return env[key] if key in env else (models or Settings().models).cheap_synthesizer


def _stable_seed(run: Planned) -> int:
    return int.from_bytes(hashlib.sha256(run.label.encode()).digest()[:4], "big")


def command(spec: StudySpec, run: Planned, out: Path, mode: Mode = "real", cap: Decimal | None = None,
            models: ScoutModels | None = None) -> list[str]:
    """The `research` arguments for one planned run under `cap` (the spec's run cap when not given); a dry or
    cheap run's `--model` is replaced too."""
    labels = ["--study", spec.study, "--arm", run.arm.name, "--replicate", str(run.replicate),
              "--max-usd", f"{_usd(spec.cap_usd) if cap is None else cap:.2f}", "--out", str(out)]
    args = list(run.arm.args)
    if mode != "real" and "--model" in args and args.index("--model") + 1 < len(args):
        args[args.index("--model") + 1] = (_cheap_synthesizer(run.arm.env, models) if mode == "cheap"
                                           and spec.kind == "synthesize"
                                           else mode_model(None, mode, "audit", run.arm.env, models))
    if spec.kind == "scout":
        return ["scout", "--case", run.target, *labels, *args]
    return [spec.kind, run.target, *labels, *args]


@dataclass
class Outcome:
    run: Planned
    exit_code: int
    record: dict[str, Any] | None = None
    grade: dict[str, Any] | None = None
    # The support audit's verdict counts and cost (audit.py).
    audit: dict[str, Any] | None = None
    # What the diagnosis's grades cost (diagnose.py).
    diagnosis: dict[str, Any] | None = None
    notes: list[str] = field(default_factory=list)
    # Invariants the run broke (dryrun.check_record), and tracebacks in its output; dry and cheap runs only.
    violations: list[str] = field(default_factory=list)
    # What the run's web searches and page reads returned (tool_counts), when its calls could be read.
    tools: dict[str, int] | None = None
    diagnosis_model: str | None = None
    mode: Mode = "real"
    # The caps of steps whose cost could not be read; the ceiling counts them as spent.
    unaccounted_usd: Decimal = Decimal(0)

    @property
    def cost_usd(self) -> float:
        """What the run, its grade, its audit, and its diagnosis reported costing."""
        run_cost = float((self.record or {}).get("cost_usd") or 0)
        return (run_cost + float((self.grade or {}).get("cost_usd") or 0)
                + float((self.audit or {}).get("cost_usd") or 0) + float((self.diagnosis or {}).get("cost_usd") or 0))

    @property
    def charged_usd(self) -> Decimal:
        """What counts against the study's ceiling: the reported costs, and the caps of steps whose cost is unknown."""
        return Decimal(str(self.cost_usd)) + self.unaccounted_usd


def _usd(amount: float) -> Decimal:
    return Decimal(str(amount))


def _cap(limit: float, room: Decimal) -> Decimal:
    """A step's hard cap: its own limit, or what room the ceiling leaves, rounded down to the cent so the cap
    passed as `--max-usd` never exceeds the room."""
    return max(min(_usd(limit), room), Decimal(0)).quantize(Decimal("0.01"), rounding=ROUND_DOWN)


# Runs `research` with these arguments and extra environment; returns the exit code and what it wrote to
# stderr, which the runner scans for tracebacks. Tests replace it.
Invoke = Callable[[list[str], dict[str, str]], tuple[int, str]]


def run_child(command: list[str], env: dict[str, str], *, capture_stdout: bool) -> subprocess.CompletedProcess[str]:
    """Run one `research` command to its end. When the study is interrupted, the command gets SIGTERM, which it
    handles like Ctrl-C by recording its run as cancelled, and up to a minute to do so. `subprocess.run` sends
    SIGKILL instead, which left two stopped rescouts marked running (audit, 28 September 2026)."""
    with subprocess.Popen(command, cwd=REPO, env={**os.environ, **env}, text=True, stderr=subprocess.PIPE,
                          stdout=subprocess.PIPE if capture_stdout else None) as child:
        try:
            out, err = child.communicate()
        except KeyboardInterrupt:
            child.terminate()
            try:
                child.communicate(timeout=60)
            except subprocess.TimeoutExpired:
                child.kill()
            raise
    return subprocess.CompletedProcess(command, child.returncode, out, err)


def _invoke(args: list[str], env: dict[str, str]) -> tuple[int, str]:
    done = run_child([str(Path(sys.executable).with_name("research")), *args], env, capture_stdout=False)
    sys.stderr.write(done.stderr)
    return done.returncode, done.stderr


def _grade_with(invoke_output: Callable[[list[str], dict[str, str]], tuple[int, str]]) -> Callable[..., dict | None]:
    def grade(run_id: str, case: str, env: dict[str, str], cap: float) -> dict[str, Any] | None:
        """The grade, or None when grading failed and said so; {"unreadable": ...} when `research grade`
        succeeded but printed a line this cannot read, which is a bug (the full-stop parser was one)."""
        code, output = invoke_output(["grade", run_id, "--case", case, "--max-usd", f"{cap:.2f}"], env)
        if code != 0:
            return None
        if not (match := _GRADE_LINE.search(output)):
            return {"unreadable": output.strip()[:200]}
        return {"met": int(match.group(1)), "points": int(match.group(2)), "score": float(match.group(3)),
                "cost_usd": float(match.group(4))}
    return grade


# `research audit` prints "<run id>: 2 partial, 5 supported; $0.0111. Recorded as ...".
_AUDIT_LINE = re.compile(r": ([^;]*); \$(\d+(?:\.\d+)?)\. Recorded as")
_AUDIT_COUNT = re.compile(r"(\d+) (\w+)")


# `research diagnose` prints "<run id>: diagnosed with <model>, 2 new grade(s) and 1 reused; $0.0812." per run.
_DIAGNOSE_LINE = re.compile(r": diagnosed with .*; \$(\d+(?:\.\d+)?)\.")


def _diagnose_with(invoke_output: Callable[[list[str], dict[str, str]], tuple[int, str]]) -> Callable[..., dict | None]:
    def diagnose(run_id: str, model: str, env: dict[str, str], cap: float) -> dict[str, Any] | None:
        """What the diagnosis's grades cost, with "incomplete" when one of them failed; None when it failed
        before grading, or {"unreadable": ...}."""
        code, output = invoke_output(["diagnose", run_id, "--model", model, "--max-usd", f"{cap:.2f}"], env)
        match = _DIAGNOSE_LINE.search(output)
        if code != 0:
            return {"cost_usd": float(match.group(1)), "incomplete": True} if match else None
        if match is None:
            return {"unreadable": output.strip()[-200:]}
        return {"cost_usd": float(match.group(1))}
    return diagnose


def _audit_with(invoke_output: Callable[[list[str], dict[str, str]], tuple[int, str]]) -> Callable[..., dict | None]:
    def audit(run_id: str, model: str, env: dict[str, str], cap: float) -> dict[str, Any] | None:
        """The audit's verdict counts and cost, None when it failed and said so, or {"unreadable": ...}."""
        code, output = invoke_output(["audit", run_id, "--model", model, "--max-usd", f"{cap:.2f}"], env)
        if code != 0:
            return None
        if not (match := _AUDIT_LINE.search(output)):
            return {"unreadable": output.strip()[:200]}
        counts = {verdict: int(n) for n, verdict in _AUDIT_COUNT.findall(match.group(1))}
        return {"counts": counts, "cost_usd": float(match.group(2))}
    return audit


# Why the command's configuration would refuse to run, or None; given the extra environment. Tests replace it.
Preflight = Callable[[dict[str, str]], str | None]
_ROLE_OF_MODEL = {"rescout": "SCOUT", "synthesize": "SYNTHESIZER"}
# What a paid command checks before its first call (cli._checked), run with the arm's environment and code.
_CHECK = """
from research_loop.config import Settings
from research_loop.scout import ConfigError, check_config
try:
    check_config(Settings())
except (ConfigError, ValueError) as exc:
    print(exc)
    raise SystemExit(2)
"""


def _preflight(env: dict[str, str]) -> str | None:
    done = subprocess.run([sys.executable, "-c", _CHECK], cwd=REPO, env={**os.environ, **env}, check=False,
                          capture_output=True, text=True)
    if done.returncode == 0:
        return None
    lines = (done.stdout.strip() or done.stderr.strip()).splitlines()
    return lines[-1] if lines else f"exit {done.returncode}"


def _run_env(run: Planned, mode: Mode, dsn: str | None, trees: dict[str, Path],
             models: ScoutModels, model_calls: ModelCallLimits) -> dict[str, str]:
    """Freeze model IDs and call limits, then apply arm, mode, and git-ref overrides."""
    # A mapping, such as the synthesizer fallbacks, is frozen as the JSON pydantic-settings reads back.
    env = {f"RESEARCH_MODELS__{role.upper()}": json.dumps(spec) if isinstance(spec, dict) else spec or ""
           for role, spec in models.model_dump().items()}
    env |= {f"RESEARCH_MODEL_CALLS__{name.upper()}": str(value)
            for name, value in model_calls.model_dump().items()}
    env |= run.arm.env | mode_env(mode, run, dsn, models)
    if run.arm.ref:
        env["PYTHONPATH"] = str(trees[run.arm.ref] / "src")
    return env


def _invoke_output(args: list[str], env: dict[str, str]) -> tuple[int, str]:
    done = run_child([str(Path(sys.executable).with_name("research")), *args], env, capture_stdout=True)
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


def _calls(dsn: str, run_id: str) -> list[dict[str, Any]]:
    """A stored run's calls with their outputs and messages, for the invariants."""
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        return conn.execute("select role, question_id, status, stop_reason, cost_usd, output, messages "
                            "from run_calls where run_id = %s order by started_at", (run_id,)).fetchall()


def run_study(spec: StudySpec, out_root: Path, *, invoke: Invoke = _invoke,
              grade: Callable[..., dict | None] | None = None, audit: Callable[..., dict | None] | None = None,
              diagnose: Callable[..., dict | None] | None = None,
              worktrees: Callable[[StudySpec], Any] = _worktrees, mode: Mode = "real",
              dsn: str | None = None, calls: Callable[[str, str], list[dict[str, Any]]] = _calls,
              preflight: Preflight | None = None) -> list[Outcome]:
    """Run every planned run of `spec` in order, each run, grade, and audit under a hard cap that fits in what
    remains of the ceiling (see the module docstring). Dry and cheap runs are also checked against the
    invariants (dryrun.check_record)."""
    from .dryrun import check_record

    frozen_settings = Settings()
    frozen_models, frozen_model_calls = frozen_settings.models, frozen_settings.model_calls
    grade = grade or _grade_with(_invoke_output)
    audit = audit or _audit_with(_invoke_output)
    diagnose = diagnose or _diagnose_with(_invoke_output)
    preflight = preflight or _preflight
    if (worst := spec.worst_case_usd()) > spec.ceiling_usd:
        raise StudyCeilingError(f"the planned runs could cost ${worst:.2f} by their estimates, over the "
                         f"${spec.ceiling_usd:.2f} ceiling; raise the ceiling or plan fewer runs")
    outcomes: list[Outcome] = []
    ceiling = _usd(spec.ceiling_usd)
    spent = Decimal(0)  # what finished runs charged against the ceiling (Outcome.charged_usd)
    grade_reserve = _usd(spec.grade_estimate_usd) if spec.grade else Decimal(0)
    audit_reserve = _usd(spec.audit_estimate_usd) if spec.audit else Decimal(0)
    diagnose_reserve = _usd(spec.diagnose_estimate_usd) if spec.diagnose else Decimal(0)
    with worktrees(spec) as trees:
        # Every arm and enabled evaluation model is checked before any arm spends money.
        from .config import model_spec_problem, split_model
        from .prices import price_per_million

        firsts = {run.arm.name: run for run in reversed(schedule(spec))}
        refusals = []
        for name, run in sorted(firsts.items()):
            env = _run_env(run, mode, dsn, trees, frozen_models, frozen_model_calls)
            args = command(spec, run, Path(tempfile.gettempdir()), mode, models=frozen_models)
            if spec.kind in _ROLE_OF_MODEL and "--model" in args:
                env[f"RESEARCH_MODELS__{_ROLE_OF_MODEL[spec.kind]}"] = args[args.index("--model") + 1]
            if problem := preflight(env):
                refusals.append(f"arm {name}: {problem}")
            for stage, enabled, selected in (("audit", spec.audit, spec.audit_model),
                                             ("diagnose", spec.diagnose, spec.diagnose_model)):
                if not enabled:
                    continue
                model = mode_model(selected, mode, stage, env, frozen_models)
                if problem := model_spec_problem(model):
                    refusals.append(f"arm {name} {stage}: {problem}")
                    continue
                if price_per_million(split_model(model)[0]) is None:
                    refusals.append(f"arm {name} {stage}: {model} has no price")
                    continue
                if problem := preflight(env | {"RESEARCH_MODELS__JUDGE": model}):
                    refusals.append(f"arm {name} {stage}: {problem}")
        if refusals:
            raise StudyConfigError("; ".join(refusals))
        for run in schedule(spec):
            run_cap = _cap(spec.cap_usd, ceiling - spent - grade_reserve - audit_reserve - diagnose_reserve)
            if spent + _usd(spec.per_run_usd()) > ceiling or run_cap <= 0:
                note = f"not run: ${spent:.2f} spent, and another run could pass the ${ceiling:.2f} ceiling"
                outcomes.append(Outcome(run, -1, notes=[note]))
                continue
            out = out_root / run.label
            env = _run_env(run, mode, dsn, trees, frozen_models, frozen_model_calls)
            audit_model = mode_model(spec.audit_model, mode, "audit", env, frozen_models)
            diagnose_model = mode_model(spec.diagnose_model, mode, "diagnose", env, frozen_models)
            code, stderr = invoke(command(spec, run, out, mode, run_cap, frozen_models), env)
            outcome = Outcome(run, code, diagnosis_model=diagnose_model, mode=mode)
            if (out / "run.json").exists():
                outcome.record = json.loads((out / "run.json").read_text(encoding="utf-8"))
            if (outcome.record or {}).get("cost_usd") is None:
                outcome.unaccounted_usd += run_cap
                outcome.notes.append(f"the run's cost is unknown, so its ${run_cap:.2f} cap counts against the ceiling")
            stored = (calls(env.get("DATABASE_URL", dsn), outcome.record["run_id"])
                      if dsn and outcome.record else None)
            if stored is not None:
                outcome.tools = tool_counts(stored)
            if mode != "real":
                if "Traceback (most recent call last)" in stderr:
                    outcome.violations.append("a traceback in the command's output: "
                                              + stderr.strip().splitlines()[-1][:200])
                if outcome.record is None:
                    outcome.violations.append(f"no run record was written (exit {code})")
                elif stored is not None:
                    outcome.violations += check_record(outcome.record, stored)
            case_id = _case_id(outcome.record)
            if spec.grade and outcome.record and outcome.record.get("report") and case_id:
                grade_cap = _cap(spec.grade_cap_usd,
                                 ceiling - spent - outcome.charged_usd - audit_reserve - diagnose_reserve)
                if grade_cap <= 0:
                    outcome.notes.append("not graded: the ceiling leaves no room")
                else:
                    outcome.grade = grade(outcome.record["run_id"], case_id, env, float(grade_cap))
                    if outcome.grade is None:
                        outcome.notes.append("grading failed; see the output above")
                    elif "unreadable" in outcome.grade:
                        outcome.notes.append(f"the grade line could not be read: {outcome.grade['unreadable']}")
                        outcome.violations.append("research grade succeeded but its line could not be read")
                        outcome.grade = None
                    if outcome.grade is None:
                        outcome.unaccounted_usd += grade_cap
            if spec.audit and outcome.record and outcome.record.get("report"):
                audit_cap = _cap(spec.audit_cap_usd, ceiling - spent - outcome.charged_usd - diagnose_reserve)
                if audit_cap <= 0:
                    outcome.notes.append("not audited: the ceiling leaves no room")
                else:
                    outcome.audit = audit(outcome.record["run_id"], audit_model, env, float(audit_cap))
                    if outcome.audit is None:
                        outcome.notes.append("the support audit failed; see the output above")
                    elif "unreadable" in outcome.audit:
                        outcome.notes.append(f"the audit line could not be read: {outcome.audit['unreadable']}")
                        outcome.violations.append("research audit succeeded but its line could not be read")
                        outcome.audit = None
                    if outcome.audit is None:
                        outcome.unaccounted_usd += audit_cap
            # A rescout has no report; its diagnosis grades its claims and research alone.
            if spec.diagnose and outcome.record and (outcome.record.get("report") or spec.kind == "rescout") and case_id:
                diagnose_cap = _cap(spec.diagnose_cap_usd, ceiling - spent - outcome.charged_usd)
                if diagnose_cap <= 0:
                    outcome.notes.append("not diagnosed: the ceiling leaves no room")
                else:
                    # The current code diagnoses every arm's run, since an older arm's code has no diagnosis.
                    current = {name: value for name, value in env.items() if name != "PYTHONPATH"}
                    outcome.diagnosis = diagnose(outcome.record["run_id"], diagnose_model, current, float(diagnose_cap))
                    if outcome.diagnosis is None:
                        outcome.notes.append("the diagnosis failed; see the output above")
                    elif outcome.diagnosis.get("incomplete"):
                        outcome.notes.append("the diagnosis is incomplete: a grade failed; see the output above")
                    elif "unreadable" in outcome.diagnosis:
                        outcome.notes.append(f"the diagnosis line could not be read: {outcome.diagnosis['unreadable']}")
                        outcome.violations.append("research diagnose succeeded but its line could not be read")
                        outcome.diagnosis = None
                    if outcome.diagnosis is None:
                        outcome.unaccounted_usd += diagnose_cap
            spent += outcome.charged_usd
            outcomes.append(outcome)
    return outcomes


def study_diagnosis(spec: StudySpec, outcomes: list[Outcome], mode: Mode = "real", dsn: str | None = None,
                    invoke_output: Callable[[list[str], dict[str, str]], tuple[int, str]] | None = None) -> str:
    """The diagnosis of every diagnosed run together, from stored grades only (`research diagnose --free`):
    where points were lost by arm, and whether the scores can show a change. Empty when nothing was diagnosed."""
    diagnosed = [o for o in outcomes if o.diagnosis is not None and o.record]
    if not spec.diagnose or not diagnosed:
        return ""
    invoke_output = invoke_output or _invoke_output
    # A model or database override may differ by arm. Reuse stored grades only with the model that made them.
    groups: dict[tuple[str, str | None], tuple[dict[str, str], list[str]]] = {}
    for outcome in diagnosed:
        env = outcome.run.arm.env | mode_env(mode, outcome.run, dsn)
        env.pop("PYTHONPATH", None)
        model = (outcome.diagnosis_model if mode == outcome.mode else None) or mode_model(
            spec.diagnose_model, mode, "diagnose", env)
        key = model, env.get("DATABASE_URL", dsn)
        if key not in groups:
            groups[key] = env, []
        groups[key][1].append(outcome.record["run_id"])
    tables = []
    for (model, _dsn), (env, run_ids) in groups.items():
        code, output = invoke_output(["diagnose", *run_ids, "--model", model, "--free"], env)
        table = output.split("\n\n", 1)[1] if "\n\n" in output else ""
        tables.append(table if code == 0 or table else
                      f"The study's diagnosis failed (exit {code}); see the output above.\n")
    return "\n".join(table.rstrip("\n") for table in tables if table).rstrip("\n") + "\n"


def tool_counts(calls: list[dict[str, Any]]) -> dict[str, int]:
    """How a run's web searches and page reads came back, from its stored calls: searches that found results,
    found nothing, or failed; distinct pages read in full or in part; and page reads that failed, blocked
    sources included. A search chain counts once, by what its last engine returned."""
    counts = dict.fromkeys(("searches_found", "searches_empty", "searches_failed", "pages_failed"), 0)
    read: set[str] = set()
    for call in calls:
        for message in call.get("messages") or []:
            for part in message.get("parts") or []:
                content = part.get("content")
                if part.get("part_kind") != "tool-return" or not isinstance(content, dict):
                    continue
                if part.get("tool_name") == "web_search":
                    outcome = "found" if content.get("results") else "failed" if content.get("error") else "empty"
                    counts[f"searches_{outcome}"] += 1
                elif part.get("tool_name") == "fetch":
                    if content.get("text"):
                        read.add(str(content.get("url") or ""))
                    elif content.get("error"):
                        counts["pages_failed"] += 1
    return counts | {"pages_read": len(read)}


def _tools_cell(tools: dict[str, int] | None) -> str:
    if tools is None:
        return ""
    return (f"{tools['searches_found']} / {tools['searches_empty']} / {tools['searches_failed']} · "
            f"{tools['pages_read']} / {tools['pages_failed']}")


def _audit_cell(audit: dict[str, Any] | None) -> str:
    if not audit:
        return ""
    counts = audit["counts"]
    return " / ".join(str(counts.get(verdict, 0)) for verdict in ("supported", "partial", "unsupported", "no_quote"))


def _case_id(record: dict[str, Any] | None) -> str | None:
    return (((record or {}).get("config") or {}).get("case") or {}).get("id")


def summary(spec: StudySpec, outcomes: list[Outcome]) -> str:
    """A Markdown table of every planned run, the ones not run included."""
    header = ("| Target | Arm | Rep | Run | Status | Answer | Cost | Time | Searches found / empty / failed · pages "
              "read / failed | Quotes verified / misattributed / not found (short) | Statements quoted / summary only "
              "/ thin | Audit supported / partial / unsupported / no quote | Coverage found (named) | Grade |")
    lines = [f"# Study {spec.study}", "", header, "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for outcome in outcomes:
        record, run = outcome.record or {}, outcome.run
        checks = record.get("checks") or {}
        quotes = checks.get("quotes") or 0
        verified, wrong = checks.get("quotes_verified") or 0, checks.get("quotes_misattributed") or 0
        short = f" ({checks['quotes_short']})" if checks.get("quotes_short") else ""
        support = [statement.get("support") for statement in checks.get("statements") or []]
        statements = (f"{support.count('read')} / {support.count('paraphrase')} / "
                      f"{len(support) - support.count('read') - support.count('paraphrase')}") if support else ""
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
                 _tools_cell(outcome.tools),
                 f"{verified} / {wrong} / {quotes - verified - wrong}{short}" if quotes else "",
                 statements, _audit_cell(outcome.audit), cover, f"{grade['met']}/{grade['points']}" if grade else ""]
        lines.append("| " + " | ".join(cells) + " |")
    spent = sum(outcome.cost_usd for outcome in outcomes)
    unaccounted = sum((outcome.unaccounted_usd for outcome in outcomes), Decimal(0))
    lines += ["", f"Total ${spent:.2f} of a ${spec.ceiling_usd:.2f} ceiling"
              + (f", plus up to ${unaccounted:.2f} from steps whose cost could not be read (research breakdown "
                 "shows a stored run's cost)." if unaccounted else ".")]
    lines += [f"- {o.run.label}: {note}" for o in outcomes for note in o.notes]
    if violations := [(o.run.label, v) for o in outcomes for v in o.violations]:
        lines += ["", f"## {len(violations)} invariant violations", ""]
        lines += [f"- {label}: {violation}" for label, violation in violations]
    return "\n".join(lines) + "\n"

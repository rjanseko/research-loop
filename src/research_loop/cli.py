"""The `research` command: run Scout, research or synthesize a stored run again, show, break down, or grade a
stored run, check the setup, and manage the database."""
from __future__ import annotations

import argparse
import asyncio
import json
import signal
import sys
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import ValidationError

from .config import Settings


def _settings(parser: argparse.ArgumentParser) -> Settings:
    try:
        return Settings()
    except ValidationError as exc:
        parser.exit(2, f"Configuration is invalid:\n{exc}\n")


def _record_from_row(row: dict[str, Any]) -> dict[str, Any]:
    seconds = (row["finished_at"] - row["started_at"]).total_seconds() if row.get("finished_at") else None
    return {"run_id": str(row["id"]), "question": row["question"], "status": row["status"], "plan": row["plan"],
            "report": row["report"], "ledger": row["ledger"] or {}, "checks": row["checks"] or {},
            "cost_usd": row["cost_usd"], "seconds": seconds, "trace_id": row["trace_id"], "config": row["config"],
            "error": row["error"]}


def _checked(settings: Settings, study: str | None) -> Settings | None:
    """`settings` for a paid command, with its study's cache when it has one, or None after saying why the
    configured models cannot run."""
    from .scout import ConfigError, check_config

    try:
        if study:
            settings = settings.for_study(study)
        check_config(settings)
    except (ConfigError, ValueError) as exc:
        print(f"Cannot run: {exc}", file=sys.stderr)
        return None
    return settings


def _start_tracing(settings: Settings) -> None:
    import faulthandler

    from .telemetry import configure_logfire

    # A native parser fault otherwise leaves only exit 139 and unfinished rows.
    faulthandler.enable(file=sys.stderr, all_threads=True)
    configure_logfire(settings)


def _report(run: Any, out: Path | None, summary: str) -> int:
    """Print a finished run as Markdown, write it to `out` when given, and end with `summary`."""
    from .render import render_markdown

    record = run.to_record()
    markdown = render_markdown(record)
    print(markdown)
    if out:
        out.mkdir(parents=True, exist_ok=True)
        (out / "report.md").write_text(markdown, encoding="utf-8")
        (out / "run.json").write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Wrote {out / 'report.md'} and {out / 'run.json'}", file=sys.stderr)
    print(summary, file=sys.stderr)
    return 1 if run.status == "failed" else 0


async def _scout(args: argparse.Namespace, settings: Settings) -> int:
    from .db import open_migrated_pool
    from .evals import case_blocked_titles, find_case
    from .evals import case_identity as frozen_case_identity
    from .scout import StudyLabels, scout
    from .store import MemoryStore, PostgresStore
    from .study_budget import StudyBudget

    try:
        case = find_case(args.case) if args.case else None
    except KeyError as exc:
        print(exc.args[0], file=sys.stderr)
        return 2
    if (checked := _checked(settings, args.study)) is None:
        return 2
    settings = checked
    _start_tracing(settings)
    limits, models = settings.limits, settings.models
    budget = StudyBudget(args.max_usd) if args.max_usd is not None else None
    question = case.objective if case else args.question
    blocked = case.blocked_urls if case else args.block
    titles = case_blocked_titles(frozen_case_identity(case)) if case else args.block_title
    case_identity = None
    if case:
        case_identity = frozen_case_identity(case)
        if case.metadata.get("role") == "held-out":
            print(f"{case.id} is a held-out case: run it to confirm a change, not while tuning one.", file=sys.stderr)
    depth = None if args.depth == "auto" else args.depth

    def envelope(chosen: str) -> str:
        tier, follow = limits.for_depth(chosen), args.follow_up or limits.follows_up(chosen)
        cost = tier.followup_cost_usd if follow else tier.cost_usd
        seconds = tier.followup_deadline_seconds if follow else tier.deadline_seconds
        return f"{chosen}{' with gap follow-up' if follow else ''}: ${cost:.2f}, {seconds / 60:.0f} minutes"

    limit_text = (envelope(depth) if depth else
                  "depth chosen by the planner (" + "; ".join(envelope(d) for d in ("quick", "standard", "deep")) + ")")
    guard = f"; ${budget.cap_usd:.2f} pre-dispatch cap" if budget else ""
    print(f"Scout, soft limits {limit_text}{guard}; planner {models.planner}, "
          f"scouts {models.scout}, synthesizer {models.synthesizer}; cache {settings.cache_dir} ({settings.cache_mode}).",
          file=sys.stderr)
    async with AsyncExitStack() as stack:
        if settings.database_dsn and not args.no_persist:
            store: Any = PostgresStore(await open_migrated_pool(stack, settings.database_dsn))
        else:
            store = MemoryStore()
        study = StudyLabels(args.study, args.arm, args.replicate) if args.study else None
        run = await scout(question, settings=settings, store=store, notes=args.note, blocked_urls=blocked,
                          study=study, follow_up=args.follow_up, budget=budget, case_identity=case_identity,
                          depth=depth, blocked_titles=titles)
    stored = "stored" if isinstance(store, PostgresStore) else "not stored (no DATABASE_URL, or --no-persist)"
    support = f", answer {run.checks.answer_support}" if run.checks.answer_support else ""
    return _report(run, args.out, f"Run {run.run_id}: {run.status}{support}, ${run.cost_usd:.2f}, "
                                  f"{run.seconds / 60:.1f} minutes, {stored}.")


async def _rerun(args: argparse.Namespace, settings: Settings, *, role: str, verb: str,
                 rerun: Callable[..., Awaitable[Any]], describe: Callable[[Settings, Any], str]) -> int:
    """A paid command that repeats one step of a stored run with the model in `--model` for `role`."""
    from .db import open_migrated_pool
    from .scout import SourceRunError, StudyLabels
    from .store import PostgresStore, load_run
    from .study_budget import StudyBudget

    if role == "synthesizer":
        from .config import synthesizer_spec
        try:
            synthesizer_spec(args.model)
        except ValueError as exc:
            print(f"research {verb}: {exc}", file=sys.stderr)
            return 2
    settings = settings.model_copy(update={"models": settings.models.model_copy(update={role: args.model})})
    if (checked := _checked(settings, args.study)) is None:
        return 2
    settings = checked
    _start_tracing(settings)
    async with AsyncExitStack() as stack:
        pool = await open_migrated_pool(stack, settings.database_dsn)
        source = await load_run(pool, args.source_run_id)
        if source is None:
            print(f"No source run {args.source_run_id}", file=sys.stderr)
            return 1
        study = StudyLabels(args.study, args.arm, args.replicate) if args.study else None
        budget = StudyBudget(args.max_usd)
        print(describe(settings, budget) + "; this is a paid run.", file=sys.stderr)
        try:
            run = await rerun(source, settings=settings, store=PostgresStore(pool), study=study, budget=budget)
        except SourceRunError as exc:
            print(f"Cannot {verb}: {exc}", file=sys.stderr)
            return 2
    support = f", answer {run.checks.answer_support}" if run.checks.answer_support else ""
    return _report(run, args.out, f"Run {run.run_id}: {run.status}{support}, ${run.cost_usd:.4f}, "
                                  f"{run.seconds / 60:.1f} minutes; source {args.source_run_id}.")


async def _synthesize(args: argparse.Namespace, settings: Settings) -> int:
    from .scout import synthesize_stored

    return await _rerun(args, settings, role="synthesizer", verb="synthesize", rerun=synthesize_stored,
                        describe=lambda settings, budget: (f"Fixed-ledger synthesis: {args.model}, "
                                                           f"${budget.cap_usd:.2f} pre-dispatch cap"))


async def _rescout(args: argparse.Namespace, settings: Settings) -> int:
    from .scout import rescout_stored

    return await _rerun(args, settings, role="scout", verb="rescout", rerun=rescout_stored,
                        describe=lambda settings, budget: (
                            f"Fixed-plan research: scouts {args.model}, ${budget.cap_usd:.2f} pre-dispatch cap, "
                            f"cache {settings.cache_dir} ({settings.cache_mode})"))


async def _fuzz(args: argparse.Namespace) -> int:
    import logging
    import os

    from .dryrun import fuzz, fuzz_one, fuzz_report

    # Fake runs make no network calls and should print nothing but findings.
    os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")
    os.environ.setdefault("LOGFIRE_IGNORE_NO_CONFIG", "1")
    logging.getLogger("logfire").setLevel(logging.ERROR)
    if args.one is not None:
        findings = await fuzz_one(args.one, args.fault_rate)
        for finding in findings:
            print(f"{finding.kind}:")
            print("\n".join(f"  {problem}" for problem in finding.problems))
        print(f"Seed {args.one}: {'no problems' if not findings else f'{len(findings)} findings'}.", file=sys.stderr)
        return 1 if findings else 0
    findings = await fuzz(args.runs, args.seed, args.fault_rate)
    print(fuzz_report(findings, args.runs, args.fault_rate))
    return 1 if findings else 0


def _study(args: argparse.Namespace, settings: Settings) -> int:
    from pydantic import ValidationError

    from .study import (
        REPO,
        StudyCeilingError,
        StudyConfigError,
        copy_sources_to_dry,
        dry_database_url,
        for_mode,
        load_spec,
        run_study,
        schedule,
        study_diagnosis,
        summary,
    )

    try:
        spec = load_spec(args.spec)
    except (OSError, ValueError, ValidationError) as exc:
        print(f"Cannot read {args.spec}: {exc}", file=sys.stderr)
        return 2
    mode = "dry" if args.dry else "cheap" if args.cheap else "real"
    spec = for_mode(spec, mode, args.seeds)
    if mode == "dry":
        print(f"Dry study: fake models, the offline world, database {dry_database_url(settings.database_dsn or '')}; "
              "nothing is paid and nothing reaches the network.", file=sys.stderr)
    planned = schedule(spec)
    print(f"Study {spec.study}: {len(planned)} {spec.kind} runs over {len(spec.arms)} arms, "
          f"worst case ${spec.worst_case_usd():.2f} by the estimates, ceiling ${spec.ceiling_usd:.2f}"
          + ("; this is a paid study." if args.study_command == "run" and mode != "dry" else "."), file=sys.stderr)
    if args.study_command == "plan":
        for run in planned:
            print(f"{run.label}: {run.target} {run.arm.name} replicate {run.replicate}"
                  + (f" at {run.arm.ref}" if run.arm.ref else ""))
        return 0 if spec.worst_case_usd() <= spec.ceiling_usd else 2
    # A dry rescout or synthesis study needs its source runs in the dry database.
    if (mode == "dry" and spec.kind != "scout" and settings.database_dsn
            and (missing := copy_sources_to_dry(settings.database_dsn, spec.sources))):
        print(f"Cannot run: no stored run {', '.join(missing)}", file=sys.stderr)
        return 2
    out = args.out or REPO / "runs" / spec.study
    try:
        outcomes = run_study(spec, out, mode=mode, dsn=settings.database_dsn)
    except (StudyCeilingError, StudyConfigError) as exc:
        print(f"Cannot run: {exc}", file=sys.stderr)
        return 2
    table = summary(spec, outcomes)
    if diagnosis := study_diagnosis(spec, outcomes, mode, settings.database_dsn):
        table += "\n" + diagnosis
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.md").write_text(table, encoding="utf-8")
    print(table)
    print(f"Wrote {out / 'summary.md'}", file=sys.stderr)
    if mode != "real":
        # Fuzzed runs fail on purpose; only broken invariants mark a dry or cheap study as failed.
        return 1 if any(o.violations for o in outcomes) else 0
    return 1 if any(o.exit_code not in (0, -1) for o in outcomes) else 0


async def _show(args: argparse.Namespace, settings: Settings) -> int:
    from .db import open_migrated_pool
    from .render import render_markdown
    from .store import load_run

    async with AsyncExitStack() as stack:
        row = await load_run(await open_migrated_pool(stack, settings.database_dsn), args.run_id)
    if row is None:
        print(f"No run {args.run_id}", file=sys.stderr)
        return 1
    record = _record_from_row(row)
    print(json.dumps(record, indent=2, ensure_ascii=False, default=str) if args.format == "json" else
          render_markdown(record, include_provenance=args.provenance))
    return 0


async def _breakdown(args: argparse.Namespace, settings: Settings) -> int:
    from .breakdown import breakdown
    from .db import open_migrated_pool
    from .store import load_calls, load_run

    async with AsyncExitStack() as stack:
        pool = await open_migrated_pool(stack, settings.database_dsn)
        row = await load_run(pool, args.run_id)
        calls = await load_calls(pool, args.run_id) if row else []
    if row is None:
        print(f"No run {args.run_id}", file=sys.stderr)
        return 1
    print(breakdown(row, calls), end="")
    return 0


async def _grade(args: argparse.Namespace, settings: Settings) -> int:
    from .db import open_migrated_pool
    from .evals import (
        JUDGE_VERSION,
        StoredReport,
        find_case,
        grade_reports,
        grade_row,
        matches_frozen_case,
        reader_text,
    )
    from .evidence import EvidenceLedger
    from .schemas import FinalReport
    from .store import load_run, save_grade
    from .study_budget import StudyBudget

    try:
        case = find_case(args.case)
    except KeyError as exc:
        print(exc.args[0], file=sys.stderr)
        return 2
    if not case.rubrics:
        print(f"{case.id} is scored by exact answer, not a rubric; only rubric grading is ported so far.", file=sys.stderr)
        return 2
    async with AsyncExitStack() as stack:
        pool = await open_migrated_pool(stack, settings.database_dsn)
        row = await load_run(pool, args.run_id)
        if row is None or not row.get("report"):
            print(f"Run {args.run_id} {'has no report' if row else 'does not exist'}", file=sys.stderr)
            return 1
        if case.id.startswith("drb2-") and not matches_frozen_case(row, case):
            print(f"Run {args.run_id} was not recorded for frozen case {case.id}.", file=sys.stderr)
            return 2
        text = reader_text(FinalReport.model_validate(row["report"]), EvidenceLedger.from_json(row["ledger"] or {}))
        budget = StudyBudget(args.max_usd)
        print(f"Grading run {args.run_id} against {case.id} (rubric v{case.rubric_version}) with {settings.models.judge}, "
              f"judge v{JUDGE_VERSION}, ${budget.cap_usd:.2f} pre-dispatch cap; this is a paid call.", file=sys.stderr)
        _, grades = await grade_reports([StoredReport(case, args.run_id, text)], settings, budget=budget)
        for grade in grades:
            await save_grade(pool, grade_row(grade))
    for grade in grades:
        cost = f"${grade.cost_usd:.4f}" if grade.cost_usd is not None else "unpriced"
        if grade.status != "succeeded":
            print(f"Grade failed ({type(grade.error).__name__}), {cost}; recorded as {grade.id}.", file=sys.stderr)
            return 1
        met = sum(p["met"] for p in grade.points)
        print(f"{case.id}: {met} of {len(grade.points)} points ({grade.score:.3f}), {cost}. "
              f"Unmet: {', '.join(grade.unmet()) or 'none'}. Recorded as {grade.id}.")
    return 0


async def _audit(args: argparse.Namespace, settings: Settings) -> int:
    from pydantic import ValidationError
    from pydantic_ai.messages import ModelMessagesTypeAdapter

    from .audit import AUDIT_VERSION, audit, audit_row
    from .config import ScoutModels, split_model
    from .db import open_migrated_pool
    from .evidence import EvidenceLedger
    from .prices import price_per_million
    from .schemas import FinalReport
    from .store import load_research_messages, load_run, save_support_audit
    from .study_budget import StudyBudget
    from .tools import source_records

    model_spec = settings.models.audit if args.model is None else args.model
    try:
        settings = settings.model_copy(update={"models": ScoutModels.model_validate(
            settings.models.model_dump() | {"judge": model_spec})})
    except ValidationError as exc:
        print(f"Cannot run: {exc.errors()[0]['msg']}", file=sys.stderr)
        return 2
    problems = [problem for problem in settings.route_problems() if problem.startswith("judge:")]
    if price_per_million(split_model(model_spec)[0]) is None:
        problems.append(f"{model_spec} has no price, so its cost cannot be capped")
    if problems:
        print(f"Cannot run: {'; '.join(problems)}", file=sys.stderr)
        return 2
    # One cap across every run audited, so a batch cannot pass it.
    budget = StudyBudget(args.max_usd)
    print(f"Auditing {len(args.run_ids)} run(s) with {model_spec}, audit v{AUDIT_VERSION}, "
          f"${budget.cap_usd:.2f} pre-dispatch cap across them; this makes paid calls.", file=sys.stderr)
    code = 0
    async with AsyncExitStack() as stack:
        pool = await open_migrated_pool(stack, settings.database_dsn)
        for run_id in args.run_ids:
            row = await load_run(pool, run_id)
            if row is None or not row.get("report"):
                print(f"Run {run_id} {'has no report' if row else 'does not exist'}", file=sys.stderr)
                code = 1
                continue
            # What the run's tools returned about its sources, so the auditor can check details that identify them.
            records = [record for messages in await load_research_messages(pool, run_id)
                       for record in source_records(ModelMessagesTypeAdapter.validate_python(messages))]
            record = await audit(FinalReport.model_validate(row["report"]), EvidenceLedger.from_json(row["ledger"] or {}),
                                 row["question"], run_id, settings, budget=budget, records=records,
                                 evidence_version=(row.get("config") or {}).get("evidence_version"))
            await save_support_audit(pool, audit_row(record))
            cost = f"${record.cost_usd:.4f}" if record.cost_usd is not None else "$0"
            if record.status != "succeeded":
                print(f"{run_id}: audit failed ({type(record.error).__name__}), {cost}; recorded as {record.id}.",
                      file=sys.stderr)
                code = 1
                continue
            counts = ", ".join(f"{n} {verdict}" for verdict, n in sorted(record.counts.items()))
            print(f"{run_id}: {counts}; {cost}. Recorded as {record.id}.")
    return code


async def _diagnose(args: argparse.Namespace, settings: Settings) -> int:
    from pydantic import ValidationError

    from .config import ScoutModels, split_model
    from .db import open_migrated_pool
    from .diagnose import (
        DIAGNOSE_VERSION,
        VIEW_TEXT,
        RunDiagnosis,
        render,
        stage_grade_row,
        verdicts,
    )
    from .evals import (
        JUDGE_VERSION,
        find_case,
        grade_row,
        judge,
        matches_frozen_case,
        reader_text,
    )
    from .evidence import EvidenceLedger
    from .prices import price_per_million
    from .schemas import FinalReport
    from .store import (
        load_case_verdicts,
        load_grades,
        load_run,
        load_stage_grades,
        save_grade,
        save_stage_grade,
    )
    from .study_budget import StudyBudget

    model_spec = settings.models.diagnose if args.model is None else args.model
    try:
        settings = settings.model_copy(update={"models": ScoutModels.model_validate(
            settings.models.model_dump() | {"judge": model_spec})})
    except ValidationError as exc:
        print(f"Cannot run: {exc.errors()[0]['msg']}", file=sys.stderr)
        return 2
    model_id, thinking = split_model(model_spec)
    if not args.free:
        problems = [problem for problem in settings.route_problems() if problem.startswith("judge:")]
        if price_per_million(model_id) is None:
            problems.append(f"{model_spec} has no price, so its cost cannot be capped")
        if problems:
            print(f"Cannot run: {'; '.join(problems)}", file=sys.stderr)
            return 2
    # One cap across every run, so a batch cannot pass it.
    budget = None if args.free else StudyBudget(args.max_usd)
    print(f"Diagnosing {len(args.run_ids)} run(s) with {model_spec}, diagnose v{DIAGNOSE_VERSION}, "
          + ("stored grades only." if budget is None else
             f"${budget.cap_usd:.2f} pre-dispatch cap across them; this makes paid calls."), file=sys.stderr)

    def stored(rows: list[dict[str, Any]], run_id: UUID, rubric_version: str, view: str | None = None) -> dict | None:
        """The latest succeeded grade of `run_id` by this judge, prompt, and rubric, for `view` when given."""
        matches = [row for row in rows if row["run_id"] == run_id and row["status"] == "succeeded"
                   and row["judge_model"] == model_id and row["judge_thinking"] == thinking
                   and row["judge_version"] == JUDGE_VERSION and row["rubric_version"] == rubric_version
                   and (view is None or (row["view"] == view and row["diagnose_version"] == DIAGNOSE_VERSION))]
        return matches[-1] if matches else None

    code = 0
    diagnoses: list[RunDiagnosis] = []
    async with AsyncExitStack() as stack:
        pool = await open_migrated_pool(stack, settings.database_dsn)
        report_grades = list(await load_grades(pool, args.run_ids))
        stage_grades = list(await load_stage_grades(pool, args.run_ids))
        for run_id in args.run_ids:
            row = await load_run(pool, run_id)
            # A fixed-plan rescout writes no report; its claims and research are graded alone.
            rescout = row is not None and row.get("mode") == "fixed-plan"
            if row is None or not (row.get("report") or rescout):
                print(f"Run {run_id} {'has no report' if row else 'does not exist'}", file=sys.stderr)
                code = 1
                continue
            case_id = ((row.get("config") or {}).get("case") or {}).get("id")
            try:
                case = find_case(case_id) if case_id else None
            except KeyError:
                case = None
            if case is None or not case.rubrics or (case.id.startswith("drb2-") and not matches_frozen_case(row, case)):
                print(f"Run {run_id} was not recorded for a rubric case, so it has nothing to be graded against.",
                      file=sys.stderr)
                code = 1
                continue
            ledger = EvidenceLedger.from_json(row["ledger"] or {})
            text = reader_text(FinalReport.model_validate(row["report"]), ledger) if row.get("report") else ""
            spent, new, reused, failed = Decimal(0), 0, 0, []
            found: dict[str, list[dict[str, Any]]] = {}
            views = {**({"report": text} if text else {}), **{view: make(ledger) for view, make in VIEW_TEXT.items()}}
            for view, view_text in views.items():
                prior = stored(report_grades if view == "report" else stage_grades, run_id, case.rubric_version,
                               None if view == "report" else view)
                if prior is not None:
                    found[view], reused = prior["points"], reused + 1
                    continue
                if budget is None:
                    continue
                grade = await judge(view_text, case, run_id, settings, budget=budget)
                if view == "report":
                    saved = grade_row(grade)
                    await save_grade(pool, saved)
                    report_grades.append(saved)
                else:
                    await save_stage_grade(pool, stage_grade_row(grade, view))
                new, spent = new + 1, spent + (grade.cost_usd or Decimal(0))
                if grade.status == "succeeded":
                    found[view] = grade.points
                else:
                    failed.append(f"{view} ({type(grade.error).__name__})")
            if "report" in found or (not text and "claims" in found):
                diagnoses.append(RunDiagnosis(run_id, case, text, verdicts(found["report"]) if text else None,
                                              claims=verdicts(found["claims"]) if "claims" in found else None,
                                              research=verdicts(found["research"]) if "research" in found else None,
                                              arm=row.get("arm")))
            if failed:
                code = 1
                print(f"{run_id}: the diagnosis is incomplete; these grades failed: {', '.join(failed)}.", file=sys.stderr)
            print(f"{run_id}: diagnosed with {model_spec}, {new} new grade(s) and {reused} reused; ${spent:.4f}."
                  + (f" Failed: {', '.join(failed)}." if failed else ""))
        history = {d.case.id: await load_case_verdicts(pool, d.case.id, d.case.rubric_version) for d in diagnoses}
    if diagnoses:
        print()
        print(render(diagnoses, model_spec, report_grades, history))
    return code


async def _assess(args: argparse.Namespace, settings: Settings) -> int:
    from .db import open_migrated_pool
    from .evidence import EvidenceLedger
    from .quality import (
        QUALITY_VERSION,
        StoredQualityReport,
        assess_reports,
        find_quality_packet,
        quality_row,
    )
    from .schemas import FinalReport
    from .store import load_run, save_quality_assessment
    from .study_budget import StudyBudget

    try:
        packet = find_quality_packet(args.case)
    except KeyError as exc:
        print(exc.args[0], file=sys.stderr)
        return 2
    async with AsyncExitStack() as stack:
        pool = await open_migrated_pool(stack, settings.database_dsn)
        row = await load_run(pool, args.run_id)
        if row is None or not row.get("report"):
            print(f"Run {args.run_id} {'has no report' if row else 'does not exist'}", file=sys.stderr)
            return 1
        if row["question"] != packet.question:
            print(f"Run question does not match frozen packet {packet.case_id} v{packet.version}.", file=sys.stderr)
            return 2
        item = StoredQualityReport(args.run_id, FinalReport.model_validate(row["report"]),
                                   EvidenceLedger.from_json(row["ledger"] or {}), packet, row["checks"] or {})
        budget = StudyBudget(args.max_usd)
        print(f"Assessing {args.run_id} with {settings.models.judge}, evaluator v{QUALITY_VERSION}, "
              f"packet {packet.case_id} v{packet.version}, ${budget.cap_usd:.2f} pre-dispatch cap; "
              f"this is a paid call.", file=sys.stderr)
        _, records = await assess_reports([item], settings, budget=budget)
        for record in records:
            await save_quality_assessment(pool, quality_row(record))
    record = records[0]
    cost = f"${record.cost_usd:.4f}" if record.cost_usd is not None else "unpriced"
    if record.judgment is None:
        print(f"Assessment failed ({type(record.error).__name__}), {cost}; recorded as {record.id}.", file=sys.stderr)
        return 1
    facts = {finding: sum(check.finding == finding for check in record.judgment.fact_checks)
             for finding in ("correct", "incorrect", "omitted", "unresolved")}
    print(f"Overall {record.judgment.overall_level}/3; facts {facts}; {cost}. Recorded as {record.id}.")
    return 0


def _db(args: argparse.Namespace, settings: Settings, parser: argparse.ArgumentParser) -> int:
    import psycopg

    from .db import apply_migrations, migration_files, migration_status, reconcile

    migrations = migration_files()
    try:
        with psycopg.connect(settings.database_dsn, autocommit=True, connect_timeout=5) as conn:
            if args.db_command == "reconcile":
                runs, calls = reconcile(conn, args.older_than, apply=args.apply)
                verb = "Marked" if args.apply else "Would mark"
                print(f"{verb} {runs} run(s) and {calls} call(s) failed (Abandoned)."
                      + ("" if args.apply else " Pass --apply to do it."))
                return 0
            if args.db_command == "migrate":
                print(f"Applied {len(apply_migrations(conn, migrations))} migration(s).")
            for migration, state in migration_status(conn, migrations):
                print(f"{state:7} {migration.name}")
    except psycopg.Error as exc:
        parser.exit(1, f"Database connection or migration failed ({type(exc).__name__}). Check DATABASE_URL.\n")
    except RuntimeError as exc:
        parser.exit(1, f"{exc}\n")
    return 0


def _interrupt_on_sigterm() -> None:
    """Stop on SIGTERM, which `kill` and process managers send, as on Ctrl-C: the run's tasks are cancelled
    and record themselves as cancelled. A process that dies without that, as on SIGKILL, leaves its run
    running until `research db reconcile` closes it out."""
    def interrupt(signum: int, frame: Any) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupt)


def main(argv: list[str] | None = None) -> None:
    from .db import MigrationsPending

    parser = argparse.ArgumentParser(prog="research", description="Cited answers to research questions")
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("scout", help="Research a question and print a cited answer (calls paid models)")
    run.add_argument("question", nargs="?", help="Research question, unless --case is given")
    run.add_argument("--case", help="Run a frozen study case by ID; uses its exact task and blocked sources")
    run.add_argument("--max-usd", type=Decimal, help="Hard pre-dispatch cap shared by every model call")
    run.add_argument("--note", action="append", default=[], help="A requirement every role follows; repeat for more")
    run.add_argument("--block", action="append", default=[], metavar="URL",
                     help="A source no tool may fetch and no evidence may cite; repeat for more")
    run.add_argument("--block-title", action="append", default=[], metavar="TITLE",
                     help="The title of a work to block wherever it appears, such as a copy at another address")
    run.add_argument("--follow-up", action="store_true",
                     help="Analyze material gaps and research up to three in parallel before synthesis (paid)")
    run.add_argument("--depth", choices=("auto", "quick", "standard", "deep"), default="auto",
                     help="How much research to do; auto lets the planner choose, and deep adds the gap follow-up")
    run.add_argument("--out", type=Path, help="Also write report.md and run.json here")
    run.add_argument("--no-persist", action="store_true", help="Keep the run in memory even when DATABASE_URL is set")
    run.add_argument("--study", help="Record the run as part of this study")
    run.add_argument("--arm", default="default", help="--study: the arm this run belongs to")
    run.add_argument("--replicate", type=int, default=1, help="--study: which repetition of the arm this is")

    synth = commands.add_parser("synthesize", help="Synthesize a stored Scout ledger with a chosen model (paid)")
    synth.add_argument("source_run_id", type=UUID)
    synth.add_argument("--model", required=True, help="Synthesizer provider:model@effort, such as anthropic:claude-opus-5-5@medium")
    synth.add_argument("--max-usd", required=True, type=Decimal, help="Pre-dispatch dollar cap for this command")
    synth.add_argument("--out", type=Path, help="Also write report.md and run.json here")
    synth.add_argument("--study", help="Record this as part of a study")
    synth.add_argument("--arm", default="default")
    synth.add_argument("--replicate", type=int, default=1)

    again = commands.add_parser("rescout", help="Research a stored Scout plan again with a chosen scout model, "
                                                "without planning or synthesis (paid)")
    again.add_argument("source_run_id", type=UUID)
    again.add_argument("--model", required=True, help="Scout provider:model@effort, such as openai:gpt-6-luna@high")
    again.add_argument("--max-usd", required=True, type=Decimal, help="Pre-dispatch dollar cap for this command")
    again.add_argument("--out", type=Path, help="Also write report.md and run.json here")
    again.add_argument("--study", help="Record this as part of a study")
    again.add_argument("--arm", default="default")
    again.add_argument("--replicate", type=int, default=1)

    show = commands.add_parser("show", help="Render a stored run")
    show.add_argument("run_id", type=UUID)
    show.add_argument("--format", choices=("md", "json"), default="md")
    show.add_argument("--provenance", action="store_true", help="Append exact cited passages and saved tool-text IDs")

    split = commands.add_parser("breakdown", help="Show where a stored run's money and time went, call by call")
    split.add_argument("run_id", type=UUID)

    grade = commands.add_parser("grade", help="Grade a stored run's report against a study case's rubric (paid)")
    grade.add_argument("run_id", type=UUID)
    grade.add_argument("--case", required=True, help="The study case, such as st05 or drb2-task8")
    grade.add_argument("--max-usd", required=True, type=Decimal, help="Pre-dispatch dollar cap for this call")

    assess = commands.add_parser("assess", help="Assess a stored report's quality and facts against a source packet (paid)")
    assess.add_argument("run_id", type=UUID)
    assess.add_argument("--case", required=True, help="The source packet, such as st04 or st07")
    assess.add_argument("--max-usd", required=True, type=Decimal, help="Pre-dispatch dollar cap for this call")

    check = commands.add_parser("audit", help="Judge whether the verified quotes behind stored reports' statements "
                                "say what the statements say (paid)")
    check.add_argument("run_ids", type=UUID, nargs="+", metavar="run_id")
    check.add_argument("--model", help="Override RESEARCH_MODELS__AUDIT with provider:model@effort")
    check.add_argument("--max-usd", required=True, type=Decimal, help="Pre-dispatch dollar cap across all the runs")

    diag = commands.add_parser("diagnose", help="Find where stored runs lost their rubric points, and whether their "
                               "scores can show a change (paid unless --free)")
    diag.add_argument("run_ids", type=UUID, nargs="+", metavar="run_id")
    diag.add_argument("--model", help="Override RESEARCH_MODELS__DIAGNOSE with provider:model@effort")
    diag.add_argument("--max-usd", type=Decimal, help="Pre-dispatch dollar cap across all the runs; needed unless --free")
    diag.add_argument("--free", action="store_true", help="Use only stored grades and make no model calls")

    study = commands.add_parser("study", help="Run a whole study from a TOML spec and summarize it (paid)")
    study.add_argument("study_command", choices=("run", "plan"),
                       help="run: execute the spec; plan: list its runs and worst case without running any")
    study.add_argument("spec", type=Path)
    study.add_argument("--out", type=Path, help="Where each run's report and the summary go; default runs/STUDY")
    how = study.add_mutually_exclusive_group()
    how.add_argument("--dry", action="store_true",
                     help="Free: fake models, the offline world, and the dry database, checked against the invariants")
    how.add_argument("--cheap", action="store_true",
                     help="Cents: every role on a cheap real model, one replicate, checked against the invariants")
    study.add_argument("--seeds", type=int, default=1, help="--dry: replicates of every arm, each a new seed")

    fuzz = commands.add_parser("fuzz", help="Hunt bugs with seeded fake models and an offline world (free)")
    fuzz.add_argument("--runs", type=int, default=100, help="How many seeded runs")
    fuzz.add_argument("--seed", type=int, default=0, help="Where the seeds start")
    fuzz.add_argument("--one", type=int, metavar="SEED", help="Reproduce one seed exactly, with its full traceback")
    fuzz.add_argument("--fault-rate", type=float, default=0.2, help="How often models and the world misbehave")

    doctor = commands.add_parser("doctor", help="Check keys, prices, the database, and the network")
    doctor.add_argument("--smoke", action="store_true", help="Also make one small paid call per configured model")

    db = commands.add_parser("db", help="Apply migrations, show their state, or close out abandoned runs")
    db.add_argument("db_command", choices=("status", "migrate", "reconcile"))
    db.add_argument("--older-than", type=float, metavar="MINUTES",
                    help="reconcile: runs still running this long after they started count as abandoned")
    db.add_argument("--apply", action="store_true", help="reconcile: make the changes instead of counting them")

    args = parser.parse_args(argv)
    settings = _settings(parser)
    if args.command == "scout":
        if bool(args.question) == bool(args.case):
            parser.error("scout needs exactly one of a question or --case")
        if args.case and (args.note or args.block or args.block_title or args.no_persist):
            parser.error("a frozen --case cannot add notes or blocks or disable persistence")
        if args.case and args.max_usd is None:
            parser.error("a frozen --case needs --max-usd for a hard stage ceiling")
        if args.case and not settings.database_dsn:
            parser.error("a frozen --case needs DATABASE_URL so paid results are stored")
        if args.max_usd is not None and args.max_usd <= 0:
            parser.error("--max-usd must be positive")
    if args.command in ("show", "breakdown", "grade", "assess", "audit", "diagnose", "synthesize", "rescout", "db",
                        "study") \
            and not settings.database_dsn:
        parser.error("this command needs DATABASE_URL; see README.md")
    if args.command in ("grade", "assess", "audit", "synthesize", "rescout") and args.max_usd <= 0:
        parser.error("--max-usd must be positive")
    if args.command == "diagnose" and not args.free and not (args.max_usd and args.max_usd > 0):
        parser.error("diagnose makes paid calls, so it needs a positive --max-usd, unless --free")
    if args.command == "db" and args.db_command == "reconcile" and not (args.older_than and args.older_than > 0):
        parser.error("reconcile needs --older-than MINUTES, longer than any run still in progress")
    _interrupt_on_sigterm()
    try:
        if args.command == "scout":
            code = asyncio.run(_scout(args, settings))
        elif args.command == "show":
            code = asyncio.run(_show(args, settings))
        elif args.command == "synthesize":
            code = asyncio.run(_synthesize(args, settings))
        elif args.command == "rescout":
            code = asyncio.run(_rescout(args, settings))
        elif args.command == "breakdown":
            code = asyncio.run(_breakdown(args, settings))
        elif args.command == "grade":
            code = asyncio.run(_grade(args, settings))
        elif args.command == "assess":
            code = asyncio.run(_assess(args, settings))
        elif args.command == "audit":
            code = asyncio.run(_audit(args, settings))
        elif args.command == "diagnose":
            code = asyncio.run(_diagnose(args, settings))
        elif args.command == "fuzz":
            code = asyncio.run(_fuzz(args))
        elif args.command == "study":
            code = _study(args, settings)
        elif args.command == "doctor":
            from .doctor import run_doctor
            code = asyncio.run(run_doctor(settings, smoke=args.smoke))
        else:
            code = _db(args, settings, parser)
    except KeyboardInterrupt:
        print("Interrupted; the run is recorded as cancelled.", file=sys.stderr)
        code = 130
    except MigrationsPending as exc:
        # Found by a dry study: a database behind on migrations ended every command in a traceback.
        print(f"Cannot run: {exc}.", file=sys.stderr)
        code = 2
    sys.exit(code)


if __name__ == "__main__":
    main()

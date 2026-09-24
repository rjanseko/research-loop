"""Reproducible, bounded launcher for long-horizon research questions and long-horizon synthesis."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
from collections.abc import Mapping
from contextlib import AsyncExitStack
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from .acquisition import FETCH_VERSION, AcquisitionCache
from .agents import long_horizon_synthesizer_agent, prompt_fingerprint
from .async_orchestrator import ResearchConfig, ResearchOutcome, review_reasons
from .citations import (
    BasisPaperReport,
    SemanticScholar,
    discover_basis_papers,
    render_basis_papers,
)
from .db import open_migrated_pool
from .diagnose import run_diagnose
from .experiment import (
    MANIFEST_SCHEMA_VERSION,
    file_sha256,
    fingerprint,
    git_state,
    package_versions,
    safe_value,
    write_manifest,
)
from .ledger import EvidenceLedger
from .long_horizon_spec import SYNTHESIS_DIR, load_spec
from .observability import configure_logfire
from .orchestrator import ResearchLoop
from .policy import ModelPolicy, ModelRoute, get_policy, retry_token_budget
from .quotes import source_keys
from .repository import InMemoryResearchRepository, PostgresResearchRepository
from .schemas import (
    EVIDENCE_VERSION,
    ClaimCheck,
    FinalReport,
    LongHorizonFindings,
    LongHorizonSynthesis,
    ResearchConstraints,
    ResearchResult,
    ResearchRole,
    SourceRef,
    VerificationReport,
)
from .settings import ResearchSettings
from .telemetry import jsonable
from .tools import ResearchToolMode

SPEC_FILE = Path(__file__).resolve().parents[2] / "long_horizon" / "agentic_se" / "spec.toml"
CATALOGS = ("benchmark_catalog", "architecture_patterns", "failure_modes", "open_questions", "hypotheses")


def render_objective(spec: dict[str, Any], question: dict[str, str]) -> str:
    source_policy = spec["source_policy"]
    return "\n".join([
        spec["title"],
        f"Question {question['id']}: {question['text']}",
        f"Publication window: {spec['period_start']} to {spec['period_end']}.",
        "Source policy:",
        *[f"{key}: {value}" for key, value in source_policy.items()],
        "Keep preprints, submissions, reviews, and published versions distinct. Report uncertainty and contradictions.",
    ])


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _spec_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _objective_sha256(spec: dict[str, Any], question: dict[str, str]) -> str:
    return hashlib.sha256(render_objective(spec, question).encode()).hexdigest()


def _manifest_base(spec: dict[str, Any], path: Path, *, kind: str, policy_snapshot: dict[str, Any],
                   fingerprint_extra: dict[str, Any], persist: bool) -> dict[str, Any]:
    spec_hash = _spec_sha256(path)
    prompts_sha256 = prompt_fingerprint()
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION, "kind": kind, "spec_id": spec["id"],
        "experiment_id": str(uuid4()), "spec_sha256": spec_hash, "prompts_sha256": prompts_sha256,
        "config_fingerprint": fingerprint({"spec": spec_hash, "policy": policy_snapshot,
                                           "prompts_sha256": prompts_sha256, **fingerprint_extra}),
        "git": git_state(), "graph_version": spec["graph_version"],
        "policy": policy_snapshot, "packages": package_versions(), "persistent": persist,
        "started_at": datetime.now(UTC).isoformat(), "finished_at": None, "status": "running",
    }


_SEVERITY_ORDER = {"major": 0, "minor": 1, "none": 2}


def _flagged_checks(verification: VerificationReport) -> list[ClaimCheck]:
    """Checks the verifier could not support or rated major, most severe first."""
    return sorted((check for check in verification.checks if not check.supported or check.severity == "major"),
                  key=lambda check: (_SEVERITY_ORDER[check.severity], check.supported))


def render_question_report(report: FinalReport, verification: VerificationReport,
                           ledger: EvidenceLedger | None = None) -> str:
    """The synthesized answer, its caveats, the verifier's unresolved findings, and quote and source checks."""
    lines = [report.answer.rstrip(), ""]
    if report.caveats:
        lines += ["## Caveats", "", *[f"- {caveat}" for caveat in report.caveats], ""]
    checks = verification.checks
    unsupported = [check for check in checks if not check.supported]
    major = sum(1 for check in unsupported if check.severity == "major")
    lines += ["## Verification", "",
              (f"The verifier checked {len(checks)} statements: {len(checks) - len(unsupported)} supported, "
               f"{len(unsupported)} not supported ({major} major).")]
    if verification.needs_research:
        lines.append("It asked for more research, which this run did not do, so the issues below are unresolved.")
    flagged = _flagged_checks(verification)
    if flagged:
        lines.append("")
    for check in flagged:
        label = f"{check.severity}, {'supported' if check.supported else 'not supported'}"
        cited = f" (claims: {', '.join(check.claim_ids)})" if check.claim_ids else ""
        lines.append(f"- **[{label}]** {check.statement}{cited}: {check.explanation}")
    for check_field, noun in (("quote_check", "quoted passages"), ("source_check", "cited sources")):
        verdicts = [(claim.id, getattr(item, check_field)) for claim in (ledger.claims() if ledger else [])
                    for item in claim.evidence if getattr(item, check_field)]
        not_found = [claim_id for claim_id, verdict in verdicts if verdict == "not_found"]
        if not_found:
            lines += ["", (f"{len(not_found)} of {len(verdicts)} {noun} {'was' if len(not_found) == 1 else 'were'} "
                           "not found in any text the research tools returned "
                           f"(claims: {', '.join(dict.fromkeys(not_found))}).")]
        elif verdicts:
            lines += ["", f"All {len(verdicts)} {noun} were found in text the research tools returned."]
    return "\n".join(lines) + "\n"


def long_horizon_policy(spec: dict[str, Any], policy_name: str, model_overrides: Mapping[str, str]) -> ModelPolicy:
    """The named policy with the study's question budget, planner range, and route limits applied."""
    policy = get_policy(policy_name, model_overrides=model_overrides)
    execution = spec["execution"]
    policy.planner_question_range = (
        int(execution["planner_question_min"]), int(execution["planner_question_max"])
    )
    deep_route = policy.routes[ResearchRole.DEEP_DIVE]
    policy.routes[ResearchRole.DEEP_DIVE] = replace(
        deep_route,
        cost_limit=min(deep_route.cost_limit or float("inf"), float(execution["deep_dive_cost_limit_usd"])),
    )
    policy.job_cost_limit = float(execution["question_cost_limit_usd"])
    policy.job_reserve_usd = float(execution["question_reserve_usd"])
    scout_limits = {
        route_field: int(execution[key])
        for key, route_field in (("scout_max_requests", "max_requests"), ("scout_max_tool_calls", "max_tool_calls"),
                                 ("scout_total_tokens_limit", "total_tokens_limit"))
        if execution[key] is not None
    }
    if scout_limits:
        policy.routes[ResearchRole.SCOUT] = replace(policy.routes[ResearchRole.SCOUT], **scout_limits)
        if policy.cheap_scout:
            policy.cheap_scout = replace(policy.cheap_scout, **scout_limits)
        if policy.multimodal_scout:
            policy.multimodal_scout = replace(policy.multimodal_scout, **scout_limits)
    return policy


def long_horizon_run_config(spec: dict[str, Any]) -> ResearchConfig:
    execution = spec["execution"]
    return ResearchConfig(
        tool_mode=ResearchToolMode.NORMALIZED,
        scholarly_cache_mode=execution["scholarly_cache_mode"],
        max_parallel_scouts=int(execution["max_parallel_scouts"]),
        max_parallel_deep_dives=int(execution["max_parallel_deep_dives"]),
        max_deep_dives_per_round=int(execution["max_deep_dives_per_round"]),
        max_verification_rounds=int(execution["max_verification_rounds"]),
        salvage_exhausted_research=True,
        max_run_seconds=execution["question_timeout_seconds"],
        keep_recent_tool_results=execution["keep_recent_tool_results"],
    )


# The manifest record of a completed question, also rebuilt from run.json when a study resumes.
_RUN_RECORD_KEYS = ("id", "job_id", "claim_count", "source_count", "status", "cost_usd", "review_reasons")


def _write_question_outputs(folder: Path, outcome: ResearchOutcome, objective: str,
                            manifest: dict[str, Any]) -> dict[str, Any]:
    """Export one completed question and publish it in one step; returns its manifest record.

    Files are written to a hidden staging folder that then replaces `folder`, so a failed or
    interrupted write leaves the previous outputs untouched. run.json, written last, records
    every other file's hash; aggregation rejects a folder whose files no longer match.
    """
    staging = folder.with_name(f".{folder.name}.{uuid4().hex}.staging")
    staging.mkdir(parents=True)
    try:
        (staging / "report.md").write_text(
            render_question_report(outcome.report, outcome.verification, outcome.ledger), encoding="utf-8")
        (staging / "report.json").write_text(outcome.report.model_dump_json(indent=2) + "\n", encoding="utf-8")
        _write_json(staging / "evidence_ledger.json", outcome.ledger.to_json())
        bibliography = list({
            json.dumps(source.model_dump(mode="json"), sort_keys=True): source.model_dump(mode="json")
            for source in outcome.ledger.sources()
        }.values())
        _write_json(staging / "bibliography.json", bibliography)
        (staging / "verification.json").write_text(outcome.verification.model_dump_json(indent=2) + "\n",
                                                   encoding="utf-8")
        files = {path.name: file_sha256(path) for path in sorted(staging.iterdir())}
        record = {
            "id": folder.name, "job_id": str(outcome.job_id),
            "claim_count": outcome.ledger.claim_count(),
            "source_count": len(bibliography), "status": "completed",
            "cost_usd": None if outcome.cost_usd is None else str(outcome.cost_usd),
            "review_reasons": review_reasons(outcome.report, outcome.verification, outcome.ledger),
        }
        # run.json marks the folder as a completed, attributable run.
        _write_json(staging / "run.json", record | {
            "experiment_id": manifest["experiment_id"],
            "spec_sha256": manifest["spec_sha256"],
            # What the evidence answers; budget-only spec edits leave it unchanged.
            "objective_sha256": hashlib.sha256(objective.encode()).hexdigest(),
            "config_fingerprint": manifest["config_fingerprint"],
            "finished_at": datetime.now(UTC).isoformat(),
            "files": files,
        })
        _publish(staging, folder)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return record


def _publish(staging: Path, folder: Path) -> None:
    """Put `staging` in place of `folder`; the old folder is deleted only once the new one is in place."""
    if not folder.exists():
        staging.rename(folder)
        return
    retired = folder.with_name(f".{folder.name}.{uuid4().hex}.retired")
    folder.rename(retired)
    try:
        staging.rename(folder)
    except BaseException:
        retired.rename(folder)
        raise
    shutil.rmtree(retired, ignore_errors=True)


def _files_match(folder: Path, run: dict[str, Any]) -> bool:
    """Whether every file run.json lists still has the hash recorded when it was published."""
    files = run.get("files")
    return (isinstance(files, dict) and bool(files)
            and all(isinstance(name, str) and Path(name).name == name and file_sha256(folder / name) == digest
                    for name, digest in files.items()))


def _completed_run(folder: Path, spec: dict[str, Any], question: dict[str, str],
                   config_fingerprint: str) -> dict[str, Any] | None:
    """run.json of a published question a resumed study keeps: completed under the same
    configuration fingerprint (spec file, policy, prompts, acquisition, evidence version, and
    run config), for the same objective, with every file still matching its recorded hash."""
    try:
        run = json.loads((folder / "run.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (isinstance(run, dict) and run.get("status") == "completed"
            and run.get("config_fingerprint") == config_fingerprint
            and run.get("objective_sha256") == _objective_sha256(spec, question)
            and _files_match(folder, run)):
        return run
    return None


def _questions_manifest(spec: dict[str, Any], path: Path, *, policy_name: str, settings: ResearchSettings,
                        persist: bool) -> tuple[dict[str, Any], ModelPolicy, ResearchConfig]:
    """The manifest a question run starts with, and the policy and run config it records."""
    policy = long_horizon_policy(spec, policy_name, settings.model_overrides)
    run_config = long_horizon_run_config(spec)
    policy_snapshot = safe_value(policy.snapshot())
    acquisition = {"search": "duckduckgo", "web_fetch": "trafilatura+bs4",
                   "scholar": ["openalex", "crossref", "arxiv", "acl", "opencitations"],
                   "cache_mode": run_config.scholarly_cache_mode, "fetch_version": FETCH_VERSION}
    manifest = _manifest_base(spec, path, kind="questions", policy_snapshot=policy_snapshot,
                              fingerprint_extra={"acquisition": acquisition, "evidence_version": EVIDENCE_VERSION,
                                                 "run_config": jsonable(asdict(run_config))},
                              persist=persist)
    manifest |= {
        "evidence_version": EVIDENCE_VERSION,
        "tool_mode": "normalized", "acquisition": acquisition, "cache_mode": run_config.scholarly_cache_mode,
        "run_config": jsonable(asdict(run_config)),
        "run_limits": {
            "max_parallel_scouts": run_config.max_parallel_scouts,
            "max_deep_dives_per_round": run_config.max_deep_dives_per_round,
            "max_parallel_deep_dives": run_config.max_parallel_deep_dives,
            "max_verification_rounds": run_config.max_verification_rounds,
            "question_cost_limit_usd": policy.job_cost_limit,
            "question_reserve_usd": policy.job_reserve_usd,
            "salvage_exhausted_research": run_config.salvage_exhausted_research,
        },
        "questions": [],
    }
    return manifest, policy, run_config


def _kept_runs(spec: dict[str, Any], output_dir: Path, question_ids: list[str],
               config_fingerprint: str) -> dict[str, dict[str, Any]]:
    questions = {item["id"]: item for item in spec["questions"]}
    return {
        question_id: run for question_id in question_ids
        if (run := _completed_run(output_dir / question_id, spec, questions[question_id],
                                  config_fingerprint)) is not None
    }


def resumable_questions(spec: dict[str, Any], path: Path, output_dir: Path, question_ids: list[str], *,
                        policy_name: str, settings: ResearchSettings) -> dict[str, dict[str, Any]]:
    """Published runs, by question ID, that a resumed study would keep instead of rerunning."""
    manifest, _, _ = _questions_manifest(spec, path, policy_name=policy_name, settings=settings, persist=False)
    return _kept_runs(spec, output_dir, question_ids, manifest["config_fingerprint"])


async def run_long_horizon(
    path: Path,
    *,
    question_ids: list[str],
    policy_name: str,
    settings: ResearchSettings,
    output_dir: Path,
    persist: bool,
    resume: bool = False,
) -> Path:
    """Run each question in turn. With `resume`, a question whose published output is still
    valid under this exact configuration (_completed_run) is kept and recorded, not rerun."""
    spec = load_spec(path)
    questions = {item["id"]: item for item in spec["questions"]}
    unknown = set(question_ids) - set(questions)
    if unknown:
        raise ValueError(f"unknown long-horizon question IDs: {', '.join(sorted(unknown))}")
    if persist and not settings.database_dsn:
        raise ValueError("DATABASE_URL required for --persist")
    configure_logfire(settings)
    execution = spec["execution"]
    manifest, policy, run_config = _questions_manifest(spec, path, policy_name=policy_name,
                                                       settings=settings, persist=persist)
    if resume:
        kept = _kept_runs(spec, output_dir, question_ids, manifest["config_fingerprint"])
        manifest["questions"] = [
            {key: run.get(key) for key in _RUN_RECORD_KEYS} | {"resumed_from": run.get("experiment_id")}
            for run in kept.values()
        ]
        question_ids = [question_id for question_id in question_ids if question_id not in kept]
    manifest_path = output_dir / "manifests" / f"{manifest['experiment_id']}.json"
    write_manifest(manifest_path, manifest)
    max_failed = int(execution["max_failed_questions"])
    failed = 0
    try:
        async with AsyncExitStack() as stack:
            pool = await open_migrated_pool(stack, settings.database_dsn) if persist else None
            for index, question_id in enumerate(question_ids):
                backend = PostgresResearchRepository(pool) if pool else InMemoryResearchRepository()
                loop = ResearchLoop(policy, run_config, repository=backend, settings=settings)
                objective = render_objective(spec, questions[question_id])
                try:
                    outcome = await loop.run(
                        objective,
                        constraints=ResearchConstraints(notes=list(execution["research_notes"])),
                    )
                except Exception as exc:  # noqa: BLE001
                    # Questions are independent: record the failure and go on, unless failures
                    # keep coming, which points to a shared cause. Cancellation stops the run.
                    manifest["questions"].append({
                        "id": question_id, "status": "failed", "error": type(exc).__name__,
                    })
                    write_manifest(manifest_path, manifest)
                    failed += 1
                    if failed >= max_failed:
                        if question_ids[index + 1:]:
                            manifest["not_run"] = question_ids[index + 1:]
                        break
                    continue
                record = _write_question_outputs(output_dir / question_id, outcome, objective, manifest)
                manifest["questions"].append(record)
                write_manifest(manifest_path, manifest)
        completed = len(manifest["questions"]) - failed
        manifest["status"] = ("completed" if not failed
                              else "completed_with_failures" if completed else "failed")
    except (Exception, asyncio.CancelledError) as exc:  # Ctrl-C arrives as cancellation
        manifest["status"] = "failed"
        manifest["error"] = type(exc).__name__
        raise
    finally:
        manifest["finished_at"] = datetime.now(UTC).isoformat()
        write_manifest(manifest_path, manifest)
    return manifest_path


@dataclass
class CompletedQuestion:
    question: dict[str, Any]
    run: dict[str, Any]
    report: FinalReport
    ledger: dict[str, list[ResearchResult]]
    verification: VerificationReport


@dataclass
class LongHorizonEvidence:
    completed: list[CompletedQuestion]
    # Every question left out of synthesis, including stale ones.
    missing: list[str]
    claims: list[dict[str, Any]]
    bibliography: list[dict[str, Any]]
    contradictions: dict[str, list[dict[str, Any]]]
    verification: dict[str, dict[str, Any]]
    # Completed outputs whose recorded objective hash is missing or differs from the current spec's.
    stale: list[str]
    # Completed outputs whose files no longer match the hashes run.json recorded.
    invalid: list[str] = field(default_factory=list)

    @property
    def refs(self) -> frozenset[str]:
        return frozenset(item["ref"] for item in self.claims)

    @property
    def uncited(self) -> list[str]:
        """Completed questions whose report cites no ledger claim, so synthesis gets no evidence from them."""
        cited = {(entry["question_id"], entry["claim"]["id"]) for entry in self.claims}
        return [item.question["id"] for item in self.completed
                if not any((item.question["id"], claim_id) in cited for claim_id in item.report.claim_ids_used)]


def aggregate_long_horizon(spec: dict[str, Any], output_dir: Path) -> LongHorizonEvidence:
    """Merge completed question outputs; claim refs are '<question>/<claim id>' in ledger order.

    Only runs whose recorded objective hash matches the current spec count; budget-only spec
    edits leave the objective, and so the hash, unchanged.
    """
    completed: list[CompletedQuestion] = []
    missing: list[str] = []
    stale: list[str] = []
    invalid: list[str] = []
    for question in spec["questions"]:
        folder = output_dir / question["id"]
        try:
            run = json.loads((folder / "run.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            missing.append(question["id"])
            continue
        if run.get("status") != "completed":
            missing.append(question["id"])
            continue
        if run.get("objective_sha256") != _objective_sha256(spec, question):
            stale.append(question["id"])
            missing.append(question["id"])
            continue
        if not _files_match(folder, run):
            invalid.append(question["id"])
            missing.append(question["id"])
            continue
        raw_ledger = json.loads((folder / "evidence_ledger.json").read_text(encoding="utf-8"))
        completed.append(CompletedQuestion(
            question=question,
            run=run,
            report=FinalReport.model_validate_json((folder / "report.json").read_text(encoding="utf-8")),
            ledger=EvidenceLedger.from_json(raw_ledger).results,
            verification=VerificationReport.model_validate_json(
                (folder / "verification.json").read_text(encoding="utf-8")
            ),
        ))

    claims: list[dict[str, Any]] = []
    contradictions: dict[str, list[dict[str, Any]]] = {}
    verification: dict[str, dict[str, Any]] = {}
    bibliography: dict[str, dict[str, Any]] = {}
    for item in completed:
        question_id = item.question["id"]
        seen: set[str] = set()
        refs_for: dict[str, list[str]] = {}
        for results in item.ledger.values():
            for result in results:
                for claim in result.claims:
                    ref, suffix = f"{question_id}/{claim.id}", 2
                    while ref in seen:
                        ref, suffix = f"{question_id}/{claim.id}~{suffix}", suffix + 1
                    seen.add(ref)
                    refs_for.setdefault(claim.id, []).append(ref)
                    claims.append({"ref": ref, "question_id": question_id,
                                   "result_question_id": result.question_id, "claim": claim.model_dump(mode="json")})
                    for evidence in claim.evidence:
                        source = evidence.source.model_dump(mode="json")
                        entry = bibliography.setdefault(json.dumps(source, sort_keys=True), {**source, "question_ids": []})
                        if question_id not in entry["question_ids"]:
                            entry["question_ids"].append(question_id)
        contradictions[question_id] = [
            {"description": contradiction.description,
             "claim_refs": _cited_refs(contradiction.claim_ids, refs_for)}
            for results in item.ledger.values() for result in results for contradiction in result.contradictions
        ]
        checks = item.verification.checks
        verification[question_id] = {
            "checked": len(checks),
            "not_supported": sum(1 for check in checks if not check.supported),
            "major": sum(1 for check in checks if not check.supported and check.severity == "major"),
            "needs_research": item.verification.needs_research,
            "findings": [
                {"statement": check.statement, "supported": check.supported, "severity": check.severity,
                 "explanation": check.explanation,
                 "claim_refs": _cited_refs(check.claim_ids, refs_for)}
                for check in _flagged_checks(item.verification)
            ],
        }
    return LongHorizonEvidence(completed, missing, claims, list(bibliography.values()), contradictions,
                            verification=verification, stale=stale, invalid=invalid)


def write_aggregate(long_horizon_dir: Path, evidence: LongHorizonEvidence) -> None:
    long_horizon_dir.mkdir(parents=True, exist_ok=True)
    _write_json(long_horizon_dir / "evidence_ledger.json", {
        "questions": {
            item.question["id"]: {key: [result.model_dump(mode="json") for result in results]
                                  for key, results in item.ledger.items()}
            for item in evidence.completed
        },
        "claims": evidence.claims,
    })
    _write_json(long_horizon_dir / "bibliography.json", evidence.bibliography)


def _cited_refs(claim_ids: list[str], refs_for: dict[str, list[str]]) -> list[str]:
    """Every long-horizon ref for these claim ids, in ledger order, without duplicates."""
    refs: list[str] = []
    for claim_id in claim_ids:
        for ref in refs_for.get(claim_id, []):
            if ref not in refs:
                refs.append(ref)
    return refs


class _Works:
    """The long-horizon prompt's source table: one row per work, numbered in the order the prompt cites them.

    Every source in the long-horizon ledger is grouped up front. Citations that share any
    `source_keys` entry are one work, including when only a third citation links them, so
    one paper keeps one id across questions. A row shows the first citation's title, url (or
    attachment id), and publication date.
    """

    def __init__(self, claims: list[dict[str, Any]]) -> None:
        self._parent: list[int] = []
        self._owner: dict[str, int] = {}
        for entry in claims:
            for item in entry["claim"].get("evidence", []):
                self._work(source_keys(SourceRef.model_validate(item["source"])))
        self._ids: dict[int, str] = {}
        self.rows: list[dict[str, Any]] = []

    def _root(self, work: int) -> int:
        while self._parent[work] != work:
            work = self._parent[work]
        return work

    def _work(self, keys: frozenset[str]) -> int:
        work = len(self._parent)
        self._parent.append(work)
        for key in sorted(keys):
            if key not in self._owner:
                self._owner[key] = work
                continue
            first, second = sorted((self._root(self._owner[key]), self._root(work)))
            self._parent[second] = first
        return self._root(work)

    def id_for(self, source: SourceRef) -> str:
        keys = source_keys(source)
        known = {self._root(self._owner[key]) for key in keys if key in self._owner}
        work = min(known) if known else self._work(keys)
        if work not in self._ids:
            self._ids[work] = f"s{len(self._ids) + 1}"
            row = {"id": self._ids[work], "title": source.title}
            row |= {"url": str(source.url)} if source.url is not None else {"attachment_id": source.attachment_id}
            if source.published_at:
                row["published_at"] = source.published_at
            self.rows.append(row)
        return self._ids[work]


def _prompt_claims(question_id: str, report: FinalReport, claims: list[dict[str, Any]],
                   works: _Works | None = None) -> list[dict[str, Any]]:
    """Report claims with long-horizon refs and the source facts the synthesizer classifies on.

    Excerpts stay in the aggregated ledger. `source_ids` name the works in `works` whose
    evidence supports the claim, and `source_count` is how many there are; `source_types` and
    `publication_statuses` also cover supporting evidence only. Works whose evidence does not
    support it are `contradicting_source_ids` and `contradicting_source_count`. One work cited
    at two locators, or fetched through two providers, counts once. `min_confidence` is the
    lowest confidence among the ledger claims cited. A not-found quote or source is kept as a
    flag on the claim, which is enough to keep that claim from being treated as well supported.
    Several stored claims can share one id; an older ledger suffixes the later long-horizon refs.
    A citation of that id includes every copy. Without `works`, a table of `claims` is used.
    """
    works = works or _Works(claims)
    by_id: dict[str, list[dict[str, Any]]] = {}
    for entry in claims:
        if entry["question_id"] == question_id:
            by_id.setdefault(entry["claim"]["id"], []).append(entry)
    rows: list[dict[str, Any]] = []
    for claim in report.claims:
        refs: list[str] = []
        supporting: list[str] = []
        contradicting: list[str] = []
        confidences: list[float] = []
        source_types: list[str] = []
        statuses: list[str] = []
        quote_not_found = source_not_found = retracted = False
        for claim_id in claim.claim_ids:
            for entry in by_id.get(claim_id, []):
                if entry["ref"] not in refs:
                    refs.append(entry["ref"])
                if entry["claim"].get("confidence") is not None:
                    confidences.append(entry["claim"]["confidence"])
                for item in entry["claim"].get("evidence", []):
                    source = SourceRef.model_validate(item["source"])
                    work = works.id_for(source)
                    if item.get("supports", True):
                        if work not in supporting:
                            supporting.append(work)
                        source_types.append(source.source_type)
                        if source.publication_status != "unknown":
                            statuses.append(source.publication_status)
                    elif work not in contradicting:
                        contradicting.append(work)
                    quote_not_found = quote_not_found or item.get("quote_check") == "not_found"
                    source_not_found = source_not_found or item.get("source_check") == "not_found"
                    retracted = retracted or bool(source.is_retracted)
        row: dict[str, Any] = {"statement": claim.statement, "claim_refs": refs}
        if confidences:
            row["min_confidence"] = min(confidences)
        row["source_count"] = len(supporting)
        if supporting:
            row["source_ids"] = supporting
        if contradicting:
            row["contradicting_source_count"] = len(contradicting)
            row["contradicting_source_ids"] = contradicting
        if source_types:
            row["source_types"] = sorted(set(source_types))
        if statuses:
            row["publication_statuses"] = sorted(set(statuses))
        if quote_not_found:
            row["quote_check"] = "not_found"
        if source_not_found:
            row["source_check"] = "not_found"
        if retracted:
            row["retracted"] = True
        rows.append(row)
    return rows


def synthesis_prompt(spec: dict[str, Any], evidence: LongHorizonEvidence) -> str:
    """Long-horizon prompt: a source table, then each question's report claims, caveats, unresolved
    questions, contradictions, and verifier findings.

    The full ledger is written beside the report for audit and is not repeated here.
    """
    works = _Works(evidence.claims)
    questions = [
        {
            "id": item.question["id"], "text": item.question["text"],
            "claims": _prompt_claims(item.question["id"], item.report, evidence.claims, works),
            "caveats": item.report.caveats,
            "unresolved_questions": list(dict.fromkeys(
                question for results in item.ledger.values() for result in results
                for question in result.unresolved_questions
            )),
            "contradictions": evidence.contradictions[item.question["id"]],
            "verification": evidence.verification[item.question["id"]],
        }
        for item in evidence.completed
    ]
    payload = {
        "study": {
            "title": spec["title"], "as_of": spec["as_of"],
            "publication_window": [spec["period_start"], spec["period_end"]],
            "source_policy": spec["source_policy"],
            "findings_sections": spec["outputs"]["findings_sections"],
            "hypothesis_fields": spec["outputs"]["hypothesis_fields"],
        },
        "missing_question_ids": evidence.missing,
        "sources": works.rows,
        "questions": questions,
    }
    return json.dumps(payload, ensure_ascii=False)


def render_long_horizon_report(spec: dict[str, Any], evidence: LongHorizonEvidence, synthesis: LongHorizonSynthesis) -> str:
    done = [item.question["id"] for item in evidence.completed]
    lines = [f"# {spec['title']}", ""]
    scope = f"As of {spec['as_of']}. Synthesized from {len(done)} of {len(spec['questions'])} questions: {', '.join(done)}."
    if evidence.missing:
        scope += f" **Partial synthesis**; missing: {', '.join(evidence.missing)}."
    if evidence.uncited:
        scope += f" Reports that cite no evidence claims, so contributed none: {', '.join(evidence.uncited)}."
    lines += [scope, "", "## Summary", "", synthesis.summary.strip(), "", "## Findings"]
    for section in LongHorizonFindings.model_fields:
        findings = getattr(synthesis.findings, section)
        lines += ["", f"### {section.replace('_', ' ').capitalize()}", ""]
        lines += [f"- {finding.statement} [{', '.join(finding.claim_refs)}]" if finding.claim_refs
                  else f"- {finding.statement}" for finding in findings] or ["- None recorded."]
    lines += ["", "## Hypotheses", ""]
    for hypothesis in synthesis.hypotheses:
        lines += [
            f"- **{hypothesis.id}** (confidence {hypothesis.confidence:.2f}): {hypothesis.statement}",
            f"  - Experiment: {hypothesis.proposed_experiment}",
            f"  - Metric: {hypothesis.expected_metric}; estimated cost: {hypothesis.estimated_cost}",
            f"  - Supporting: {', '.join(hypothesis.supporting_evidence)}"
            + (f"; contradicting: {', '.join(hypothesis.contradicting_evidence)}" if hypothesis.contradicting_evidence else ""),
        ]
    if not synthesis.hypotheses:
        lines.append("- None recorded.")
    lines += ["", "## Catalogs", ""]
    lines += [f"- `{name}.json`: {len(getattr(synthesis, name))} entries" for name in CATALOGS]
    lines += ["", "Claim refs such as `q01/q1/c3` resolve in `evidence_ledger.json` under `claims`.", ""]
    return "\n".join(lines)


def write_synthesis(long_horizon_dir: Path, spec: dict[str, Any], evidence: LongHorizonEvidence,
                    synthesis: LongHorizonSynthesis) -> None:
    (long_horizon_dir / "synthesis.json").write_text(synthesis.model_dump_json(indent=2) + "\n", encoding="utf-8")
    for name in CATALOGS:
        _write_json(long_horizon_dir / f"{name}.json", [item.model_dump(mode="json") for item in getattr(synthesis, name)])
    (long_horizon_dir / "report.md").write_text(render_long_horizon_report(spec, evidence, synthesis), encoding="utf-8")


def synthesis_route(policy: ModelPolicy, limits: Mapping[str, Any]) -> ModelRoute:
    """Synthesizer route with this study's request, token, cost, and output caps."""
    base = policy.for_role(ResearchRole.SYNTHESIZER)
    return replace(
        base,
        max_requests=int(limits["max_requests"]),
        total_tokens_limit=int(limits["total_tokens_limit"]),
        cost_limit=float(limits["cost_limit_usd"]),
        settings={**base.settings, "max_tokens": int(limits["max_output_tokens"])},
    )


def prepare_synthesis(
    path: Path,
    output_dir: Path,
    *,
    allow_partial: bool,
    route: ModelRoute | None = None,
) -> tuple[dict[str, Any], LongHorizonEvidence, str]:
    """Validate synthesis inputs without model calls; raise before any paid step.

    ``route``, when given, is the long-horizon synthesizer route. The prompt is then also
    refused when one citation retry at that route's output cap would not fit its token limit.
    """
    spec = load_spec(path)
    evidence = aggregate_long_horizon(spec, output_dir)
    stale = (f" ({', '.join(evidence.stale)} cannot be matched to the current spec's objective; rerun them)"
             if evidence.stale else "")
    if evidence.invalid:
        stale += f" ({', '.join(evidence.invalid)} no longer match their recorded file hashes; rerun them)"
    if not evidence.completed:
        raise ValueError(f"no completed long-horizon questions to synthesize{stale}")
    if evidence.missing and not allow_partial:
        raise ValueError(f"questions not completed: {', '.join(evidence.missing)}{stale}; "
                         "pass --allow-partial to synthesize anyway")
    prompt = synthesis_prompt(spec, evidence)
    max_chars = int(spec["synthesis"]["max_prompt_chars"])
    if len(prompt) > max_chars:
        raise ValueError(f"synthesis prompt has {len(prompt)} chars, above synthesis.max_prompt_chars={max_chars}")
    if route is not None:
        allowance = int(route.settings["max_tokens"])
        needed = retry_token_budget(
            prompt, route, ResearchRole.SYNTHESIZER, output_allowance=allowance,
        )
        if needed > route.total_tokens_limit:
            raise ValueError(
                f"synthesis prompt needs about {needed} tokens for one retry, "
                f"above total_tokens_limit={route.total_tokens_limit}"
            )
    return spec, evidence, prompt


async def synthesize_long_horizon(
    path: Path,
    *,
    policy_name: str,
    settings: ResearchSettings,
    output_dir: Path,
    persist: bool,
    allow_partial: bool,
) -> Path:
    if persist and not settings.database_dsn:
        raise ValueError("DATABASE_URL required for --persist")
    configure_logfire(settings)
    policy = get_policy(policy_name, model_overrides=settings.model_overrides)
    route = synthesis_route(policy, load_spec(path)["synthesis"])
    spec, evidence, prompt = prepare_synthesis(
        path, output_dir, allow_partial=allow_partial, route=route,
    )
    policy.job_cost_limit = float(spec["synthesis"]["cost_limit_usd"])
    prompt_sha256 = hashlib.sha256(prompt.encode()).hexdigest()
    inputs = [
        {"id": item.question["id"], "job_id": item.run.get("job_id"), "experiment_id": item.run.get("experiment_id"),
         "config_fingerprint": item.run.get("config_fingerprint"),
         "spec_sha256": item.run.get("spec_sha256"),
         "review_reasons": item.run.get("review_reasons")}
        for item in evidence.completed
    ]
    manifest = _manifest_base(spec, path, kind="synthesis", policy_snapshot=safe_value(policy.snapshot()),
                              fingerprint_extra={"route": route.snapshot(), "prompt_sha256": prompt_sha256},
                              persist=persist)
    manifest |= {
        "synthesis_route": safe_value(route.snapshot()), "prompt_sha256": prompt_sha256, "prompt_chars": len(prompt),
        "inputs": inputs, "missing_question_ids": evidence.missing, "stale_question_ids": evidence.stale,
        "uncited_question_ids": evidence.uncited,
        "invalid_question_ids": evidence.invalid,
        "partial": bool(evidence.missing),
        "mixed_question_configs": len({item["config_fingerprint"] for item in inputs}) > 1,
        "claim_count": len(evidence.claims), "source_count": len(evidence.bibliography),
    }
    manifest_path = output_dir / "manifests" / f"{manifest['experiment_id']}.json"
    long_horizon_dir = output_dir / SYNTHESIS_DIR
    write_aggregate(long_horizon_dir, evidence)
    write_manifest(manifest_path, manifest)
    try:
        async with AsyncExitStack() as stack:
            pool = await open_migrated_pool(stack, settings.database_dsn) if persist else None
            loop = ResearchLoop(policy, repository=PostgresResearchRepository(pool) if pool else InMemoryResearchRepository(),
                                settings=settings)
            outcome = await loop.run_agent_job(
                f"{spec['title']}: long-horizon synthesis",
                agent=long_horizon_synthesizer_agent,
                role=ResearchRole.SYNTHESIZER,
                route=route,
                prompt=prompt,
                deps=evidence.refs,
                config={"long_horizon": {"id": spec["id"], "kind": "synthesis", "prompt_sha256": prompt_sha256,
                                     "question_ids": [item["id"] for item in inputs]}},
            )
        write_synthesis(long_horizon_dir, spec, evidence, outcome.output)
        manifest |= {"status": "completed", "job_id": str(outcome.job_id),
                     "cost_usd": None if outcome.cost_usd is None else str(outcome.cost_usd),
                     "hypothesis_count": len(outcome.output.hypotheses)}
    except (Exception, asyncio.CancelledError) as exc:  # Ctrl-C arrives as cancellation
        manifest["status"] = "failed"
        manifest["error"] = type(exc).__name__
        raise
    finally:
        manifest["finished_at"] = datetime.now(UTC).isoformat()
        write_manifest(manifest_path, manifest)
    return manifest_path


async def find_basis_papers(spec: dict[str, Any], evidence: LongHorizonEvidence, settings: ResearchSettings,
                            *, client: Any = None) -> BasisPaperReport:
    """Backward snowballing over the completed questions' bibliography; no model calls."""
    key = settings.semantic_scholar_api_key
    # A study records its research calls without reading the cache. Basis papers do not feed back into
    # research, so they read recent entries: a rerun after throttling resumes instead of starting over.
    mode = spec["execution"]["scholarly_cache_mode"]
    scholar = SemanticScholar(
        AcquisitionCache(settings.benchmark_cache / "scholarly", mode="live" if mode == "record" else mode),
        api_key=key.get_secret_value() if key else None,
        client=client,
    )
    return await discover_basis_papers(evidence.bibliography, scholar)


def write_basis_papers(folder: Path, spec: dict[str, Any], report: BasisPaperReport) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "basis_papers.json").write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    (folder / "basis_papers.md").write_text(render_basis_papers(report, spec["title"]), encoding="utf-8")


def _paid_preflight(settings: ResearchSettings, policy_name: str, persist: bool) -> None:
    local_checks = run_diagnose(settings, policy_name=policy_name, smoke=False)
    critical = {"runtime", "dependencies", "graph", "web_tools", "scholar_tools", "cache", "output"}
    if persist:
        critical.update({"database", "migrations"})
    if any(check.name in critical and check.status != "PASS" for check in local_checks):
        raise RuntimeError("local preflight failed; run research-diagnose for details")
    checks = run_diagnose(settings, policy_name=policy_name, smoke=True)
    if any(check.name.startswith("model:") and check.status != "PASS" for check in checks):
        raise RuntimeError("live model preflight failed; run research-diagnose --smoke for details")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run bounded long-horizon research questions or synthesize completed ones")
    parser.add_argument("--spec", type=Path, default=SPEC_FILE)
    parser.add_argument("--question", default=None, help="Question ID; defaults to the first question")
    parser.add_argument("--all-questions", action="store_true", help="Run every question sequentially")
    parser.add_argument("--resume", action="store_true",
                        help="Keep questions already completed under this exact configuration instead of rerunning them")
    parser.add_argument("--aggregate", action="store_true", help="Merge completed question outputs without model calls")
    parser.add_argument("--synthesize", action="store_true", help="Write long-horizon catalogs and hypotheses from completed questions")
    parser.add_argument("--basis-papers", action="store_true",
                        help="Rank the works completed questions' sources cite (Semantic Scholar; no model calls)")
    parser.add_argument("--allow-partial", action="store_true", help="Synthesize even if some questions are not completed")
    parser.add_argument("--policy", choices=("quality", "breadth", "glm-heavy"), default="quality")
    parser.add_argument("--paid", action="store_true", help="Authorize model provider calls")
    parser.add_argument("--dry-run", action="store_true", help="Validate and report what would run, without calls or writes")
    parser.add_argument("--persist", action="store_true", help="Store runs in Postgres")
    parser.add_argument("--output", type=Path, default=Path("benchmark_outputs/long_horizon"))
    args = parser.parse_args()
    if args.all_questions and args.question:
        parser.error("choose --question or --all-questions")
    if sum((args.aggregate, args.synthesize, args.basis_papers)) > 1:
        parser.error("choose one of --aggregate, --synthesize, or --basis-papers")
    if (args.aggregate or args.synthesize or args.basis_papers) and (args.question or args.all_questions):
        parser.error("--aggregate, --synthesize, and --basis-papers work on completed questions; "
                     "omit --question/--all-questions")
    if args.allow_partial and not args.synthesize:
        parser.error("--allow-partial applies only to --synthesize")
    if args.resume and (args.aggregate or args.synthesize or args.basis_papers):
        parser.error("--resume applies only to question runs")
    # Local inputs only: these messages carry no provider responses, so they are shown in full.
    try:
        if args.aggregate:
            spec = load_spec(args.spec)
            evidence = aggregate_long_horizon(spec, args.output)
            if not args.dry_run:
                write_aggregate(args.output / SYNTHESIS_DIR, evidence)
            print(f"Aggregated {len(evidence.completed)} questions, {len(evidence.claims)} claims, "
                  f"{len(evidence.bibliography)} sources; missing: {', '.join(evidence.missing) or 'none'}"
                  + (f"; not matched to the current objective: {', '.join(evidence.stale)}" if evidence.stale else "")
                  + (f"; files changed since publication: {', '.join(evidence.invalid)}" if evidence.invalid else "")
                  + (f"; reports citing no claims: {', '.join(evidence.uncited)}" if evidence.uncited else ""))
            return
        if args.basis_papers:
            spec = load_spec(args.spec)
            evidence = aggregate_long_horizon(spec, args.output)
            if not evidence.completed:
                raise ValueError("no completed questions to take seeds from")
            if args.dry_run:
                print(f"Basis papers would be seeded from {len(evidence.bibliography)} sources in "
                      f"{', '.join(item.question['id'] for item in evidence.completed)}")
                return
    except (ValueError, OSError) as exc:
        parser.exit(2, f"Long-horizon input invalid: {exc}\n")
    if args.basis_papers:
        try:
            report = asyncio.run(find_basis_papers(spec, evidence, ResearchSettings.from_env()))
        except (httpx.HTTPError, LookupError, ValueError) as exc:
            status = f" HTTP {exc.response.status_code}" if isinstance(exc, httpx.HTTPStatusError) else ""
            hint = "; set SEMANTIC_SCHOLAR_API_KEY for a dedicated rate limit" if status == " HTTP 429" else ""
            parser.exit(1, f"Basis papers failed ({type(exc).__name__}{status}){hint}.\n")
        write_basis_papers(args.output / SYNTHESIS_DIR, spec, report)
        new = sum(not paper.in_study for paper in report.papers)
        print(f"Basis papers: {len(report.papers)} works cited by at least {report.min_seed_citations} of "
              f"{report.resolved_seeds} resolved seeds ({new} not yet in the study); "
              f"{len(report.citing_works)} later works citing at least {report.min_seed_citations} of them; "
              f"{len(report.unresolved)} seeds not found; wrote {args.output / SYNTHESIS_DIR / 'basis_papers.md'}")
        return
    try:
        if args.synthesize:
            policy = get_policy(args.policy)
            route = synthesis_route(policy, load_spec(args.spec)["synthesis"])
            spec, evidence, prompt = prepare_synthesis(
                args.spec, args.output, allow_partial=args.allow_partial, route=route,
            )
            if args.dry_run:
                needed = retry_token_budget(
                    prompt, route, ResearchRole.SYNTHESIZER,
                    output_allowance=int(route.settings["max_tokens"]),
                )
                print(f"Synthesis inputs: {', '.join(item.question['id'] for item in evidence.completed)}; "
                      f"missing: {', '.join(evidence.missing) or 'none'}; "
                      + (f"reports citing no claims: {', '.join(evidence.uncited)}; " if evidence.uncited else "")
                      + f"prompt {len(prompt)} of "
                      f"{spec['synthesis']['max_prompt_chars']} chars; one retry about {needed} of "
                      f"{route.total_tokens_limit} tokens")
                return
        else:
            spec = load_spec(args.spec)
            ids = [item["id"] for item in spec["questions"]]
            selected = ids if args.all_questions else [args.question or ids[0]]
            if set(selected) - set(ids):
                raise ValueError("unknown long-horizon question ID")
            if args.dry_run:
                kept = (resumable_questions(spec, args.spec, args.output, selected, policy_name=args.policy,
                                            settings=ResearchSettings.from_env()) if args.resume else {})
                to_run = [question_id for question_id in selected if question_id not in kept]
                print(f"Long-horizon study {spec['id']}: {', '.join(to_run) or 'nothing to run'}"
                      + (f"; kept from earlier runs: {', '.join(kept)}" if kept else ""))
                return
    except (ValueError, OSError) as exc:
        parser.exit(2, f"Long-horizon input invalid: {exc}\n")
    if not args.paid:
        parser.error("model runs require --paid; use --dry-run to validate without calls")
    try:
        settings = ResearchSettings.from_env()
        _paid_preflight(settings, args.policy, args.persist)
        if args.synthesize:
            manifest = asyncio.run(synthesize_long_horizon(
                args.spec, policy_name=args.policy, settings=settings, output_dir=args.output,
                persist=args.persist, allow_partial=args.allow_partial,
            ))
        else:
            manifest = asyncio.run(run_long_horizon(
                args.spec, question_ids=selected, policy_name=args.policy,
                settings=settings, output_dir=args.output, persist=args.persist, resume=args.resume,
            ))
    except Exception as exc:  # noqa: BLE001 - report the type only; provider errors can carry response bodies
        parser.exit(1, f"Long-horizon run failed ({type(exc).__name__}); check settings and run manifest.\n")
    print(f"Long-horizon manifest: {manifest}")
    record = json.loads(manifest.read_text(encoding="utf-8"))
    review = [f"{item['id']} ({'; '.join(item['review_reasons'])})"
              for item in record.get("questions", []) if item.get("review_reasons")]
    if review:
        print(f"Completed but needs review: {', '.join(review)}")
    if record["status"] != "completed":
        failures = ", ".join(f"{item['id']} ({item['error']})" for item in record.get("questions", [])
                             if item["status"] == "failed")
        not_run = f"; not run: {', '.join(record['not_run'])}" if record.get("not_run") else ""
        parser.exit(1, f"Long-horizon run finished with status {record['status']}; failed: {failures}{not_run}\n")


if __name__ == "__main__":
    main()

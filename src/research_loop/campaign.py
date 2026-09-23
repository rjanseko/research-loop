"""Reproducible, bounded launcher for research campaign questions and campaign synthesis."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import tomllib
from contextlib import AsyncExitStack
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from .agents import campaign_synthesizer_agent
from .async_orchestrator import ResearchConfig, ResearchOutcome, review_reasons
from .acquisition import FETCH_VERSION
from .db import open_migrated_pool
from .diagnose import run_diagnose
from .experiment import fingerprint, git_state, package_versions, safe_value, write_manifest
from .ledger import EvidenceLedger
from .observability import configure_logfire
from .orchestrator import ResearchLoop
from .policy import ModelPolicy, get_policy
from .repository import InMemoryResearchRepository, PostgresResearchRepository
from .schemas import (
    EVIDENCE_VERSION,
    CampaignFindings,
    ClaimCheck,
    CampaignSynthesis,
    FinalReport,
    ResearchConstraints,
    ResearchResult,
    ResearchRole,
    VerificationReport,
)
from .settings import ResearchSettings
from .tools import ResearchToolMode


CAMPAIGN_FILE = Path(__file__).resolve().parents[2] / "campaigns" / "long_horizon_agentic_se" / "campaign.toml"
SYNTHESIS_DIR = "campaign"
# Question IDs name folders under the output directory, next to these.
_RESERVED_QUESTION_IDS = (".", "..", SYNTHESIS_DIR, "manifests")
_DEFAULT_MAX_FAILED_QUESTIONS = 2
CATALOGS = ("benchmark_catalog", "architecture_patterns", "failure_modes", "open_questions", "hypotheses")
_SYNTHESIS_LIMITS = ("cost_limit_usd", "total_tokens_limit", "max_requests", "max_output_tokens", "max_prompt_chars")


def _positive_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def load_campaign(path: Path) -> dict[str, Any]:
    campaign = tomllib.loads(path.read_text(encoding="utf-8"))
    if campaign.get("graph_version") != "research-graph-v1":
        raise ValueError("campaign requires research-graph-v1")
    questions = campaign.get("questions") or []
    ids = [item.get("id") for item in questions]
    if not ids or any(not isinstance(item, str) or not item or "/" in item for item in ids) or len(ids) != len(set(ids)) or any(not item.get("text") for item in questions):
        raise ValueError("campaign needs unique question IDs without '/' and nonempty question text")
    if reserved := sorted(set(ids) & set(_RESERVED_QUESTION_IDS)):
        raise ValueError(f"campaign question IDs cannot be {', '.join(map(repr, _RESERVED_QUESTION_IDS))}; "
                         f"found {', '.join(map(repr, reserved))}")
    execution = campaign.get("execution") or {}
    if not _positive_number(execution.get("question_cost_limit_usd")):
        raise ValueError("campaign needs a positive execution.question_cost_limit_usd")
    reserve = execution.get("question_reserve_usd", 0.0)
    if not isinstance(reserve, (int, float)) or isinstance(reserve, bool) or not 0 <= reserve < execution["question_cost_limit_usd"]:
        raise ValueError("execution.question_reserve_usd must be at least 0 and below question_cost_limit_usd")
    max_failed = execution.get("max_failed_questions", _DEFAULT_MAX_FAILED_QUESTIONS)
    if not isinstance(max_failed, int) or isinstance(max_failed, bool) or max_failed < 1:
        raise ValueError("execution.max_failed_questions must be a positive integer")
    notes = execution.get("research_notes", [])
    if not isinstance(notes, list) or not all(isinstance(note, str) and note for note in notes):
        raise ValueError("execution.research_notes must be a list of nonempty strings")
    synthesis = campaign.get("synthesis") or {}
    if not all(_positive_number(synthesis.get(key)) for key in _SYNTHESIS_LIMITS):
        raise ValueError(f"campaign needs positive synthesis limits: {', '.join(_SYNTHESIS_LIMITS)}")
    return campaign


def render_objective(campaign: dict[str, Any], question: dict[str, str]) -> str:
    source_policy = campaign["source_policy"]
    return "\n".join([
        campaign["title"],
        f"Question {question['id']}: {question['text']}",
        f"Publication window: {campaign['period_start']} to {campaign['period_end']}.",
        "Source policy:",
        *[f"{key}: {value}" for key, value in source_policy.items()],
        "Keep preprints, submissions, reviews, and published versions distinct. Report uncertainty and contradictions.",
    ])


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _spec_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _objective_sha256(campaign: dict[str, Any], question: dict[str, str]) -> str:
    return hashlib.sha256(render_objective(campaign, question).encode()).hexdigest()


def _manifest_base(campaign: dict[str, Any], path: Path, *, kind: str, policy_snapshot: dict[str, Any],
                   fingerprint_extra: dict[str, Any], persist: bool) -> dict[str, Any]:
    spec_hash = _spec_sha256(path)
    return {
        "schema_version": 2, "kind": kind, "campaign_id": campaign["id"], "experiment_id": str(uuid4()),
        "campaign_spec_sha256": spec_hash,
        "config_fingerprint": fingerprint({"spec": spec_hash, "policy": policy_snapshot, **fingerprint_extra}),
        "git": git_state(), "graph_version": campaign["graph_version"],
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
              f"The verifier checked {len(checks)} statements: {len(checks) - len(unsupported)} supported, "
              f"{len(unsupported)} not supported ({major} major)."]
    if verification.needs_research:
        lines.append("It asked for more research, which this run did not do, so the issues below are unresolved.")
    flagged = _flagged_checks(verification)
    if flagged:
        lines.append("")
    for check in flagged:
        label = f"{check.severity}, {'supported' if check.supported else 'not supported'}"
        cited = f" (claims: {', '.join(check.claim_ids)})" if check.claim_ids else ""
        lines.append(f"- **[{label}]** {check.statement}{cited}: {check.explanation}")
    for field, noun in (("quote_check", "quoted passages"), ("source_check", "cited sources")):
        verdicts = [(claim.id, getattr(item, field)) for claim in (ledger.claims() if ledger else [])
                    for item in claim.evidence if getattr(item, field)]
        not_found = [claim_id for claim_id, verdict in verdicts if verdict == "not_found"]
        if not_found:
            lines += ["", f"{len(not_found)} of {len(verdicts)} {noun} {'was' if len(not_found) == 1 else 'were'} "
                          "not found in any text the research tools returned "
                          f"(claims: {', '.join(dict.fromkeys(not_found))})."]
        elif verdicts:
            lines += ["", f"All {len(verdicts)} {noun} were found in text the research tools returned."]
    return "\n".join(lines) + "\n"


def campaign_policy(campaign: dict[str, Any], policy_name: str, model_overrides: Mapping[str, str]) -> ModelPolicy:
    """The named policy with the campaign's question budget, planner range, and route limits applied."""
    policy = get_policy(policy_name, model_overrides=model_overrides)
    execution = campaign["execution"]
    policy.planner_question_range = (
        int(execution["planner_question_min"]), int(execution["planner_question_max"])
    )
    deep_route = policy.routes[ResearchRole.DEEP_DIVE]
    policy.routes[ResearchRole.DEEP_DIVE] = replace(
        deep_route,
        cost_limit=min(deep_route.cost_limit or float("inf"), float(execution["deep_dive_cost_limit_usd"])),
    )
    policy.job_cost_limit = float(execution["question_cost_limit_usd"])
    policy.job_reserve_usd = float(execution.get("question_reserve_usd", 0.0))
    scout_limits = {
        route_field: int(execution[key])
        for key, route_field in (("scout_max_requests", "max_requests"), ("scout_max_tool_calls", "max_tool_calls"),
                                 ("scout_total_tokens_limit", "total_tokens_limit"))
        if key in execution
    }
    if scout_limits:
        policy.routes[ResearchRole.SCOUT] = replace(policy.routes[ResearchRole.SCOUT], **scout_limits)
        if policy.cheap_scout:
            policy.cheap_scout = replace(policy.cheap_scout, **scout_limits)
        if policy.multimodal_scout:
            policy.multimodal_scout = replace(policy.multimodal_scout, **scout_limits)
    return policy


def campaign_run_config(campaign: dict[str, Any]) -> ResearchConfig:
    execution = campaign["execution"]
    return ResearchConfig(
        tool_mode=ResearchToolMode.NORMALIZED,
        scholarly_cache_mode="record",
        max_parallel_scouts=int(execution["max_parallel_scouts"]),
        max_parallel_deep_dives=int(execution.get("max_parallel_deep_dives", ResearchConfig.max_parallel_deep_dives)),
        max_deep_dives_per_round=int(execution["max_deep_dives_per_round"]),
        max_verification_rounds=int(execution["max_verification_rounds"]),
        salvage_exhausted_research=True,
    )


def _write_question_outputs(folder: Path, outcome: ResearchOutcome, objective: str,
                            manifest: dict[str, Any]) -> dict[str, Any]:
    """Export one completed question; returns its manifest record. run.json is written last."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "report.md").write_text(render_question_report(outcome.report, outcome.verification, outcome.ledger),
                                      encoding="utf-8")
    (folder / "report.json").write_text(outcome.report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    _write_json(folder / "evidence_ledger.json", outcome.ledger.to_json())
    bibliography = list({
        json.dumps(source.model_dump(mode="json"), sort_keys=True): source.model_dump(mode="json")
        for source in outcome.ledger.sources()
    }.values())
    _write_json(folder / "bibliography.json", bibliography)
    (folder / "verification.json").write_text(outcome.verification.model_dump_json(indent=2) + "\n", encoding="utf-8")
    record = {
        "id": folder.name, "job_id": str(outcome.job_id),
        "claim_count": outcome.ledger.claim_count(),
        "source_count": len(bibliography), "status": "completed",
        "cost_usd": None if outcome.cost_usd is None else str(outcome.cost_usd),
        "review_reasons": review_reasons(outcome.report, outcome.verification, outcome.ledger),
    }
    # run.json marks the folder as a completed, attributable run.
    _write_json(folder / "run.json", record | {
        "experiment_id": manifest["experiment_id"],
        "campaign_spec_sha256": manifest["campaign_spec_sha256"],
        # What the evidence answers; budget-only spec edits leave it unchanged.
        "objective_sha256": hashlib.sha256(objective.encode()).hexdigest(),
        "config_fingerprint": manifest["config_fingerprint"],
        "finished_at": datetime.now(UTC).isoformat(),
    })
    return record


async def run_campaign(
    path: Path,
    *,
    question_ids: list[str],
    policy_name: str,
    settings: ResearchSettings,
    output_dir: Path,
    persist: bool,
) -> Path:
    campaign = load_campaign(path)
    questions = {item["id"]: item for item in campaign["questions"]}
    unknown = set(question_ids) - set(questions)
    if unknown:
        raise ValueError(f"unknown campaign question IDs: {', '.join(sorted(unknown))}")
    if persist and not settings.database_dsn:
        raise ValueError("DATABASE_URL required for --persist")
    configure_logfire(settings)
    execution = campaign["execution"]
    policy = campaign_policy(campaign, policy_name, settings.model_overrides)
    run_config = campaign_run_config(campaign)
    policy_snapshot = safe_value(policy.snapshot())
    acquisition = {"search": "duckduckgo", "web_fetch": "trafilatura+bs4",
                   "scholar": ["openalex", "crossref", "arxiv", "acl", "opencitations"],
                   "cache_mode": "record", "fetch_version": FETCH_VERSION}
    manifest = _manifest_base(campaign, path, kind="questions", policy_snapshot=policy_snapshot,
                              fingerprint_extra={"acquisition": acquisition, "evidence_version": EVIDENCE_VERSION},
                              persist=persist)
    manifest |= {
        "evidence_version": EVIDENCE_VERSION,
        "tool_mode": "normalized", "acquisition": acquisition, "cache_mode": "record",
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
    manifest_path = output_dir / "manifests" / f"{manifest['experiment_id']}.json"
    write_manifest(manifest_path, manifest)
    max_failed = int(execution.get("max_failed_questions", _DEFAULT_MAX_FAILED_QUESTIONS))
    failed = 0
    try:
        async with AsyncExitStack() as stack:
            pool = await open_migrated_pool(stack, settings.database_dsn) if persist else None
            for index, question_id in enumerate(question_ids):
                backend = PostgresResearchRepository(pool) if pool else InMemoryResearchRepository()
                loop = ResearchLoop(policy, run_config, repository=backend, settings=settings)
                objective = render_objective(campaign, questions[question_id])
                try:
                    outcome = await loop.run(
                        objective,
                        constraints=ResearchConstraints(notes=list(execution.get("research_notes", []))),
                    )
                except Exception as exc:
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
class CampaignEvidence:
    completed: list[CompletedQuestion]
    # Every question left out of synthesis, including stale ones.
    missing: list[str]
    claims: list[dict[str, Any]]
    bibliography: list[dict[str, Any]]
    contradictions: dict[str, list[dict[str, Any]]]
    verification: dict[str, dict[str, Any]]
    # Completed outputs whose recorded objective hash is missing or differs from the current spec's.
    stale: list[str]

    @property
    def refs(self) -> frozenset[str]:
        return frozenset(item["ref"] for item in self.claims)


def aggregate_campaign(campaign: dict[str, Any], output_dir: Path) -> CampaignEvidence:
    """Merge completed question outputs; claim refs are '<question>/<claim id>' in ledger order.

    Only runs whose recorded objective hash matches the current spec count; budget-only spec
    edits leave the objective, and so the hash, unchanged.
    """
    completed: list[CompletedQuestion] = []
    missing: list[str] = []
    stale: list[str] = []
    for question in campaign["questions"]:
        folder = output_dir / question["id"]
        try:
            run = json.loads((folder / "run.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            missing.append(question["id"])
            continue
        if run.get("status") != "completed":
            missing.append(question["id"])
            continue
        if run.get("objective_sha256") != _objective_sha256(campaign, question):
            stale.append(question["id"])
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
        first_ref: dict[str, str] = {}
        for results in item.ledger.values():
            for result in results:
                for claim in result.claims:
                    ref, suffix = f"{question_id}/{claim.id}", 2
                    while ref in seen:
                        ref, suffix = f"{question_id}/{claim.id}~{suffix}", suffix + 1
                    seen.add(ref)
                    first_ref.setdefault(claim.id, ref)
                    claims.append({"ref": ref, "question_id": question_id,
                                   "result_question_id": result.question_id, "claim": claim.model_dump(mode="json")})
                    for evidence in claim.evidence:
                        source = evidence.source.model_dump(mode="json")
                        entry = bibliography.setdefault(json.dumps(source, sort_keys=True), {**source, "question_ids": []})
                        if question_id not in entry["question_ids"]:
                            entry["question_ids"].append(question_id)
        contradictions[question_id] = [
            {"description": contradiction.description,
             "claim_refs": [first_ref[claim_id] for claim_id in contradiction.claim_ids if claim_id in first_ref]}
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
                 "claim_refs": [first_ref[claim_id] for claim_id in check.claim_ids if claim_id in first_ref]}
                for check in _flagged_checks(item.verification)
            ],
        }
    return CampaignEvidence(completed, missing, claims, list(bibliography.values()), contradictions,
                            verification=verification, stale=stale)


def write_aggregate(campaign_dir: Path, evidence: CampaignEvidence) -> None:
    campaign_dir.mkdir(parents=True, exist_ok=True)
    _write_json(campaign_dir / "evidence_ledger.json", {
        "questions": {
            item.question["id"]: {key: [result.model_dump(mode="json") for result in results]
                                  for key, results in item.ledger.items()}
            for item in evidence.completed
        },
        "claims": evidence.claims,
    })
    _write_json(campaign_dir / "bibliography.json", evidence.bibliography)


_SOURCE_FIELDS = {"title", "url", "source_type", "publication_status", "published_at", "doi", "arxiv_id",
                  "openalex_id", "acl_id", "provider", "is_retracted", "attachment_id", "locator"}


def synthesis_prompt(campaign: dict[str, Any], evidence: CampaignEvidence, *, excerpt_chars: int = 300) -> str:
    payload = {
        "campaign": {
            "title": campaign["title"], "as_of": campaign["as_of"],
            "publication_window": [campaign["period_start"], campaign["period_end"]],
            "source_policy": campaign["source_policy"],
            "findings_sections": campaign["outputs"]["findings_sections"],
            "hypothesis_fields": campaign["outputs"]["hypothesis_fields"],
        },
        "missing_question_ids": evidence.missing,
        "questions": [
            {
                "id": item.question["id"], "text": item.question["text"],
                "report": item.report.answer, "caveats": item.report.caveats,
                "contradictions": evidence.contradictions[item.question["id"]],
                "verification": evidence.verification[item.question["id"]],
                "unresolved_questions": [
                    text for results in item.ledger.values() for result in results
                    for text in result.unresolved_questions
                ],
            }
            for item in evidence.completed
        ],
        "evidence": [
            {
                "ref": entry["ref"], "question_id": entry["question_id"],
                "statement": entry["claim"]["statement"], "confidence": entry["claim"]["confidence"],
                "evidence": [
                    {
                        "source": {key: value for key, value in item["source"].items()
                                   if key in _SOURCE_FIELDS and value is not None},
                        "excerpt": item["excerpt"][:excerpt_chars],
                        "supports": item["supports"],
                        **{check: item[check] for check in ("quote_check", "source_check") if item.get(check)},
                    }
                    for item in entry["claim"]["evidence"]
                ],
            }
            for entry in evidence.claims
        ],
    }
    return json.dumps(payload, ensure_ascii=False)


def render_campaign_report(campaign: dict[str, Any], evidence: CampaignEvidence, synthesis: CampaignSynthesis) -> str:
    done = [item.question["id"] for item in evidence.completed]
    lines = [f"# {campaign['title']}", ""]
    scope = f"As of {campaign['as_of']}. Synthesized from {len(done)} of {len(campaign['questions'])} questions: {', '.join(done)}."
    if evidence.missing:
        scope += f" **Partial synthesis**; missing: {', '.join(evidence.missing)}."
    lines += [scope, "", "## Summary", "", synthesis.summary.strip(), "", "## Findings"]
    for section in CampaignFindings.model_fields:
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


def write_synthesis(campaign_dir: Path, campaign: dict[str, Any], evidence: CampaignEvidence,
                    synthesis: CampaignSynthesis) -> None:
    (campaign_dir / "synthesis.json").write_text(synthesis.model_dump_json(indent=2) + "\n", encoding="utf-8")
    for name in CATALOGS:
        _write_json(campaign_dir / f"{name}.json", [item.model_dump(mode="json") for item in getattr(synthesis, name)])
    (campaign_dir / "report.md").write_text(render_campaign_report(campaign, evidence, synthesis), encoding="utf-8")


def prepare_synthesis(path: Path, output_dir: Path, *, allow_partial: bool) -> tuple[dict[str, Any], CampaignEvidence, str]:
    """Validate synthesis inputs without model calls; raise before any paid step."""
    campaign = load_campaign(path)
    evidence = aggregate_campaign(campaign, output_dir)
    stale = (f" ({', '.join(evidence.stale)} cannot be matched to the current spec's objective; rerun them)"
             if evidence.stale else "")
    if not evidence.completed:
        raise ValueError(f"no completed campaign questions to synthesize{stale}")
    if evidence.missing and not allow_partial:
        raise ValueError(f"questions not completed: {', '.join(evidence.missing)}{stale}; "
                         "pass --allow-partial to synthesize anyway")
    prompt = synthesis_prompt(campaign, evidence)
    max_chars = int(campaign["synthesis"]["max_prompt_chars"])
    if len(prompt) > max_chars:
        raise ValueError(f"synthesis prompt has {len(prompt)} chars, above synthesis.max_prompt_chars={max_chars}")
    return campaign, evidence, prompt


async def synthesize_campaign(
    path: Path,
    *,
    policy_name: str,
    settings: ResearchSettings,
    output_dir: Path,
    persist: bool,
    allow_partial: bool,
) -> Path:
    campaign, evidence, prompt = prepare_synthesis(path, output_dir, allow_partial=allow_partial)
    if persist and not settings.database_dsn:
        raise ValueError("DATABASE_URL required for --persist")
    configure_logfire(settings)
    limits = campaign["synthesis"]
    policy = get_policy(policy_name, model_overrides=settings.model_overrides)
    base = policy.for_role(ResearchRole.SYNTHESIZER)
    route = replace(
        base,
        max_requests=int(limits["max_requests"]),
        total_tokens_limit=int(limits["total_tokens_limit"]),
        cost_limit=float(limits["cost_limit_usd"]),
        settings={**base.settings, "max_tokens": int(limits["max_output_tokens"])},
    )
    policy.job_cost_limit = float(limits["cost_limit_usd"])
    prompt_sha256 = hashlib.sha256(prompt.encode()).hexdigest()
    inputs = [
        {"id": item.question["id"], "job_id": item.run.get("job_id"), "experiment_id": item.run.get("experiment_id"),
         "config_fingerprint": item.run.get("config_fingerprint"),
         "campaign_spec_sha256": item.run.get("campaign_spec_sha256"),
         "review_reasons": item.run.get("review_reasons")}
        for item in evidence.completed
    ]
    manifest = _manifest_base(campaign, path, kind="synthesis", policy_snapshot=safe_value(policy.snapshot()),
                              fingerprint_extra={"route": route.snapshot(), "prompt_sha256": prompt_sha256},
                              persist=persist)
    manifest |= {
        "synthesis_route": safe_value(route.snapshot()), "prompt_sha256": prompt_sha256, "prompt_chars": len(prompt),
        "inputs": inputs, "missing_question_ids": evidence.missing, "stale_question_ids": evidence.stale,
        "partial": bool(evidence.missing),
        "mixed_question_configs": len({item["config_fingerprint"] for item in inputs}) > 1,
        "claim_count": len(evidence.claims), "source_count": len(evidence.bibliography),
    }
    manifest_path = output_dir / "manifests" / f"{manifest['experiment_id']}.json"
    campaign_dir = output_dir / SYNTHESIS_DIR
    write_aggregate(campaign_dir, evidence)
    write_manifest(manifest_path, manifest)
    try:
        async with AsyncExitStack() as stack:
            pool = await open_migrated_pool(stack, settings.database_dsn) if persist else None
            loop = ResearchLoop(policy, repository=PostgresResearchRepository(pool) if pool else InMemoryResearchRepository(),
                                settings=settings)
            outcome = await loop.run_agent_job(
                f"{campaign['title']}: campaign synthesis",
                agent=campaign_synthesizer_agent,
                role=ResearchRole.SYNTHESIZER,
                route=route,
                prompt=prompt,
                deps=evidence.refs,
                config={"campaign": {"id": campaign["id"], "kind": "synthesis", "prompt_sha256": prompt_sha256,
                                     "question_ids": [item["id"] for item in inputs]}},
            )
        write_synthesis(campaign_dir, campaign, evidence, outcome.output)
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
    parser = argparse.ArgumentParser(description="Run bounded research campaign questions or synthesize completed ones")
    parser.add_argument("--spec", type=Path, default=CAMPAIGN_FILE)
    parser.add_argument("--question", default=None, help="Question ID; defaults to the first question")
    parser.add_argument("--all-questions", action="store_true", help="Run every question sequentially")
    parser.add_argument("--aggregate", action="store_true", help="Merge completed question outputs without model calls")
    parser.add_argument("--synthesize", action="store_true", help="Write campaign catalogs and hypotheses from completed questions")
    parser.add_argument("--allow-partial", action="store_true", help="Synthesize even if some questions are not completed")
    parser.add_argument("--policy", choices=("quality", "breadth", "glm-heavy"), default="quality")
    parser.add_argument("--paid", action="store_true", help="Authorize model provider calls")
    parser.add_argument("--dry-run", action="store_true", help="Validate and report what would run, without calls or writes")
    parser.add_argument("--persist", action="store_true", help="Store runs in Postgres")
    parser.add_argument("--output", type=Path, default=Path("benchmark_outputs/long_horizon_campaign"))
    args = parser.parse_args()
    if args.all_questions and args.question:
        parser.error("choose --question or --all-questions")
    if args.aggregate and args.synthesize:
        parser.error("choose --aggregate or --synthesize")
    if (args.aggregate or args.synthesize) and (args.question or args.all_questions):
        parser.error("--aggregate and --synthesize work on completed questions; omit --question/--all-questions")
    if args.allow_partial and not args.synthesize:
        parser.error("--allow-partial applies only to --synthesize")
    # Local inputs only: these messages carry no provider responses, so they are shown in full.
    try:
        if args.aggregate:
            campaign = load_campaign(args.spec)
            evidence = aggregate_campaign(campaign, args.output)
            if not args.dry_run:
                write_aggregate(args.output / SYNTHESIS_DIR, evidence)
            print(f"Aggregated {len(evidence.completed)} questions, {len(evidence.claims)} claims, "
                  f"{len(evidence.bibliography)} sources; missing: {', '.join(evidence.missing) or 'none'}"
                  + (f"; not matched to the current objective: {', '.join(evidence.stale)}" if evidence.stale else ""))
            return
        if args.synthesize:
            campaign, evidence, prompt = prepare_synthesis(args.spec, args.output, allow_partial=args.allow_partial)
            if args.dry_run:
                print(f"Synthesis inputs: {', '.join(item.question['id'] for item in evidence.completed)}; "
                      f"missing: {', '.join(evidence.missing) or 'none'}; prompt {len(prompt)} of "
                      f"{campaign['synthesis']['max_prompt_chars']} chars")
                return
        else:
            campaign = load_campaign(args.spec)
            ids = [item["id"] for item in campaign["questions"]]
            selected = ids if args.all_questions else [args.question or ids[0]]
            if set(selected) - set(ids):
                raise ValueError("unknown campaign question ID")
            if args.dry_run:
                print(f"Campaign {campaign['id']}: {', '.join(selected)}")
                return
    except (ValueError, OSError) as exc:
        parser.exit(2, f"Campaign input invalid: {exc}\n")
    if not args.paid:
        parser.error("model runs require --paid; use --dry-run to validate without calls")
    try:
        settings = ResearchSettings.from_env()
        _paid_preflight(settings, args.policy, args.persist)
        if args.synthesize:
            manifest = asyncio.run(synthesize_campaign(
                args.spec, policy_name=args.policy, settings=settings, output_dir=args.output,
                persist=args.persist, allow_partial=args.allow_partial,
            ))
        else:
            manifest = asyncio.run(run_campaign(
                args.spec, question_ids=selected, policy_name=args.policy,
                settings=settings, output_dir=args.output, persist=args.persist,
            ))
    except Exception as exc:
        parser.exit(1, f"Campaign failed ({type(exc).__name__}); check settings and run manifest.\n")
    print(f"Campaign manifest: {manifest}")
    record = json.loads(manifest.read_text(encoding="utf-8"))
    review = [f"{item['id']} ({'; '.join(item['review_reasons'])})"
              for item in record.get("questions", []) if item.get("review_reasons")]
    if review:
        print(f"Completed but needs review: {', '.join(review)}")
    if record["status"] != "completed":
        failures = ", ".join(f"{item['id']} ({item['error']})" for item in record.get("questions", [])
                             if item["status"] == "failed")
        not_run = f"; not run: {', '.join(record['not_run'])}" if record.get("not_run") else ""
        parser.exit(1, f"Campaign finished with status {record['status']}; failed: {failures}{not_run}\n")


if __name__ == "__main__":
    main()

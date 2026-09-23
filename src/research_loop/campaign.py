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
from typing import Any
from uuid import uuid4

from .agents import campaign_synthesizer_agent
from .async_orchestrator import ResearchConfig
from .db import migration_files, migration_status
from .diagnose import run_diagnose
from .experiment import _fingerprint, _git_state, _packages, _safe_value, write_manifest
from .observability import configure_logfire
from .orchestrator import ResearchLoop
from .policy import get_policy
from .repository import InMemoryResearchRepository, PostgresResearchRepository
from .schemas import (
    CampaignFindings,
    CampaignSynthesis,
    FinalReport,
    ResearchConstraints,
    ResearchResult,
    ResearchRole,
)
from .settings import ResearchSettings
from .tools import ResearchToolMode


CAMPAIGN_FILE = Path(__file__).resolve().parents[2] / "campaigns" / "long_horizon_agentic_se" / "campaign.toml"
SYNTHESIS_DIR = "campaign"
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
    if not _positive_number((campaign.get("execution") or {}).get("question_cost_limit_usd")):
        raise ValueError("campaign needs a positive execution.question_cost_limit_usd")
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


def _check_database(dsn: str) -> None:
    import psycopg
    with psycopg.connect(dsn, autocommit=True, connect_timeout=5) as conn:
        pending = [m.name for m, state in migration_status(conn, migration_files()) if state != "applied"]
    if pending:
        raise RuntimeError("database migrations pending or changed; run research-db migrate")


async def _open_pool(stack: AsyncExitStack, settings: ResearchSettings, persist: bool) -> Any:
    if not persist:
        return None
    await asyncio.to_thread(_check_database, settings.database_dsn)
    from psycopg_pool import AsyncConnectionPool
    return await stack.enter_async_context(AsyncConnectionPool(conninfo=settings.database_dsn, open=False))


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _manifest_base(campaign: dict[str, Any], path: Path, *, kind: str, policy_snapshot: dict[str, Any],
                   fingerprint_extra: dict[str, Any], persist: bool) -> dict[str, Any]:
    spec_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "schema_version": 2, "kind": kind, "campaign_id": campaign["id"], "experiment_id": str(uuid4()),
        "campaign_spec_sha256": spec_hash,
        "config_fingerprint": _fingerprint({"spec": spec_hash, "policy": policy_snapshot, **fingerprint_extra}),
        "git": _git_state(), "graph_version": campaign["graph_version"],
        "policy": policy_snapshot, "packages": _packages(), "persistent": persist,
        "started_at": datetime.now(UTC).isoformat(), "finished_at": None, "status": "running",
    }


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
    policy = get_policy(policy_name, model_overrides=settings.model_overrides)
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
    run_config = ResearchConfig(
        tool_mode=ResearchToolMode.NORMALIZED,
        scholarly_cache_mode="record",
        max_parallel_scouts=int(execution["max_parallel_scouts"]),
        max_deep_dives_per_round=int(execution["max_deep_dives_per_round"]),
        max_verification_rounds=int(execution["max_verification_rounds"]),
    )
    policy_snapshot = _safe_value(policy.snapshot())
    acquisition = {"search": "duckduckgo", "web_fetch": "trafilatura+bs4",
                   "scholar": ["openalex", "crossref", "arxiv", "acl", "opencitations"],
                   "cache_mode": "record"}
    manifest = _manifest_base(campaign, path, kind="questions", policy_snapshot=policy_snapshot,
                              fingerprint_extra={"acquisition": acquisition}, persist=persist)
    manifest |= {
        "tool_mode": "normalized", "acquisition": acquisition, "cache_mode": "record",
        "run_limits": {
            "max_parallel_scouts": run_config.max_parallel_scouts,
            "max_deep_dives_per_round": run_config.max_deep_dives_per_round,
            "max_verification_rounds": run_config.max_verification_rounds,
            "question_cost_limit_usd": policy.job_cost_limit,
        },
        "questions": [],
    }
    manifest_path = output_dir / "manifests" / f"{manifest['experiment_id']}.json"
    write_manifest(manifest_path, manifest)
    try:
        async with AsyncExitStack() as stack:
            pool = await _open_pool(stack, settings, persist)
            for question_id in question_ids:
                backend = PostgresResearchRepository(pool) if pool else InMemoryResearchRepository()
                loop = ResearchLoop(
                    policy,
                    run_config,
                    repository=backend,
                )
                question = questions[question_id]
                try:
                    outcome = await loop.run(
                        render_objective(campaign, question),
                        constraints=ResearchConstraints(notes=[
                            "Use primary benchmark papers and official repositories for benchmark claims.",
                            "Label publication status and include DOI/arXiv/OpenAlex/ACL IDs when available.",
                        ]),
                    )
                except Exception as exc:
                    manifest["questions"].append({
                        "id": question_id, "status": "failed", "error": type(exc).__name__,
                    })
                    raise
                folder = output_dir / question_id
                folder.mkdir(parents=True, exist_ok=True)
                (folder / "report.md").write_text(outcome.report.answer + "\n", encoding="utf-8")
                (folder / "report.json").write_text(outcome.report.model_dump_json(indent=2) + "\n", encoding="utf-8")
                _write_json(folder / "evidence_ledger.json", {
                    key: [item.model_dump(mode="json") for item in values]
                    for key, values in outcome.ledger.results.items()
                })
                bibliography = list({
                    json.dumps(source.model_dump(mode="json"), sort_keys=True): source.model_dump(mode="json")
                    for source in outcome.ledger.sources()
                }.values())
                _write_json(folder / "bibliography.json", bibliography)
                (folder / "verification.json").write_text(outcome.verification.model_dump_json(indent=2) + "\n", encoding="utf-8")
                record = {
                    "id": question_id, "job_id": str(outcome.job_id),
                    "claim_count": outcome.ledger.claim_count(),
                    "source_count": len(bibliography), "status": "completed",
                    "cost_usd": None if outcome.cost_usd is None else str(outcome.cost_usd),
                }
                # run.json is written last: it marks the folder as a completed, attributable run.
                _write_json(folder / "run.json", record | {
                    "experiment_id": manifest["experiment_id"],
                    "campaign_spec_sha256": manifest["campaign_spec_sha256"],
                    "config_fingerprint": manifest["config_fingerprint"],
                    "finished_at": datetime.now(UTC).isoformat(),
                })
                manifest["questions"].append(record)
                write_manifest(manifest_path, manifest)
        manifest["status"] = "completed"
    except Exception as exc:
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


@dataclass
class CampaignEvidence:
    completed: list[CompletedQuestion]
    missing: list[str]
    claims: list[dict[str, Any]]
    bibliography: list[dict[str, Any]]
    contradictions: dict[str, list[dict[str, Any]]]

    @property
    def refs(self) -> frozenset[str]:
        return frozenset(item["ref"] for item in self.claims)


def aggregate_campaign(campaign: dict[str, Any], output_dir: Path) -> CampaignEvidence:
    """Merge completed question outputs; claim refs are '<question>/<claim id>' in ledger order."""
    completed: list[CompletedQuestion] = []
    missing: list[str] = []
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
        raw_ledger = json.loads((folder / "evidence_ledger.json").read_text(encoding="utf-8"))
        completed.append(CompletedQuestion(
            question=question,
            run=run,
            report=FinalReport.model_validate_json((folder / "report.json").read_text(encoding="utf-8")),
            ledger={key: [ResearchResult.model_validate(item) for item in items] for key, items in raw_ledger.items()},
        ))

    claims: list[dict[str, Any]] = []
    contradictions: dict[str, list[dict[str, Any]]] = {}
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
    return CampaignEvidence(completed, missing, claims, list(bibliography.values()), contradictions)


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
    lines += ["", "Claim refs such as `q01/c3` resolve in `evidence_ledger.json` under `claims`.", ""]
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
    if not evidence.completed:
        raise ValueError("no completed campaign questions to synthesize")
    if evidence.missing and not allow_partial:
        raise ValueError(f"questions not completed: {', '.join(evidence.missing)}; pass --allow-partial to synthesize anyway")
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
         "campaign_spec_sha256": item.run.get("campaign_spec_sha256")}
        for item in evidence.completed
    ]
    manifest = _manifest_base(campaign, path, kind="synthesis", policy_snapshot=_safe_value(policy.snapshot()),
                              fingerprint_extra={"route": route.snapshot(), "prompt_sha256": prompt_sha256},
                              persist=persist)
    manifest |= {
        "synthesis_route": _safe_value(route.snapshot()), "prompt_sha256": prompt_sha256, "prompt_chars": len(prompt),
        "inputs": inputs, "missing_question_ids": evidence.missing, "partial": bool(evidence.missing),
        "mixed_question_configs": len({item["config_fingerprint"] for item in inputs}) > 1,
        "claim_count": len(evidence.claims), "source_count": len(evidence.bibliography),
    }
    manifest_path = output_dir / "manifests" / f"{manifest['experiment_id']}.json"
    campaign_dir = output_dir / SYNTHESIS_DIR
    write_aggregate(campaign_dir, evidence)
    write_manifest(manifest_path, manifest)
    try:
        async with AsyncExitStack() as stack:
            pool = await _open_pool(stack, settings, persist)
            loop = ResearchLoop(policy, repository=PostgresResearchRepository(pool) if pool else InMemoryResearchRepository())
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
    except Exception as exc:
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
        raise RuntimeError("live model preflight failed; run research-diagnose --live for details")


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
                  f"{len(evidence.bibliography)} sources; missing: {', '.join(evidence.missing) or 'none'}")
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


if __name__ == "__main__":
    main()

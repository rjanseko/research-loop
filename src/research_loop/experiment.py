from __future__ import annotations

import hashlib
import json
import platform
import subprocess
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from .acquisition import FETCH_VERSION
from .benchmarks.manifest import load_manifest
from .benchmarks.models import BenchmarkCaseSpec
from .graph import RESEARCH_GRAPH_VERSION
from .policy import get_policy
from .schemas import EVIDENCE_VERSION


PACKAGE_NAMES = ("research-loop-v5", "pydantic", "pydantic-ai", "pydantic-graph", "pydantic-evals", "psycopg")
SENSITIVE_KEYS = ("secret", "password", "api_key", "credential", "dsn", "url", "path", "host")


def safe_value(value: Any, key: str = "") -> Any:
    """Redact secrets, credentials, URLs, and local paths before a value goes into a manifest."""
    if any(part in key.lower() for part in SENSITIVE_KEYS) or key.lower().endswith("_token"):
        return "[redacted]"
    if isinstance(value, dict):
        return {str(k): safe_value(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [safe_value(item) for item in value]
    if isinstance(value, str) and (value.startswith("/") or value.startswith("file:")):
        return "[redacted]"
    return value


def package_versions() -> dict[str, str | None]:
    result = {}
    for name in PACKAGE_NAMES:
        try:
            result[name] = version(name)
        except PackageNotFoundError:
            result[name] = None
    return result


def git_state() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[2]
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=False
        )
        status = subprocess.run(
            ["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True, check=False
        )
    except OSError:
        return {"commit": None, "dirty": None}
    return {
        "commit": commit.stdout.strip() if commit.returncode == 0 else None,
        "dirty": bool(status.stdout) if status.returncode == 0 else None,
    }


def fingerprint(value: Any) -> str:
    """Stable SHA-256 of a JSON-serializable configuration."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def build_manifest(
    suite_path: Path,
    suite_name: str,
    cases: list[BenchmarkCaseSpec],
    policies: list[str],
    *,
    attachment_mode: str,
    tool_mode: str,
    repository_mode: str,
    evaluator_version: int,
    model_overrides: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    if suite_path.suffix.lower() == ".toml":
        sources = [
            {
                "name": source.name,
                "kind": source.kind.value,
                "split": source.split,
                "limit": source.limit,
                "seed": source.seed,
                "include_attachments": source.include_attachments,
                "levels": source.levels,
                "languages": source.languages,
                "indices": source.indices,
            }
            for source in load_manifest(suite_path).sources
        ]
    else:
        sources = [{"name": suite_name, "kind": "legacy_json"}]
    policy_snapshots = {
        name: safe_value(get_policy(name, model_overrides=model_overrides).snapshot())
        for name in policies
    }
    acquisition = {
        "search_backend": "duckduckgo" if tool_mode == "normalized" else "adaptive/provider-specific",
        "fetch_backend": "trafilatura+bs4" if tool_mode == "normalized" else "adaptive/provider-specific",
        "scholarly_backends": ["openalex", "crossref", "arxiv", "acl", "opencitations"],
        "scholarly_cache_mode": "off",
        "fetch_version": FETCH_VERSION,
    }
    config_fingerprint = fingerprint({
        "policy_schema_version": 1,
        "policies": policy_snapshots,
        "attachment_mode": attachment_mode,
        "tool_mode": tool_mode,
        "repository_mode": repository_mode,
        "acquisition": acquisition,
        "evidence_version": EVIDENCE_VERSION,
        "evaluator_version": evaluator_version,
    })
    return {
        "schema_version": 2,
        "experiment_id": str(uuid4()),
        "git": git_state(),
        "config_fingerprint": config_fingerprint,
        "policy_schema_version": 1,
        "acquisition": acquisition,
        "evidence_version": EVIDENCE_VERSION,
        "evaluator_version": evaluator_version,
        "python_version": platform.python_version(),
        "status": "running",
        "graph_version": RESEARCH_GRAPH_VERSION,
        "suite": suite_name,
        "benchmark_manifest": {
            "sha256": hashlib.sha256(suite_path.read_bytes()).hexdigest(),
            "sources": sources,
            "cases": [{"benchmark_id": case.benchmark_id, "case_id": case.case_id} for case in cases],
        },
        "policies": policy_snapshots,
        "attachment_mode": attachment_mode,
        "tool_mode": tool_mode,
        "repository_mode": repository_mode,
        "packages": package_versions(),
        "started_at": datetime.now(UTC).isoformat(),
        "finished_at": None,
        "runs": [],
    }


def write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)

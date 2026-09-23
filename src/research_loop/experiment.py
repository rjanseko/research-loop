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
from .agents import prompt_fingerprint
from .benchmarks.manifest import ADAPTERS, load_manifest
from .benchmarks.models import BenchmarkCaseSpec
from .graph import RESEARCH_GRAPH_VERSION
from .policy import get_policy
from .schemas import EVIDENCE_VERSION


PACKAGE_NAMES = (
    "research-loop", "pydantic", "pydantic-ai", "pydantic-graph", "pydantic-evals", "psycopg",
    # Acquisition and extraction: they shape what the models read.
    "httpx", "anyio", "ddgs", "trafilatura", "pypdf", "fonttools", "beautifulsoup4", "python-docx", "openpyxl", "pillow",
)
# Recorded in manifests. 3: adds tree_sha256, prompts_sha256, run_config, and dataset digests.
MANIFEST_SCHEMA_VERSION = 3
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


def git_state(root: Path | None = None) -> dict[str, Any]:
    """The commit, whether the tree is dirty, and for a dirty tree a hash of every uncommitted change.

    tree_sha256 covers tracked changes (`git diff HEAD`) and untracked files git does not ignore, so
    two different uncommitted trees on one commit hash differently. Ignored files, such as .env and
    benchmark outputs, are left out.
    """
    root = root or Path(__file__).resolve().parents[2]

    def git(*args: str) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(["git", *args], cwd=root, capture_output=True, check=False)

    try:
        commit, status = git("rev-parse", "HEAD"), git("status", "--porcelain")
    except OSError:
        return {"commit": None, "dirty": None, "tree_sha256": None}
    if commit.returncode or status.returncode:
        return {"commit": None if commit.returncode else commit.stdout.decode().strip(),
                "dirty": None if status.returncode else bool(status.stdout), "tree_sha256": None}
    tree = None
    if status.stdout:
        digest = hashlib.sha256(git("diff", "HEAD", "--binary").stdout)
        untracked = git("ls-files", "--others", "--exclude-standard", "-z").stdout.split(b"\0")
        for name in sorted(filter(None, untracked)):
            digest.update(b"\0" + name + b"\0")
            path = root / name.decode(errors="surrogateescape")
            if path.is_file():
                digest.update(path.read_bytes())
        tree = digest.hexdigest()
    return {"commit": commit.stdout.decode().strip(), "dirty": bool(status.stdout), "tree_sha256": tree}


def file_sha256(path: Path | None) -> str | None:
    if path is None or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


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
    run_config: dict[str, Any],
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
                "dataset_sha256": file_sha256(ADAPTERS[source.kind].dataset_path(source, base_dir=suite_path.parent)),
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
    prompts_sha256 = prompt_fingerprint()
    config_fingerprint = fingerprint({
        "policy_schema_version": 1,
        "run_config": run_config,
        "prompts_sha256": prompts_sha256,
        "datasets": [source.get("dataset_sha256") for source in sources],
        "policies": policy_snapshots,
        "attachment_mode": attachment_mode,
        "tool_mode": tool_mode,
        "repository_mode": repository_mode,
        "acquisition": acquisition,
        "evidence_version": EVIDENCE_VERSION,
        "evaluator_version": evaluator_version,
    })
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "experiment_id": str(uuid4()),
        "git": git_state(),
        "config_fingerprint": config_fingerprint,
        "policy_schema_version": 1,
        "acquisition": acquisition,
        "evidence_version": EVIDENCE_VERSION,
        "evaluator_version": evaluator_version,
        "prompts_sha256": prompts_sha256,
        "run_config": run_config,
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

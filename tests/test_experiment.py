"""Experiment identity: what a manifest records so that results can be attributed and compared."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from research_loop.experiment import build_manifest, git_state


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
                   cwd=root, check=True, capture_output=True)


def test_different_uncommitted_trees_get_different_hashes(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    (tmp_path / ".gitignore").write_text(".env\n")
    (tmp_path / "loop.py").write_text("x = 1\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "base")
    clean = git_state(tmp_path)
    assert clean["dirty"] is False and clean["tree_sha256"] is None

    (tmp_path / "loop.py").write_text("x = 2\n")
    first = git_state(tmp_path)["tree_sha256"]
    (tmp_path / "loop.py").write_text("x = 3\n")
    second = git_state(tmp_path)["tree_sha256"]
    (tmp_path / "notes.py").write_text("y = 1\n")  # untracked, not ignored
    third = git_state(tmp_path)["tree_sha256"]
    (tmp_path / ".env").write_text("OPENAI_API_KEY=secret\n")  # ignored: never part of the hash
    assert len({first, second, third}) == 3
    assert git_state(tmp_path)["tree_sha256"] == third
    assert git_state(tmp_path)["commit"] == clean["commit"]


def test_prompt_fingerprint_follows_instructions(monkeypatch: pytest.MonkeyPatch) -> None:
    from research_loop import agents

    before = agents.prompt_fingerprint()
    assert agents.prompt_fingerprint() == before
    monkeypatch.setitem(agents.INSTRUCTIONS, "planner", agents.INSTRUCTIONS["planner"] + " Be brief.")
    assert agents.prompt_fingerprint() != before


def test_manifest_fingerprint_changes_with_dataset_bytes_and_run_config(tmp_path: Path) -> None:
    data = tmp_path / "cases.jsonl"
    suite = tmp_path / "suite.toml"
    suite.write_text('name = "s"\n[[sources]]\nname = "local"\nkind = "jsonl"\npath = "cases.jsonl"\n')

    def manifest(run_config: dict) -> dict:
        return build_manifest(suite, "s", [], ["synthetic"], attachment_mode="normalized", tool_mode="normalized",
                              repository_mode="memory", evaluator_version=1, run_config=run_config)

    data.write_text(json.dumps({"id": "a", "objective": "A"}) + "\n")
    first = manifest({"max_parallel_scouts": 8})
    assert first["schema_version"] == 3
    assert first["benchmark_manifest"]["sources"][0]["dataset_sha256"] == hashlib.sha256(data.read_bytes()).hexdigest()
    assert len(first["prompts_sha256"]) == 64 and first["run_config"] == {"max_parallel_scouts": 8}
    assert "trafilatura" in first["packages"]
    assert manifest({"max_parallel_scouts": 8})["config_fingerprint"] == first["config_fingerprint"]
    assert manifest({"max_parallel_scouts": 2})["config_fingerprint"] != first["config_fingerprint"]
    data.write_text(json.dumps({"id": "a", "objective": "A, edited"}) + "\n")
    assert manifest({"max_parallel_scouts": 8})["config_fingerprint"] != first["config_fingerprint"]

from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
from pathlib import Path

import pytest

from research_loop.benchmarks.browsecomp import BrowseCompAdapter
from research_loop.benchmarks.deepresearch2 import DeepResearchBench2Adapter
from research_loop.benchmarks.gaia import GaiaAdapter
from research_loop.benchmarks.io import download_if_missing
from research_loop.benchmarks.manifest import load_suite
from research_loop.benchmarks.models import BenchmarkKind, BenchmarkSourceSpec


def _encrypt(text: str, password: str) -> str:
    raw = text.encode()
    digest = hashlib.sha256(password.encode()).digest()
    key = digest * (len(raw) // len(digest)) + digest[: len(raw) % len(digest)]
    return base64.b64encode(bytes(a ^ b for a, b in zip(raw, key))).decode()


def test_browsecomp_decrypts_in_memory(tmp_path: Path) -> None:
    path = tmp_path / "browse.csv"
    canary = "local-test-canary"
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["problem", "answer", "canary"])
        writer.writeheader()
        writer.writerow(
            {
                "problem": _encrypt("hard research question", canary),
                "answer": _encrypt("short answer", canary),
                "canary": canary,
            }
        )
    spec = BenchmarkSourceSpec(name="bc", kind=BenchmarkKind.BROWSECOMP, path=str(path))
    cases = BrowseCompAdapter().load(spec, base_dir=tmp_path)
    assert len(cases) == 1
    assert cases[0].objective == "hard research question"
    assert cases[0].expected_answer == "short answer"
    assert cases[0].leakage_sensitive is True


def test_gaia_can_exclude_attachment_cases(tmp_path: Path) -> None:
    path = tmp_path / "metadata.jsonl"
    rows = [
        {"task_id": "plain", "Question": "Q1", "Final answer": "A1", "Level": 2},
        {
            "task_id": "file",
            "Question": "Q2",
            "Final answer": "A2",
            "Level": 3,
            "file_name": "attachment.pdf",
        },
    ]
    path.write_text("\n".join(json.dumps(x) for x in rows), encoding="utf-8")
    spec = BenchmarkSourceSpec(
        name="gaia",
        kind=BenchmarkKind.GAIA,
        path=str(path),
        include_attachments=False,
        levels=[2, 3],
    )
    cases = GaiaAdapter().load(spec, base_dir=tmp_path)
    assert [c.case_id for c in cases] == ["plain"]


def test_drb2_preserves_rubrics_and_blocked_urls(tmp_path: Path) -> None:
    path = tmp_path / "drb2.jsonl"
    row = {
        "id": "task-x",
        "idx": 7,
        "language": "en",
        "prompt": "Write a report.",
        "content": {
            "rubric": {"info_recall": ["fact A"], "analysis": ["connect A to B"]},
            "blocked": {"urls": ["https://blocked.example/paper"]},
        },
        "license": "CC BY 4.0",
    }
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    spec = BenchmarkSourceSpec(name="drb2", kind=BenchmarkKind.DEEPRESEARCH_BENCH_2, path=str(path))
    cases = DeepResearchBench2Adapter().load(spec, base_dir=tmp_path)
    assert cases[0].rubrics["info_recall"] == ["fact A"]
    assert cases[0].blocked_urls == ["https://blocked.example/paper"]
    assert "Do not open" in cases[0].render_objective()


def test_toml_manifest_loads_multiple_sources(tmp_path: Path) -> None:
    custom = tmp_path / "custom.jsonl"
    custom.write_text(json.dumps({"id": "x", "objective": "Research X"}) + "\n", encoding="utf-8")
    manifest = tmp_path / "suite.toml"
    manifest.write_text(
        """
name = "smoke"

[[sources]]
name = "custom"
kind = "jsonl"
path = "custom.jsonl"
limit = 1
""".strip(),
        encoding="utf-8",
    )
    suite, cases = load_suite(manifest)
    assert suite.name == "smoke"
    assert len(cases) == 1
    assert cases[0].benchmark_id == "custom"


def test_benchmark_download_does_not_cache_partial_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class InterruptedResponse(io.BytesIO):
        def __init__(self) -> None:
            super().__init__(b"partial")
            self.reads = 0

        def read(self, size: int = -1) -> bytes:
            self.reads += 1
            if self.reads > 1:
                raise OSError("download interrupted")
            return super().read(size)

    destination = tmp_path / "dataset.jsonl"
    monkeypatch.setattr(
        "research_loop.benchmarks.io.urllib.request.urlopen",
        lambda *_args, **_kwargs: InterruptedResponse(),
    )
    with pytest.raises(OSError, match="download interrupted"):
        download_if_missing("https://example.org/dataset.jsonl", destination)
    assert not destination.exists()
    assert list(tmp_path.iterdir()) == []

    monkeypatch.setattr(
        "research_loop.benchmarks.io.urllib.request.urlopen",
        lambda *_args, **_kwargs: io.BytesIO(b'{"id": "complete"}\n'),
    )
    assert download_if_missing("https://example.org/dataset.jsonl", destination) == destination
    assert destination.read_bytes() == b'{"id": "complete"}\n'

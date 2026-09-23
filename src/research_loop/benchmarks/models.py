from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class BenchmarkKind(StrEnum):
    BROWSECOMP = "browsecomp"
    GAIA = "gaia"
    DEEPRESEARCH_BENCH = "deepresearch_bench"
    DEEPRESEARCH_BENCH_2 = "deepresearch_bench_2"
    FUTURESEARCH_DRB = "futuresearch_drb"
    JSONL = "jsonl"


class BenchmarkOutputMode(StrEnum):
    SHORT_ANSWER = "short_answer"
    REPORT = "report"


class BenchmarkCaseSpec(BaseModel):
    benchmark_id: str
    case_id: str
    objective: str
    output_mode: BenchmarkOutputMode = BenchmarkOutputMode.REPORT
    expected_answer: str | list[str] | None = None
    attachments: list[str] = Field(default_factory=list)
    blocked_urls: list[str] = Field(default_factory=list)
    rubrics: dict[str, list[str]] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def leakage_sensitive(self) -> bool:
        return bool(self.metadata.get("leakage_sensitive"))

    def render_objective(self) -> str:
        parts = [self.objective.strip()]
        if self.blocked_urls:
            parts.append(
                "BENCHMARK CONSTRAINT: Do not open, fetch, quote, or rely on any of these blocked "
                "URLs during research:\n- " + "\n- ".join(self.blocked_urls)
            )
        if self.attachments:
            names = [Path(value).name for value in self.attachments]
            parts.append(
                "BENCHMARK MATERIALS: This task has local attachments available through the attachment "
                "tools. Filenames:\n- " + "\n- ".join(names)
                + "\nDo not guess file contents. Use the attachment tools (or multimodal image input when enabled)."
            )
        if self.output_mode is BenchmarkOutputMode.SHORT_ANSWER:
            parts.append(
                "BENCHMARK OUTPUT CONTRACT: Do the research needed, but make the final report answer "
                "end with a separate line exactly in the form `Exact Answer: <answer>`. Keep the exact "
                "answer succinct."
            )
        return "\n\n".join(parts)


class BenchmarkSourceSpec(BaseModel):
    name: str
    kind: BenchmarkKind
    path: str | None = None
    split: str | None = None
    limit: int | None = Field(default=None, ge=1)
    seed: int = 0
    include_attachments: bool = True
    levels: list[int] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    indices: list[int] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def resolved_path(self, base_dir: Path) -> Path | None:
        if not self.path:
            return None
        p = Path(self.path).expanduser()
        return p if p.is_absolute() else (base_dir / p).resolve()


class BenchmarkSuiteManifest(BaseModel):
    name: str = "research-suite"
    sources: list[BenchmarkSourceSpec]

from __future__ import annotations

from pathlib import Path

from .base import BenchmarkAdapter, deterministic_select
from .io import pick, read_jsonl
from .models import BenchmarkCaseSpec, BenchmarkOutputMode, BenchmarkSourceSpec


class DeepResearchBenchAdapter(BenchmarkAdapter):
    """Adapter for the original USTC DeepResearch Bench prompt JSONL."""

    def load(self, spec: BenchmarkSourceSpec, *, base_dir: Path) -> list[BenchmarkCaseSpec]:
        source = spec.resolved_path(base_dir)
        if source is None:
            raise ValueError("DeepResearch Bench requires a local query JSONL via `path`")
        rows = read_jsonl(source)
        cases: list[BenchmarkCaseSpec] = []
        for idx, row in enumerate(rows):
            language = str(pick(row, "language", "lang") or "")
            if spec.languages and language and language not in spec.languages:
                continue
            prompt = pick(row, "prompt", "query", "task", "objective", "question")
            if prompt is None:
                continue
            answer = pick(row, "answer", "reference_answer", "gold_answer")
            cases.append(
                BenchmarkCaseSpec(
                    benchmark_id=spec.name,
                    case_id=str(pick(row, "id", "task_id", "idx") or idx),
                    objective=str(prompt),
                    expected_answer=str(answer) if answer is not None else None,
                    output_mode=BenchmarkOutputMode.REPORT,
                    metadata={
                        "benchmark_kind": "deepresearch_bench",
                        "language": language or None,
                        **spec.metadata,
                    },
                )
            )
        return deterministic_select(cases, limit=spec.limit, seed=spec.seed, indices=spec.indices)

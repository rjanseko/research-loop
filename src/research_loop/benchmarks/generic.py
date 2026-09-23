from __future__ import annotations

from pathlib import Path

from .base import BenchmarkAdapter, deterministic_select
from .io import pick, read_jsonl
from .models import BenchmarkCaseSpec, BenchmarkOutputMode, BenchmarkSourceSpec


class JsonlAdapter(BenchmarkAdapter):
    def load(self, spec: BenchmarkSourceSpec, *, base_dir: Path) -> list[BenchmarkCaseSpec]:
        source = spec.resolved_path(base_dir)
        if source is None:
            raise ValueError("jsonl benchmark requires `path`")
        cases: list[BenchmarkCaseSpec] = []
        for idx, row in enumerate(read_jsonl(source)):
            prompt = pick(row, "objective", "prompt", "question", "problem", "task")
            if prompt is None:
                continue
            answer = pick(row, "answer", "expected_answer", "reference_answer")
            mode = BenchmarkOutputMode(str(row.get("output_mode") or "report"))
            cases.append(
                BenchmarkCaseSpec(
                    benchmark_id=spec.name,
                    case_id=str(pick(row, "id", "case_id", "task_id") or idx),
                    objective=str(prompt),
                    expected_answer=answer,
                    output_mode=mode,
                    attachments=[str(x) for x in row.get("attachments", [])],
                    blocked_urls=[str(x) for x in row.get("blocked_urls", [])],
                    rubrics=row.get("rubrics") or {},
                    metadata=row.get("metadata") or {},
                )
            )
        return deterministic_select(cases, limit=spec.limit, seed=spec.seed, indices=spec.indices)

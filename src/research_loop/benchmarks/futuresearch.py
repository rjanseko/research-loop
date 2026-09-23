from __future__ import annotations

from pathlib import Path

from .base import BenchmarkAdapter, deterministic_select
from .io import pick, read_jsonl
from .models import BenchmarkCaseSpec, BenchmarkOutputMode, BenchmarkSourceSpec


class FutureSearchDRBAdapter(BenchmarkAdapter):
    """Adapter for a local FutureSearch DRB/RetroSearch task export.

    FutureSearch currently directs prospective benchmark runners to contact them for access.
    This adapter therefore consumes a local JSONL export rather than redistributing tasks or
    pretending to implement RetroSearch. Put any RetroSearch endpoint/token information in your
    runtime integration, not in the suite manifest.
    """

    def load(self, spec: BenchmarkSourceSpec, *, base_dir: Path) -> list[BenchmarkCaseSpec]:
        source = spec.resolved_path(base_dir)
        if source is None:
            raise ValueError(
                "FutureSearch DRB requires an authorized local task export via `path`; "
                "RetroSearch access is not bundled"
            )
        cases: list[BenchmarkCaseSpec] = []
        for idx, row in enumerate(read_jsonl(source)):
            prompt = pick(row, "question", "task", "problem", "objective", "prompt")
            if prompt is None:
                continue
            answer = pick(row, "answer", "reference_answer", "gold_answer")
            cases.append(
                BenchmarkCaseSpec(
                    benchmark_id=spec.name,
                    case_id=str(pick(row, "id", "task_id", "uid") or idx),
                    objective=str(prompt),
                    expected_answer=str(answer) if answer is not None else None,
                    output_mode=BenchmarkOutputMode.REPORT,
                    metadata={
                        "benchmark_kind": "futuresearch_drb",
                        "requires_retrosearch": True,
                        **spec.metadata,
                    },
                )
            )
        return deterministic_select(cases, limit=spec.limit, seed=spec.seed, indices=spec.indices)

from __future__ import annotations

from pathlib import Path

from ..settings import ResearchSettings
from .base import BenchmarkAdapter, deterministic_select
from .io import download_if_missing, read_jsonl
from .models import BenchmarkCaseSpec, BenchmarkOutputMode, BenchmarkSourceSpec

OFFICIAL_TASKS_URL = (
    "https://raw.githubusercontent.com/imlrz/DeepResearch-Bench-II/main/tasks_and_rubrics.jsonl"
)


class DeepResearchBench2Adapter(BenchmarkAdapter):
    def dataset_path(self, spec: BenchmarkSourceSpec, *, base_dir: Path) -> Path:
        return spec.resolved_path(base_dir) or ResearchSettings.from_env().benchmark_cache / "drb2_tasks_and_rubrics.jsonl"

    def load(self, spec: BenchmarkSourceSpec, *, base_dir: Path) -> list[BenchmarkCaseSpec]:
        source = self.dataset_path(spec, base_dir=base_dir)
        if not spec.path:
            download_if_missing(OFFICIAL_TASKS_URL, source)

        cases: list[BenchmarkCaseSpec] = []
        for row in read_jsonl(source):
            language = str(row.get("language") or "")
            if spec.languages and language not in spec.languages:
                continue
            content = row.get("content") if isinstance(row.get("content"), dict) else {}
            rubric = content.get("rubric") if isinstance(content.get("rubric"), dict) else {}
            rubrics = {
                str(k): [str(x) for x in v]
                for k, v in rubric.items()
                if isinstance(v, list)
            }
            blocked = content.get("blocked") if isinstance(content.get("blocked"), dict) else {}
            blocked_urls = [str(u) for u in blocked.get("urls", []) if isinstance(u, str)]
            idx = int(row.get("idx") or 0)
            cases.append(
                BenchmarkCaseSpec(
                    benchmark_id=spec.name,
                    case_id=str(row.get("id") or idx),
                    objective=str(row.get("prompt") or content.get("task") or ""),
                    output_mode=BenchmarkOutputMode.REPORT,
                    blocked_urls=blocked_urls,
                    rubrics=rubrics,
                    metadata={
                        "benchmark_kind": "deepresearch_bench_2",
                        "idx": idx,
                        "language": language,
                        "theme": row.get("theme"),
                        "description": row.get("description"),
                        "license": row.get("license"),
                        "official_evaluator": "DeepResearch-Bench-II",
                    },
                )
            )
        return deterministic_select(cases, limit=spec.limit, seed=spec.seed, indices=spec.indices)

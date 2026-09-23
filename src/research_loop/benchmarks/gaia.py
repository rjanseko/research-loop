from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .base import BenchmarkAdapter, deterministic_select
from .io import read_jsonl
from .models import BenchmarkCaseSpec, BenchmarkOutputMode, BenchmarkSourceSpec


class GaiaAdapter(BenchmarkAdapter):
    """Loads a local GAIA snapshot (JSONL or Parquet).

    GAIA is gated upstream, so this adapter intentionally does not scrape or redistribute it.
    Download the authorized snapshot yourself, then point the manifest at the local file/folder.
    """

    def _rows(self, source: Path, split: str | None) -> list[dict[str, Any]]:
        if source.is_dir():
            candidates = []
            if split:
                candidates.extend(
                    [source / f"metadata.{split}.parquet", source / split / "metadata.parquet"]
                )
            candidates.extend([source / "metadata.parquet", source / "metadata.jsonl"])
            source = next((p for p in candidates if p.exists()), source)

        if source.suffix == ".jsonl":
            return read_jsonl(source)
        if source.suffix == ".json":
            raw = json.loads(source.read_text(encoding="utf-8"))
            return raw if isinstance(raw, list) else list(raw.values())
        if source.suffix == ".parquet":
            try:
                import pandas as pd
            except ImportError as exc:  # pragma: no cover - optional dependency
                raise RuntimeError("GAIA parquet loading requires `pip install pandas pyarrow`") from exc
            return pd.read_parquet(source).to_dict(orient="records")
        raise ValueError(f"unsupported GAIA source: {source}")

    def load(self, spec: BenchmarkSourceSpec, *, base_dir: Path) -> list[BenchmarkCaseSpec]:
        source = spec.resolved_path(base_dir)
        if source is None:
            raise ValueError("GAIA is gated; set `path` to your authorized local snapshot")

        cases: list[BenchmarkCaseSpec] = []
        for idx, row in enumerate(self._rows(source, spec.split)):
            level = int(row.get("Level") or row.get("level") or 0)
            if spec.levels and level not in spec.levels:
                continue
            attachment = row.get("file_path") or row.get("file_name")
            attachments: list[str] = []
            if attachment:
                if not spec.include_attachments:
                    continue
                p = Path(str(attachment))
                if not p.is_absolute():
                    anchor = source if source.is_dir() else source.parent
                    p = (anchor / p).resolve()
                attachments.append(str(p))
            answer = row.get("Final answer") or row.get("final_answer") or row.get("answer")
            cases.append(
                BenchmarkCaseSpec(
                    benchmark_id=spec.name,
                    case_id=str(row.get("task_id") or row.get("id") or idx),
                    objective=str(row.get("Question") or row.get("question") or ""),
                    expected_answer=str(answer) if answer is not None else None,
                    output_mode=BenchmarkOutputMode.SHORT_ANSWER,
                    attachments=attachments,
                    metadata={"benchmark_kind": "gaia", "level": level},
                )
            )
        return deterministic_select(cases, limit=spec.limit, seed=spec.seed, indices=spec.indices)

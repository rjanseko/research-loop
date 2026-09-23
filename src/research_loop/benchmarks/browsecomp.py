from __future__ import annotations

import base64
import hashlib
from pathlib import Path

from ..settings import ResearchSettings
from .base import BenchmarkAdapter, deterministic_select
from .io import download_if_missing, read_csv
from .models import BenchmarkCaseSpec, BenchmarkOutputMode, BenchmarkSourceSpec

OFFICIAL_URL = "https://openaipublic.blob.core.windows.net/simple-evals/browse_comp_test_set.csv"


def _derive_key(password: str, length: int) -> bytes:
    key = hashlib.sha256(password.encode()).digest()
    return key * (length // len(key)) + key[: length % len(key)]


def decrypt(ciphertext_b64: str, password: str) -> str:
    encrypted = base64.b64decode(ciphertext_b64)
    key = _derive_key(password, len(encrypted))
    return bytes(a ^ b for a, b in zip(encrypted, key)).decode()


class BrowseCompAdapter(BenchmarkAdapter):
    """Loads BrowseComp without ever writing plaintext questions/answers to disk.

    The upstream dataset is deliberately distributed encrypted to reduce benchmark
    contamination. We cache only the encrypted CSV and decrypt individual rows in memory.
    """

    def dataset_path(self, spec: BenchmarkSourceSpec, *, base_dir: Path) -> Path:
        return spec.resolved_path(base_dir) or ResearchSettings.from_env().benchmark_cache / "browse_comp_test_set.csv"

    def load(self, spec: BenchmarkSourceSpec, *, base_dir: Path) -> list[BenchmarkCaseSpec]:
        source = self.dataset_path(spec, base_dir=base_dir)
        if not spec.path:
            download_if_missing(OFFICIAL_URL, source)

        rows = read_csv(source)
        cases: list[BenchmarkCaseSpec] = []
        for idx, row in enumerate(rows):
            canary = row.get("canary", "")
            if not canary:
                raise ValueError("BrowseComp row missing canary")
            cases.append(
                BenchmarkCaseSpec(
                    benchmark_id=spec.name,
                    case_id=str(row.get("id") or row.get("uid") or idx),
                    objective=decrypt(row.get("problem", ""), canary),
                    expected_answer=decrypt(row.get("answer", ""), canary),
                    output_mode=BenchmarkOutputMode.SHORT_ANSWER,
                    metadata={
                        "benchmark_kind": "browsecomp",
                        "leakage_sensitive": True,
                        "row_index": idx,
                    },
                )
            )
        return deterministic_select(cases, limit=spec.limit, seed=spec.seed, indices=spec.indices)

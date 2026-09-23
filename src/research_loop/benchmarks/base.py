from __future__ import annotations

import random
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Iterable

from .models import BenchmarkCaseSpec, BenchmarkSourceSpec


class BenchmarkAdapter(ABC):
    @abstractmethod
    def load(self, spec: BenchmarkSourceSpec, *, base_dir: Path) -> list[BenchmarkCaseSpec]:
        raise NotImplementedError


def deterministic_select(
    cases: Iterable[BenchmarkCaseSpec],
    *,
    limit: int | None,
    seed: int,
    indices: list[int] | None = None,
) -> list[BenchmarkCaseSpec]:
    items = list(cases)
    if indices:
        wanted = set(indices)
        items = [item for i, item in enumerate(items) if i in wanted]
    if limit is not None and len(items) > limit:
        rng = random.Random(seed)
        items = rng.sample(items, limit)
    return items

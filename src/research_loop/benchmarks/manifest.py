from __future__ import annotations

import tomllib
from pathlib import Path

from .base import BenchmarkAdapter
from .browsecomp import BrowseCompAdapter
from .deepresearch import DeepResearchBenchAdapter
from .deepresearch2 import DeepResearchBench2Adapter
from .futuresearch import FutureSearchDRBAdapter
from .gaia import GaiaAdapter
from .generic import JsonlAdapter
from .models import BenchmarkCaseSpec, BenchmarkKind, BenchmarkSuiteManifest

ADAPTERS: dict[BenchmarkKind, BenchmarkAdapter] = {
    BenchmarkKind.BROWSECOMP: BrowseCompAdapter(),
    BenchmarkKind.GAIA: GaiaAdapter(),
    BenchmarkKind.DEEPRESEARCH_BENCH: DeepResearchBenchAdapter(),
    BenchmarkKind.DEEPRESEARCH_BENCH_2: DeepResearchBench2Adapter(),
    BenchmarkKind.FUTURESEARCH_DRB: FutureSearchDRBAdapter(),
    BenchmarkKind.JSONL: JsonlAdapter(),
}


def load_manifest(path: Path) -> BenchmarkSuiteManifest:
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    # TOML uses [[sources]], which maps directly to the pydantic model.
    return BenchmarkSuiteManifest.model_validate(raw)


def load_suite(path: Path) -> tuple[BenchmarkSuiteManifest, list[BenchmarkCaseSpec]]:
    manifest = load_manifest(path)
    cases: list[BenchmarkCaseSpec] = []
    for source in manifest.sources:
        adapter = ADAPTERS[source.kind]
        cases.extend(adapter.load(source, base_dir=path.parent))
    return manifest, cases

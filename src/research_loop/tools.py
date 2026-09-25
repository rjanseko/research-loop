from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Any

from pydantic_ai.capabilities import WebFetch, WebSearch

if TYPE_CHECKING:
    from .acquisition import AcquisitionCache


class ResearchToolMode(StrEnum):
    """How web tools are supplied to research workers.

    ADAPTIVE uses provider-native tools where available and falls back locally.
    NORMALIZED disables native tools so every model gets the same local search/fetch
    implementation, which is preferable for comparative model evals.
    """

    ADAPTIVE = "adaptive"
    NORMALIZED = "normalized"


def build_research_capabilities(mode: ResearchToolMode, *, search_cache: AcquisitionCache | None = None) -> list[Any]:
    from .web import resilient_duckduckgo_tool

    if mode is ResearchToolMode.NORMALIZED:
        return [WebSearch(native=False, local=resilient_duckduckgo_tool(cache=search_cache))]

    return [
        WebSearch(local=resilient_duckduckgo_tool(cache=search_cache)),
        WebFetch(local=True),
    ]

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic_ai.capabilities import WebFetch, WebSearch


class ResearchToolMode(StrEnum):
    """How web tools are supplied to research workers.

    ADAPTIVE uses provider-native tools where available and falls back locally.
    NORMALIZED disables native tools so every model gets the same local search/fetch
    implementation, which is preferable for comparative model evals.
    """

    ADAPTIVE = "adaptive"
    NORMALIZED = "normalized"


def build_research_capabilities(mode: ResearchToolMode) -> list[Any]:
    if mode is ResearchToolMode.NORMALIZED:
        return [
            WebSearch(native=False, local="duckduckgo"),
            WebFetch(native=False, local=True),
        ]

    return [
        WebSearch(local="duckduckgo"),
        WebFetch(local=True),
    ]

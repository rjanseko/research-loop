"""Evidence-first, role-routed research loop.

Imports are lazy so the deterministic benchmark/attachment utilities can be used
without importing provider SDK integrations at package import time.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .attachments import AttachmentMode
    from .orchestrator import (
        LegacyResearchLoop,
        ResearchConfig,
        ResearchLoop,
        ResearchOutcome,
    )
    from .policy import ModelPolicy, ModelRoute
    from .schemas import ResearchConstraints
    from .tools import ResearchToolMode

__all__ = [
    "POLICY_PRESETS",
    "AttachmentMode",
    "LegacyResearchLoop",
    "ModelPolicy",
    "ModelRoute",
    "ResearchConfig",
    "ResearchConstraints",
    "ResearchLoop",
    "ResearchOutcome",
    "ResearchToolMode",
    "get_policy",
]


def __getattr__(name: str) -> Any:
    if name == "AttachmentMode":
        from .attachments import AttachmentMode
        return AttachmentMode
    if name in {"LegacyResearchLoop", "ResearchConfig", "ResearchLoop", "ResearchOutcome"}:
        from . import orchestrator
        return getattr(orchestrator, name)
    if name in {"ModelPolicy", "ModelRoute", "POLICY_PRESETS", "get_policy"}:
        from . import policy
        return getattr(policy, name)
    if name == "ResearchConstraints":
        from .schemas import ResearchConstraints
        return ResearchConstraints
    if name == "ResearchToolMode":
        from .tools import ResearchToolMode
        return ResearchToolMode
    raise AttributeError(name)

from __future__ import annotations

from contextlib import AbstractContextManager, nullcontext
from typing import Any
from uuid import UUID

from .settings import ResearchSettings

_configured = False
_instance: Any = None


def configure_logfire(settings: ResearchSettings) -> bool:
    """Opt in to content-free PydanticAI traces for this process."""
    global _configured
    if not settings.logfire_enabled:
        return False
    if _configured:
        return True
    try:
        import logfire
    except ImportError as exc:
        raise RuntimeError('Install the observability extra: pip install -e ".[observability]"') from exc

    instance = logfire.configure(
        local=True,
        service_name="research-loop",
        send_to_logfire="if-token-present",
        console=False,
    )
    # Keep Pydantic Evals on the unconfigured global tracer: its case spans contain protected inputs.
    instance.instrument_pydantic_ai(
        include_content=False,
        include_binary_content=False,
        include_model_request_parameters=False,
    )
    global _instance
    _instance = instance
    _configured = True
    return True


def job_span(job_id: UUID, policy_name: str) -> AbstractContextManager[Any]:
    """One span per research job, so its agent calls share a trace; a no-op without Logfire.

    Attributes identify the job only; the objective and constraints stay out of traces.
    """
    if _instance is None:
        return nullcontext()
    return _instance.span("research job {job_id}", job_id=str(job_id), policy=policy_name)

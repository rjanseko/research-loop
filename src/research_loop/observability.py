from __future__ import annotations

from .settings import ResearchSettings


_configured = False


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
    _configured = True
    return True

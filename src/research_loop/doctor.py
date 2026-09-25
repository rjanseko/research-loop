"""`research doctor`: whether the configured models, prices, database, and network can run Scout."""
from __future__ import annotations

import asyncio
import socket
from urllib.parse import urlparse

from pydantic import BaseModel
from pydantic_ai.exceptions import ModelHTTPError

from .config import PROVIDER_KEYS, Settings, model_provider
from .prices import override_for, price_per_million

# Hosts a Scout run reaches besides the model providers.
SOURCE_HOSTS = ("duckduckgo.com", "api.openalex.org", "export.arxiv.org", "api.crossref.org")


class _Ok(BaseModel):
    ok: bool


def _line(status: str, name: str, detail: str) -> str:
    return f"{status:4}  {name}: {detail}"


def _smoke_failure(exc: Exception) -> str:
    """What a failed smoke call means, without echoing the provider's response body."""
    if isinstance(exc, ModelHTTPError):
        body = str(exc.body).lower()
        if exc.status_code == 402 or any(marker in body for marker in (
                "insufficient", "balance", "credit", "quota", "'code': 1113", '"code": 1113')):
            return "the provider says the balance is exhausted; add credits"
        return {401: "HTTP 401: check the API key", 403: "HTTP 403: check account and model access",
                404: "HTTP 404: check the model ID", 429: "HTTP 429: a rate limit or an empty balance"}.get(
            exc.status_code, f"HTTP {exc.status_code}")
    return f"{type(exc).__name__}: check the model ID and access"


async def _smoke(model_id: str, role: str, settings: Settings) -> str:
    from pydantic_ai import Agent, UsageLimits

    from .models import build_model

    agent = Agent(build_model(model_id, role, settings), output_type=_Ok)  # type: ignore[arg-type]
    called = False

    @agent.tool_plain
    def ping() -> str:
        """Return a fixed value for a capability check."""
        nonlocal called
        called = True
        return "pong"

    result = await agent.run("Call ping once, then return ok=true.",
                             usage_limits=UsageLimits(request_limit=3, cost_limit=0.05))
    if not (result.output.ok and called):
        raise RuntimeError("the model did not call the tool and return the output")
    return f"answered with a tool call for ${result.usage.cost or 0:.4f}"


async def run_doctor(settings: Settings, *, smoke: bool = False) -> int:
    failed = False

    def report(status: str, name: str, detail: str) -> None:
        nonlocal failed
        failed |= status == "FAIL"
        print(_line(status, name, detail))

    roles = {"planner": settings.models.planner, "scout": settings.models.scout,
             "synthesizer": settings.models.synthesizer}
    if settings.models.fallback:
        roles["fallback"] = settings.models.fallback
    problems = settings.route_problems()
    for problem in problems:
        report("FAIL", "models", problem)
    for role, model_id in roles.items():
        price = price_per_million(model_id)
        if price is None:
            report("FAIL", f"price {model_id}", "no price, so its cost cannot be capped; add it to prices.toml")
        else:
            corrected = f", corrected in prices.toml ({override_for(model_id).checked})" if override_for(model_id) else ""
            report("OK", f"price {model_id}", f"${price[0]:.2f} in, ${price[1]:.2f} out per million tokens ({role}{corrected})")

    if settings.database_dsn:
        from .db import pending_migrations
        try:
            pending = await asyncio.to_thread(pending_migrations, settings.database_dsn, connect_timeout=3)
            report("WARN" if pending else "OK", "database",
                   f"pending migrations: {', '.join(pending)}; run `research db migrate`" if pending else "migrated")
        except Exception as exc:  # noqa: BLE001 - every check reports its own failure instead of raising
            report("FAIL", "database", f"cannot connect ({type(exc).__name__}); check DATABASE_URL and the server")
    else:
        report("WARN", "database", "DATABASE_URL is not set, so runs are not stored")

    if not settings.logfire:
        report("WARN", "logfire", "tracing is off (RESEARCH_LOGFIRE=false)")
    elif settings.logfire_token:
        report("OK", "logfire", "traces are sent")
    else:
        report("WARN", "logfire", "no LOGFIRE_TOKEN, so traces stay in the process")

    try:
        settings.cache_dir.mkdir(parents=True, exist_ok=True)
        report("OK", "cache", f"{settings.cache_dir} ({settings.cache_mode})")
    except OSError as exc:
        report("FAIL", "cache", f"{settings.cache_dir} is not writable ({type(exc).__name__})")

    hosts = [*SOURCE_HOSTS, *sorted({_provider_host(m) for m in roles.values() if _provider_host(m)})]
    unresolved = [host for host in hosts if not await _resolves(host)]
    report("FAIL" if unresolved else "OK", "network",
           f"cannot resolve {', '.join(unresolved)}" if unresolved else f"{len(hosts)} hosts resolve")

    if smoke and not problems:
        tried: set[str] = set()
        for role, model_id in roles.items():
            if model_id in tried:
                continue
            tried.add(model_id)
            try:
                report("OK", f"smoke {model_id}",
                       await _smoke(model_id, "planner" if role == "fallback" else role, settings))
            except Exception as exc:  # noqa: BLE001 - every check reports its own failure instead of raising
                report("FAIL", f"smoke {model_id}", _smoke_failure(exc))
    return 1 if failed else 0


_PROVIDER_HOSTS = {"openai": "api.openai.com", "anthropic": "api.anthropic.com", "zai": "api.z.ai",
                   "google": "generativelanguage.googleapis.com"}


def _provider_host(model_id: str) -> str | None:
    provider = model_provider(model_id)
    return _PROVIDER_HOSTS.get(provider) if provider in PROVIDER_KEYS else None


async def _resolves(host: str) -> bool:
    try:
        await asyncio.to_thread(socket.getaddrinfo, urlparse(f"https://{host}").hostname, 443)
        return True
    except OSError:
        return False

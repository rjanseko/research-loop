from __future__ import annotations

import argparse
import asyncio
import importlib.util
import io
import sys
import tempfile
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel
from pydantic_ai.exceptions import ModelHTTPError

from .acquisition import AcquisitionCache, shared_ssl_context
from .citations import SEMANTIC_SCHOLAR_API
from .db import pending_migrations
from .graph import get_research_graph
from .observability import configure_logfire
from .policy import ModelRoute, get_policy
from .schemas import ResearchRole
from .scholar import SCHOLAR_HOSTS, ScholarClient, build_scholar_toolset
from .settings import PROVIDER_KEY_ENV, ResearchSettings, model_provider
from .tools import ResearchToolMode, build_research_capabilities
from .web import WebAcquisition, build_web_toolset


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str


class SmokeOutput(BaseModel):
    ok: bool


def _web_probe() -> None:
    request = urllib.request.Request("https://pydantic.dev/", method="HEAD")
    with urllib.request.urlopen(request, timeout=3):
        pass


# The API host each model provider is called at.
PROVIDER_HOSTS = {
    "anthropic": "https://api.anthropic.com",
    "openai": "https://api.openai.com",
    "google": "https://generativelanguage.googleapis.com",
    "xai": "https://api.x.ai",
    "zai": "https://api.z.ai",
    "openrouter": "https://openrouter.ai",
}
# Engines the web search tool (ddgs with its "auto" backend) tries in turn.
SEARCH_HOSTS = (
    "https://en.wikipedia.org", "https://html.duckduckgo.com", "https://search.brave.com",
    "https://www.google.com", "https://www.mojeek.com", "https://search.yahoo.com",
)
# Ordinary public sites. web_fetch follows links to any site, so a network that reaches the APIs
# but none of these allows only listed domains, and fetching evidence will fail.
GENERAL_WEB_HOSTS = ("https://www.bbc.com", "https://www.who.int", "https://www.nature.com")


def _network_probe(urls: list[str]) -> dict[str, str | None]:
    """Why each URL could not be reached, or None when its server answered with any HTTP status."""
    async def probe(client: httpx.AsyncClient, url: str) -> tuple[str, str | None]:
        try:
            await client.head(url)
            return url, None
        except httpx.ProxyError:
            return url, "refused by the proxy"
        except httpx.TransportError as exc:
            return url, type(exc).__name__

    async def probe_all() -> dict[str, str | None]:
        # Like the research tools' clients, this uses HTTPS_PROXY when one is set.
        async with httpx.AsyncClient(timeout=8, follow_redirects=False, verify=shared_ssl_context()) as client:
            return dict(await asyncio.gather(*(probe(client, url) for url in urls)))

    return asyncio.run(probe_all())


def _host(url: str) -> str:
    return urllib.parse.urlsplit(url).hostname or url


def _reach_check(name: str, what: str, urls: Iterable[str], failures: dict[str, str | None], consequence: str) -> Check:
    urls = list(urls)
    unreached = [f"{_host(url)} ({failures[url]})" for url in urls if failures[url]]
    if not unreached:
        return Check(name, "PASS", f"All {len(urls)} {what} reachable")
    if len(unreached) == len(urls):
        return Check(name, "FAIL", f"None of {len(urls)} {what} reachable ({failures[urls[0]]}); {consequence}")
    return Check(name, "WARN", f"Unreachable: {', '.join(unreached)}")


def _model_price(model: str) -> tuple[Decimal, Decimal] | None:
    """USD per million input and output tokens, from the genai-prices data PydanticAI prices calls with.

    Priced at 100k tokens and scaled, so a price tier for long prompts does not set the rate.
    None when the data has no price for the model: its calls then have no cost_usd, and cost caps
    cannot be enforced for them.
    """
    from genai_prices import calc_price
    from pydantic_ai.usage import RequestUsage

    provider, _, name = model.partition(":")
    try:
        per_million = [calc_price(usage, name, provider_id=provider).total_price * 10
                       for usage in (RequestUsage(input_tokens=100_000), RequestUsage(output_tokens=100_000))]
    except LookupError:
        return None
    return per_million[0], per_million[1]


def _update_prices() -> None:
    """Fetch the latest genai-prices data; this process then prices every call with it."""
    from genai_prices import UpdatePrices

    with UpdatePrices() as updater:
        if not updater.wait(timeout=30):
            raise TimeoutError("no price data fetched within 30 seconds")


def _price_checks(
    routes: list[tuple[str, ModelRoute, bool, bool]],
    *,
    update: bool,
    price_lookup: Callable[[str], tuple[Decimal, Decimal] | None],
    price_updater: Callable[[], None],
) -> list[Check]:
    """Each distinct model's price per million tokens, and the roles that use it."""
    from importlib.metadata import version

    bundled = f"prices bundled with genai-prices {version('genai-prices')}"
    checks = []
    if update:
        try:
            price_updater()
            checks.append(Check("prices", "PASS", "Latest genai-prices data fetched; prices below use it"))
        except Exception as exc:
            checks.append(Check("prices", "WARN", f"Could not fetch latest prices ({type(exc).__name__}); using {bundled}"))
    else:
        checks.append(Check("prices", "PASS", f"Using {bundled}; --update-prices fetches the latest"))
    roles: dict[str, list[str]] = {}
    for label, route, _, _ in routes:
        roles.setdefault(route.model, []).append(label)
    for model, labels in roles.items():
        price = price_lookup(model)
        used_by = ", ".join(labels)
        if price is None:
            checks.append(Check(f"price:{model}", "WARN",
                                f"No price data ({used_by}); cost_usd is unknown and cost caps cannot be enforced"))
        else:
            checks.append(Check(f"price:{model}", "PASS",
                                f"${price[0]:.2f} in / ${price[1]:.2f} out per million tokens ({used_by})"))
    return checks


def _writable_probe(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path):
        pass


def _database_probe(dsn: str) -> list[str]:
    return pending_migrations(dsn, connect_timeout=3)


def _model_profile(model_id: str) -> dict[str, Any]:
    from pydantic_ai.models import infer_model

    return dict(infer_model(model_id).profile)


async def _smoke_model(route: ModelRoute, *, tools: bool, image: bool) -> bool:
    """Run a bounded live call; return whether PydanticAI could price it."""
    from pydantic_ai import Agent, BinaryContent, UsageLimits

    agent = Agent(output_type=SmokeOutput)
    called = False
    if tools:
        @agent.tool_plain
        def diagnose_ping() -> str:
            """Return a fixed value for a capability check."""
            nonlocal called
            called = True
            return "pong"

    prompt: Any = "Call diagnose_ping once, then return ok=true." if tools else "Return ok=true."
    if image:
        from PIL import Image

        buffer = io.BytesIO()
        Image.new("RGB", (2, 2), "red").save(buffer, format="PNG")
        prompt = [prompt, BinaryContent(data=buffer.getvalue(), media_type="image/png")]
    result = await agent.run(
        prompt,
        model=route.model,
        model_settings=route.model_settings(),
        usage_limits=UsageLimits(request_limit=2, total_tokens_limit=3000, cost_limit=0.10),
    )
    if not result.output.ok or (tools and not called):
        raise RuntimeError("smoke result did not confirm required output or tool call")
    return result.usage.cost is not None


def _smoke_failure_detail(exc: Exception) -> str:
    """Summarize provider failures without exposing untrusted response bodies."""
    if isinstance(exc, ModelHTTPError):
        body = str(exc.body).lower()
        if any(marker in body for marker in (
            "credit_balance_exhausted",
            "insufficient_quota",
            "insufficient balance",
            "credit balance is too low",
            "no credits remaining",
            "'code': 1113",
            '"code": 1113',
        )) or exc.status_code == 402:
            return "Provider credit balance exhausted; add credits before smoke"
        if exc.status_code == 401:
            return "Provider returned HTTP 401; check API key"
        if exc.status_code == 403:
            return "Provider returned HTTP 403; check account and model access"
        if exc.status_code == 404:
            return "Provider returned HTTP 404; check model ID and account access"
        if exc.status_code == 429:
            return "Provider returned HTTP 429; check rate limit or balance"
        return f"Provider returned HTTP {exc.status_code}; check account access and model capabilities"
    if isinstance(exc, ImportError) and ("xai_sdk" in str(exc) or "xai-sdk" in str(exc)):
        return "Missing xAI SDK; install .[all]"
    return f"Live smoke failed ({type(exc).__name__}); verify model ID, access, and capabilities"


def _routes(policy_name: str, multimodal: bool, model_overrides: dict[str, str]) -> list[tuple[str, ModelRoute, bool, bool]]:
    policy = get_policy(policy_name, model_overrides=model_overrides)
    routes = [
        (role.value, route, role in {ResearchRole.SCOUT, ResearchRole.DEEP_DIVE}, False)
        for role, route in policy.routes.items()
    ]
    if policy.cheap_scout:
        routes.append(("cheap_scout", policy.cheap_scout, True, False))
    if policy.alternate_deep_dive:
        routes.append(("alternate_deep_dive", policy.alternate_deep_dive, True, False))
    if multimodal and policy.multimodal_scout:
        routes.append(("multimodal_scout", policy.multimodal_scout, True, True))
    return routes


def run_diagnose(
    settings: ResearchSettings,
    *,
    policy_name: str = "quality",
    attachments: bool = False,
    multimodal: bool = False,
    smoke: bool = False,
    scholar_live: bool = False,
    network: bool = False,
    prices: bool = False,
    update_prices: bool = False,
    price_lookup: Callable[[str], tuple[Decimal, Decimal] | None] = _model_price,
    price_updater: Callable[[], None] = _update_prices,
    network_probe: Callable[[list[str]], dict[str, str | None]] = _network_probe,
    web_probe: Callable[[], None] = _web_probe,
    writable_probe: Callable[[Path], None] = _writable_probe,
    database_probe: Callable[[str], list[str]] = _database_probe,
    profile_probe: Callable[[str], dict[str, Any]] = _model_profile,
    smoke_probe: Callable[..., Any] = _smoke_model,
) -> list[Check]:
    checks: list[Check] = []
    python_ok = sys.version_info >= (3, 12)
    checks.append(Check("runtime", "PASS" if python_ok else "FAIL", "Python 3.12+" if python_ok else "Install Python 3.12+"))
    modules = ["pydantic_ai", "pydantic_graph", "pydantic_evals", "httpx", "trafilatura", "bs4"]
    if settings.database_dsn:
        modules.append("psycopg")
    if settings.provider_enabled("xai"):
        modules.append("xai_sdk")
    if attachments or multimodal:
        modules.extend(["pypdf", "docx", "openpyxl", "bs4", "PIL"])
    missing = [name for name in modules if importlib.util.find_spec(name) is None]
    checks.append(Check("dependencies", "FAIL" if missing else "PASS", f"Missing {', '.join(missing)}; install .[all]" if missing else "Required modules importable"))
    try:
        get_research_graph()
        checks.append(Check("graph", "PASS", "research-graph-v1 constructs"))
    except Exception as exc:
        checks.append(Check("graph", "FAIL", f"Graph construction failed ({type(exc).__name__})"))
    try:
        capabilities = build_research_capabilities(ResearchToolMode.NORMALIZED)
        build_web_toolset(WebAcquisition(cache_root=settings.benchmark_cache / "web", cache_mode="off"))
        if len(capabilities) != 1:
            raise RuntimeError("expected normalized search capability")
        checks.append(Check("web_tools", "PASS", "Normalized search and fetch available"))
    except Exception as exc:
        checks.append(Check("web_tools", "FAIL", f"Normalized web tools unavailable ({type(exc).__name__})"))
    try:
        build_scholar_toolset(ScholarClient(
            cache=AcquisitionCache(settings.benchmark_cache / "scholarly", "off"),
        ))
        checks.append(Check("scholar_tools", "PASS", "Five provider-neutral scholarly tools construct"))
    except Exception as exc:
        checks.append(Check("scholar_tools", "FAIL", f"Scholarly toolset unavailable ({type(exc).__name__})"))
    if scholar_live:
        async def probe_scholarly() -> list[Check]:
            client = ScholarClient(
                cache=AcquisitionCache(settings.benchmark_cache / "scholarly", "off"),
                api_key=settings.openalex_api_key.get_secret_value() if settings.openalex_api_key else None,
                contact_email=settings.crossref_mailto,
            )
            probes = {
                # Search, not a plain listing: OpenAlex rate-limits anonymous search separately.
                "openalex": ("/works", {"search": "software engineering agent", "per_page": 1}, False),
                "crossref": ("/works", {"rows": 0}, False),
                "arxiv": ("/api/query", {"id_list": "2601.01234", "max_results": 1}, True),
                "acl": ("/2024.acl-long.1.bib", None, True),
                "opencitations": ("/index/v2/citations/doi:10.1038/nphys1170", None, False),
            }
            outcome = []
            for provider, (path, params, as_text) in probes.items():
                try:
                    await client._request(provider, path, params, text=as_text)
                    outcome.append(Check(f"scholar:{provider}", "PASS", "Public metadata endpoint responded"))
                except Exception as exc:
                    detail = f"Endpoint unavailable ({type(exc).__name__})"
                    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429:
                        detail = "Rate-limited (HTTP 429)"
                        if provider == "openalex" and not settings.openalex_api_key:
                            detail += "; set OPENALEX_API_KEY for search access"
                    outcome.append(Check(f"scholar:{provider}", "WARN", detail))
            return outcome
        checks.extend(asyncio.run(probe_scholarly()))
    try:
        web_probe()
        checks.append(Check("web", "PASS", "Outbound HTTPS reachable"))
    except Exception:
        checks.append(Check("web", "WARN", "Outbound HTTPS unavailable; check network/proxy settings"))
    for name, path in (("cache", settings.benchmark_cache), ("output", settings.benchmark_output)):
        try:
            writable_probe(path)
            checks.append(Check(name, "PASS", "Directory writable"))
        except OSError:
            checks.append(Check(name, "FAIL", "Directory not writable; check permissions or change its environment setting"))
    if settings.database_dsn:
        try:
            pending = database_probe(settings.database_dsn)
            checks.append(Check("database", "PASS", "Connection succeeded"))
            checks.append(Check("migrations", "WARN" if pending else "PASS", f"Run research-db migrate ({len(pending)} pending/changed)" if pending else "All SQL migrations applied"))
        except Exception as exc:
            checks.append(Check("database", "FAIL", f"Connection failed ({type(exc).__name__}); check DATABASE_URL and server status"))
    else:
        checks.append(Check("database", "SKIP", "Set DATABASE_URL to check Postgres"))

    for provider, key_name in PROVIDER_KEY_ENV.items():
        if settings.provider_enabled(provider) and settings.has_credential(provider):
            checks.append(Check(f"provider:{provider}", "PASS", "Enabled; credential present"))
        elif settings.provider_enabled(provider):
            checks.append(Check(f"provider:{provider}", "WARN", f"Enabled without credential; set {key_name}"))
        else:
            checks.append(Check(f"provider:{provider}", "SKIP", f"Disabled; set {key_name} to enable"))

    routes = _routes(policy_name, multimodal, settings.model_overrides)
    smoke_requirements: dict[str, tuple[bool, bool]] = {}
    for _, route, needs_tools, needs_image in routes:
        prior_tools, prior_image = smoke_requirements.get(route.model, (False, False))
        smoke_requirements[route.model] = (prior_tools or needs_tools, prior_image or needs_image)
    smoke_results: dict[str, str | None] = {}
    unpriced: set[str] = set()
    for label, route, needs_tools, needs_image in routes:
        name = f"model:{label}"
        # from_env refuses a bad override; this covers settings and policies built another way.
        provider = model_provider(route.model)
        if provider is None:
            checks.append(Check(name, "FAIL", "Invalid provider:model ID; set a valid RESEARCH_*_MODEL override"))
            continue
        if not settings.provider_enabled(provider) or not settings.has_credential(provider):
            checks.append(Check(name, "SKIP", f"{provider} unavailable; configure {PROVIDER_KEY_ENV[provider]} and enable provider"))
            continue
        try:
            profile = profile_probe(route.model)
        except Exception as exc:
            checks.append(Check(name, "FAIL", f"Model configuration rejected ({type(exc).__name__}); check model ID"))
            continue
        structured = any(profile.get(key) for key in ("supports_tools", "supports_json_schema_output", "supports_json_object_output"))
        if not structured or (needs_tools and not profile.get("supports_tools")):
            checks.append(Check(name, "FAIL", "Model profile lacks structured output or required tool calling"))
            continue
        if smoke:
            if route.model not in smoke_results:
                required_tools, required_image = smoke_requirements[route.model]
                try:
                    priced = asyncio.run(smoke_probe(route, tools=required_tools, image=required_image))
                    smoke_results[route.model] = None
                    if priced is False:
                        unpriced.add(route.model)
                except Exception as exc:
                    smoke_results[route.model] = _smoke_failure_detail(exc)
            failure = smoke_results[route.model]
            if failure:
                checks.append(Check(name, "FAIL", failure))
            elif route.model in unpriced:
                checks.append(Check(name, "WARN", "Live smoke passed, but no pricing data; cost limits cannot be enforced"))
            else:
                checks.append(Check(name, "PASS", "Structured output and requested capabilities passed live smoke"))
        else:
            detail = "Profile supports required output/tools; run --smoke to verify model access"
            if needs_image:
                detail = "Image input needs a live check; run --smoke"
            checks.append(Check(name, "WARN", detail))
    if network:
        checks.extend(_network_checks(settings, routes, network_probe))
    if prices or update_prices:
        checks.extend(_price_checks(routes, update=update_prices, price_lookup=price_lookup,
                                    price_updater=price_updater))
    return checks


def _network_checks(
    settings: ResearchSettings,
    routes: list[tuple[str, ModelRoute, bool, bool]],
    network_probe: Callable[[list[str]], dict[str, str | None]],
) -> list[Check]:
    """Whether the policy's providers and the research tools' sources are reachable, without model calls."""
    providers = sorted({provider for _, route, _, _ in routes if (provider := model_provider(route.model))})
    scholarly = [*SCHOLAR_HOSTS.values(), SEMANTIC_SCHOLAR_API]
    urls = [*(PROVIDER_HOSTS[provider] for provider in providers), *scholarly, *SEARCH_HOSTS, *GENERAL_WEB_HOSTS]
    failures = network_probe(list(dict.fromkeys(urls)))
    checks = []
    for provider in providers:
        url = PROVIDER_HOSTS[provider]
        if not failures[url]:
            checks.append(Check(f"network:{provider}", "PASS", f"{_host(url)} reachable"))
            continue
        # A provider the run cannot use anyway is only a warning.
        usable = settings.provider_enabled(provider) and settings.has_credential(provider)
        checks.append(Check(
            f"network:{provider}", "FAIL" if usable else "WARN",
            f"{_host(url)} unreachable ({failures[url]}); allow it in the network settings",
        ))
    checks.append(_reach_check("network:scholarly", "scholarly APIs", scholarly, failures,
                               "scholarly tools will fail; allow these hosts"))
    checks.append(_reach_check("network:search", "search engines", SEARCH_HOSTS, failures,
                               "web search will fail; allow these hosts"))
    checks.append(_reach_check("network:web", "general web sites", GENERAL_WEB_HOSTS, failures,
                               "web_fetch will fail on most pages; allow general web access"))
    return checks


def main() -> None:
    parser = argparse.ArgumentParser(description="Check research-loop local readiness")
    parser.add_argument("--policy", choices=("quality", "breadth", "glm-heavy", "value"), default="quality")
    parser.add_argument("--attachments", action="store_true", help="Check normalized attachment dependencies")
    parser.add_argument("--multimodal", action="store_true", help="Check image input route and dependencies")
    parser.add_argument("--smoke", action="store_true", help="Make bounded, paid model calls for each unique configured model")
    parser.add_argument("--scholar-live", action="store_true", help="Probe public scholarly metadata endpoints without model calls")
    parser.add_argument("--network", action="store_true",
                        help="Check that model providers, scholarly APIs, search engines, and general web sites are reachable, without model calls")
    parser.add_argument("--prices", action="store_true",
                        help="Show each route model's price per million tokens, without model calls")
    parser.add_argument("--update-prices", action="store_true",
                        help="Like --prices, after fetching the latest genai-prices data")
    args = parser.parse_args()
    try:
        settings = ResearchSettings.from_env()
        if args.smoke:
            configure_logfire(settings)
        checks = run_diagnose(settings, policy_name=args.policy, attachments=args.attachments, multimodal=args.multimodal, smoke=args.smoke, scholar_live=args.scholar_live, network=args.network, prices=args.prices, update_prices=args.update_prices)
    except (ValueError, RuntimeError) as exc:
        parser.error(str(exc))
    for check in checks:
        print(f"{check.status:4} {check.name:24} {check.detail}")
    if any(check.status == "FAIL" for check in checks):
        parser.exit(1)


if __name__ == "__main__":
    main()

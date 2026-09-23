from __future__ import annotations

from research_loop.diagnose import run_diagnose
from research_loop.settings import ResearchSettings


def test_diagnosis_with_no_credentials_skips_providers() -> None:
    settings = ResearchSettings.from_env({})
    checks = run_diagnose(
        settings,
        web_probe=lambda: None,
        writable_probe=lambda _path: None,
    )
    assert not [check for check in checks if check.status == "FAIL"]
    assert any(check.name == "database" and check.status == "SKIP" for check in checks)
    assert any(check.name == "provider:openai" and check.status == "SKIP" for check in checks)
    assert any(check.name == "model:planner" and check.status == "SKIP" for check in checks)


def test_diagnosis_reports_missing_migrations_without_leaking_dsn() -> None:
    settings = ResearchSettings.from_env({"DATABASE_URL": "postgresql://user:password@localhost/db"})
    checks = run_diagnose(
        settings,
        web_probe=lambda: None,
        writable_probe=lambda _path: None,
        database_probe=lambda _dsn: ["001_research.sql"],
    )
    assert any(check.name == "migrations" and check.status == "WARN" for check in checks)
    assert "password" not in repr(checks)


def test_diagnosis_checks_configured_model_with_injected_smoke() -> None:
    settings = ResearchSettings.from_env({"OPENAI_API_KEY": "private-key"})
    checked = []

    async def smoke_probe(route, *, tools, image):
        checked.append((route.model, tools, image))

    checks = run_diagnose(
        settings,
        web_probe=lambda: None,
        writable_probe=lambda _path: None,
        profile_probe=lambda _model: {"supports_tools": True},
        smoke_probe=smoke_probe,
        smoke=True,
    )
    assert any(check.name == "model:gap_analyst" and check.status == "PASS" for check in checks)
    assert checked
    assert "private-key" not in repr(checks)


def test_smoke_failure_classifies_credit_errors_without_response_body() -> None:
    from pydantic_ai.exceptions import ModelHTTPError

    from research_loop.diagnose import _smoke_failure_detail

    error = ModelHTTPError(
        429,
        "secret-model-id",
        {"error": {"code": "credit_balance_exhausted", "message": "private-token"}},
    )
    detail = _smoke_failure_detail(error)
    assert detail == "Provider credit balance exhausted; add credits before smoke"
    assert "secret-model-id" not in detail
    assert "private-token" not in detail


def test_smoke_failure_classifies_access_and_missing_sdk() -> None:
    from pydantic_ai.exceptions import ModelHTTPError

    from research_loop.diagnose import _smoke_failure_detail

    assert "HTTP 403" in _smoke_failure_detail(ModelHTTPError(403, "xai:model", {"message": "private"}))
    assert "xAI SDK" in _smoke_failure_detail(ImportError("Please install xai-sdk"))
    assert "private" not in _smoke_failure_detail(ModelHTTPError(403, "xai:model", {"message": "private"}))


def test_live_smoke_deduplicates_shared_model_routes() -> None:
    model = "openai:shared-model"
    settings = ResearchSettings.from_env({
        "OPENAI_API_KEY": "private-key",
        "RESEARCH_GAP_MODEL": model,
        "RESEARCH_DEEP_MODEL": model,
        "RESEARCH_VERIFY_MODEL": model,
    })
    calls = []

    async def smoke_probe(route, *, tools, image):
        calls.append((route.model, tools, image))

    checks = run_diagnose(
        settings,
        web_probe=lambda: None,
        writable_probe=lambda _path: None,
        profile_probe=lambda _model: {"supports_tools": True},
        smoke_probe=smoke_probe,
        smoke=True,
    )
    assert calls.count((model, True, False)) == 1
    assert all(check.status == "PASS" for check in checks if check.name in {
        "model:gap_analyst", "model:deep_dive", "model:verifier"
    })
    assert any(check.name == "graph" and check.status == "PASS" for check in checks)
    assert any(check.name == "web_tools" and check.status == "PASS" for check in checks)


def test_live_smoke_warns_when_model_has_no_pricing() -> None:
    settings = ResearchSettings.from_env({"OPENAI_API_KEY": "private-key"})

    async def smoke_probe(route, *, tools, image):
        return False

    checks = run_diagnose(
        settings,
        web_probe=lambda: None,
        writable_probe=lambda _path: None,
        profile_probe=lambda _model: {"supports_tools": True},
        smoke_probe=smoke_probe,
        smoke=True,
    )
    gap = next(check for check in checks if check.name == "model:gap_analyst")
    assert gap.status == "WARN"
    assert "pricing" in gap.detail


def test_scholar_live_probes_openalex_search_and_explains_rate_limit(monkeypatch) -> None:
    import httpx

    calls = []

    async def fake_request(self, provider, path, params=None, *, text=False):
        calls.append((provider, params))
        if provider == "openalex":
            request = httpx.Request("GET", "https://api.openalex.org/works")
            raise httpx.HTTPStatusError("429", request=request, response=httpx.Response(429, request=request))
        return {}

    monkeypatch.setattr("research_loop.diagnose.ScholarClient._request", fake_request)
    checks = run_diagnose(
        ResearchSettings.from_env({}),
        web_probe=lambda: None,
        writable_probe=lambda _path: None,
        profile_probe=lambda _model: {"supports_tools": True},
        scholar_live=True,
    )
    assert "search" in dict(calls)["openalex"]
    openalex = next(check for check in checks if check.name == "scholar:openalex")
    assert openalex.status == "WARN"
    assert "OPENALEX_API_KEY" in openalex.detail
    assert all(check.status == "PASS" for check in checks if check.name in {"scholar:crossref", "scholar:arxiv"})

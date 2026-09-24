"""Shared test guards and fixtures.

Every test runs offline: model providers are refused and non-loopback DNS lookups and socket
connections fail the test. Loopback stays open for tests that expect a local connection to fail.
Settings never load the local .env.
"""
from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from pydantic_ai import models

_FETCH_MODULES = ("research_loop.acquisition", "research_loop.scholar", "research_loop.web")


def _loopback(host: Any) -> bool:
    if host in (None, "localhost", b"localhost"):
        return True
    try:
        return ipaddress.ip_address(host.decode() if isinstance(host, bytes) else host).is_loopback
    except ValueError:
        return False


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(models, "ALLOW_MODEL_REQUESTS", False)
    attempts: list[str] = []
    real_getaddrinfo, real_connect = socket.getaddrinfo, socket.socket.connect

    def getaddrinfo(host, *args, **kwargs):
        if not _loopback(host):
            attempts.append(f"DNS lookup of {host!r}")
            raise RuntimeError(f"tests must not resolve {host!r}")
        return real_getaddrinfo(host, *args, **kwargs)

    def connect(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6) and not _loopback(address[0]):
            attempts.append(f"connection to {address[0]!r}")
            raise RuntimeError(f"tests must not connect to {address[0]!r}")
        return real_connect(sock, address)

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(socket.socket, "connect", connect)
    yield
    # Code under test may catch the error above; the attempt still fails the test.
    assert not attempts, f"test reached the network: {attempts}"


@pytest.fixture(autouse=True)
def _no_local_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    """Settings see the environment a test sets up, never the developer's .env and its API keys."""
    monkeypatch.setattr("research_loop.settings.load_dotenv", lambda *_args, **_kwargs: False)


@pytest.fixture
def public_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Treat every HTTPS URL as public, so fetch tests skip the DNS check."""
    async def public(_url: str) -> bool:
        return True

    for module in _FETCH_MODULES:
        monkeypatch.setattr(f"{module}.public_url", public)


@pytest.fixture
def serve(monkeypatch: pytest.MonkeyPatch, public_urls: None) -> Callable[[Callable[[str], httpx.Response]], None]:
    """Answer guarded downloads with `respond(url)` instead of the network."""
    def install(respond: Callable[[str], httpx.Response]) -> None:
        async def download(_client, url: str, _max_bytes: int, _policy: Any = None) -> httpx.Response:
            response = respond(url)
            response.request = httpx.Request("GET", url)
            return response

        for module in ("research_loop.scholar", "research_loop.web"):
            monkeypatch.setattr(f"{module}.bounded_public_get", download)

    return install


@pytest.fixture
def go_offline(monkeypatch: pytest.MonkeyPatch) -> Callable[[], None]:
    """Make any later DNS check or download fail the test; replay must not need either."""
    async def offline(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("replay must not touch DNS or network")

    def install() -> None:
        for module in ("research_loop.scholar", "research_loop.web"):
            monkeypatch.setattr(f"{module}.public_url", offline)
            monkeypatch.setattr(f"{module}.bounded_public_get", offline)

    return install


@pytest.fixture(autouse=True)
def _no_rate_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provider rate slots apply to every client; tests answer locally and need no spacing."""
    from research_loop import acquisition

    monkeypatch.setattr(acquisition, "_RATE_INTERVAL", dict.fromkeys(acquisition._RATE_INTERVAL, 0.0))

"""Unit tests for the job-discovery fetch dispatch.

Covers the two lanes of ``app.job_discovery.fetch``:

* the ``http`` lane — dispatch, header pass-through, byte-bound truncation, and
  timeout/transport-error mapping — driven through httpx's built-in
  ``MockTransport`` (no network, no ``respx`` dependency);
* the ``stealth`` lane — the concurrency gate actually serialises concurrent
  fetches to the configured limit, with the browser mocked.

Requirements: 5.1, 5.2, 5.3, 5.4.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from app.job_discovery import fetch as fetch_mod
from app.job_discovery.fetch import (
    FetchError,
    FetchResult,
    FetchTimeoutError,
    fetch,
)

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def _client(handler) -> httpx.AsyncClient:
    """An AsyncClient whose requests are served by ``handler`` (no network)."""
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# --------------------------------------------------------------------------- #
# http lane
# --------------------------------------------------------------------------- #
async def test_http_fetch_returns_body_and_metadata():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            text="<html>jobs</html>",
        )

    async with _client(handler) as client:
        result = await fetch(
            "https://example.com/jobs", fetch_mode="http", client=client
        )

    assert isinstance(result, FetchResult)
    assert result.status == 200
    assert result.text == "<html>jobs</html>"
    assert result.mode == "http"
    assert result.content_type == "text/html; charset=utf-8"
    assert result.truncated is False


async def test_http_fetch_forwards_headers():
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["ua"] = request.headers.get("user-agent", "")
        return httpx.Response(200, text="ok")

    async with _client(handler) as client:
        await fetch(
            "https://example.com",
            fetch_mode="http",
            headers={"User-Agent": "fitwright-test"},
            client=client,
        )

    assert seen["ua"] == "fitwright-test"


async def test_http_fetch_truncates_at_max_bytes():
    body = "x" * 10_000

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=body)

    async with _client(handler) as client:
        result = await fetch(
            "https://example.com/big",
            fetch_mode="http",
            max_bytes=1_000,
            client=client,
        )

    assert result.truncated is True
    assert len(result.text.encode("utf-8")) == 1_000


async def test_http_fetch_not_truncated_when_within_bound():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="small")

    async with _client(handler) as client:
        result = await fetch(
            "https://example.com", fetch_mode="http", max_bytes=1_000, client=client
        )

    assert result.truncated is False
    assert result.text == "small"


async def test_http_fetch_timeout_maps_to_fetch_timeout_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("slow", request=request)

    async with _client(handler) as client:
        with pytest.raises(FetchTimeoutError):
            await fetch("https://example.com", fetch_mode="http", client=client)


async def test_http_fetch_transport_error_maps_to_fetch_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    async with _client(handler) as client:
        with pytest.raises(FetchError) as excinfo:
            await fetch("https://example.com", fetch_mode="http", client=client)
    # A plain transport error is a FetchError but NOT the timeout subclass.
    assert not isinstance(excinfo.value, FetchTimeoutError)


async def test_unknown_fetch_mode_raises_value_error():
    with pytest.raises(ValueError):
        await fetch("https://example.com", fetch_mode="teleport")  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# stealth lane + concurrency gate
# --------------------------------------------------------------------------- #
async def test_stealth_fetch_uses_injected_browser():
    calls: list[str] = []

    async def fake_browser(url: str, *, timeout: float, max_bytes: int) -> FetchResult:
        calls.append(url)
        return FetchResult(url=url, status=200, text="rendered", mode="stealth")

    result = await fetch(
        "https://spa.example.com",
        fetch_mode="stealth",
        browser_fetch=fake_browser,
        semaphore=asyncio.Semaphore(1),
    )

    assert calls == ["https://spa.example.com"]
    assert result.mode == "stealth"
    assert result.text == "rendered"


async def test_stealth_concurrency_gate_caps_in_flight():
    """With a gate of size 2, no more than 2 browser fetches run at once."""
    limit = 2
    gate = asyncio.Semaphore(limit)
    in_flight = 0
    peak = 0
    lock = asyncio.Lock()

    async def slow_browser(url: str, *, timeout: float, max_bytes: int) -> FetchResult:
        nonlocal in_flight, peak
        async with lock:
            in_flight += 1
            peak = max(peak, in_flight)
        try:
            await asyncio.sleep(0.02)
        finally:
            async with lock:
                in_flight -= 1
        return FetchResult(url=url, status=200, text="ok", mode="stealth")

    await asyncio.gather(
        *(
            fetch(
                f"https://spa.example.com/{i}",
                fetch_mode="stealth",
                browser_fetch=slow_browser,
                semaphore=gate,
            )
            for i in range(8)
        )
    )

    assert peak <= limit
    assert peak == limit  # the gate is actually saturated, not just under-used


async def test_default_stealth_gate_reads_settings(monkeypatch):
    """The process-wide gate is sized from JOB_DISCOVERY_STEALTH_MAX_CONCURRENCY."""
    monkeypatch.setattr(
        fetch_mod.settings, "JOB_DISCOVERY_STEALTH_MAX_CONCURRENCY", 3, raising=False
    )
    fetch_mod._reset_stealth_semaphore()
    try:
        gate = fetch_mod._get_stealth_semaphore()
        assert gate._value == 3
        # Cached: a second call returns the same object.
        assert fetch_mod._get_stealth_semaphore() is gate
    finally:
        fetch_mod._reset_stealth_semaphore()


async def test_default_browser_fetch_missing_module_raises_fetch_error(monkeypatch):
    """When app/jd browser is absent, the stealth default surfaces a FetchError."""
    import builtins

    real_import = builtins.__import__

    def blocked_import(name, *args, **kwargs):
        if name.startswith("app.jd"):
            raise ModuleNotFoundError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked_import)

    with pytest.raises(FetchError):
        await fetch(
            "https://spa.example.com",
            fetch_mode="stealth",
            semaphore=asyncio.Semaphore(1),
        )

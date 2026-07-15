"""Cloudinary provider hardening tests (retry / backoff / recovery / failure).

Uses an injected fake transport and a no-op sleep, so these run offline with no
Cloudinary account and no real network — the retry/backoff logic is exercised
deterministically. Live-service behavior (real signatures/CDN) is NOT verified
here; that requires credentials (documented as such in the report).
"""

from __future__ import annotations

import pytest

from app.storage.provider import CloudinaryStorageProvider, StorageError


class FakeTransport:
    """Scripted transport: each call pops the next scripted response/exception."""

    def __init__(self, script):
        self.script = list(script)
        self.calls: list = []

    async def post(self, url, *, data, files=None):
        self.calls.append((url, data, files))
        item = self.script.pop(0)
        if item[0] == "raise":
            raise item[1]
        if item[0] == "status":
            return item[1], item[2]
        return 200, item[1]  # ("ok", payload)


def _provider(script, **kw):
    sleeps: list[float] = []

    async def fake_sleep(d):
        sleeps.append(d)

    t = FakeTransport(script)
    p = CloudinaryStorageProvider(
        "cloud", "key", "secret",
        transport=t, base_delay=0.01, sleep=fake_sleep, **kw,
    )
    return p, t, sleeps


async def test_put_success_returns_secure_url():
    p, t, sleeps = _provider([("ok", {"secure_url": "https://res/x.webp"})])
    url = await p.put("u/x.webp", b"bytes", content_type="image/webp")
    assert url == "https://res/x.webp"
    assert len(t.calls) == 1
    assert sleeps == []  # no retry needed


async def test_put_retries_transient_status_then_succeeds():
    p, t, sleeps = _provider(
        [("status", 503, {}), ("ok", {"secure_url": "https://res/x.webp"})]
    )
    url = await p.put("u/x.webp", b"b", content_type="image/webp")
    assert url == "https://res/x.webp"
    assert len(t.calls) == 2
    assert len(sleeps) == 1  # backed off once


async def test_put_recovers_from_transport_exception():
    p, t, sleeps = _provider(
        [("raise", RuntimeError("connreset")), ("ok", {"secure_url": "https://res/y"})]
    )
    url = await p.put("u/y.webp", b"b", content_type="image/webp")
    assert url == "https://res/y"
    assert len(t.calls) == 2


async def test_put_permanent_4xx_fails_fast_no_retry():
    p, t, sleeps = _provider([("status", 400, {"error": "bad"})])
    with pytest.raises(StorageError):
        await p.put("u/x.webp", b"b", content_type="image/webp")
    assert len(t.calls) == 1  # no retry on a permanent error
    assert sleeps == []


async def test_put_exhausts_retries_then_raises():
    p, t, sleeps = _provider(
        [("status", 503, {}), ("status", 503, {})], max_attempts=2
    )
    with pytest.raises(StorageError):
        await p.put("u/x.webp", b"b", content_type="image/webp")
    assert len(t.calls) == 2
    assert len(sleeps) == 1


async def test_put_missing_secure_url_is_error():
    p, t, sleeps = _provider([("ok", {})])
    with pytest.raises(StorageError):
        await p.put("u/x.webp", b"b", content_type="image/webp")


async def test_delete_is_best_effort_and_swallows_failure():
    # 3 transient failures with 3 attempts → delete must NOT raise (orphan → GC).
    p, t, sleeps = _provider(
        [("status", 500, {}), ("status", 500, {}), ("status", 500, {})],
        max_attempts=3,
    )
    await p.delete("u/x.webp")  # no exception
    assert len(t.calls) == 3


async def test_delete_success():
    p, t, sleeps = _provider([("ok", {"result": "ok"})])
    await p.delete("u/x.webp")
    assert len(t.calls) == 1


def test_signature_is_deterministic_and_sorted():
    p, _, _ = _provider([("ok", {})])
    sig1 = p._sign({"b": "2", "a": "1"})
    sig2 = p._sign({"a": "1", "b": "2"})
    assert sig1 == sig2  # order-independent (params sorted before signing)

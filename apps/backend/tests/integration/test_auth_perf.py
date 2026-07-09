"""Performance & scalability tests for the auth stack (Task 11.1).

These are *deterministic* performance-shape tests, not wall-clock benchmarks:
they assert the architectural guarantees the design (`§Performance & scalability`,
R17.1/17.2) makes, using tolerance-based bounds and behavioural assertions
(e.g. "the cache-hit path issues **zero** DB queries") rather than exact timings,
so they never flake on a slow/loaded CI box.

Covered:

- **Login + session-resolution under concurrency** — many parallel resolves of
  the same session all succeed and agree (O(1) resolution, R17.1); a burst of
  concurrent logins all issue distinct, independently-resolvable sessions.
- **Cache-hit fast path** — the first resolve populates the KVStore snapshot; a
  subsequent resolve is served entirely from the cache and touches the DB
  **zero** times (R17.1), verified by counting real DB sessions opened.
- **Lockout under a burst of failed logins** — a burst drives the account into a
  uniform ``429 rate_limited`` with a ``Retry-After`` (R13.1).
- **Argon2 cost budget** — the *production-default* Argon2 parameters hash a
  password within a sane bounded time (R17.2), so a misconfiguration that made
  hashing explode would be caught, while the dialed-down test fixture stays fast.

Requirements: 17.1, 17.2, 13.5
"""

from __future__ import annotations

import asyncio
import time

import pytest
from httpx import ASGITransport, AsyncClient

from app.auth.accounts import create_user
from app.auth.passwords import PasswordService, get_password_service
from app.auth.sessions import SessionService
from app.config import Settings
from app.config import settings as app_settings
from app.main import app

pytestmark = pytest.mark.integration

STRONG_PW = "correct-horse-battery-staple-9"


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="https://test")


async def _csrf(client: AsyncClient) -> str:
    resp = await client.get("/api/v1/auth/csrf")
    assert resp.status_code == 200
    return resp.json()["csrfToken"]


async def _login(client, email, *, password=STRONG_PW):
    token = await _csrf(client)
    return await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
        headers={"X-CSRF-Token": token},
    )


async def _seed(db, email, *, password=STRONG_PW, name="Alice"):
    hashed = get_password_service().hash_password(password)
    return await create_user(
        email=email,
        name=name,
        password_hash=hashed,
        status="active",
        email_verified_at="2024-01-01T00:00:00+00:00",
        db=db,
    )


class _CountingSessionFactory:
    """Wrap an ``async_sessionmaker`` and count how many sessions it opens.

    A DB round-trip in :meth:`SessionService.resolve` always opens exactly one
    session, so this is a precise, deterministic proxy for "did we hit the DB?".
    """

    def __init__(self, inner) -> None:
        self._inner = inner
        self.opened = 0

    def __call__(self, *args, **kwargs):
        self.opened += 1
        return self._inner(*args, **kwargs)


# ---------------------------------------------------------------------------
# Concurrency: many parallel session resolves + concurrent logins
# ---------------------------------------------------------------------------


class TestSessionResolutionConcurrency:
    async def test_many_parallel_resolves_agree(self, auth_env):
        """N concurrent resolves of one session all succeed and agree (R17.1)."""
        from app.auth.runtime import get_kvstore

        record = await _seed(auth_env, "concurrent@example.com")
        service = SessionService(
            auth_env.session_factory, get_kvstore(), settings=app_settings
        )
        raw_token, _info = await service.create_session(record.id)

        # Fire a wide fan-out of resolves in parallel against the same token.
        started = time.monotonic()
        results = await asyncio.gather(
            *(service.resolve(raw_token) for _ in range(50))
        )
        elapsed = time.monotonic() - started

        assert all(r is not None for r in results)
        assert {r.user_id for r in results} == {record.id}
        assert {r.session_id for r in results} == {results[0].session_id}
        # Tolerance-based, not exact: O(1) resolution over a cache should finish
        # this small fan-out well under a very generous ceiling.
        assert elapsed < 10.0

    async def test_concurrent_logins_issue_distinct_sessions(self, auth_env):
        """A burst of concurrent logins each yields a distinct, live session."""
        await _seed(auth_env, "burst-login@example.com")

        async def _one() -> str | None:
            async with _client() as client:
                resp = await _login(client, "burst-login@example.com")
                if resp.status_code != 200:
                    return None
                for raw in resp.headers.get_list("set-cookie"):
                    if raw.startswith("__Host-session="):
                        return raw.split("=", 1)[1].split(";", 1)[0]
            return None

        # Login rate limit is 10/60s per IP; stay within it so the burst is about
        # concurrency, not the limiter (that is exercised in the lockout test).
        tokens = await asyncio.gather(*(_one() for _ in range(8)))
        tokens = [t for t in tokens if t]
        assert len(tokens) == 8
        # Fixation defense: every login mints a brand-new session token.
        assert len(set(tokens)) == 8

        # Each issued session independently resolves to a valid principal.
        async with _client() as client:
            for tok in tokens:
                sess = await client.get(
                    "/api/v1/auth/session",
                    headers={"Cookie": f"__Host-session={tok}"},
                )
                assert sess.status_code == 200
                assert sess.json()["email"] == "burst-login@example.com"


# ---------------------------------------------------------------------------
# Cache-hit fast path: a warm resolve issues zero DB queries (R17.1)
# ---------------------------------------------------------------------------


class TestCacheHitAvoidsDb:
    async def test_warm_resolve_does_not_touch_the_db(self, auth_env):
        """First resolve populates the cache; the second is served without a DB hit."""
        from app.auth.runtime import get_kvstore

        record = await _seed(auth_env, "cachehit@example.com")
        counting = _CountingSessionFactory(auth_env.session_factory)
        service = SessionService(counting, get_kvstore(), settings=app_settings)

        raw_token, _info = await service.create_session(record.id)
        opened_after_create = counting.opened

        # 1) Cold resolve → cache miss → exactly one DB session opened.
        first = await service.resolve(raw_token)
        assert first is not None
        assert counting.opened == opened_after_create + 1

        # 2) Warm resolve → cache hit → NO additional DB session opened (R17.1).
        second = await service.resolve(raw_token)
        assert second is not None
        assert second.user_id == record.id
        assert counting.opened == opened_after_create + 1

    async def test_cache_hit_ratio_is_tracked(self, auth_env):
        """Repeated warm resolves push the session-cache hit ratio above zero."""
        from app.auth.metrics import get_metrics
        from app.auth.runtime import get_kvstore

        record = await _seed(auth_env, "ratio@example.com")
        service = SessionService(
            auth_env.session_factory, get_kvstore(), settings=app_settings
        )
        raw_token, _info = await service.create_session(record.id)

        for _ in range(5):
            await service.resolve(raw_token)

        snap = get_metrics().snapshot()
        assert snap.get("session_cache_hit", 0) >= 1
        assert 0.0 <= snap["session_cache_hit_ratio"] <= 1.0


# ---------------------------------------------------------------------------
# Lockout under a burst of failed logins (R13.1)
# ---------------------------------------------------------------------------


class TestLockoutUnderBurst:
    async def test_burst_of_failures_locks_out_with_retry_after(self, auth_env):
        await _seed(auth_env, "burst@example.com")
        statuses: list[int] = []
        retry_after: str | None = None
        async with _client() as client:
            csrf = await _csrf(client)
            for _ in range(15):
                resp = await client.post(
                    "/api/v1/auth/login",
                    json={"email": "burst@example.com", "password": "definitely-wrong-1"},
                    headers={"X-CSRF-Token": csrf},
                )
                statuses.append(resp.status_code)
                if resp.status_code == 429:
                    retry_after = resp.headers.get("Retry-After")
                    break

        # The burst is contained: it terminates in a 429 with a Retry-After hint.
        assert 429 in statuses
        assert retry_after is not None
        assert int(retry_after) >= 1


# ---------------------------------------------------------------------------
# Argon2 cost budget (R17.2)
# ---------------------------------------------------------------------------


class TestArgon2CostBudget:
    def test_production_default_params_hash_within_budget(self):
        """The shipped default Argon2 params produce a hash in a sane bounded time.

        Uses a *fresh* ``Settings`` (not the dialed-down test fixture) so a
        misconfiguration that made hashing explode would be caught. The ceiling
        is deliberately generous (seconds, not the ~50-100ms target) so the test
        is robust on slow/loaded CI while still catching a runaway cost.
        """
        defaults = Settings()
        service = PasswordService(
            time_cost=defaults.argon2_time_cost,
            memory_cost=defaults.argon2_memory_cost,
            parallelism=defaults.argon2_parallelism,
        )
        started = time.monotonic()
        hashed = service.hash_password(STRONG_PW)
        elapsed = time.monotonic() - started

        # Produces a real Argon2id hash that verifies…
        assert hashed.startswith("$argon2id$")
        assert service.verify_password(hashed, STRONG_PW) is True
        # …within a sane, bounded budget (params are not pathologically large).
        assert elapsed < 5.0

    def test_dialed_down_fixture_params_are_fast(self, auth_env):
        """The test fixture's dialed-down params keep the suite fast (deterministic)."""
        service = get_password_service()
        started = time.monotonic()
        hashed = service.hash_password(STRONG_PW)
        elapsed = time.monotonic() - started
        assert service.verify_password(hashed, STRONG_PW) is True
        # The fixture exists precisely so hashing is near-instant in tests.
        assert elapsed < 1.0

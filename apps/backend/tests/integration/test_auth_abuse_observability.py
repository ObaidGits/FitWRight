"""Integration tests for abuse controls, headers & observability (Task 9).

Covers, end-to-end over the real ASGI app against an isolated temp DB:

- **9.1** CAPTCHA + breach wiring: breach/CAPTCHA fail-open (provider raises ->
  auth proceeds, warning logged); breach reject on a known-breached password;
  lockout/backoff returns a uniform ``rate_limited`` (429 + ``Retry-After``) with
  **no enumeration**; KVStore-outage fail-closed for auth rate limits.
- **9.2** Security headers/CSP present on all responses (incl. CSP directives +
  HSTS); auth metrics emitted; audit events written for key actions.

Requirements: 12.3, 13.1, 13.2, 13.3, 13.4, 13.5, 16.1, 16.2
"""

from __future__ import annotations

import logging

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.auth.accounts import create_user
from app.auth.breach import BreachResult
from app.auth.captcha import CaptchaResult
from app.auth.metrics import get_metrics
from app.auth.passwords import get_password_service
from app.config import settings as app_settings
from app.main import app
from app.models import AuditLog

pytestmark = pytest.mark.integration

STRONG_PW = "correct-horse-battery-staple-9"


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="https://test")


async def _csrf(client: AsyncClient) -> str:
    resp = await client.get("/api/v1/auth/csrf")
    assert resp.status_code == 200
    return resp.json()["csrfToken"]


async def _signup(client, email, *, password=STRONG_PW, name="Alice", captcha_token=None):
    token = await _csrf(client)
    body = {"email": email, "password": password, "name": name}
    if captcha_token is not None:
        body["captcha_token"] = captcha_token
    return await client.post(
        "/api/v1/auth/signup", json=body, headers={"X-CSRF-Token": token}
    )


async def _login(client, email, *, password=STRONG_PW, captcha_token=None):
    token = await _csrf(client)
    body = {"email": email, "password": password}
    if captcha_token is not None:
        body["captcha_token"] = captcha_token
    return await client.post(
        "/api/v1/auth/login", json=body, headers={"X-CSRF-Token": token}
    )


async def _seed(db, email, *, password=STRONG_PW, status="active", name="Alice"):
    hashed = get_password_service().hash_password(password)
    return await create_user(
        email=email,
        name=name,
        password_hash=hashed,
        status=status,
        email_verified_at="2024-01-01T00:00:00+00:00",
        db=db,
    )


async def _events(db) -> list[str]:
    async with db.session_factory() as session:
        rows = (await session.execute(select(AuditLog))).scalars().all()
    return [r.event for r in rows]


# ---------------------------------------------------------------------------
# 9.1 - breach / captcha fail-open
# ---------------------------------------------------------------------------


class TestBreachFailOpen:
    async def test_signup_proceeds_when_breach_provider_raises(self, auth_env, caplog):
        """A breach provider that raises must not block signup (fail-open, logged)."""

        class _BoomBreach:
            async def check(self, password: str) -> BreachResult:
                raise RuntimeError("HIBP unreachable")

        # Verification off (local) -> signup signs in immediately on success.
        get_password_service()._breach_check = _BoomBreach()
        with caplog.at_level(logging.WARNING):
            async with _client() as client:
                resp = await _signup(client, "failopen@example.com")
        assert resp.status_code == 200  # auth proceeded despite the outage
        assert any("failing open" in r.message.lower() for r in caplog.records)

    async def test_breached_password_rejected(self, auth_env):
        """A *positive* breach result still rejects (breached_password)."""

        class _Pwned:
            async def check(self, password: str) -> BreachResult:
                return BreachResult(breached=True, count=99)

        get_password_service()._breach_check = _Pwned()
        async with _client() as client:
            resp = await _signup(client, "pwned@example.com")
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "breached_password"


class TestCaptchaGate:
    async def test_captcha_fail_open_when_unconfigured(self, auth_env):
        """With no CAPTCHA provider, login proceeds even past the soft threshold."""
        await _seed(auth_env, "cap@example.com")
        async with _client() as client:
            # A few wrong attempts push failures past the soft threshold (3).
            for _ in range(4):
                await _login(client, "cap@example.com", password="wrong-password-xyz")
            # The next *correct* login still succeeds - the default verifier
            # allows (fail-open), it does not demand a token.
            ok = await _login(client, "cap@example.com")
        assert ok.status_code == 200

    async def test_configured_captcha_required_past_threshold(self, auth_env):
        """A configured verifier that rejects yields 403 captcha_required."""
        from app.auth.ratelimit import get_rate_limiter

        class _RejectCaptcha:
            async def verify(self, token, *, remote_ip=None):
                return CaptchaResult(allowed=bool(token), reason="needs_token")

        limiter = get_rate_limiter()
        limiter._captcha = _RejectCaptcha()
        limiter._soft_threshold = 1  # trip quickly for the test

        await _seed(auth_env, "capreq@example.com")
        async with _client() as client:
            # Two failures lift the count strictly above the soft threshold (1).
            await _login(client, "capreq@example.com", password="wrong-password-xyz")
            await _login(client, "capreq@example.com", password="wrong-password-xyz")
            # Now a login with no captcha token is challenged.
            blocked = await _login(client, "capreq@example.com")
            assert blocked.status_code == 403
            assert blocked.json()["error"]["code"] == "captcha_required"
            # Supplying a token clears the challenge -> the correct login succeeds.
            ok = await _login(client, "capreq@example.com", captcha_token="solved")
        assert ok.status_code == 200


# ---------------------------------------------------------------------------
# 9.1 - lockout UX: uniform rate_limited, no enumeration
# ---------------------------------------------------------------------------


class TestLockoutNoEnumeration:
    async def _hammer(self, client, email) -> tuple[int, str | None]:
        csrf = await _csrf(client)
        last_status = 0
        retry_after = None
        for _ in range(12):
            resp = await client.post(
                "/api/v1/auth/login",
                json={"email": email, "password": "definitely-wrong-1"},
                headers={"X-CSRF-Token": csrf},
            )
            last_status = resp.status_code
            if resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After")
                break
        return last_status, retry_after

    async def test_lockout_is_uniform_for_existing_and_unknown(self, auth_env):
        await _seed(auth_env, "real@example.com")
        async with _client() as c1:
            existing_status, existing_retry = await self._hammer(c1, "real@example.com")
        async with _client() as c2:
            unknown_status, unknown_retry = await self._hammer(c2, "ghost@example.com")

        # Both accounts lock out identically (429 + Retry-After) - an attacker
        # cannot tell a real account from an unknown one (R13.4).
        assert existing_status == unknown_status == 429
        assert existing_retry is not None and unknown_retry is not None


# ---------------------------------------------------------------------------
# 9.1 - KVStore outage fails CLOSED for auth rate limits (R13.5)
# ---------------------------------------------------------------------------


class TestKVStoreOutageFailClosed:
    async def test_login_denied_when_ratelimit_store_down(self, auth_env):
        from app.auth.ratelimit import get_rate_limiter

        limiter = get_rate_limiter()

        class _DeadKV:
            async def incr(self, *a, **k):
                raise RuntimeError("kv down")

            async def get(self, *a, **k):
                raise RuntimeError("kv down")

            async def set(self, *a, **k):
                raise RuntimeError("kv down")

            async def delete(self, *a, **k):
                raise RuntimeError("kv down")

        limiter._kv = _DeadKV()
        await _seed(auth_env, "closed@example.com")
        async with _client() as client:
            resp = await _login(client, "closed@example.com")
        # Fail-closed: deny with 429 + Retry-After rather than an unlimited window.
        assert resp.status_code == 429
        assert resp.headers.get("Retry-After") is not None


# ---------------------------------------------------------------------------
# 9.2 - security headers / CSP on all responses
# ---------------------------------------------------------------------------


class TestSecurityHeaders:
    async def test_headers_present_on_auth_response(self, auth_env):
        async with _client() as client:
            resp = await client.get("/api/v1/auth/csrf")
        h = resp.headers
        assert h["X-Content-Type-Options"] == "nosniff"
        assert h["Referrer-Policy"] == "strict-origin-when-cross-origin"
        csp = h["Content-Security-Policy"]
        assert "frame-ancestors 'none'" in csp
        assert "default-src 'self'" in csp
        assert "object-src 'none'" in csp
        assert h.get("X-Request-ID") is not None

    async def test_hsts_present_when_secure(self, auth_env, monkeypatch):
        # HSTS is only emitted in secure (hosted) mode.
        monkeypatch.setattr(app_settings, "cookie_secure", True)
        async with _client() as client:
            resp = await client.get("/api/v1/auth/csrf")
        assert "max-age=" in resp.headers.get("Strict-Transport-Security", "")

    async def test_headers_present_on_error_response(self, auth_env):
        # Even a 401 (from an inner route) carries the security headers.
        async with _client() as client:
            resp = await client.get("/api/v1/auth/session")
        assert resp.status_code == 401
        assert resp.headers["X-Content-Type-Options"] == "nosniff"
        assert "Content-Security-Policy" in resp.headers


# ---------------------------------------------------------------------------
# 9.2 - metrics emitted
# ---------------------------------------------------------------------------


class TestMetricsEmitted:
    async def test_login_success_and_failure_metrics(self, auth_env):
        await _seed(auth_env, "metric@example.com")
        async with _client() as client:
            await _login(client, "metric@example.com", password="wrong-password-xyz")
            await _login(client, "metric@example.com")
        snap = get_metrics().snapshot()
        assert snap["login_failure"] >= 1
        assert snap["login_success"] >= 1

    async def test_signup_metric(self, auth_env):
        async with _client() as client:
            await _signup(client, "smetric@example.com")
        assert get_metrics().snapshot()["signup"] >= 1

    async def test_session_cache_ratio_tracked(self, auth_env):
        await _seed(auth_env, "cache@example.com")
        async with _client() as client:
            await _login(client, "cache@example.com")
            # Repeated authenticated calls exercise the session-resolution cache.
            for _ in range(3):
                await client.get("/api/v1/auth/session")
        snap = get_metrics().snapshot()
        assert snap.get("session_cache_hit", 0) + snap.get("session_cache_miss", 0) > 0
        assert 0.0 <= snap["session_cache_hit_ratio"] <= 1.0


# ---------------------------------------------------------------------------
# 9.2 - audit events for key actions
# ---------------------------------------------------------------------------


class TestAuditEvents:
    async def test_login_and_logout_audited(self, auth_env, monkeypatch):
        monkeypatch.setattr(app_settings, "single_user_mode", False)
        await _seed(auth_env, "aud@example.com")
        async with _client() as client:
            await _login(client, "aud@example.com")
            csrf_value = client.cookies.get("csrf")
            await client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf_value})
        events = await _events(auth_env)
        assert "login" in events
        assert "logout" in events

    async def test_step_up_audited(self, auth_env, monkeypatch):
        monkeypatch.setattr(app_settings, "single_user_mode", False)
        await _seed(auth_env, "step@example.com")
        async with _client() as client:
            await _login(client, "step@example.com")
            csrf_value = client.cookies.get("csrf")
            await client.post(
                "/api/v1/auth/step-up",
                json={"password": STRONG_PW},
                headers={"X-CSRF-Token": csrf_value},
            )
        assert "auth.step_up" in await _events(auth_env)

    async def test_password_reset_audited(self, auth_env):
        record = await _seed(auth_env, "reset@example.com")
        from app.auth.tokens import get_token_service

        raw = await get_token_service().issue_reset(record.id)
        async with _client() as client:
            token = await _csrf(client)
            resp = await client.post(
                "/api/v1/auth/password/reset",
                json={"token": raw, "password": "a-brand-new-passphrase-42"},
                headers={"X-CSRF-Token": token},
            )
        assert resp.status_code == 200
        assert "password_reset" in await _events(auth_env)

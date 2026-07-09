"""Integration tests for the email/password auth + session API (Task 4.1).

Exercises the real routers end-to-end over an ASGI transport against an isolated
temp database: signup (new / existing-uniform / weak / breached), login
(success / uniform-invalid / remember-me / pre-session-CSRF / fixation rotation /
lockout), logout (+CSRF), logout-all (+step-up), and ``GET /auth/session``.
Also asserts the ``SafeUser`` projection never leaks secrets and the ``__Host-``
cookie attributes are hardened.

Requirements: 1.*, 2.*, 3.1, 3.2, 7.5, 12.1
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.auth.accounts import create_user, get_by_id
from app.auth.passwords import get_password_service
from app.config import settings as app_settings
from app.main import app
from app.schemas.auth import SAFE_USER_FIELDS

pytestmark = pytest.mark.integration

# A strong passphrase that clears the length + strength gate and echoes neither
# the test name nor email local-part.
STRONG_PW = "correct-horse-battery-staple-9"


def _client() -> AsyncClient:
    # https base_url so the httpx cookie jar stores/returns the Secure __Host- cookie.
    return AsyncClient(transport=ASGITransport(app=app), base_url="https://test")


async def _csrf(client: AsyncClient) -> str:
    """Fetch a pre-session CSRF token (also sets the matching cookie)."""
    resp = await client.get("/api/v1/auth/csrf")
    assert resp.status_code == 200
    return resp.json()["csrfToken"]


async def _signup(client: AsyncClient, email: str, *, password: str = STRONG_PW, name: str = "Alice"):
    token = await _csrf(client)
    return await client.post(
        "/api/v1/auth/signup",
        json={"email": email, "password": password, "name": name},
        headers={"X-CSRF-Token": token},
    )


async def _login(client: AsyncClient, email: str, *, password: str = STRONG_PW, remember_me: bool = False):
    token = await _csrf(client)
    return await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password, "remember_me": remember_me},
        headers={"X-CSRF-Token": token},
    )


async def _seed_active_user(db, email: str, *, password: str = STRONG_PW, name: str = "Alice"):
    """Create an active, verified user with a real password hash directly in the DB."""
    hashed = get_password_service().hash_password(password)
    return await create_user(
        email=email,
        name=name,
        password_hash=hashed,
        status="active",
        email_verified_at="2024-01-01T00:00:00+00:00",
        db=db,
    )


def _cookie_str(resp, name: str) -> str | None:
    for raw in resp.headers.get_list("set-cookie"):
        if raw.startswith(f"{name}="):
            return raw
    return None


# ---------------------------------------------------------------------------
# signup
# ---------------------------------------------------------------------------


class TestSignup:
    async def test_signup_creates_user_and_session(self, auth_env):
        async with _client() as client:
            resp = await _signup(client, "alice@example.com")
        assert resp.status_code == 200
        body = resp.json()
        # SafeUser safeguard: only whitelisted fields, no secrets.
        assert set(body) <= SAFE_USER_FIELDS
        assert "password_hash" not in body and "mfa_enrolled" not in body
        assert body["email"] == "alice@example.com"
        assert body["role"] == "user"
        assert body["status"] == "active"
        assert body["emailVerified"] is True
        assert body["aal"] == "aal1"

        # __Host- session cookie is hardened; csrf cookie is JS-readable.
        session_cookie = _cookie_str(resp, "__Host-session")
        assert session_cookie is not None
        assert "HttpOnly" in session_cookie
        assert "Secure" in session_cookie
        assert "Path=/" in session_cookie
        assert "samesite=lax" in session_cookie.lower()
        assert "Domain=" not in session_cookie
        csrf_cookie = _cookie_str(resp, "csrf")
        assert csrf_cookie is not None and "HttpOnly" not in csrf_cookie

    async def test_signup_existing_email_is_email_unavailable(self, auth_env):
        async with _client() as client:
            first = await _signup(client, "dup@example.com")
            assert first.status_code == 200
        async with _client() as client:
            second = await _signup(client, "dup@example.com")
        assert second.status_code == 409
        assert second.json()["error"]["code"] == "email_unavailable"

    async def test_signup_weak_password_rejected(self, auth_env):
        async with _client() as client:
            resp = await _signup(client, "weak@example.com", password="short")
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "weak_password"

    async def test_signup_breached_password_rejected(self, auth_env, monkeypatch):
        # Force the (otherwise fail-open) breach check to report a hit.
        from app.auth.breach import BreachResult

        class _FakeBreach:
            async def check(self, password: str) -> BreachResult:
                return BreachResult(breached=True, count=42)

        monkeypatch.setattr(get_password_service(), "_breach_check", _FakeBreach())
        async with _client() as client:
            resp = await _signup(client, "pwned@example.com")
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "breached_password"

    async def test_signup_requires_presession_csrf(self, auth_env):
        async with _client() as client:
            # No GET /auth/csrf and no header → double-submit fails.
            resp = await client.post(
                "/api/v1/auth/signup",
                json={"email": "nocsrf@example.com", "password": STRONG_PW, "name": "A"},
            )
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "csrf_failed"

    async def test_signup_uniform_when_verification_on(self, auth_env, monkeypatch):
        # Hosted mode → email verification on: new and existing emails must be
        # indistinguishable (no session, identical body) — Property 4.
        monkeypatch.setattr(app_settings, "single_user_mode", False)
        # Pre-existing account for the "existing" branch.
        await _seed_active_user(auth_env, "known@example.com")

        async with _client() as client:
            new_resp = await _signup(client, "brand-new@example.com")
        async with _client() as client:
            existing_resp = await _signup(client, "known@example.com")

        assert new_resp.status_code == existing_resp.status_code == 200
        assert new_resp.json() == existing_resp.json() == {"status": "pending_verification"}
        # Neither branch established a session.
        assert _cookie_str(new_resp, "__Host-session") is None
        assert _cookie_str(existing_resp, "__Host-session") is None


# ---------------------------------------------------------------------------
# login
# ---------------------------------------------------------------------------


class TestLogin:
    async def test_login_success(self, auth_env):
        await _seed_active_user(auth_env, "bob@example.com")
        async with _client() as client:
            resp = await _login(client, "bob@example.com")
        assert resp.status_code == 200
        body = resp.json()
        assert set(body) <= SAFE_USER_FIELDS
        assert body["email"] == "bob@example.com"
        assert _cookie_str(resp, "__Host-session") is not None

    async def test_login_invalid_password_is_uniform(self, auth_env):
        await _seed_active_user(auth_env, "carol@example.com")
        async with _client() as client:
            resp = await _login(client, "carol@example.com", password="wrong-password-xyz")
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "invalid_credentials"

    async def test_login_unknown_email_is_uniform(self, auth_env):
        async with _client() as client:
            resp = await _login(client, "ghost@example.com")
        # Same status + code as a wrong password → no enumeration.
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "invalid_credentials"

    async def test_login_disabled_account_rejected(self, auth_env):
        await create_user(
            email="disabled@example.com",
            name="D",
            password_hash=get_password_service().hash_password(STRONG_PW),
            status="disabled",
            db=auth_env,
        )
        async with _client() as client:
            resp = await _login(client, "disabled@example.com")
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "account_disabled"

    async def test_login_requires_presession_csrf(self, auth_env):
        await _seed_active_user(auth_env, "dee@example.com")
        async with _client() as client:
            resp = await client.post(
                "/api/v1/auth/login",
                json={"email": "dee@example.com", "password": STRONG_PW},
            )
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "csrf_failed"

    async def test_remember_me_uses_longer_cookie_cap(self, auth_env):
        await _seed_active_user(auth_env, "rem@example.com")
        async with _client() as client:
            plain = await _login(client, "rem@example.com", remember_me=False)
        async with _client() as client:
            remembered = await _login(client, "rem@example.com", remember_me=True)
        plain_cookie = _cookie_str(plain, "__Host-session")
        remembered_cookie = _cookie_str(remembered, "__Host-session")
        assert f"Max-Age={app_settings.session_absolute_ttl}" in plain_cookie
        assert f"Max-Age={app_settings.remember_me_ttl}" in remembered_cookie

    async def test_login_rotates_session_and_revokes_old(self, auth_env):
        await _seed_active_user(auth_env, "rot@example.com")
        async with _client() as client:
            await _login(client, "rot@example.com")
            token1 = client.cookies.get("__Host-session")
            # A second login on the same client rotates to a brand-new id/token.
            await _login(client, "rot@example.com")
            token2 = client.cookies.get("__Host-session")
        assert token1 and token2 and token1 != token2

        # The old token no longer authorizes anything (fixation defense).
        async with _client() as fresh:
            resp = await fresh.get(
                "/api/v1/auth/session", headers={"Cookie": f"__Host-session={token1}"}
            )
        assert resp.status_code == 401

    async def test_login_lockout_after_repeated_failures(self, auth_env):
        await _seed_active_user(auth_env, "lock@example.com")
        statuses: list[int] = []
        async with _client() as client:
            csrf = await _csrf(client)
            for _ in range(11):
                resp = await client.post(
                    "/api/v1/auth/login",
                    json={"email": "lock@example.com", "password": "definitely-wrong-1"},
                    headers={"X-CSRF-Token": csrf},
                )
                statuses.append(resp.status_code)
                if resp.status_code == 429:
                    break
        assert 401 in statuses  # early attempts are generic invalid_credentials
        assert 429 in statuses  # lockout eventually kicks in with Retry-After


# ---------------------------------------------------------------------------
# logout / logout-all (hosted mode exercises the per-session CSRF gate)
# ---------------------------------------------------------------------------


class TestLogout:
    async def test_logout_requires_csrf_then_revokes(self, auth_env, monkeypatch):
        monkeypatch.setattr(app_settings, "single_user_mode", False)
        await _seed_active_user(auth_env, "lo@example.com")
        async with _client() as client:
            await _login(client, "lo@example.com")

            # Without the per-session CSRF header the middleware rejects the mutation.
            no_csrf = await client.post("/api/v1/auth/logout")
            assert no_csrf.status_code == 403

            csrf_value = client.cookies.get("csrf")
            ok = await client.post(
                "/api/v1/auth/logout", headers={"X-CSRF-Token": csrf_value}
            )
            assert ok.status_code == 200

            # Session is revoked: it no longer authorizes.
            after = await client.get("/api/v1/auth/session")
        assert after.status_code == 401

    async def test_logout_all_requires_step_up(self, auth_env, monkeypatch):
        monkeypatch.setattr(app_settings, "single_user_mode", False)
        record = await _seed_active_user(auth_env, "la@example.com")
        async with _client() as client:
            await _login(client, "la@example.com")
            csrf_value = client.cookies.get("csrf")

            # No recent step-up → blocked.
            blocked = await client.post(
                "/api/v1/auth/logout-all", headers={"X-CSRF-Token": csrf_value}
            )
            assert blocked.status_code == 401
            assert blocked.json()["error"]["code"] == "step_up_required"

            # Simulate a recent step-up on the active session, then it succeeds.
            from app.auth.sessions import get_session_service

            svc = get_session_service()
            sessions = await svc.list_active_sessions(record.id)
            await svc.bump_step_up(sessions[0].id)

            csrf_value = client.cookies.get("csrf")
            ok = await client.post(
                "/api/v1/auth/logout-all", headers={"X-CSRF-Token": csrf_value}
            )
        assert ok.status_code == 200
        assert ok.json()["count"] >= 1


# ---------------------------------------------------------------------------
# GET /auth/session
# ---------------------------------------------------------------------------


class TestSessionEndpoint:
    async def test_anonymous_gets_401(self, auth_env):
        async with _client() as client:
            resp = await client.get("/api/v1/auth/session")
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "unauthorized"

    async def test_authenticated_returns_safe_user(self, auth_env):
        await _seed_active_user(auth_env, "sess@example.com")
        async with _client() as client:
            await _login(client, "sess@example.com")
            resp = await client.get("/api/v1/auth/session")
        assert resp.status_code == 200
        body = resp.json()
        assert set(body) <= SAFE_USER_FIELDS
        assert body["email"] == "sess@example.com"
        assert body["aal"] == "aal1"

"""Unit tests for Principal, RBAC, deps, cookies, and middleware (Task 2.3).

Covers the role->capability map, the ``Principal`` object + step-up window, the
FastAPI deps (401/403/step_up_required), the hardened ``__Host-`` / csrf cookie
attributes, the security-headers middleware, the per-session CSRF gate in
``AuthMiddleware``, and the ``GET /auth/csrf`` pre-session endpoint.

Requirements: 8.1, 8.2, 9.1, 12.1, 12.2, 12.3
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import Depends, FastAPI
from fastapi.responses import PlainTextResponse
from fastapi.testclient import TestClient
from starlette.responses import Response

from app.auth import csrf as csrf_mod
from app.auth.principal import (
    AuthMiddleware,
    Capabilities,
    Principal,
    SecurityHeadersMiddleware,
    auth_csrf_router,
    capabilities_for,
    clear_session_cookies,
    get_principal,
    require_capability,
    require_step_up,
    set_session_cookies,
)
from app.auth.sessions import ResolvedSession
from app.config import Settings
from fastapi.exceptions import HTTPException

pytestmark = pytest.mark.unit


def _principal(*, role: str = "user", step_up_at: str | None = None) -> Principal:
    return Principal(
        user_id="u1",
        session_id="s1",
        role=role,
        capabilities=capabilities_for(role),
        aal="aal1",
        step_up_at=step_up_at,
        email="u1@example.com",
        name="U One",
        status="active",
        email_verified=True,
        csrf_secret="csrf-secret-value",
    )


def _fake_request(principal: Principal | None) -> SimpleNamespace:
    return SimpleNamespace(
        state=SimpleNamespace(principal=principal),
        url=SimpleNamespace(path="/x"),
    )


# ---------------------------------------------------------------------------
# capability map
# ---------------------------------------------------------------------------


class TestCapabilities:
    def test_admin_has_admin_caps(self):
        caps = capabilities_for("admin")
        assert Capabilities.ADMIN_READ in caps
        assert Capabilities.ADMIN_MANAGE in caps

    def test_user_has_no_admin_caps(self):
        caps = capabilities_for("user")
        assert Capabilities.ADMIN_MANAGE not in caps

    def test_unknown_role_has_no_caps(self):
        assert capabilities_for("wizard") == frozenset()

    def test_principal_has_capability(self):
        assert _principal(role="admin").has_capability(Capabilities.ADMIN_MANAGE)
        assert not _principal(role="user").has_capability(Capabilities.ADMIN_MANAGE)


# ---------------------------------------------------------------------------
# step-up window
# ---------------------------------------------------------------------------


class TestStepUpWindow:
    def test_recent_step_up_is_within_window(self):
        now = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
        recent = (now - timedelta(seconds=60)).isoformat()
        assert _principal(step_up_at=recent).stepped_up_within(300, now=now) is True

    def test_stale_step_up_is_outside_window(self):
        now = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
        stale = (now - timedelta(seconds=600)).isoformat()
        assert _principal(step_up_at=stale).stepped_up_within(300, now=now) is False

    def test_no_step_up_is_outside_window(self):
        assert _principal(step_up_at=None).stepped_up_within(300) is False

    def test_from_resolved_maps_fields(self):
        resolved = ResolvedSession(
            session_id="s9",
            user_id="u9",
            csrf_secret="cs",
            aal="aal1",
            step_up_at=None,
            role="admin",
            status="active",
            email="a@b.c",
            name="Admin",
            email_verified=True,
            expires_at="2099-01-01T00:00:00+00:00",
        )
        p = Principal.from_resolved(resolved)
        assert p.user_id == "u9"
        assert p.role == "admin"
        assert Capabilities.ADMIN_MANAGE in p.capabilities


# ---------------------------------------------------------------------------
# dependencies
# ---------------------------------------------------------------------------


class TestDependencies:
    def test_get_principal_raises_401_when_anonymous(self):
        with pytest.raises(HTTPException) as exc:
            get_principal(_fake_request(None))
        assert exc.value.status_code == 401

    def test_get_principal_returns_principal(self):
        p = _principal()
        assert get_principal(_fake_request(p)) is p

    async def test_require_capability_allows_with_cap(self):
        dep = require_capability(Capabilities.ADMIN_MANAGE)
        p = _principal(role="admin")
        result = await dep(_fake_request(p), p)
        assert result is p

    async def test_require_capability_403_without_cap(self, monkeypatch):
        # Avoid touching the audit DB in this unit test.
        monkeypatch.setattr(
            "app.auth.principal._audit_denied",
            lambda *a, **k: _async_noop(),
        )
        dep = require_capability(Capabilities.ADMIN_MANAGE)
        p = _principal(role="user")
        with pytest.raises(HTTPException) as exc:
            await dep(_fake_request(p), p)
        assert exc.value.status_code == 403

    def test_require_step_up_ok_within_window(self):
        recent = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
        p = _principal(step_up_at=recent)
        assert require_step_up(_fake_request(p), p) is p

    def test_require_step_up_401_outside_window(self):
        p = _principal(step_up_at=None)
        with pytest.raises(HTTPException) as exc:
            require_step_up(_fake_request(p), p)
        assert exc.value.status_code == 401
        assert exc.value.detail == "step_up_required"


async def _async_noop():
    return None


# ---------------------------------------------------------------------------
# cookies
# ---------------------------------------------------------------------------


class TestCookies:
    def test_set_session_cookies_are_hardened(self):
        response = Response()
        cfg = Settings(single_user_mode=True)
        set_session_cookies(
            response,
            raw_token="rawtok",
            session_id="s1",
            csrf_secret="cs",
            config=cfg,
        )
        cookies = response.headers.getlist("set-cookie")
        session_cookie = next(c for c in cookies if c.startswith("__Host-session="))
        csrf_cookie = next(c for c in cookies if c.startswith("csrf="))

        # Session cookie: HttpOnly + Secure + Path=/ + SameSite + no Domain.
        assert "HttpOnly" in session_cookie
        assert "Secure" in session_cookie
        assert "Path=/" in session_cookie
        assert "samesite=lax" in session_cookie.lower()
        assert "Domain=" not in session_cookie

        # CSRF cookie: readable by JS (not HttpOnly) and holds the derived value.
        assert "HttpOnly" not in csrf_cookie
        assert csrf_mod.derive_csrf_token("s1", "cs") in csrf_cookie

    def test_clear_session_cookies(self):
        response = Response()
        cfg = Settings(single_user_mode=True)
        clear_session_cookies(response, config=cfg)
        cookies = "\n".join(response.headers.getlist("set-cookie"))
        assert "__Host-session=" in cookies
        assert "csrf=" in cookies


# ---------------------------------------------------------------------------
# middleware + endpoint (integration-ish, via TestClient)
# ---------------------------------------------------------------------------


class _StubSessionService:
    def __init__(self, resolved: ResolvedSession) -> None:
        self._resolved = resolved

    async def resolve(self, raw_token):
        return self._resolved if raw_token == "good-token" else None


def _hosted_settings() -> Settings:
    return Settings(
        single_user_mode=False,
        session_secret="session-secret-value-1234",
        ip_hash_secret="ip-hash-secret-value-1234",
        app_encryption_key="app-encryption-key-value-1234",
        database_url="postgresql+asyncpg://user:pass@localhost/db",
        cookie_secure=False,  # allow TestClient over http
        # external_cron hosted mode now requires a job token; and keep hermetic
        # from the developer's real .env (google creds + localhost redirect).
        internal_job_token="internal-job-token-value-1234",
        google_client_id="",
        google_client_secret="",
        oauth_redirect_uri="",
        _env_file=None,
    )


class TestSecurityHeaders:
    def test_headers_present(self):
        app = FastAPI()
        app.add_middleware(SecurityHeadersMiddleware, config=Settings(single_user_mode=True))

        @app.get("/ping")
        async def ping():
            return PlainTextResponse("ok")

        client = TestClient(app)
        resp = client.get("/ping")
        assert resp.headers["X-Content-Type-Options"] == "nosniff"
        assert resp.headers["X-Frame-Options"] == "DENY"
        assert "frame-ancestors 'none'" in resp.headers["Content-Security-Policy"]
        assert "Referrer-Policy" in resp.headers


class TestAuthMiddlewareCsrf:
    @pytest.fixture
    def app_and_secret(self, monkeypatch):
        cfg = _hosted_settings()
        resolved = ResolvedSession(
            session_id="s1",
            user_id="u1",
            csrf_secret="csrf-secret-value",
            aal="aal1",
            step_up_at=None,
            role="user",
            status="active",
            email="u@e.c",
            name="U",
            email_verified=True,
            expires_at="2099-01-01T00:00:00+00:00",
        )
        monkeypatch.setattr(
            "app.auth.sessions.get_session_service",
            lambda: _StubSessionService(resolved),
        )
        app = FastAPI()
        app.add_middleware(AuthMiddleware, config=cfg)

        @app.get("/read")
        async def read():
            return {"ok": True}

        @app.post("/write")
        async def write():
            return {"ok": True}

        return app, resolved

    def test_safe_method_allowed_without_csrf(self, app_and_secret):
        app, _ = app_and_secret
        client = TestClient(app)
        resp = client.get("/read", cookies={"__Host-session": "good-token"})
        assert resp.status_code == 200

    def test_mutation_rejected_without_csrf_header(self, app_and_secret):
        app, _ = app_and_secret
        client = TestClient(app)
        resp = client.post("/write", cookies={"__Host-session": "good-token"})
        assert resp.status_code == 403
        assert resp.json()["detail"] == "csrf_failed"

    def test_mutation_allowed_with_valid_csrf_header(self, app_and_secret):
        app, resolved = app_and_secret
        token = csrf_mod.derive_csrf_token(resolved.session_id, resolved.csrf_secret)
        client = TestClient(app)
        resp = client.post(
            "/write",
            cookies={"__Host-session": "good-token"},
            headers={"X-CSRF-Token": token},
        )
        assert resp.status_code == 200

    def test_anonymous_mutation_passes_middleware(self, app_and_secret):
        # No session -> no principal -> CSRF gate does not apply here (route-level
        # auth would 401 in a real endpoint); middleware must not 403 anon.
        app, _ = app_and_secret
        client = TestClient(app)
        resp = client.post("/write")
        assert resp.status_code == 200


class TestCsrfEndpoint:
    def test_issues_token_cookie_and_body(self, monkeypatch):
        # Bind the endpoint's settings to a known secret.
        import app.auth.principal as principal_mod

        monkeypatch.setattr(
            principal_mod, "settings", Settings(
                single_user_mode=True,
                session_secret="session-secret-value-1234",
                cookie_secure=False,
            )
        )
        app = FastAPI()
        app.include_router(auth_csrf_router, prefix="/api/v1")
        client = TestClient(app)
        resp = client.get("/api/v1/auth/csrf")
        assert resp.status_code == 200
        token = resp.json()["csrfToken"]
        assert token
        # The token is a valid signed pre-session token and is set as a cookie.
        assert csrf_mod.verify_presession_token(token, "session-secret-value-1234")
        assert "csrf=" in (resp.headers.get("set-cookie") or "")

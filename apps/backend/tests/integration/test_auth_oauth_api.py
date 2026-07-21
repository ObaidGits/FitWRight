"""Integration tests for the OAuth start/callback endpoints (Task 7.1/7.2).

Exercises the real provider-generic routes end-to-end over an ASGI transport
against an isolated temp DB, using a **mock IdP provider** registered on the
allow-list so nothing touches Google. Covers: start (sets signed transient
cookie + redirects + carries next / rejects unknown+unconfigured / open-redirect
next), and callback (happy-path create+session, state mismatch, missing state
cookie, id_token verify failure, unverified email, safe-linking, replay of a
reused code/state, and transient-cookie clearing on success + failure).

Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest
from httpx import ASGITransport, AsyncClient

from app.auth.oauth import (
    OAUTH_TXN_COOKIE,
    OAuthTokens,
    OAuthUserInfo,
    registry as oauth_registry,
)
from app.auth.oauth.base import OAuthError, OAuthProvider
from app.auth.accounts import create_user
from app.auth.passwords import get_password_service
from app.config import settings as app_settings
from app.main import app

pytestmark = pytest.mark.integration

FRONTEND = app_settings.frontend_base_url.rstrip("/")


# ---------------------------------------------------------------------------
# Mock IdP provider - deterministic, records single-use codes for replay tests
# ---------------------------------------------------------------------------


class MockProvider(OAuthProvider):
    name = "mock"

    def __init__(self) -> None:
        self.email = "oauth-user@example.com"
        self.email_verified = True
        self.sub = "mock-sub-1"
        self.name_claim = "OAuth User"
        self.used_codes: set[str] = set()
        self.fail_exchange = False
        self.fail_verify = False

    def authorize_url(self, *, state, nonce, challenge, next=None):
        return f"https://idp.mock/authorize?state={state}&nonce={nonce}&code_challenge={challenge}"

    async def exchange(self, code, verifier):
        if self.fail_exchange:
            raise OAuthError("token_exchange_failed", "boom")
        if code in self.used_codes:
            # Single-use authorization code - replay is rejected (R4.6).
            raise OAuthError("code_replayed", "authorization code already used")
        self.used_codes.add(code)
        return OAuthTokens(id_token=f"idtoken-for-{code}", raw={"code": code})

    async def verify_id_token(self, id_token, nonce):
        if self.fail_verify:
            raise OAuthError("bad_signature", "boom")
        return OAuthUserInfo(
            sub=self.sub,
            email=self.email,
            email_verified=self.email_verified,
            name=self.name_claim,
        )


@pytest.fixture
def mock_provider():
    """Register a mock provider on the allow-list; restore the registry after."""
    provider = MockProvider()
    oauth_registry.register("mock", lambda: provider)
    try:
        yield provider
    finally:
        # Drop the mock and its cached instance so other tests are unaffected.
        oauth_registry._factories.pop("mock", None)
        oauth_registry.reset()


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="https://test")


def _cookie_str(resp, name: str) -> str | None:
    for raw in resp.headers.get_list("set-cookie"):
        if raw.startswith(f"{name}="):
            return raw
    return None


def _is_cleared(cookie: str | None) -> bool:
    """A Set-Cookie that deletes the cookie (empty value + expiry in the past)."""
    if cookie is None:
        return False
    lowered = cookie.lower()
    return "max-age=0" in lowered or "expires=thu, 01 jan 1970" in lowered


async def _start(client, provider="mock", next=None):
    params = {} if next is None else {"next": next}
    return await client.get(
        f"/api/v1/auth/oauth/{provider}/start", params=params, follow_redirects=False
    )


# ---------------------------------------------------------------------------
# start
# ---------------------------------------------------------------------------


class TestOAuthStart:
    async def test_start_sets_transient_cookie_and_redirects(self, auth_env, mock_provider):
        async with _client() as client:
            resp = await _start(client)
        assert resp.status_code == 302
        assert resp.headers["location"].startswith("https://idp.mock/authorize")
        txn_cookie = _cookie_str(resp, OAUTH_TXN_COOKIE)
        assert txn_cookie is not None
        # Signed transient cookie is httpOnly + hardened.
        assert "HttpOnly" in txn_cookie
        assert "Path=/" in txn_cookie
        assert "Max-Age=300" in txn_cookie

    async def test_start_carries_validated_next(self, auth_env, mock_provider):
        async with _client() as client:
            resp = await _start(client, next="/dashboard")
            # Complete the flow and confirm we land on the carried next.
            state = _extract_state(resp)
            cb = await client.get(
                "/api/v1/auth/oauth/mock/callback",
                params={"code": "c1", "state": state},
                follow_redirects=False,
            )
        assert cb.status_code == 302
        assert cb.headers["location"] == f"{FRONTEND}/dashboard"

    async def test_start_rejects_open_redirect_next(self, auth_env, mock_provider):
        async with _client() as client:
            resp = await _start(client, next="//evil.example/phish")
            state = _extract_state(resp)
            cb = await client.get(
                "/api/v1/auth/oauth/mock/callback",
                params={"code": "c1", "state": state},
                follow_redirects=False,
            )
        # The unsafe next is dropped -> falls back to /home (never off-site).
        assert cb.headers["location"] == f"{FRONTEND}/home"

    async def test_start_unknown_provider_404(self, auth_env):
        async with _client() as client:
            resp = await _start(client, provider="myspace")
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "unknown_provider"

    async def test_start_unconfigured_google_clean_error(self, auth_env, monkeypatch):
        # Google is on the allow-list but has no credentials locally -> clean error,
        # not a 500, and local zero-config boot is unaffected.
        monkeypatch.setattr(app_settings, "google_client_id", "")
        monkeypatch.setattr(app_settings, "google_client_secret", "")
        oauth_registry.reset()
        async with _client() as client:
            resp = await _start(client, provider="google")
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "oauth_not_configured"


def _extract_state(resp) -> str:
    """Pull the ``state`` back out of the mock authorize redirect."""
    location = resp.headers["location"]
    return parse_qs(urlparse(location).query)["state"][0]


# ---------------------------------------------------------------------------
# callback
# ---------------------------------------------------------------------------


class TestOAuthCallback:
    async def test_callback_happy_path_creates_user_and_session(self, auth_env, mock_provider):
        async with _client() as client:
            start = await _start(client)
            state = _extract_state(start)
            cb = await client.get(
                "/api/v1/auth/oauth/mock/callback",
                params={"code": "code-1", "state": state},
                follow_redirects=False,
            )
        assert cb.status_code == 302
        assert cb.headers["location"] == f"{FRONTEND}/home"
        # A remembered session cookie was issued and the transient cookie was cleared.
        session_cookie = _cookie_str(cb, "__Host-session")
        assert session_cookie is not None
        assert f"Max-Age={app_settings.remember_me_ttl}" in session_cookie
        assert _is_cleared(_cookie_str(cb, OAUTH_TXN_COOKIE))

        # The session actually authorizes and resolves to the created user.
        async with _client() as client2:
            token = None
            for raw in cb.headers.get_list("set-cookie"):
                if raw.startswith("__Host-session="):
                    token = raw.split("=", 1)[1].split(";", 1)[0]
            sess = await client2.get(
                "/api/v1/auth/session", headers={"Cookie": f"__Host-session={token}"}
            )
        assert sess.status_code == 200
        assert sess.json()["email"] == "oauth-user@example.com"
        assert sess.json()["emailVerified"] is True

    async def test_callback_state_mismatch_fails_no_session(self, auth_env, mock_provider):
        async with _client() as client:
            await _start(client)
            cb = await client.get(
                "/api/v1/auth/oauth/mock/callback",
                params={"code": "code-1", "state": "not-the-real-state"},
                follow_redirects=False,
            )
        assert cb.status_code == 302
        assert cb.headers["location"] == f"{FRONTEND}/login?error=oauth_failed"
        assert _cookie_str(cb, "__Host-session") is None
        assert _is_cleared(_cookie_str(cb, OAUTH_TXN_COOKIE))

    async def test_callback_missing_state_cookie_fails(self, auth_env, mock_provider):
        # No prior /start -> no transient cookie -> fail closed.
        async with _client() as client:
            cb = await client.get(
                "/api/v1/auth/oauth/mock/callback",
                params={"code": "code-1", "state": "whatever"},
                follow_redirects=False,
            )
        assert cb.headers["location"] == f"{FRONTEND}/login?error=oauth_failed"
        assert _cookie_str(cb, "__Host-session") is None

    async def test_callback_verify_failure_fails(self, auth_env, mock_provider):
        mock_provider.fail_verify = True
        async with _client() as client:
            start = await _start(client)
            state = _extract_state(start)
            cb = await client.get(
                "/api/v1/auth/oauth/mock/callback",
                params={"code": "code-1", "state": state},
                follow_redirects=False,
            )
        assert cb.headers["location"] == f"{FRONTEND}/login?error=oauth_failed"
        assert _cookie_str(cb, "__Host-session") is None
        assert _is_cleared(_cookie_str(cb, OAUTH_TXN_COOKIE))

    async def test_callback_exchange_failure_fails(self, auth_env, mock_provider):
        mock_provider.fail_exchange = True
        async with _client() as client:
            start = await _start(client)
            state = _extract_state(start)
            cb = await client.get(
                "/api/v1/auth/oauth/mock/callback",
                params={"code": "code-1", "state": state},
                follow_redirects=False,
            )
        assert cb.headers["location"] == f"{FRONTEND}/login?error=oauth_failed"
        assert _cookie_str(cb, "__Host-session") is None

    async def test_callback_unverified_email_rejected(self, auth_env, mock_provider):
        mock_provider.email_verified = False
        async with _client() as client:
            start = await _start(client)
            state = _extract_state(start)
            cb = await client.get(
                "/api/v1/auth/oauth/mock/callback",
                params={"code": "code-1", "state": state},
                follow_redirects=False,
            )
        assert cb.headers["location"] == f"{FRONTEND}/login?error=oauth_failed"
        assert _cookie_str(cb, "__Host-session") is None

    async def test_callback_missing_code_fails(self, auth_env, mock_provider):
        async with _client() as client:
            start = await _start(client)
            state = _extract_state(start)
            cb = await client.get(
                "/api/v1/auth/oauth/mock/callback",
                params={"state": state},
                follow_redirects=False,
            )
        assert cb.headers["location"] == f"{FRONTEND}/login?error=oauth_failed"

    async def test_callback_replay_reused_state_rejected(self, auth_env, mock_provider):
        # After a successful callback the transient cookie is cleared, so a replay
        # of the same state on the same client no longer has a matching cookie.
        async with _client() as client:
            start = await _start(client)
            state = _extract_state(start)
            first = await client.get(
                "/api/v1/auth/oauth/mock/callback",
                params={"code": "code-1", "state": state},
                follow_redirects=False,
            )
            assert first.headers["location"] == f"{FRONTEND}/home"
            # The client's cookie jar has cleared oauth_txn; replay fails.
            replay = await client.get(
                "/api/v1/auth/oauth/mock/callback",
                params={"code": "code-2", "state": state},
                follow_redirects=False,
            )
        assert replay.headers["location"] == f"{FRONTEND}/login?error=oauth_failed"
        assert _cookie_str(replay, "__Host-session") is None

    async def test_callback_reused_code_rejected(self, auth_env, mock_provider):
        # Even with a fresh transient cookie, a provider single-use code that was
        # already redeemed is rejected by the provider -> oauth_failed (no session).
        async with _client() as client:
            start1 = await _start(client)
            state1 = _extract_state(start1)
            ok = await client.get(
                "/api/v1/auth/oauth/mock/callback",
                params={"code": "dupe-code", "state": state1},
                follow_redirects=False,
            )
            assert ok.headers["location"] == f"{FRONTEND}/home"
            # New start (new transient cookie/state) but reuse the burnt code.
            start2 = await _start(client)
            state2 = _extract_state(start2)
            reused = await client.get(
                "/api/v1/auth/oauth/mock/callback",
                params={"code": "dupe-code", "state": state2},
                follow_redirects=False,
            )
        assert reused.headers["location"] == f"{FRONTEND}/login?error=oauth_failed"
        assert _cookie_str(reused, "__Host-session") is None

    async def test_callback_links_existing_verified_account(self, auth_env, mock_provider):
        # A pre-existing verified account with the same email is linked (not
        # duplicated); the OAuth sign-in resolves to that user.
        acct = await create_user(
            email="oauth-user@example.com",
            name="Existing",
            password_hash=get_password_service().hash_password("pw-abcdef-123456"),
            status="active",
            email_verified_at="2024-01-01T00:00:00+00:00",
            db=auth_env,
        )
        async with _client() as client:
            start = await _start(client)
            state = _extract_state(start)
            token = None
            cb = await client.get(
                "/api/v1/auth/oauth/mock/callback",
                params={"code": "code-1", "state": state},
                follow_redirects=False,
            )
            for raw in cb.headers.get_list("set-cookie"):
                if raw.startswith("__Host-session="):
                    token = raw.split("=", 1)[1].split(";", 1)[0]
        assert cb.headers["location"] == f"{FRONTEND}/home"
        async with _client() as client2:
            sess = await client2.get(
                "/api/v1/auth/session", headers={"Cookie": f"__Host-session={token}"}
            )
        assert sess.status_code == 200
        assert sess.json()["id"] == acct.id

    async def test_callback_refuses_unverified_password_account(self, auth_env, mock_provider):
        # A password account with an unverified email must NOT be hijacked (R4.4).
        await create_user(
            email="oauth-user@example.com",
            name="Pending",
            password_hash=get_password_service().hash_password("pw-abcdef-123456"),
            status="pending_verification",
            email_verified_at=None,
            db=auth_env,
        )
        async with _client() as client:
            start = await _start(client)
            state = _extract_state(start)
            cb = await client.get(
                "/api/v1/auth/oauth/mock/callback",
                params={"code": "code-1", "state": state},
                follow_redirects=False,
            )
        assert cb.headers["location"] == f"{FRONTEND}/login?error=oauth_failed"
        assert _cookie_str(cb, "__Host-session") is None

    async def test_callback_unknown_provider_404(self, auth_env):
        async with _client() as client:
            cb = await client.get(
                "/api/v1/auth/oauth/myspace/callback",
                params={"code": "c", "state": "s"},
                follow_redirects=False,
            )
        assert cb.status_code == 404
        assert cb.json()["error"]["code"] == "unknown_provider"

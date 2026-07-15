"""Failure & recovery tests for the auth stack (Task 11.1).

Verifies the design's `§Reliability` guarantees hold when a dependency is
degraded, deterministically (no real network / no real providers):

- **KVStore outage — asymmetric failure modes (R13.5).** Auth rate limiting
  fails **closed** (deny with ``Retry-After``) so an attacker cannot buy an
  unlimited window by knocking the store over; read-path session *resolution*
  fails **open**, falling back to the DB (the source of truth) so a cache
  outage never logs users out or blocks a scoped read.
- **JWKS fetch failure — graceful OAuth degradation (R4.2).** A warm JWKS cache
  keeps serving (stale-cache) when a refresh would fail; a cold fetch failure is
  normalized to a clean ``OAuthError`` and the callback collapses to
  ``oauth_failed`` (no session, no crash).
- **Email provider outage — uniform ack preserved (§Reliability).** A provider
  whose ``send`` raises must not 500 verify/reset requests: the failure is
  swallowed + logged and the same enumeration-safe acknowledgement is returned.

Requirements: 17.1, 17.2, 13.5, 4.2
"""

from __future__ import annotations

import logging

import pytest
from httpx import ASGITransport, AsyncClient

from app.auth.accounts import create_user
from app.auth.oauth import OAuthTokens, OAuthUserInfo, registry as oauth_registry
from app.auth.oauth.base import OAuthError, OAuthProvider
from app.auth.oauth.google import GoogleOAuthProvider, HttpxJwksClient
from app.auth.passwords import get_password_service
from app.auth.sessions import SessionService
from app.config import settings as app_settings
from app.main import app

pytestmark = pytest.mark.integration

STRONG_PW = "correct-horse-battery-staple-9"
FRONTEND = app_settings.frontend_base_url.rstrip("/")


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="https://test")


async def _csrf(client: AsyncClient) -> str:
    resp = await client.get("/api/v1/auth/csrf")
    assert resp.status_code == 200
    return resp.json()["csrfToken"]


async def _seed(db, email, *, status="active", email_verified_at="2024-01-01T00:00:00+00:00"):
    hashed = get_password_service().hash_password(STRONG_PW)
    return await create_user(
        email=email,
        name="Alice",
        password_hash=hashed,
        status=status,
        email_verified_at=email_verified_at,
        db=db,
    )


class _DeadCacheKV:
    """A KVStore whose cache ops (get/set/incr) fail, but delete is a no-op.

    Models a KVStore outage from the read path's perspective: every cache read
    raises, forcing the DB fallback.
    """

    async def get(self, *a, **k):
        raise RuntimeError("kv down")

    async def set(self, *a, **k):
        raise RuntimeError("kv down")

    async def incr(self, *a, **k):
        raise RuntimeError("kv down")

    async def delete(self, *a, **k):
        return None


# ---------------------------------------------------------------------------
# KVStore outage — rate limit fails CLOSED, resolution fails OPEN
# ---------------------------------------------------------------------------


class TestKVStoreOutage:
    async def test_auth_rate_limit_fails_closed(self, auth_env):
        """Login is denied (429 + Retry-After) when the rate-limit store is down."""
        from app.auth.ratelimit import get_rate_limiter

        get_rate_limiter()._kv = _DeadCacheKV()
        await _seed(auth_env, "closed@example.com")
        async with _client() as client:
            csrf = await _csrf(client)
            resp = await client.post(
                "/api/v1/auth/login",
                json={"email": "closed@example.com", "password": STRONG_PW},
                headers={"X-CSRF-Token": csrf},
            )
        assert resp.status_code == 429
        assert resp.headers.get("Retry-After") is not None

    async def test_session_resolution_falls_back_to_db(self, auth_env):
        """A dead cache does not break resolution — it falls back to the DB (R17.1)."""
        record = await _seed(auth_env, "fallback@example.com")
        # A live session is created against the real DB…
        real_kv_service = SessionService(
            auth_env.session_factory,
            _DeadCacheKV(),
            settings=app_settings,
        )
        raw_token, _info = await real_kv_service.create_session(record.id)

        # …and resolves fine even though every cache read/write raises: the DB is
        # the source of truth, so the read path fails OPEN (never logs out).
        resolved = await real_kv_service.resolve(raw_token)
        assert resolved is not None
        assert resolved.user_id == record.id
        # A second resolve still works (the failed cache write did not poison it).
        again = await real_kv_service.resolve(raw_token)
        assert again is not None and again.user_id == record.id

    async def test_scoped_read_still_serves_when_cache_down(self, auth_env, monkeypatch):
        """An owned-resource read fails OPEN under a KVStore outage (single-user)."""
        from app.auth.ratelimit import reset_rate_limiter
        from app.auth.sessions import reset_session_service
        from app.platform import get_container

        # Force the composition root to hand out the dead store, then rebuild the
        # services so they pick it up (KVStore is owned by the container — Phase 3).
        get_container().override("kvstore", _DeadCacheKV())
        reset_session_service()
        reset_rate_limiter()

        async with _client() as client:
            # Local single-user mode → owner is resolved without touching the KV;
            # the scoped list read still succeeds despite the cache being down.
            resp = await client.get("/api/v1/applications")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# JWKS fetch failure — stale cache serves; cold failure degrades cleanly
# ---------------------------------------------------------------------------


def _make_key(kid: str):
    from authlib.jose import JsonWebKey

    key = JsonWebKey.generate_key("RSA", 2048, is_private=True)
    priv = key.as_dict(is_private=True)
    priv["kid"] = kid
    pub = key.as_dict(is_private=False)
    pub["kid"] = kid
    return priv, pub


def _sign_id_token(priv: dict, claims: dict, *, kid: str) -> str:
    from authlib.jose import jwt as jose_jwt

    token = jose_jwt.encode({"alg": "RS256", "kid": kid}, claims, priv)
    return token.decode() if isinstance(token, bytes) else token


_CLIENT_ID = "test-client-id.apps.googleusercontent.com"


def _claims(**over) -> dict:
    base = {
        "iss": "https://accounts.google.com",
        "aud": _CLIENT_ID,
        "sub": "google-sub-123",
        "email": "user@example.com",
        "email_verified": True,
        "name": "Test User",
        "nonce": "the-nonce",
        "iat": 1_000,
        "exp": 2_000,
    }
    base.update(over)
    return base


class _StaleCacheJwksClient(HttpxJwksClient):
    """A JWKS client whose network ``_fetch`` always fails.

    Pre-populate ``_cache`` to model a *warm* cache that must keep serving while
    the upstream JWKS endpoint is unreachable.
    """

    def __init__(self, cache):
        super().__init__()
        self._cache = cache
        self.fetch_attempts = 0

    async def _fetch(self):
        self.fetch_attempts += 1
        raise OAuthError("jwks_fetch_failed", "jwks endpoint unreachable")


class TestJwksFailure:
    async def test_warm_cache_serves_when_fetch_fails(self):
        """A verifiable id_token still verifies from the stale cache (R4.2)."""
        priv, pub = _make_key("k1")
        token = _sign_id_token(priv, _claims(), kid="k1")
        jwks_client = _StaleCacheJwksClient({"keys": [pub]})
        provider = GoogleOAuthProvider(
            client_id=_CLIENT_ID,
            client_secret="secret",
            redirect_uri="https://app.example.com/api/v1/auth/oauth/google/callback",
            jwks_client=jwks_client,
            clock=lambda: 1_500.0,
        )
        info = await provider.verify_id_token(token, "the-nonce")
        assert info.email == "user@example.com"
        # The known kid was found in cache — no (failing) network refresh needed.
        assert jwks_client.fetch_attempts == 0

    async def test_cold_fetch_failure_degrades_cleanly(self):
        """An unknown kid + failing refresh yields a clean OAuthError, not a crash."""
        priv, pub = _make_key("k-new")
        token = _sign_id_token(priv, _claims(), kid="k-new")
        # Cache holds a DIFFERENT kid, so the unknown kid forces a refresh, which
        # fails — the provider must normalize this to an OAuthError.
        _stale_priv, stale_pub = _make_key("k-old")
        jwks_client = _StaleCacheJwksClient({"keys": [stale_pub]})
        provider = GoogleOAuthProvider(
            client_id=_CLIENT_ID,
            client_secret="secret",
            redirect_uri="https://app.example.com/api/v1/auth/oauth/google/callback",
            jwks_client=jwks_client,
            clock=lambda: 1_500.0,
        )
        with pytest.raises(OAuthError):
            await provider.verify_id_token(token, "the-nonce")
        assert jwks_client.fetch_attempts >= 1

    async def test_callback_collapses_jwks_failure_to_oauth_failed(self, auth_env):
        """At the endpoint, a JWKS/verify failure → oauth_failed (no session, no 500)."""

        class _JwksDownProvider(OAuthProvider):
            name = "jwksdown"

            def authorize_url(self, *, state, nonce, challenge, next=None):
                return f"https://idp.mock/authorize?state={state}"

            async def exchange(self, code, verifier):
                return OAuthTokens(id_token="idtoken", raw={})

            async def verify_id_token(self, id_token, nonce):
                # Signature can't be verified because JWKS is unreachable.
                raise OAuthError("jwks_fetch_failed", "jwks endpoint unreachable")

        provider = _JwksDownProvider()
        oauth_registry.register("jwksdown", lambda: provider)
        try:
            async with _client() as client:
                start = await client.get(
                    "/api/v1/auth/oauth/jwksdown/start", follow_redirects=False
                )
                from urllib.parse import parse_qs, urlparse

                state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
                cb = await client.get(
                    "/api/v1/auth/oauth/jwksdown/callback",
                    params={"code": "c1", "state": state},
                    follow_redirects=False,
                )
            assert cb.status_code == 302
            assert cb.headers["location"] == f"{FRONTEND}/login?error=oauth_failed"
            # No session was minted on the failure path.
            assert not any(
                raw.startswith("__Host-session=")
                for raw in cb.headers.get_list("set-cookie")
            )
        finally:
            oauth_registry._factories.pop("jwksdown", None)
            oauth_registry.reset()


# ---------------------------------------------------------------------------
# Email provider outage — uniform ack preserved, never 500 / leaks
# ---------------------------------------------------------------------------


class _DeadEmailSender:
    """An EmailSender whose delivery always raises (provider outage)."""

    async def send(self, message) -> None:
        raise RuntimeError("smtp connection refused")


class TestEmailProviderDown:
    async def test_forgot_password_returns_uniform_ack_when_email_down(
        self, auth_env, monkeypatch, caplog
    ):
        import app.routers.auth as auth_router

        record = await _seed(auth_env, "mailer-down@example.com")
        monkeypatch.setattr(auth_router, "get_email_sender", lambda: _DeadEmailSender())

        with caplog.at_level(logging.WARNING):
            async with _client() as client:
                csrf = await _csrf(client)
                resp = await client.post(
                    "/api/v1/auth/password/forgot",
                    json={"email": record.email},
                    headers={"X-CSRF-Token": csrf},
                )
        # Uniform ack (200), NOT a 500 — the send failure is swallowed + logged.
        assert resp.status_code == 200
        assert any("email delivery failed" in r.message.lower() for r in caplog.records)

    async def test_verify_request_returns_uniform_ack_when_email_down(
        self, auth_env, monkeypatch
    ):
        import app.routers.auth as auth_router

        record = await _seed(
            auth_env,
            "verify-down@example.com",
            status="pending_verification",
            email_verified_at=None,
        )
        # This account is unverified, so the send branch is exercised.
        monkeypatch.setattr(auth_router, "get_email_sender", lambda: _DeadEmailSender())

        async with _client() as client:
            csrf = await _csrf(client)
            resp = await client.post(
                "/api/v1/auth/verify/request",
                json={"email": record.email},
                headers={"X-CSRF-Token": csrf},
            )
        # Still the uniform acknowledgement despite the provider being down.
        assert resp.status_code == 200

    async def test_uniform_ack_identical_for_known_and_unknown_when_email_down(
        self, auth_env, monkeypatch
    ):
        """Even with the mailer down, forgot-password can't be used to enumerate."""
        import app.routers.auth as auth_router

        await _seed(auth_env, "known@example.com")
        monkeypatch.setattr(auth_router, "get_email_sender", lambda: _DeadEmailSender())

        async with _client() as client:
            csrf = await _csrf(client)
            known = await client.post(
                "/api/v1/auth/password/forgot",
                json={"email": "known@example.com"},
                headers={"X-CSRF-Token": csrf},
            )
            unknown = await client.post(
                "/api/v1/auth/password/forgot",
                json={"email": "ghost@example.com"},
                headers={"X-CSRF-Token": csrf},
            )
        assert known.status_code == unknown.status_code == 200
        assert known.json() == unknown.json()

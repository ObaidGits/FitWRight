"""Unit tests for the provider-abstracted OAuth building blocks (Task 7.1/7.2).

Covers the pieces that must be correct in isolation, with **no live Google
calls**: PKCE + signed transient state cookies, the provider registry/allow-list,
and the Google id_token verification path driven by a mock JWKS + a fixed clock
(happy path, nonce/aud/issuer/expiry/rotation/unverified-email).
"""

from __future__ import annotations

import base64
import json

import pytest

from app.auth.oauth import (
    OAUTH_TXN_TTL_SECONDS,
    GoogleOAuthProvider,
    OAuthError,
    OAuthTransaction,
    ProviderNotConfigured,
    ProviderRegistry,
    UnknownProvider,
    deserialize_transaction,
    generate_pkce_verifier,
    pkce_challenge,
    serialize_transaction,
)
from app.config import Settings

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# PKCE
# ---------------------------------------------------------------------------


class TestPkce:
    def test_challenge_is_s256_base64url_unpadded(self):
        import hashlib

        verifier = "test-verifier-value-1234567890"
        challenge = pkce_challenge(verifier)
        expected = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        assert challenge == expected
        assert "=" not in challenge

    def test_verifier_length_within_rfc7636_window(self):
        for _ in range(20):
            v = generate_pkce_verifier()
            assert 43 <= len(v) <= 128


# ---------------------------------------------------------------------------
# Signed transient state cookie
# ---------------------------------------------------------------------------


def _settings(**over) -> Settings:
    base = dict(
        single_user_mode=True,
        session_secret="unit-test-session-secret-abc",
        session_secret_prev="",
        ip_hash_secret="unit-test-ip-hash-secret-abc",
    )
    base.update(over)
    return Settings(**base)


class TestTransientState:
    def test_round_trip(self):
        cfg = _settings()
        txn = OAuthTransaction(
            provider="google", state="s1", nonce="n1", verifier="v1", next="/home"
        )
        blob = serialize_transaction(txn, config=cfg)
        back = deserialize_transaction(blob, config=cfg)
        assert back == txn

    def test_tampered_blob_is_rejected(self):
        cfg = _settings()
        txn = OAuthTransaction(provider="google", state="s", nonce="n", verifier="v")
        blob = serialize_transaction(txn, config=cfg)
        assert deserialize_transaction(blob + "x", config=cfg) is None

    def test_wrong_secret_is_rejected(self):
        cfg = _settings()
        other = _settings(session_secret="a-totally-different-secret-xyz")
        blob = serialize_transaction(
            OAuthTransaction(provider="google", state="s", nonce="n", verifier="v"),
            config=cfg,
        )
        assert deserialize_transaction(blob, config=other) is None

    def test_previous_secret_rotation_window(self):
        old = _settings(session_secret="old-secret-value-abcdefgh")
        rotated = _settings(
            session_secret="new-secret-value-abcdefgh",
            session_secret_prev="old-secret-value-abcdefgh",
        )
        blob = serialize_transaction(
            OAuthTransaction(provider="google", state="s", nonce="n", verifier="v"),
            config=old,
        )
        # A cookie signed with the previous secret still validates during rollover.
        assert deserialize_transaction(blob, config=rotated) is not None

    def test_expired_blob_is_rejected(self, monkeypatch):
        cfg = _settings()
        txn = OAuthTransaction(provider="google", state="s", nonce="n", verifier="v")
        blob = serialize_transaction(txn, config=cfg)

        # Fast-forward itsdangerous' clock past the TTL on the read side.
        import itsdangerous.timed as timed_mod

        real_time = timed_mod.time.time

        class _FakeTime:
            @staticmethod
            def time():
                return real_time() + OAUTH_TXN_TTL_SECONDS + 10

        monkeypatch.setattr(timed_mod, "time", _FakeTime)
        assert deserialize_transaction(blob, config=cfg) is None

    def test_none_and_empty_are_none(self):
        assert deserialize_transaction(None) is None
        assert deserialize_transaction("") is None


# ---------------------------------------------------------------------------
# Registry / allow-list
# ---------------------------------------------------------------------------


class _DummyProvider:
    name = "dummy"


class TestRegistry:
    def test_unknown_provider_raises(self):
        reg = ProviderRegistry()
        assert not reg.is_known("nope")
        with pytest.raises(UnknownProvider):
            reg.resolve("nope")

    def test_resolves_and_caches_instance(self):
        reg = ProviderRegistry()
        built = []

        def factory():
            inst = _DummyProvider()
            built.append(inst)
            return inst

        reg.register("dummy", factory)
        a = reg.resolve("dummy")
        b = reg.resolve("dummy")
        assert a is b  # cached
        assert len(built) == 1

    def test_not_configured_propagates(self):
        reg = ProviderRegistry()

        def factory():
            raise ProviderNotConfigured("dummy")

        reg.register("dummy", factory)
        with pytest.raises(ProviderNotConfigured):
            reg.resolve("dummy")

    def test_oauth_error_from_factory_becomes_not_configured(self):
        reg = ProviderRegistry()

        def factory():
            raise OAuthError("provider_not_configured", "missing creds")

        reg.register("dummy", factory)
        with pytest.raises(ProviderNotConfigured):
            reg.resolve("dummy")

    def test_reset_clears_cache(self):
        reg = ProviderRegistry()
        reg.register("dummy", _DummyProvider)
        first = reg.resolve("dummy")
        reg.reset()
        assert reg.resolve("dummy") is not first


# ---------------------------------------------------------------------------
# Google id_token verification (mock JWKS + fixed clock - no live calls)
# ---------------------------------------------------------------------------

_CLIENT_ID = "test-client-id.apps.googleusercontent.com"


class _MockJwksClient:
    """Serves a fixed JWKS; counts fetches so rotation can be asserted."""

    def __init__(self, keysets):
        # ``keysets`` is a list of JWKS dicts returned successively on refresh.
        self._keysets = keysets
        self.fetches = 0

    async def get_jwks(self, *, force_refresh: bool = False):
        if force_refresh:
            self.fetches += 1
            idx = min(self.fetches, len(self._keysets) - 1)
            return self._keysets[idx]
        return self._keysets[0]


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

    header = {"alg": "RS256", "kid": kid}
    token = jose_jwt.encode(header, claims, priv)
    return token.decode() if isinstance(token, bytes) else token


def _base_claims(**over) -> dict:
    claims = {
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
    claims.update(over)
    return claims


def _provider(jwks_client, *, now: float = 1_500.0) -> GoogleOAuthProvider:
    return GoogleOAuthProvider(
        client_id=_CLIENT_ID,
        client_secret="secret",
        redirect_uri="https://app.example.com/api/v1/auth/oauth/google/callback",
        jwks_client=jwks_client,
        clock=lambda: now,
    )


class TestGoogleVerifyIdToken:
    async def test_happy_path(self):
        priv, pub = _make_key("k1")
        token = _sign_id_token(priv, _base_claims(), kid="k1")
        provider = _provider(_MockJwksClient([{"keys": [pub]}]))
        info = await provider.verify_id_token(token, "the-nonce")
        assert info.sub == "google-sub-123"
        assert info.email == "user@example.com"
        assert info.email_verified is True
        assert info.name == "Test User"

    async def test_nonce_mismatch_rejected(self):
        priv, pub = _make_key("k1")
        token = _sign_id_token(priv, _base_claims(), kid="k1")
        provider = _provider(_MockJwksClient([{"keys": [pub]}]))
        with pytest.raises(OAuthError) as exc:
            await provider.verify_id_token(token, "wrong-nonce")
        assert exc.value.reason == "nonce_mismatch"

    async def test_bad_audience_rejected(self):
        priv, pub = _make_key("k1")
        token = _sign_id_token(priv, _base_claims(aud="someone-else"), kid="k1")
        provider = _provider(_MockJwksClient([{"keys": [pub]}]))
        with pytest.raises(OAuthError) as exc:
            await provider.verify_id_token(token, "the-nonce")
        assert exc.value.reason == "bad_audience"

    async def test_bad_issuer_rejected(self):
        priv, pub = _make_key("k1")
        token = _sign_id_token(priv, _base_claims(iss="https://evil.example"), kid="k1")
        provider = _provider(_MockJwksClient([{"keys": [pub]}]))
        with pytest.raises(OAuthError) as exc:
            await provider.verify_id_token(token, "the-nonce")
        assert exc.value.reason == "bad_issuer"

    async def test_expired_rejected(self):
        priv, pub = _make_key("k1")
        token = _sign_id_token(priv, _base_claims(exp=1_000), kid="k1")
        # now (1500) is well past exp (1000) + leeway (60).
        provider = _provider(_MockJwksClient([{"keys": [pub]}]), now=1_500.0)
        with pytest.raises(OAuthError) as exc:
            await provider.verify_id_token(token, "the-nonce")
        assert exc.value.reason == "expired"

    async def test_clock_skew_within_leeway_allowed(self):
        priv, pub = _make_key("k1")
        # exp is 1000; now is 1050 which is within the 60s default leeway.
        token = _sign_id_token(priv, _base_claims(exp=1_000, iat=990), kid="k1")
        provider = _provider(_MockJwksClient([{"keys": [pub]}]), now=1_050.0)
        info = await provider.verify_id_token(token, "the-nonce")
        assert info.sub == "google-sub-123"

    async def test_unverified_email_still_returns_flag(self):
        # verify_id_token itself does not enforce email_verified (the callback
        # does); it must faithfully report the flag.
        priv, pub = _make_key("k1")
        token = _sign_id_token(priv, _base_claims(email_verified=False), kid="k1")
        provider = _provider(_MockJwksClient([{"keys": [pub]}]))
        info = await provider.verify_id_token(token, "the-nonce")
        assert info.email_verified is False

    async def test_wrong_signing_key_rejected(self):
        priv_signing, _ = _make_key("k1")
        _, pub_other = _make_key("k1")  # same kid, different key material
        token = _sign_id_token(priv_signing, _base_claims(), kid="k1")
        provider = _provider(_MockJwksClient([{"keys": [pub_other]}]))
        with pytest.raises(OAuthError) as exc:
            await provider.verify_id_token(token, "the-nonce")
        assert exc.value.reason == "bad_signature"

    async def test_key_rotation_refetches_jwks(self):
        # Token signed by k2; the initially-cached JWKS only knows k1, so the
        # provider must refetch (force_refresh) to pick up the rotated key.
        _, pub_k1 = _make_key("k1")
        priv_k2, pub_k2 = _make_key("k2")
        token = _sign_id_token(priv_k2, _base_claims(), kid="k2")
        jwks_client = _MockJwksClient([{"keys": [pub_k1]}, {"keys": [pub_k1, pub_k2]}])
        provider = _provider(jwks_client)
        info = await provider.verify_id_token(token, "the-nonce")
        assert info.sub == "google-sub-123"
        assert jwks_client.fetches == 1  # exactly one forced refresh

    async def test_unknown_kid_after_refresh_rejected(self):
        priv_k2, _ = _make_key("k2")
        _, pub_k1 = _make_key("k1")
        token = _sign_id_token(priv_k2, _base_claims(), kid="k2")
        jwks_client = _MockJwksClient([{"keys": [pub_k1]}])  # never learns k2
        provider = _provider(jwks_client)
        with pytest.raises(OAuthError) as exc:
            await provider.verify_id_token(token, "the-nonce")
        assert exc.value.reason == "unknown_signing_key"


class TestGoogleAuthorizeAndExchange:
    def test_authorize_url_has_pkce_and_state(self):
        provider = _provider(_MockJwksClient([{"keys": []}]))
        url = provider.authorize_url(
            state="st", nonce="no", challenge="ch", next="/home"
        )
        assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
        assert "response_type=code" in url
        assert "code_challenge=ch" in url
        assert "code_challenge_method=S256" in url
        assert "state=st" in url
        assert "nonce=no" in url
        # next is carried in the transient cookie, never the authorize URL (SSRF-safe).
        assert "next" not in url

    async def test_exchange_uses_verifier_and_returns_id_token(self):
        captured = {}

        class _TokenClient:
            async def post_form(self, url, data):
                captured["url"] = url
                captured["data"] = data
                return {"id_token": "the.id.token", "access_token": "at"}

        provider = GoogleOAuthProvider(
            client_id=_CLIENT_ID,
            client_secret="secret",
            redirect_uri="https://app.example.com/cb",
            token_client=_TokenClient(),
            jwks_client=_MockJwksClient([{"keys": []}]),
        )
        tokens = await provider.exchange("the-code", "the-verifier")
        assert tokens.id_token == "the.id.token"
        assert captured["data"]["code"] == "the-code"
        assert captured["data"]["code_verifier"] == "the-verifier"
        assert captured["data"]["grant_type"] == "authorization_code"

    async def test_exchange_error_response_raises(self):
        class _TokenClient:
            async def post_form(self, url, data):
                return {"error": "invalid_grant"}

        provider = GoogleOAuthProvider(
            client_id=_CLIENT_ID,
            client_secret="secret",
            redirect_uri="https://app.example.com/cb",
            token_client=_TokenClient(),
            jwks_client=_MockJwksClient([{"keys": []}]),
        )
        with pytest.raises(OAuthError):
            await provider.exchange("bad-code", "verifier")

    def test_missing_config_raises(self):
        with pytest.raises(OAuthError):
            GoogleOAuthProvider(client_id="", client_secret="s", redirect_uri="r")

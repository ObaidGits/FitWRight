"""Integration tests for email verification & password reset (Task 5).

Exercises the real routers end-to-end over an ASGI transport against an isolated
temp database:

- ``POST /auth/verify/request`` - uniform/enumeration-safe response, per-IP rate
  limiting, and prior-token invalidation on re-issue.
- ``POST /auth/verify/confirm`` - single-use, expired, invalid, and the
  ``pending_verification`` -> ``active`` state transition.
- The sensitive-action gate (provider-cost generation) - 403 when the caller's
  email is unverified vs. allowed once verified.
- ``POST /auth/password/forgot`` - uniform for existing/non-existent emails.
- ``POST /auth/password/reset`` - single-use, policy/breach reject, revoke ALL
  sessions, fresh session, and OAuth-only set-password.

Also asserts the emailed token is single-use and that only its ``sha256`` is
persisted (never the raw token).

Requirements: 5.1, 5.2, 5.3, 5.5, 5.6, 6.1, 6.2, 6.3, 6.4, 6.5
"""

from __future__ import annotations

import re
from urllib.parse import unquote

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.auth import hash_token_value
from app.auth.accounts import create_user, get_by_id
from app.auth.email import EmailMessage, EmailSender
from app.auth.passwords import get_password_service
from app.config import settings as app_settings
from app.main import app
from app.models import EmailVerificationToken
from app.schemas.auth import SAFE_USER_FIELDS

from tests.integration.test_auth_api import (
    STRONG_PW,
    _client,
    _login,
    _seed_active_user,
)

pytestmark = pytest.mark.integration

NEW_PW = "brand-new-battery-horse-42"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


class _CapturingSender(EmailSender):
    """Test double that records every message instead of delivering it."""

    def __init__(self) -> None:
        self.messages: list[EmailMessage] = []

    async def send(self, message: EmailMessage) -> None:
        self.messages.append(message)

    @property
    def last(self) -> EmailMessage:
        assert self.messages, "no email was sent"
        return self.messages[-1]


def _install_sender(monkeypatch) -> _CapturingSender:
    """Bind a capturing EmailSender via the composition root (Phase 3 owner of adapters).

    ``monkeypatch`` is retained in the signature for call-site compatibility; the
    override is cleared between tests by the conftest ``reset_container()``.
    """
    from app.platform import get_container

    sender = _CapturingSender()
    get_container().override("email_sender", sender)
    return sender


def _token_from(message: EmailMessage) -> str:
    """Extract the raw token from an email link (``...?token=<raw>``)."""
    match = re.search(r"token=([^\s]+)", message.text_body)
    assert match, f"no token in email body: {message.text_body!r}"
    return unquote(match.group(1))


async def _seed_pending_user(db, email: str, *, name: str = "Pat"):
    """Create a ``pending_verification`` (unverified) user with a real password."""
    hashed = get_password_service().hash_password(STRONG_PW)
    return await create_user(
        email=email,
        name=name,
        password_hash=hashed,
        status="pending_verification",
        email_verified_at=None,
        db=db,
    )


async def _seed_active_unverified(db, email: str, *, name: str = "Uma"):
    """Create an ``active`` but email-unverified user (the gate's target state)."""
    hashed = get_password_service().hash_password(STRONG_PW)
    return await create_user(
        email=email,
        name=name,
        password_hash=hashed,
        status="active",
        email_verified_at=None,
        db=db,
    )


async def _verify_request(client: AsyncClient, *, email: str | None = None):
    body = {} if email is None else {"email": email}
    return await client.post("/api/v1/auth/verify/request", json=body)


async def _stored_tokens(db, model):
    async with db.session_factory() as session:
        return (await session.execute(select(model))).scalars().all()


# ---------------------------------------------------------------------------
# signup -> verification email is actually sent (regression: previously the
# signup handler created a pending_verification user but never issued a token
# or sent an email, so "check your inbox" was a lie until the user resent).
# ---------------------------------------------------------------------------


class TestSignupSendsVerificationEmail:
    async def test_signup_dispatches_a_working_verification_link(
        self, auth_env, monkeypatch
    ):
        from tests.integration.test_auth_api import _signup

        # Hosted mode -> email verification on.
        monkeypatch.setattr(app_settings, "single_user_mode", False)
        sender = _install_sender(monkeypatch)

        async with _client() as client:
            resp = await _signup(client, "welcome@example.com")
            assert resp.status_code == 200
            assert resp.json() == {"status": "pending_verification"}

            # The signup itself sent a verification email (post-response
            # background task) carrying a real, single-use token - the whole
            # point of the fix.
            assert sender.messages, "signup did not send a verification email"
            msg = sender.last
            assert msg.to == "welcome@example.com"
            assert msg.html_body, "verification email must have an HTML body"
            assert "verify" in msg.subject.lower()
            token = _token_from(msg)

            # And the emailed token verifies the account (pending -> active).
            confirm = await client.post(
                "/api/v1/auth/verify/confirm", json={"token": token}
            )
        assert confirm.status_code == 200
        assert confirm.json()["status"] == "verified"

    async def test_signup_existing_email_sends_no_email(self, auth_env, monkeypatch):
        """The existing-email branch stays silent (enumeration-safe): identical
        pending response, but no verification email is dispatched to a stranger."""
        from tests.integration.test_auth_api import _signup

        monkeypatch.setattr(app_settings, "single_user_mode", False)
        await _seed_active_user(auth_env, "taken@example.com")
        sender = _install_sender(monkeypatch)

        async with _client() as client:
            resp = await _signup(client, "taken@example.com")
        assert resp.status_code == 200
        assert resp.json() == {"status": "pending_verification"}
        # No email to the already-registered address (would leak existence).
        assert sender.messages == []


class TestLoginBlockedUntilVerified:
    async def test_login_pending_verification_is_blocked_with_verify_message(
        self, auth_env, monkeypatch
    ):
        """A correct password for a still-unverified account must NOT create a
        session and must return the verify-your-email guidance (not 'disabled')."""
        from tests.integration.test_auth_api import _csrf

        await _seed_pending_user(auth_env, "unverified-login@example.com")

        async with _client() as client:
            token = await _csrf(client)
            resp = await client.post(
                "/api/v1/auth/login",
                json={"email": "unverified-login@example.com", "password": STRONG_PW},
                headers={"X-CSRF-Token": token},
            )
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "email_unverified"
        # No session cookie was set for the unverified account.
        assert "__Host-session" not in resp.headers.get("set-cookie", "")


# ---------------------------------------------------------------------------
# verify/request
# ---------------------------------------------------------------------------


class TestVerifyRequest:
    async def test_uniform_for_existing_and_unknown_email(self, auth_env, monkeypatch):
        """Same shape/status whether or not the address is registered (Property 4)."""
        _install_sender(monkeypatch)
        await _seed_pending_user(auth_env, "known-vr@example.com")

        async with _client() as client:
            existing = await _verify_request(client, email="known-vr@example.com")
        async with _client() as client:
            unknown = await _verify_request(client, email="nobody-vr@example.com")

        assert existing.status_code == unknown.status_code == 200
        assert existing.json() == unknown.json() == {"status": "ok"}

    async def test_issues_token_and_emails_only_the_hash_is_stored(self, auth_env, monkeypatch):
        sender = _install_sender(monkeypatch)
        record = await _seed_pending_user(auth_env, "hash-vr@example.com")

        async with _client() as client:
            resp = await _verify_request(client, email="hash-vr@example.com")
        assert resp.status_code == 200

        # An email was sent carrying the raw token in the link.
        raw = _token_from(sender.last)
        assert sender.last.to == "hash-vr@example.com"

        # Only sha256(raw) is persisted - never the raw token.
        rows = await _stored_tokens(auth_env, EmailVerificationToken)
        assert len(rows) == 1
        assert rows[0].token_hash == hash_token_value(raw)
        assert rows[0].token_hash != raw
        assert rows[0].user_id == record.id

    async def test_reissue_invalidates_prior_unused_tokens(self, auth_env, monkeypatch):
        sender = _install_sender(monkeypatch)
        await _seed_pending_user(auth_env, "reissue-vr@example.com")

        async with _client() as client:
            await _verify_request(client, email="reissue-vr@example.com")
            first_token = _token_from(sender.last)
            await _verify_request(client, email="reissue-vr@example.com")
            second_token = _token_from(sender.last)
        assert first_token != second_token

        # The superseded first token no longer verifies; the newest one does.
        async with _client() as client:
            stale = await client.post(
                "/api/v1/auth/verify/confirm", json={"token": first_token}
            )
        assert stale.status_code == 400
        assert stale.json()["error"]["code"] == "invalid_token"

        async with _client() as client:
            fresh = await client.post(
                "/api/v1/auth/verify/confirm", json={"token": second_token}
            )
        assert fresh.status_code == 200

    async def test_rate_limited_per_ip(self, auth_env, monkeypatch):
        _install_sender(monkeypatch)
        # The default "verify" rule is 5 events / 300s per IP.
        statuses: list[int] = []
        async with _client() as client:
            for _ in range(7):
                resp = await _verify_request(client, email="rl-vr@example.com")
                statuses.append(resp.status_code)
                if resp.status_code == 429:
                    break
        assert 429 in statuses
        # The 429 must carry a Retry-After header.
        async with _client() as client:
            for _ in range(7):
                resp = await _verify_request(client, email="rl-vr@example.com")
                if resp.status_code == 429:
                    assert resp.headers.get("Retry-After") is not None
                    break


# ---------------------------------------------------------------------------
# verify/confirm
# ---------------------------------------------------------------------------


class TestVerifyConfirm:
    async def test_confirm_activates_pending_user(self, auth_env, monkeypatch):
        sender = _install_sender(monkeypatch)
        record = await _seed_pending_user(auth_env, "confirm-vr@example.com")

        async with _client() as client:
            await _verify_request(client, email="confirm-vr@example.com")
            token = _token_from(sender.last)
            resp = await client.post("/api/v1/auth/verify/confirm", json={"token": token})
        assert resp.status_code == 200
        assert resp.json()["status"] == "verified"

        after = await get_by_id(record.id, db=auth_env)
        assert after.status == "active"
        assert after.email_verified is True

    async def test_confirm_is_single_use(self, auth_env, monkeypatch):
        sender = _install_sender(monkeypatch)
        await _seed_pending_user(auth_env, "single-vr@example.com")

        async with _client() as client:
            await _verify_request(client, email="single-vr@example.com")
            token = _token_from(sender.last)
            first = await client.post("/api/v1/auth/verify/confirm", json={"token": token})
            second = await client.post("/api/v1/auth/verify/confirm", json={"token": token})
        assert first.status_code == 200
        assert second.status_code == 400
        assert second.json()["error"]["code"] == "invalid_token"

    async def test_confirm_expired_token_rejected(self, auth_env, monkeypatch):
        sender = _install_sender(monkeypatch)
        record = await _seed_pending_user(auth_env, "expired-vr@example.com")

        async with _client() as client:
            await _verify_request(client, email="expired-vr@example.com")
            token = _token_from(sender.last)

        # Force the stored token to be in the past.
        async with auth_env.session_factory() as session:
            row = await session.get(EmailVerificationToken, hash_token_value(token))
            row.expires_at = "2000-01-01T00:00:00+00:00"
            await session.commit()

        async with _client() as client:
            resp = await client.post("/api/v1/auth/verify/confirm", json={"token": token})
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "invalid_token"
        # Still pending - an expired token must not verify.
        assert (await get_by_id(record.id, db=auth_env)).status == "pending_verification"

    async def test_confirm_invalid_token_rejected(self, auth_env, monkeypatch):
        _install_sender(monkeypatch)
        async with _client() as client:
            resp = await client.post(
                "/api/v1/auth/verify/confirm", json={"token": "not-a-real-token"}
            )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "invalid_token"


# ---------------------------------------------------------------------------
# sensitive-action gate (R5.6)
# ---------------------------------------------------------------------------


class TestSensitiveActionGate:
    async def test_unverified_user_is_gated_from_generation(self, auth_env, monkeypatch):
        # Hosted mode -> verification required -> an active-but-unverified session
        # is blocked from provider-cost generation.
        monkeypatch.setattr(app_settings, "single_user_mode", False)
        await _seed_active_unverified(auth_env, "gated@example.com")

        async with _client() as client:
            await _login(client, "gated@example.com")
            csrf = client.cookies.get("csrf")
            resp = await client.post(
                "/api/v1/resumes/some-resume-id/generate-cover-letter",
                headers={"X-CSRF-Token": csrf},
            )
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "email_verification_required"

    async def test_verified_user_passes_the_gate(self, auth_env, monkeypatch):
        # A verified session clears the gate (the endpoint then 404s on the
        # missing resume - the point is the gate did NOT reject it).
        monkeypatch.setattr(app_settings, "single_user_mode", False)
        await _seed_active_user(auth_env, "ungated@example.com")

        async with _client() as client:
            await _login(client, "ungated@example.com")
            csrf = client.cookies.get("csrf")
            resp = await client.post(
                "/api/v1/resumes/some-resume-id/generate-cover-letter",
                headers={"X-CSRF-Token": csrf},
            )
        assert resp.status_code != 403
        if resp.headers.get("content-type", "").startswith("application/json"):
            body = resp.json()
            if isinstance(body, dict) and "error" in body:
                assert body["error"]["code"] != "email_verification_required"


# ---------------------------------------------------------------------------
# password/forgot
# ---------------------------------------------------------------------------


class TestPasswordForgot:
    async def test_uniform_for_existing_and_nonexistent(self, auth_env, monkeypatch):
        sender = _install_sender(monkeypatch)
        await _seed_active_user(auth_env, "known-pf@example.com")

        async with _client() as client:
            existing = await client.post(
                "/api/v1/auth/password/forgot", json={"email": "known-pf@example.com"}
            )
        async with _client() as client:
            unknown = await client.post(
                "/api/v1/auth/password/forgot", json={"email": "nobody-pf@example.com"}
            )

        assert existing.status_code == unknown.status_code == 200
        assert existing.json() == unknown.json() == {"status": "ok"}
        # Exactly one email (for the registered address) was sent.
        assert len(sender.messages) == 1
        assert sender.messages[0].to == "known-pf@example.com"

    async def test_reissue_invalidates_prior_reset_tokens(self, auth_env, monkeypatch):
        sender = _install_sender(monkeypatch)
        await _seed_active_user(auth_env, "reissue-pf@example.com")

        async with _client() as client:
            await client.post(
                "/api/v1/auth/password/forgot", json={"email": "reissue-pf@example.com"}
            )
            first = _token_from(sender.last)
            await client.post(
                "/api/v1/auth/password/forgot", json={"email": "reissue-pf@example.com"}
            )

        # The superseded token can no longer be used to reset.
        async with _client() as client:
            resp = await client.post(
                "/api/v1/auth/password/reset",
                json={"token": first, "password": NEW_PW},
            )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "invalid_token"


# ---------------------------------------------------------------------------
# password/reset
# ---------------------------------------------------------------------------


class TestPasswordReset:
    async def _forgot_token(self, client, sender, email: str) -> str:
        resp = await client.post("/api/v1/auth/password/forgot", json={"email": email})
        assert resp.status_code == 200
        return _token_from(sender.last)

    async def test_reset_sets_password_revokes_all_and_issues_fresh_session(
        self, auth_env, monkeypatch
    ):
        sender = _install_sender(monkeypatch)
        record = await _seed_active_user(auth_env, "reset-ok@example.com")

        # Two live sessions on two devices.
        async with _client() as dev1:
            await _login(dev1, "reset-ok@example.com")
            token_dev1 = dev1.cookies.get("__Host-session")
        async with _client() as dev2:
            await _login(dev2, "reset-ok@example.com")
            token_dev2 = dev2.cookies.get("__Host-session")

        async with _client() as client:
            raw = await self._forgot_token(client, sender, "reset-ok@example.com")
            resp = await client.post(
                "/api/v1/auth/password/reset",
                json={"token": raw, "password": NEW_PW},
            )
            assert resp.status_code == 200
            body = resp.json()
            assert set(body) <= SAFE_USER_FIELDS
            assert body["email"] == "reset-ok@example.com"
            # A fresh session cookie was issued.
            fresh = client.cookies.get("__Host-session")
        assert fresh and fresh not in {token_dev1, token_dev2}

        # ALL pre-existing sessions were revoked.
        for tok in (token_dev1, token_dev2):
            async with _client() as probe:
                r = await probe.get(
                    "/api/v1/auth/session", headers={"Cookie": f"__Host-session={tok}"}
                )
            assert r.status_code == 401

        # The new password works; the old one no longer does.
        async with _client() as client:
            good = await _login(client, "reset-ok@example.com", password=NEW_PW)
        assert good.status_code == 200
        async with _client() as client:
            bad = await _login(client, "reset-ok@example.com", password=STRONG_PW)
        assert bad.status_code == 401
        assert record.id  # sanity

    async def test_reset_is_single_use(self, auth_env, monkeypatch):
        sender = _install_sender(monkeypatch)
        await _seed_active_user(auth_env, "reset-single@example.com")

        async with _client() as client:
            raw = await self._forgot_token(client, sender, "reset-single@example.com")
            first = await client.post(
                "/api/v1/auth/password/reset", json={"token": raw, "password": NEW_PW}
            )
        assert first.status_code == 200

        async with _client() as client:
            second = await client.post(
                "/api/v1/auth/password/reset",
                json={"token": raw, "password": "another-strong-passphrase-77"},
            )
        assert second.status_code == 400
        assert second.json()["error"]["code"] == "invalid_token"

    async def test_reset_rejects_weak_password(self, auth_env, monkeypatch):
        sender = _install_sender(monkeypatch)
        await _seed_active_user(auth_env, "reset-weak@example.com")

        async with _client() as client:
            raw = await self._forgot_token(client, sender, "reset-weak@example.com")
            resp = await client.post(
                "/api/v1/auth/password/reset", json={"token": raw, "password": "short"}
            )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "weak_password"

    async def test_reset_rejects_breached_password(self, auth_env, monkeypatch):
        from app.auth.breach import BreachResult

        sender = _install_sender(monkeypatch)
        await _seed_active_user(auth_env, "reset-breach@example.com")

        class _FakeBreach:
            async def check(self, password: str) -> BreachResult:
                return BreachResult(breached=True, count=7)

        monkeypatch.setattr(get_password_service(), "_breach_check", _FakeBreach())
        async with _client() as client:
            raw = await self._forgot_token(client, sender, "reset-breach@example.com")
            resp = await client.post(
                "/api/v1/auth/password/reset", json={"token": raw, "password": NEW_PW}
            )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "breached_password"

    async def test_oauth_only_account_can_set_password(self, auth_env, monkeypatch):
        """An OAuth-only account (no password) can *set* one via reset (R6.3)."""
        sender = _install_sender(monkeypatch)
        record = await create_user(
            email="oauth-only@example.com",
            name="Odette",
            password_hash=None,  # OAuth-only: no local password
            status="active",
            email_verified_at="2024-01-01T00:00:00+00:00",
            db=auth_env,
        )

        async with _client() as client:
            raw = await self._forgot_token(client, sender, "oauth-only@example.com")
            resp = await client.post(
                "/api/v1/auth/password/reset", json={"token": raw, "password": NEW_PW}
            )
        assert resp.status_code == 200

        # The account can now log in with the freshly-set password.
        async with _client() as client:
            login = await _login(client, "oauth-only@example.com", password=NEW_PW)
        assert login.status_code == 200
        assert record.id

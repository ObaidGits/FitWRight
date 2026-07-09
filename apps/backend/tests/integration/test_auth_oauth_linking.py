"""Unit tests for the safe OAuth link/create decision (Property 5 / R4.4).

Drives :func:`app.auth.oauth.linking.link_or_create_user` against the isolated
temp database to exercise the full anti-hijack matrix: returning linked identity,
new-user creation, allowed linking (no-password / already-verified /
authenticated-self), and the refusal that protects an unverified password
account.
"""

from __future__ import annotations

import pytest

from app.auth.accounts import create_user, get_password_hash
from app.auth.oauth.linking import LinkAction, link_or_create_user
from app.auth.passwords import get_password_service

pytestmark = pytest.mark.integration


async def _seed(db, email, *, password=None, verified=True, status="active"):
    hashed = get_password_service().hash_password(password) if password else None
    return await create_user(
        email=email,
        name="Seed",
        password_hash=hashed,
        status=status,
        email_verified_at="2024-01-01T00:00:00+00:00" if verified else None,
        db=db,
    )


class TestLinkOrCreate:
    async def test_new_email_creates_verified_user(self, auth_env):
        result = await link_or_create_user(
            provider="google",
            subject="sub-1",
            email="new@example.com",
            provider_email_verified=True,
            name="New Person",
            db=auth_env,
        )
        assert result.action is LinkAction.CREATED
        assert result.ok
        # The created user is OAuth-only (no password) and verified.
        assert await get_password_hash(result.user_id, db=auth_env) is None

    async def test_returning_identity_logs_in_same_user(self, auth_env):
        first = await link_or_create_user(
            provider="google",
            subject="sub-2",
            email="again@example.com",
            provider_email_verified=True,
            db=auth_env,
        )
        second = await link_or_create_user(
            provider="google",
            subject="sub-2",
            email="again@example.com",
            provider_email_verified=True,
            db=auth_env,
        )
        assert second.action is LinkAction.LOGIN_EXISTING
        assert second.user_id == first.user_id

    async def test_links_to_passwordless_account(self, auth_env):
        acct = await _seed(auth_env, "nopw@example.com", password=None, verified=False)
        result = await link_or_create_user(
            provider="google",
            subject="sub-3",
            email="nopw@example.com",
            provider_email_verified=True,
            db=auth_env,
        )
        assert result.action is LinkAction.LINKED
        assert result.user_id == acct.id

    async def test_links_to_verified_password_account(self, auth_env):
        acct = await _seed(auth_env, "verified@example.com", password="pw-abcdef-123456", verified=True)
        result = await link_or_create_user(
            provider="google",
            subject="sub-4",
            email="verified@example.com",
            provider_email_verified=True,
            db=auth_env,
        )
        assert result.action is LinkAction.LINKED
        assert result.user_id == acct.id

    async def test_refuses_unverified_password_account(self, auth_env):
        # Password account, email NOT verified, request NOT authenticated → refuse.
        await _seed(
            auth_env,
            "unverified@example.com",
            password="pw-abcdefg-123456",
            verified=False,
            status="pending_verification",
        )
        result = await link_or_create_user(
            provider="google",
            subject="sub-5",
            email="unverified@example.com",
            provider_email_verified=True,
            db=auth_env,
        )
        assert result.action is LinkAction.LINK_REQUIRED
        assert not result.ok
        assert result.user_id is None

    async def test_authenticated_self_may_link_unverified_password_account(self, auth_env):
        # Same unverified password account, but the request is authenticated as
        # that user (linking from Settings) → allowed.
        acct = await _seed(
            auth_env,
            "settings@example.com",
            password="pw-abcdefg-123456",
            verified=False,
            status="pending_verification",
        )
        result = await link_or_create_user(
            provider="google",
            subject="sub-6",
            email="settings@example.com",
            provider_email_verified=True,
            authenticated_user_id=acct.id,
            db=auth_env,
        )
        assert result.action is LinkAction.LINKED
        assert result.user_id == acct.id

    async def test_unverified_provider_email_refused_for_existing_account(self, auth_env):
        # Even a no-password account is not linked when the provider email is
        # unverified (guard is total, though the callback rejects this earlier).
        await _seed(auth_env, "guard@example.com", password=None, verified=False)
        result = await link_or_create_user(
            provider="google",
            subject="sub-7",
            email="guard@example.com",
            provider_email_verified=False,
            db=auth_env,
        )
        assert result.action is LinkAction.LINK_REQUIRED
        assert not result.ok

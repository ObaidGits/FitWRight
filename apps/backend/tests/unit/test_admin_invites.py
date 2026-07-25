"""Unit tests for secure admin creation.

Option A - the bootstrap owner is provisioned as an ``admin`` with a verifiable
password (when ``OWNER_PASSWORD`` is set) via ``ensure_owner`` (mirrors migration
0004).

Option B - the admin-invite service: create -> claim (single-use, email-bound,
TTL) -> list/revoke, with the token stored only as a hash.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.auth.admin_invites import (
    claim_invite,
    create_invite,
    hash_invite_token,
    list_invites,
    revoke_invite,
)

pytestmark = pytest.mark.unit

STRONG_PW = "correct-horse-battery-staple-9"


# ---------------------------------------------------------------------------
# Option A - bootstrap owner is an admin (env-seeded), password verifiable
# ---------------------------------------------------------------------------


class TestBootstrapOwner:
    async def test_owner_is_admin_and_active(self, isolated_db, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "owner_email", "owner@localhost")
        monkeypatch.setattr(settings, "owner_password", "")
        from app.auth.accounts import get_by_email
        from app.auth.owner import ensure_owner

        owner_id = await ensure_owner(isolated_db)
        rec = await get_by_email("owner@localhost", db=isolated_db)
        assert rec is not None
        assert rec.id == owner_id
        assert rec.role == "admin"
        assert rec.status == "active"

    async def test_owner_password_is_hashed_and_verifiable(self, isolated_db, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "owner_email", "owner2@localhost")
        monkeypatch.setattr(settings, "owner_password", STRONG_PW)
        from app.auth.accounts import get_by_email, get_password_hash
        from app.auth.owner import ensure_owner
        from app.auth.passwords import get_password_service

        await ensure_owner(isolated_db)
        rec = await get_by_email("owner2@localhost", db=isolated_db)
        stored = await get_password_hash(rec.id, db=isolated_db)
        assert stored  # a hash was set (not OAuth-only)
        assert get_password_service().verify_password(stored, STRONG_PW) is True


# ---------------------------------------------------------------------------
# Option B - invite service
# ---------------------------------------------------------------------------


class TestCreateInvite:
    async def test_create_returns_raw_token_and_stores_only_hash(self, isolated_db):
        raw, rec = await create_invite(
            email="Invitee@Example.com", created_by="admin-1", ttl_hours=24, db=isolated_db
        )
        assert raw and len(raw) >= 20
        assert rec.email == "invitee@example.com"  # normalized
        assert rec.role == "admin"
        assert rec.status == "active"
        # The row is keyed by the HASH of the raw token, never the raw token.
        from sqlalchemy import select

        from app.models import AdminInvite

        async with isolated_db.session_factory() as s:
            row = (
                await s.execute(select(AdminInvite).where(AdminInvite.id == rec.id))
            ).scalar_one()
        assert row.token_hash == hash_invite_token(raw)
        assert row.token_hash != raw

    async def test_creating_a_new_invite_supersedes_the_prior_one(self, isolated_db):
        _, first = await create_invite(email="dup@example.com", created_by="a", db=isolated_db)
        _, second = await create_invite(email="dup@example.com", created_by="a", db=isolated_db)
        history = await list_invites(db=isolated_db)
        by_id = {invite.id: invite for invite in history}
        assert by_id[second.id].status == "active"
        assert by_id[first.id].status == "superseded"
        assert by_id[first.id].used_at is None
        assert by_id[first.id].revoked_at is not None
        assert by_id[first.id].revoked_by == "a"
        assert by_id[first.id].revoke_reason == "superseded"


class TestClaimInvite:
    async def test_claim_ok_returns_role_and_is_single_use(self, isolated_db):
        raw, rec = await create_invite(email="a@example.com", created_by="admin", db=isolated_db)
        status, role = await claim_invite(
            raw_token=raw, email="a@example.com", used_by="new-user", db=isolated_db
        )
        assert status == "ok"
        assert role == "admin"
        # Second claim must fail - single-use.
        status2, role2 = await claim_invite(
            raw_token=raw, email="a@example.com", db=isolated_db
        )
        assert status2 == "used"
        assert role2 is None

    async def test_claim_unknown_token(self, isolated_db):
        status, role = await claim_invite(
            raw_token="not-a-real-token", email="a@example.com", db=isolated_db
        )
        assert status == "not_found"
        assert role is None

    async def test_claim_email_mismatch_does_not_consume(self, isolated_db):
        raw, _ = await create_invite(email="bound@example.com", created_by="a", db=isolated_db)
        status, _ = await claim_invite(
            raw_token=raw, email="someone-else@example.com", db=isolated_db
        )
        assert status == "email_mismatch"
        # Still claimable by the correct email afterwards (was not consumed).
        ok_status, role = await claim_invite(
            raw_token=raw, email="bound@example.com", db=isolated_db
        )
        assert ok_status == "ok" and role == "admin"

    async def test_claim_expired(self, isolated_db):
        raw, rec = await create_invite(email="exp@example.com", created_by="a", db=isolated_db)
        # Force expiry into the past.
        from sqlalchemy import update

        from app.models import AdminInvite

        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        async with isolated_db.session_factory() as s:
            await s.execute(
                update(AdminInvite).where(AdminInvite.id == rec.id).values(expires_at=past)
            )
            await s.commit()
        status, role = await claim_invite(raw_token=raw, email="exp@example.com", db=isolated_db)
        assert status == "expired"
        assert role is None


class TestListAndRevoke:
    async def test_list_includes_active_used_and_expired_lifecycle(self, isolated_db):
        _, live = await create_invite(email="live@example.com", created_by="a", db=isolated_db)
        raw_used, used = await create_invite(email="used@example.com", created_by="a", db=isolated_db)
        await claim_invite(
            raw_token=raw_used,
            email="used@example.com",
            used_by="new-user",
            db=isolated_db,
        )

        from sqlalchemy import update
        from app.models import AdminInvite

        _, expired = await create_invite(
            email="history-expired@example.com", created_by="a", db=isolated_db
        )
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        async with isolated_db.session_factory() as session:
            await session.execute(
                update(AdminInvite).where(AdminInvite.id == expired.id).values(expires_at=past)
            )
            await session.commit()

        history = {invite.id: invite for invite in await list_invites(db=isolated_db)}
        assert history[live.id].status == "active"
        assert history[used.id].status == "used"
        assert history[used.id].used_by == "new-user"
        assert history[expired.id].status == "expired"

    async def test_revoke_is_idempotent(self, isolated_db):
        _, rec = await create_invite(email="rev@example.com", created_by="a", db=isolated_db)
        assert await revoke_invite(rec.id, revoked_by="admin-2", db=isolated_db) is True
        history = {invite.id: invite for invite in await list_invites(db=isolated_db)}
        revoked = history[rec.id]
        assert revoked.status == "revoked"
        assert revoked.used_at is None
        assert revoked.revoked_at is not None
        assert revoked.revoked_by == "admin-2"
        assert revoked.revoke_reason == "manual"
        assert await revoke_invite(rec.id, revoked_by="admin-2", db=isolated_db) is False

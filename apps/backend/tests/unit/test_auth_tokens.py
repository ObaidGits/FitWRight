"""Unit tests for the hashed single-use TTL token service (Task 5).

Exercises :class:`~app.auth.tokens.TokenService` directly against an isolated
temp database: hashed-at-rest storage, single-use consumption, TTL expiry,
prior-token invalidation on re-issue, and the read-only peek.

Requirements: 5.1, 5.3, 6.1, 6.4, 12.4
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.auth.tokens import TokenService, hash_token_value
from app.config import settings as app_settings
from app.models import EmailVerificationToken, PasswordResetToken, User

pytestmark = pytest.mark.unit


async def _make_user(db, *, email: str = "tok@example.com") -> str:
    from uuid import uuid4

    now = datetime.now(timezone.utc).isoformat()
    uid = str(uuid4())
    async with db.session_factory() as session:
        session.add(
            User(
                id=uid,
                email=email,
                name="Tok",
                password_hash=None,
                role="user",
                status="pending_verification",
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()
    return uid


def _service(db, *, clock=None) -> TokenService:
    return TokenService(db.session_factory, settings=app_settings, clock=clock)


class TestIssue:
    async def test_only_hash_is_stored(self, isolated_db):
        uid = await _make_user(isolated_db)
        svc = _service(isolated_db)
        raw = await svc.issue_verification(uid)

        async with isolated_db.session_factory() as session:
            rows = (await session.execute(select(EmailVerificationToken))).scalars().all()
        assert len(rows) == 1
        assert rows[0].token_hash == hash_token_value(raw)
        assert rows[0].token_hash != raw
        assert rows[0].used_at is None

    async def test_reissue_invalidates_prior_unused(self, isolated_db):
        uid = await _make_user(isolated_db)
        svc = _service(isolated_db)
        first = await svc.issue_verification(uid)
        second = await svc.issue_verification(uid)
        assert first != second

        # The first token is now marked used (invalidated); the second is live.
        assert (await svc.consume_verification(first)).reason == "used"
        assert (await svc.consume_verification(second)).ok is True


class TestConsume:
    async def test_single_use(self, isolated_db):
        uid = await _make_user(isolated_db)
        svc = _service(isolated_db)
        raw = await svc.issue_verification(uid)

        first = await svc.consume_verification(raw)
        assert first.ok and first.user_id == uid
        second = await svc.consume_verification(raw)
        assert not second.ok and second.reason == "used"

    async def test_invalid_token(self, isolated_db):
        svc = _service(isolated_db)
        result = await svc.consume_verification("no-such-token")
        assert not result.ok and result.reason == "invalid"

    async def test_empty_token(self, isolated_db):
        svc = _service(isolated_db)
        result = await svc.consume_reset("")
        assert not result.ok and result.reason == "invalid"

    async def test_expired_token(self, isolated_db):
        uid = await _make_user(isolated_db)
        # Issue with a clock in the past so the TTL has already lapsed.
        past = datetime.now(timezone.utc) - timedelta(days=365)
        svc = _service(isolated_db, clock=lambda: past)
        raw = await svc.issue_reset(uid)

        # Consume with the real clock → expired.
        live = _service(isolated_db)
        result = await live.consume_reset(raw)
        assert not result.ok and result.reason == "expired"


class TestPeek:
    async def test_peek_does_not_consume(self, isolated_db):
        uid = await _make_user(isolated_db)
        svc = _service(isolated_db)
        raw = await svc.issue_reset(uid)

        peek = await svc.peek_reset(raw)
        assert peek.ok and peek.user_id == uid

        # Peeking left the token usable — a subsequent consume still succeeds.
        async with isolated_db.session_factory() as session:
            row = await session.get(PasswordResetToken, hash_token_value(raw))
        assert row.used_at is None
        assert (await svc.consume_reset(raw)).ok is True

    async def test_peek_reports_used(self, isolated_db):
        uid = await _make_user(isolated_db)
        svc = _service(isolated_db)
        raw = await svc.issue_reset(uid)
        await svc.consume_reset(raw)
        assert (await svc.peek_reset(raw)).reason == "used"

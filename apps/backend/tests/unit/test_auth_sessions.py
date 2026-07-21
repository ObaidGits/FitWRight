"""Unit tests for the session service (Task 2.2).

Covers create/rotate/revoke/resolve, ``sha256``-at-rest, sliding + absolute
expiry (write-behind), remember-me cap, write-through cache eviction, disabled-
user rejection, keyed ``ip_hash``, device-label parsing, and the reaper.

Requirements: 2.1, 3.1, 3.3, 3.4, 3.6, 12.4, 12.5, 17.1, 17.3
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.auth.kvstore import LocalKVStore
from app.auth.sessions import (
    SessionService,
    hash_token,
    parse_device_label,
)
from app.config import Settings
from app.db_engine import make_async_engine
from app.models import (
    Base,
    EmailVerificationToken,
    PasswordResetToken,
    Session as SessionRow,
    User,
)

pytestmark = pytest.mark.unit


class _Clock:
    """A controllable clock for deterministic expiry/sliding tests."""

    def __init__(self, start: datetime | None = None) -> None:
        self.now = start or datetime(2024, 1, 1, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now = self.now + timedelta(seconds=seconds)


def _settings(**overrides) -> Settings:
    base = dict(
        single_user_mode=True,
        ip_hash_secret="ip-hash-secret-value-1234",
        idle_ttl=100,
        session_absolute_ttl=120,
        remember_me_ttl=200,
    )
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
async def factory(tmp_path):
    engine = make_async_engine(tmp_path / "sessions.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    yield session_factory
    await engine.dispose()


async def _make_user(factory, *, status: str = "active", verified: bool = True) -> str:
    from uuid import uuid4

    uid = str(uuid4())
    async with factory() as session:
        session.add(
            User(
                id=uid,
                email=f"{uid}@example.com",
                name="Test User",
                password_hash=None,
                role="user",
                status=status,
                email_verified_at="2024-01-01T00:00:00+00:00" if verified else None,
            )
        )
        await session.commit()
    return uid


@pytest.fixture
async def service(factory):
    clock = _Clock()
    kv = LocalKVStore()
    svc = SessionService(factory, kv, settings=_settings(), clock=clock)
    svc._clock_obj = clock  # expose for tests
    svc._kv_obj = kv
    return svc


# ---------------------------------------------------------------------------
# create / token hashing
# ---------------------------------------------------------------------------


class TestCreate:
    async def test_create_returns_raw_token_and_stores_only_hash(self, service, factory):
        uid = await _make_user(factory)
        raw_token, info = await service.create_session(uid)
        assert raw_token
        # DB stores sha256(raw), never the raw token.
        async with factory() as session:
            row = await session.get(SessionRow, info.id)
        assert row is not None
        assert row.token_hash == hash_token(raw_token)
        assert row.token_hash != raw_token

    async def test_created_session_resolves(self, service, factory):
        uid = await _make_user(factory)
        raw_token, _ = await service.create_session(uid)
        resolved = await service.resolve(raw_token)
        assert resolved is not None
        assert resolved.user_id == uid
        assert resolved.role == "user"
        assert resolved.status == "active"

    async def test_two_sessions_have_distinct_tokens(self, service, factory):
        uid = await _make_user(factory)
        t1, _ = await service.create_session(uid)
        t2, _ = await service.create_session(uid)
        assert t1 != t2

    async def test_ip_hash_is_keyed_and_deterministic(self, service):
        h1 = service.hash_ip("203.0.113.5")
        h2 = service.hash_ip("203.0.113.5")
        assert h1 == h2  # deterministic
        assert h1 != "203.0.113.5"  # hashed, not stored raw
        assert service.hash_ip(None) is None
        # A different secret yields a different hash (keyed).
        other = SessionService(
            service._session_factory,
            service._kv_obj,
            settings=_settings(ip_hash_secret="different-secret-value-9999"),
            clock=service._clock_obj,
        )
        assert other.hash_ip("203.0.113.5") != h1

    async def test_device_label_recorded(self, service, factory):
        uid = await _make_user(factory)
        ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/120.0"
        _, info = await service.create_session(uid, user_agent=ua)
        assert info.device_label == "Chrome on macOS"


# ---------------------------------------------------------------------------
# resolve edge cases
# ---------------------------------------------------------------------------


class TestResolve:
    async def test_none_token_returns_none(self, service):
        assert await service.resolve(None) is None
        assert await service.resolve("") is None

    async def test_unknown_token_returns_none(self, service):
        assert await service.resolve("bogustoken") is None

    async def test_expired_session_rejected(self, service, factory):
        uid = await _make_user(factory)
        raw_token, _ = await service.create_session(uid)
        # Advance past the idle TTL (100s).
        service._clock_obj.advance(150)
        assert await service.resolve(raw_token) is None

    async def test_disabled_user_rejected(self, service, factory):
        uid = await _make_user(factory)
        raw_token, _ = await service.create_session(uid)
        # Populate the cache, then disable + evict (write-through on disable).
        assert await service.resolve(raw_token) is not None
        async with factory() as session:
            user = await session.get(User, uid)
            user.status = "disabled"
            await session.commit()
        await service._evict(hash_token(raw_token))
        assert await service.resolve(raw_token) is None

    async def test_missing_user_rejected(self, service, factory):
        uid = await _make_user(factory)
        raw_token, _ = await service.create_session(uid)
        async with factory() as session:
            user = await session.get(User, uid)
            await session.delete(user)
            await session.commit()
        await service._evict(hash_token(raw_token))
        assert await service.resolve(raw_token) is None


# ---------------------------------------------------------------------------
# revoke + cache eviction (prompt revocation, Property 3)
# ---------------------------------------------------------------------------


class TestRevoke:
    async def test_revoke_session_rejects_within_one_cycle(self, service, factory):
        uid = await _make_user(factory)
        raw_token, info = await service.create_session(uid)
        assert await service.resolve(raw_token) is not None  # caches it
        await service.revoke_session(info.id)
        # Cache was evicted write-through -> next resolve fails immediately.
        assert await service.resolve(raw_token) is None

    async def test_cache_entry_evicted_on_revoke(self, service, factory):
        uid = await _make_user(factory)
        raw_token, info = await service.create_session(uid)
        await service.resolve(raw_token)
        cache_key = service._cache_key(hash_token(raw_token))
        assert await service._kv_obj.get(cache_key) is not None
        await service.revoke_session(info.id)
        assert await service._kv_obj.get(cache_key) is None

    async def test_revoke_by_token(self, service, factory):
        uid = await _make_user(factory)
        raw_token, _ = await service.create_session(uid)
        assert await service.revoke_by_token(raw_token) is True
        assert await service.resolve(raw_token) is None

    async def test_revoke_all_for_user(self, service, factory):
        uid = await _make_user(factory)
        t1, _ = await service.create_session(uid)
        t2, _ = await service.create_session(uid)
        count = await service.revoke_all_for_user(uid)
        assert count == 2
        assert await service.resolve(t1) is None
        assert await service.resolve(t2) is None

    async def test_revoke_all_except_current(self, service, factory):
        uid = await _make_user(factory)
        t1, keep = await service.create_session(uid)
        t2, _ = await service.create_session(uid)
        count = await service.revoke_all_for_user(uid, except_session_id=keep.id)
        assert count == 1
        assert await service.resolve(t1) is not None  # kept
        assert await service.resolve(t2) is None  # revoked

    async def test_revoke_is_idempotent(self, service, factory):
        uid = await _make_user(factory)
        _, info = await service.create_session(uid)
        assert await service.revoke_session(info.id) is True
        assert await service.revoke_session(info.id) is True  # no error second time


# ---------------------------------------------------------------------------
# rotation (fixation defense)
# ---------------------------------------------------------------------------


class TestRotate:
    async def test_rotate_issues_new_token_and_kills_old(self, service, factory):
        uid = await _make_user(factory)
        old_token, old_info = await service.create_session(uid)
        result = await service.rotate_session(old_token)
        assert result is not None
        new_token, new_info = result
        assert new_token != old_token
        assert new_info.id != old_info.id
        # Old dead, new alive.
        assert await service.resolve(old_token) is None
        assert await service.resolve(new_token) is not None

    async def test_rotate_unknown_token_returns_none(self, service):
        assert await service.rotate_session("nope") is None

    async def test_rotate_preserves_remember_me(self, service, factory):
        uid = await _make_user(factory)
        old_token, _ = await service.create_session(uid, remember_me=True)
        result = await service.rotate_session(old_token)
        assert result is not None
        _, new_info = result
        assert new_info.remember_me is True


# ---------------------------------------------------------------------------
# sliding + absolute expiry (write-behind)
# ---------------------------------------------------------------------------


class TestSlidingExpiry:
    async def test_expiry_extends_on_activity(self, service, factory):
        uid = await _make_user(factory)
        raw_token, info = await service.create_session(uid)
        original_expiry = info.expires_at
        # Advance past the refresh window (60s) but within idle TTL (100s).
        service._clock_obj.advance(80)
        resolved = await service.resolve(raw_token)
        assert resolved is not None
        assert resolved.expires_at > original_expiry  # slid forward

    async def test_no_write_within_refresh_window(self, service, factory):
        uid = await _make_user(factory)
        raw_token, info = await service.create_session(uid)
        service._clock_obj.advance(10)  # < 60s refresh window
        resolved = await service.resolve(raw_token)
        assert resolved is not None
        # last_seen_at unchanged (no write-behind yet).
        async with factory() as session:
            row = await session.get(SessionRow, info.id)
        assert row.last_seen_at == info.last_seen_at

    async def test_absolute_cap_bounds_extension(self, service, factory):
        uid = await _make_user(factory)
        raw_token, _ = await service.create_session(uid)  # non-remember: cap=120
        service._clock_obj.advance(80)  # now t0+80
        resolved = await service.resolve(raw_token)
        assert resolved is not None
        # idle_deadline = t0+80+100 = t0+180; absolute cap = t0+120 -> min = t0+120.
        created = service._clock_obj.now - timedelta(seconds=80)
        expected_cap = (created + timedelta(seconds=120)).isoformat()
        assert resolved.expires_at == expected_cap

    async def test_remember_me_uses_full_persistent_window(self, service, factory):
        uid = await _make_user(factory)
        raw_token, info = await service.create_session(uid, remember_me=True)  # cap=200
        created = service._clock_obj.now
        # The initial DB expiry now matches the remembered 200s cookie/cap,
        # rather than the ordinary 100s idle window.
        assert info.expires_at == (created + timedelta(seconds=200)).isoformat()
        service._clock_obj.advance(80)
        resolved = await service.resolve(raw_token)
        assert resolved is not None
        # class idle deadline=t0+280, absolute cap=t0+200 => cap wins.
        assert resolved.expires_at == (created + timedelta(seconds=200)).isoformat()


# ---------------------------------------------------------------------------
# step-up + device list
# ---------------------------------------------------------------------------


class TestStepUpAndList:
    async def test_bump_step_up_sets_timestamp(self, service, factory):
        uid = await _make_user(factory)
        _, info = await service.create_session(uid)
        assert info.step_up_at is None
        assert await service.bump_step_up(info.id, aal="aal2") is True
        async with factory() as session:
            row = await session.get(SessionRow, info.id)
        assert row.step_up_at is not None
        assert row.aal == "aal2"

    async def test_bump_step_up_missing_session(self, service):
        assert await service.bump_step_up("nope") is False

    async def test_list_active_sessions(self, service, factory):
        uid = await _make_user(factory)
        await service.create_session(uid)
        _, info2 = await service.create_session(uid)
        sessions = await service.list_active_sessions(uid)
        assert len(sessions) == 2
        # Revoked sessions drop off the list.
        await service.revoke_session(info2.id)
        sessions = await service.list_active_sessions(uid)
        assert len(sessions) == 1


# ---------------------------------------------------------------------------
# reaper
# ---------------------------------------------------------------------------


class TestReaper:
    async def test_reaps_expired_and_old_revoked_sessions(self, service, factory):
        uid = await _make_user(factory)
        # Expired session (created far in the past).
        raw_token, expired = await service.create_session(uid)
        # Manually age it out.
        async with factory() as session:
            row = await session.get(SessionRow, expired.id)
            row.expires_at = "2020-01-01T00:00:00+00:00"
            await session.commit()
        counts = await service.reap()
        assert counts["sessions"] >= 1
        async with factory() as session:
            gone = await session.get(SessionRow, expired.id)
        assert gone is None

    async def test_reaps_expired_tokens(self, service, factory):
        uid = await _make_user(factory)
        async with factory() as session:
            session.add(
                EmailVerificationToken(
                    token_hash="evt-old",
                    user_id=uid,
                    expires_at="2020-01-01T00:00:00+00:00",
                )
            )
            session.add(
                PasswordResetToken(
                    token_hash="prt-old",
                    user_id=uid,
                    expires_at="2020-01-01T00:00:00+00:00",
                )
            )
            await session.commit()
        counts = await service.reap()
        assert counts["email_tokens"] == 1
        assert counts["reset_tokens"] == 1

    async def test_reaper_single_flight(self, service, factory):
        # Hold the reaper lock externally; reap() must no-op (all zeros).
        lock = service._kv_obj.lock("session:reaper", ttl_seconds=60, blocking=False)
        assert await lock.acquire() is True
        try:
            counts = await service.reap()
            assert counts == {
                "sessions": 0,
                "email_tokens": 0,
                "reset_tokens": 0,
                "email_change_tokens": 0,
            }
        finally:
            await lock.release()


# ---------------------------------------------------------------------------
# device-label parsing (pure)
# ---------------------------------------------------------------------------


class TestDeviceLabel:
    @pytest.mark.parametrize(
        "ua,expected",
        [
            ("Mozilla/5.0 (Windows NT 10.0) Firefox/121.0", "Firefox on Windows"),
            ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0) Safari/604.1", "Safari on iOS"),
            (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/120.0",
                "Chrome on macOS",
            ),
            ("Mozilla/5.0 (X11; Linux x86_64) Edg/120.0", "Edge on Linux"),
        ],
    )
    def test_known_pairs(self, ua, expected):
        assert parse_device_label(ua) == expected

    def test_none_and_empty(self):
        assert parse_device_label(None) is None
        assert parse_device_label("") is None

    def test_unknown_falls_back_to_snippet(self):
        label = parse_device_label("CustomBot/1.0 crawler")
        assert label and "CustomBot" in label

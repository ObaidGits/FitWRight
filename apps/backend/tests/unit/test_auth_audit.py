"""Unit tests for the audit writer + meta sanitization (Task 2.4).

Covers secret-key dropping, CRLF/log-injection stripping, length bounds, and the
append-only persistence path.

Requirements: 16.2, 13.4
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.auth.audit import (
    AuditEvent,
    AuditService,
    sanitize_log_value,
    sanitize_meta,
)
from app.db_engine import make_async_engine
from app.models import AuditLog, Base

pytestmark = pytest.mark.unit


class TestSanitizeLogValue:
    def test_strips_crlf(self):
        cleaned = sanitize_log_value("line1\r\nlogin_failed forged=true")
        assert "\r" not in cleaned and "\n" not in cleaned

    def test_strips_control_chars(self):
        cleaned = sanitize_log_value("a\x00b\x1fc\x7fd")
        assert cleaned == "a b c d"  # control chars -> spaces
        assert "\x00" not in cleaned

    def test_length_bounded(self):
        cleaned = sanitize_log_value("x" * 1000)
        assert len(cleaned) <= 501  # 500 + ellipsis

    def test_scalars_passthrough(self):
        assert sanitize_log_value(5) == 5
        assert sanitize_log_value(True) is True
        assert sanitize_log_value(None) is None

    def test_nested_list(self):
        assert sanitize_log_value(["a\nb", "c"]) == ["a b", "c"]


class TestSanitizeMeta:
    def test_none_and_empty(self):
        assert sanitize_meta(None) is None
        assert sanitize_meta({}) is None

    def test_drops_secret_keys(self):
        meta = {
            "password": "hunter2",
            "session_token": "abc",
            "csrf": "xyz",
            "authorization": "Bearer x",
            "api_key": "sk-123",
            "email": "user@example.com",
        }
        cleaned = sanitize_meta(meta)
        assert cleaned == {"email": "user@example.com"}

    def test_sanitizes_values(self):
        cleaned = sanitize_meta({"reason": "bad\r\ninput"})
        # Each control char (CR, LF) collapses to a space.
        assert cleaned == {"reason": "bad  input"}

    def test_bounds_key_count(self):
        big = {f"k{i}": i for i in range(200)}
        cleaned = sanitize_meta(big)
        assert cleaned is not None
        assert len(cleaned) <= 50

    def test_nested_dict_sanitized(self):
        cleaned = sanitize_meta({"outer": {"password": "x", "ok": "v\nw"}})
        assert cleaned == {"outer": {"ok": "v w"}}


@pytest.fixture
async def session_factory(tmp_path):
    engine = make_async_engine(tmp_path / "audit.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


class TestAuditPersistence:
    async def test_records_row(self, session_factory):
        clock = lambda: datetime(2024, 1, 1, tzinfo=timezone.utc)
        service = AuditService(session_factory, clock=clock)
        await service.record(
            AuditEvent.LOGIN,
            actor_user_id="user-1",
            ip_hash="deadbeef",
            request_id="req-1",
            meta={"remember_me": True, "password": "leaked"},
        )
        async with session_factory() as session:
            rows = (await session.execute(select(AuditLog))).scalars().all()
        assert len(rows) == 1
        row = rows[0]
        assert row.event == AuditEvent.LOGIN
        assert row.actor_user_id == "user-1"
        assert row.ip_hash == "deadbeef"
        assert row.ts == "2024-01-01T00:00:00+00:00"
        # Secret dropped, safe value kept.
        assert row.meta == {"remember_me": True}

    async def test_record_never_raises_on_db_error(self, tmp_path):
        # Point the factory at a disposed engine so commit fails; must fail soft.
        engine = make_async_engine(tmp_path / "broken.db")
        factory = async_sessionmaker(engine, expire_on_commit=False)
        await engine.dispose()
        service = AuditService(factory)
        # Should not raise even though the table doesn't exist / engine disposed.
        await service.record(AuditEvent.LOGIN, actor_user_id="u")

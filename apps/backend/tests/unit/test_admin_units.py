"""Unit tests for the P2 admin building blocks (Tasks 1-2, 9.2).

Fast, dependency-light tests for the pure logic: cursor encode/decode + tamper
rejection, search sanitization, the response field allowlist, and the metric
registry + rollup idempotency + counter reconciliation against an isolated DB.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.admin.cursor import CursorError, decode_cursor, encode_cursor, sanitize_query
from app.admin.schemas import (
    FORBIDDEN_SUBSTRINGS,
    AdminUserRow,
    assert_no_forbidden_fields,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Cursor
# ---------------------------------------------------------------------------


class TestCursor:
    def test_roundtrip(self):
        c = encode_cursor("2026-07-09T00:00:00+00:00", "abc-123")
        assert decode_cursor(c) == ("2026-07-09T00:00:00+00:00", "abc-123")

    def test_none_and_empty(self):
        assert decode_cursor(None) is None
        assert decode_cursor("") is None

    def test_tampered_raises(self):
        with pytest.raises(CursorError):
            decode_cursor("!!!not-base64!!!")

    def test_wrong_shape_raises(self):
        import base64
        import json

        bad = base64.urlsafe_b64encode(json.dumps(["only-one"]).encode()).decode()
        with pytest.raises(CursorError):
            decode_cursor(bad)


class TestSanitizeQuery:
    def test_strips_control_chars(self):
        assert sanitize_query("ab\r\ncd") == "ab  cd"

    def test_length_bounded(self):
        assert len(sanitize_query("x" * 500)) == 128

    def test_empty_to_none(self):
        assert sanitize_query("") is None
        assert sanitize_query("   ") is None
        assert sanitize_query(None) is None


# ---------------------------------------------------------------------------
# Response allowlist (Property 2)
# ---------------------------------------------------------------------------


class TestAllowlist:
    def test_clean_payload_passes(self):
        row = AdminUserRow(
            id="1", name="A", email="a@b.c", role="user", status="active",
            emailVerified=True, createdAt="2026-01-01T00:00:00+00:00",
        )
        assert_no_forbidden_fields(row.model_dump())

    def test_ip_hash_allowed(self):
        assert_no_forbidden_fields({"ipHash": "deadbeef"})

    @pytest.mark.parametrize("bad_key", ["password_hash", "sessionToken", "apiKey", "csrfSecret"])
    def test_forbidden_keys_rejected(self, bad_key):
        with pytest.raises(ValueError):
            assert_no_forbidden_fields({bad_key: "x"})

    def test_nested_forbidden_rejected(self):
        with pytest.raises(ValueError):
            assert_no_forbidden_fields({"items": [{"ok": 1, "password": "leak"}]})

    def test_admin_user_row_forbids_extra(self):
        # extra="forbid" means a stray field can't be constructed at all.
        with pytest.raises(Exception):
            AdminUserRow(
                id="1", name="A", email="a@b.c", role="user", status="active",
                emailVerified=True, createdAt="t", password_hash="leak",  # type: ignore
            )

    def test_forbidden_substrings_cover_secrets(self):
        for marker in ("password", "hash", "token", "secret", "apikey"):
            assert marker in FORBIDDEN_SUBSTRINGS


# ---------------------------------------------------------------------------
# Metric registry + rollup idempotency + reconciliation
# ---------------------------------------------------------------------------


def _iso(dt: datetime) -> str:
    return dt.isoformat()


class TestMetricsService:
    async def test_unknown_metric_raises(self, isolated_db):
        from app.admin.metrics_service import MetricsService, UnknownMetricError
        from app.admin.repo import AdminRepo

        svc = MetricsService(isolated_db.session_factory, AdminRepo(isolated_db.session_factory))
        with pytest.raises(UnknownMetricError):
            await svc.usage_series("bogus", 7)

    async def test_signups_metric_definition_utc_day(self, isolated_db, owner_id):
        """signups = users created that UTC day (exact boundary)."""
        from app.admin.repo import AdminRepo
        from app.auth.accounts import create_user

        repo = AdminRepo(isolated_db.session_factory)
        # Create a user and backdate created_at to a specific day.
        rec = await create_user(email="d@e.f", name="D", password_hash=None, status="active", db=isolated_db)
        day = "2026-06-15"
        from app.models import User

        async with isolated_db.session_factory() as session:
            row = await session.get(User, rec.id)
            row.created_at = "2026-06-15T12:00:00+00:00"
            await session.commit()

        start = "2026-06-15T00:00:00+00:00"
        end = "2026-06-16T00:00:00+00:00"
        assert await repo.metric_for_day("signups", start, end) == 1
        # A neighboring day sees zero (no double-count across boundaries).
        assert await repo.metric_for_day("signups", "2026-06-16T00:00:00+00:00", "2026-06-17T00:00:00+00:00") == 0

    async def test_rollup_is_idempotent_upsert(self, isolated_db, owner_id):
        from app.admin.metrics_service import MetricsService
        from app.admin.repo import AdminRepo
        from app.models import MetricsDaily

        from app.admin.metrics_service import _TOTALS_DAY

        svc = MetricsService(isolated_db.session_factory, AdminRepo(isolated_db.session_factory))
        await svc.run_rollup(lookback_days=2)

        from sqlalchemy import func, select

        async def _counts():
            async with isolated_db.session_factory() as session:
                closed = (
                    await session.execute(
                        select(func.count())
                        .select_from(MetricsDaily)
                        .where(MetricsDaily.day_utc != _TOTALS_DAY)
                    )
                ).scalar()
                total = (
                    await session.execute(select(func.count()).select_from(MetricsDaily))
                ).scalar()
            return int(closed), int(total)

        closed1, total1 = await _counts()
        # 2 closed days * 3 registry metrics = 6 daily rows.
        assert closed1 == 6
        # Re-run must UPSERT, not duplicate (idempotent) — counts unchanged.
        await svc.run_rollup(lookback_days=2)
        closed2, total2 = await _counts()
        assert closed2 == 6
        assert total2 == total1  # totals snapshot re-UPSERTed in place, no dupes

    async def test_reconcile_counters_fixes_drift(self, isolated_db, owner_id):
        from app.admin.metrics_service import MetricsService
        from app.admin.repo import AdminRepo
        from app.models import User

        # Create resumes then corrupt the denormalized counter.
        await isolated_db.create_resume(owner_id, content="a")
        await isolated_db.create_resume(owner_id, content="b")
        async with isolated_db.session_factory() as session:
            row = await session.get(User, owner_id)
            row.resume_count = 99  # drift
            await session.commit()

        svc = MetricsService(isolated_db.session_factory, AdminRepo(isolated_db.session_factory))
        result = await svc.reconcile_counters()
        assert result["reconciled"] >= 1
        async with isolated_db.session_factory() as session:
            row = await session.get(User, owner_id)
            assert row.resume_count == 2

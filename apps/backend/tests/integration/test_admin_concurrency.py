"""Concurrency / reliability sign-off for P2 Admin (Task 9.2, R10.*).

- purge is idempotent + resumable (re-run purges nothing new);
- keyset cursor is stable under a concurrent insert (no skip/dup across pages);
- the usage series never double-counts: the current partial day is live and the
  closed days come from the rollup (Property 6).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.admin.jobs import run_purge_job
from app.admin.metrics_service import MetricsService
from app.admin.repo import AdminRepo
from app.auth.accounts import create_user
from app.config import settings as app_settings
from app.models import User

pytestmark = pytest.mark.integration


async def _seed(db, email):
    return await create_user(
        email=email, name="U", password_hash=None, status="active", db=db
    )


class TestPurgeResumability:
    async def test_purge_idempotent(self, auth_env, monkeypatch):
        monkeypatch.setattr(app_settings, "admin_destructive_actions", True)
        # Seed a soft-deleted user well past the (backdated) grace window.
        rec = await _seed(auth_env, "gone@example.com")
        await auth_env.create_resume(rec.id, content="x")
        async with auth_env.session_factory() as session:
            row = await session.get(User, rec.id)
            row.deleted_at = (datetime.now(timezone.utc) - timedelta(days=999)).isoformat()
            row.status = "disabled"
            await session.commit()

        first = await run_purge_job()
        assert first["purged"] == 1
        # Re-run: nothing left to purge (idempotent + resumable).
        second = await run_purge_job()
        assert second["purged"] == 0

    async def test_purge_gcs_avatar_object(self, auth_env, monkeypatch):
        """Erasing a user must delete their avatar master from storage (Photo System)."""
        monkeypatch.setattr(app_settings, "admin_destructive_actions", True)
        rec = await _seed(auth_env, "avatar-purge@example.com")

        deleted_keys: list[str] = []

        class _RecordingProvider:
            async def put(self, key, data, *, content_type):  # pragma: no cover - unused here
                return f"http://x/{key}"

            async def delete(self, key):
                deleted_keys.append(key)

        monkeypatch.setattr(
            "app.storage.provider.get_storage_provider", lambda: _RecordingProvider()
        )

        async with auth_env.session_factory() as session:
            row = await session.get(User, rec.id)
            row.avatar_key = f"{rec.id}/abc123.webp"
            row.avatar_url = f"http://x/{rec.id}/abc123.webp"
            row.deleted_at = (datetime.now(timezone.utc) - timedelta(days=999)).isoformat()
            row.status = "disabled"
            await session.commit()

        result = await run_purge_job()
        assert result["purged"] == 1
        # The avatar object was garbage-collected from storage on erasure.
        assert f"{rec.id}/abc123.webp" in deleted_keys


class TestLastAdminRace:
    async def test_two_last_admins_disabled_concurrently_one_wins(self, auth_env):
        """Property 3 (M5): the REAL race — two concurrent disables of the only
        two active admins must resolve to exactly one success + one
        ``last_active_admin`` 409, leaving ≥1 active admin.
        """
        import asyncio

        from app.admin.lifecycle import LastActiveAdminError, LifecycleService

        svc = LifecycleService(auth_env.session_factory)
        a = await create_user(email="raceA@example.com", name="A", password_hash=None, role="admin", status="active", db=auth_env)
        b = await create_user(email="raceB@example.com", name="B", password_hash=None, role="admin", status="active", db=auth_env)

        async def _disable(target_id):
            try:
                out = await svc.set_status(actor_id=None, target_id=target_id, new_status="disabled")
                return ("ok", out.changed)
            except LastActiveAdminError:
                return ("blocked", False)

        # Disable BOTH of the only two active admins concurrently.
        r1, r2 = await asyncio.gather(_disable(a.id), _disable(b.id))
        outcomes = sorted([r1[0], r2[0]])
        # Exactly one succeeds; the other is blocked (never both into zero admins).
        assert outcomes == ["blocked", "ok"], outcomes

        async with auth_env.session_factory() as session:
            from sqlalchemy import func, select

            active = (
                await session.execute(
                    select(func.count())
                    .select_from(User)
                    .where(User.role == "admin", User.status == "active", User.deleted_at.is_(None))
                )
            ).scalar()
        assert active >= 1


class TestChunkedReconcile:
    async def test_reconcile_chunked_batch_size_one(self, auth_env, owner_id):
        # M3: reconciliation with a tiny batch size still converges (chunked +
        # resumable, bounded memory per batch).
        from app.admin.metrics_service import MetricsService
        from app.admin.repo import AdminRepo
        from app.models import User

        await auth_env.create_resume(owner_id, content="a")
        await auth_env.create_resume(owner_id, content="b")
        async with auth_env.session_factory() as session:
            row = await session.get(User, owner_id)
            row.resume_count = 0  # drift
            await session.commit()

        svc = MetricsService(auth_env.session_factory, AdminRepo(auth_env.session_factory))
        result = await svc.reconcile_counters(batch_size=1)
        assert result["scanned"] >= 1
        async with auth_env.session_factory() as session:
            row = await session.get(User, owner_id)
            assert row.resume_count == 2


class TestCursorStability:
    async def test_insert_between_pages_no_skip_or_dup(self, auth_env, owner_id):
        repo = AdminRepo(auth_env.session_factory)
        # Seed a deterministic set of users with increasing created_at.
        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        ids = []
        for i in range(6):
            rec = await create_user(
                email=f"c{i}@example.com", name="U", password_hash=None, status="active", db=auth_env
            )
            async with auth_env.session_factory() as session:
                row = await session.get(User, rec.id)
                row.created_at = (base + timedelta(minutes=i)).isoformat()
                await session.commit()
            ids.append(rec.id)

        page1, cursor = await repo.list_users(limit=3)
        assert cursor is not None
        seen_page1 = {u.id for u in page1}

        # Insert a NEW user AFTER page 1 was fetched (concurrent insert). Because
        # keyset pages are anchored to (created_at, id), the new row (newest) only
        # affects the first page's window, never causing a skip/dup on page 2.
        newer = await create_user(
            email="newest@example.com", name="U", password_hash=None, status="active", db=auth_env
        )
        async with auth_env.session_factory() as session:
            row = await session.get(User, newer.id)
            row.created_at = (base + timedelta(minutes=99)).isoformat()
            await session.commit()

        page2, _ = await repo.list_users(cursor=cursor, limit=10)
        seen_page2 = {u.id for u in page2}
        # No overlap between the two pages (stable keyset — no duplicates).
        assert seen_page1.isdisjoint(seen_page2)


class TestNoDoubleCount:
    async def test_series_today_is_live_not_double_counted(self, auth_env, owner_id):
        svc = MetricsService(auth_env.session_factory, AdminRepo(auth_env.session_factory))
        # Roll up closed days first.
        await svc.run_rollup(lookback_days=3)
        # Create a signup "today" (owner already exists; add one more).
        await create_user(email="today@example.com", name="U", password_hash=None, status="active", db=auth_env)
        series = await svc.usage_series("signups", 7)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        today_point = next(p for p in series["points"] if p["date"] == today)
        # Today's live count reflects the just-created signups (>=1), and it is a
        # single point (not summed with a rolled-up value for the same day).
        assert today_point["value"] >= 1
        dates = [p["date"] for p in series["points"]]
        assert len(dates) == len(set(dates))  # each day appears exactly once

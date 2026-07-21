"""P3 cross-feature verification (Task 10): security + concurrency + recovery.

Consolidates the cross-cutting guarantees not already covered by the per-feature
suites: IDOR across users on reminders/interviews, the AI cost-guard
(never-auto-fire), unread-counter correctness under interleaved read/create, and
failure-recovery (scheduler catch-up never double-fires; search rebuild after
"indexer down").
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.auth.kvstore.local import LocalKVStore
from app.main import app
from app.models import User


def _client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _past() -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()


async def _second_user(db) -> str:
    async with db.session_factory() as s:
        s.add(User(id="user-b", email="b@localhost", name="B"))
        await s.commit()
    return "user-b"


async def _app(db, owner_id):
    job = await db.create_job(owner_id, content="JD")
    return await db.create_application(owner_id, job_id=job["job_id"], resume_id="r1", company="Acme")


# ---------------------------------------------------------------------------
# Security: IDOR across users (data layer - the authz matrix covers anon->401)
# ---------------------------------------------------------------------------


class TestIdor:
    async def test_reminder_not_visible_cross_user(self, isolated_db, owner_id):
        from app.scheduling.repo import get_scheduling_repo

        other = await _second_user(isolated_db)
        card = await _app(isolated_db, owner_id)
        repo = get_scheduling_repo()
        r = await repo.create_reminder(owner_id, card["application_id"], due_at=_past(), tz="UTC", note="mine", recurrence=None)
        # user-b cannot read or mutate owner's reminder.
        assert await repo.get_reminder(other, r["id"]) is None
        assert await repo.update_reminder(other, r["id"], {"note": "hax"}) is None

    async def test_interview_not_visible_cross_user(self, isolated_db, owner_id):
        from app.scheduling.repo import get_scheduling_repo

        other = await _second_user(isolated_db)
        card = await _app(isolated_db, owner_id)
        repo = get_scheduling_repo()
        iv = await repo.create_interview(
            owner_id, card["application_id"], starts_at=_past(), tz="UTC",
            duration_min=30, kind="screen", location=None, notes=None, lead_times=[],
        )
        assert await repo.get_interview(other, iv["id"]) is None
        assert await repo.update_interview(other, iv["id"], {"kind": "onsite"}) is None


# ---------------------------------------------------------------------------
# AI cost-guard: never auto-fires (R15)
# ---------------------------------------------------------------------------


class TestAiCostGuard:
    async def test_jd_fetch_without_use_ai_never_calls_llm(self, isolated_db, owner_id):
        from app.platform import reset_container

        reset_container()
        html = "<html><body><article>" + ("A real job description here. " * 40) + "</article></body></html>"
        with patch("app.jd.service.fetch_url_safely", new=AsyncMock(return_value=html)), \
             patch("app.llm.complete", new=AsyncMock(return_value="cleaned")) as mock_llm:
            async with _client() as c:
                resp = await c.post("/api/v1/jobs/fetch-url", json={"url": "https://x.example.com/j", "use_ai": False})
        assert resp.status_code == 200
        mock_llm.assert_not_called()  # opt-in only - never auto-fires
        reset_container()


# ---------------------------------------------------------------------------
# Concurrency: unread counter correctness + no double-fire
# ---------------------------------------------------------------------------


class TestConcurrency:
    async def test_unread_counter_matches_after_interleaved_ops(self, isolated_db, owner_id):
        from app.notifications.service import NotificationService

        svc = NotificationService()
        a = await svc.notify(owner_id, type="t", title="a")
        await svc.notify(owner_id, type="t", title="b")
        await svc.notify(owner_id, type="t", title="c")
        await svc._repo.mark_read(owner_id, a["id"])
        # 3 created, 1 read -> 2 unread; reconcile must agree with the counter.
        assert await svc._repo.unread_count(owner_id) == 2
        assert await svc._repo.reconcile_unread(owner_id) == 2

    async def test_scheduler_catch_up_no_double_fire(self, isolated_db, owner_id):
        from app.scheduling.repo import get_scheduling_repo
        from app.scheduling.scheduler import run_due_scans

        card = await _app(isolated_db, owner_id)
        await get_scheduling_repo().create_reminder(
            owner_id, card["application_id"], due_at=_past(), tz="UTC", note="x", recurrence=None
        )
        kv = LocalKVStore()
        # Simulate a backlog catch-up: multiple scans in a row.
        for _ in range(3):
            await run_due_scans(kvstore=kv)
        from app.events.jobs import run_productivity_jobs

        await run_productivity_jobs(kvstore=kv)
        from app.notifications.service import get_notification_service

        fired = [r for r in await get_notification_service()._repo.list(owner_id, limit=20) if r["type"] == "reminder.due"]
        assert len(fired) == 1  # exactly-once despite repeated scans


# ---------------------------------------------------------------------------
# Failure recovery
# ---------------------------------------------------------------------------


class TestFailureRecovery:
    async def test_search_rebuild_after_indexer_down(self, isolated_db, owner_id):
        # "Indexer down": create data but never drain the outbox -> index empty.
        await isolated_db.create_resume(owner_id, content="{}", content_type="json", processed_data={"summary": "recoverable token"})
        from app.search.indexer import rebuild_user_index, search_drift
        from app.search.repo import get_search_repo

        assert (await search_drift(owner_id))["missing"] >= 1
        await rebuild_user_index(owner_id)
        assert (await search_drift(owner_id))["missing"] == 0
        assert len(await get_search_repo().search(owner_id, "recoverable", limit=10)) == 1

    async def test_email_failure_leaves_row_for_retry(self, isolated_db, owner_id):
        from app.notifications.service import NotificationService

        svc = NotificationService()
        await svc.notify(owner_id, type="t", title="Security", category="security")  # email-on
        with patch("app.auth.email.send_email_safe", new=AsyncMock(return_value=False)):
            result = await svc.process_pending_emails()
        assert result["sent"] == 0
        # Not marked emailed -> will retry next pass (DLQ-like behavior).
        pending = await svc._repo.emails_pending(limit=10)
        assert any(p["title"] == "Security" for p in pending)

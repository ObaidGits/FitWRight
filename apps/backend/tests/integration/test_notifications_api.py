"""Integration tests for notifications (real isolated DB, P3 §B, R4–R6).

Covers the single-writer service (create/dedupe/unread-counter/prefs/content-
safety), the REST surface (list/read/read-all/dismiss/dismiss-group/prefs/
unread-count), the outbox→notification consumer pipeline, and the email worker
(immediate + kill-switch + digest).
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import settings as app_settings
from app.main import app
from app.notifications.service import NotificationService, get_notification_service


def _client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture
def svc(isolated_db) -> NotificationService:
    # Fresh service bound (lazily) to the isolated db.
    return NotificationService()


# ---------------------------------------------------------------------------
# Service: create, dedupe, unread counter, content-safety
# ---------------------------------------------------------------------------


class TestService:
    async def test_notify_creates_and_bumps_unread(self, svc, owner_id):
        n = await svc.notify(owner_id, type="t", title="Hello", category="system")
        assert n is not None
        assert await svc._repo.unread_count(owner_id) == 1

    async def test_dedupe_key_is_idempotent(self, svc, owner_id):
        first = await svc.notify(owner_id, type="t", title="A", dedupe_key="k1")
        second = await svc.notify(owner_id, type="t", title="A", dedupe_key="k1")
        assert first is not None
        assert second is None  # duplicate suppressed (R5.2)
        assert await svc._repo.unread_count(owner_id) == 1

    async def test_content_safety_strips_newlines(self, svc, owner_id):
        n = await svc.notify(owner_id, type="t", title="Line1\r\nInjected: header")
        assert "\n" not in n["title"] and "\r" not in n["title"]

    async def test_invalid_category_priority_coerced(self, svc, owner_id):
        n = await svc.notify(owner_id, type="t", title="x", category="bogus", priority="urgent")
        assert n["category"] == "system"
        assert n["priority"] == "normal"

    async def test_in_app_off_suppresses_and_no_unread(self, svc, owner_id):
        await svc._repo.set_pref(owner_id, "system", in_app=False, email=False)
        n = await svc.notify(owner_id, type="t", title="x", category="system")
        assert n is None  # fully opted out
        assert await svc._repo.unread_count(owner_id) == 0


# ---------------------------------------------------------------------------
# REST surface
# ---------------------------------------------------------------------------


class TestApi:
    async def test_list_and_unread_count(self, svc, owner_id):
        await svc.notify(owner_id, type="t", title="one")
        await svc.notify(owner_id, type="t", title="two")
        async with _client() as c:
            listed = (await c.get("/api/v1/notifications")).json()
            count = (await c.get("/api/v1/notifications/unread-count")).json()
        assert len(listed["items"]) == 2
        assert count["unread"] == 2
        assert count["transport"] == "polling"
        assert count["poll_interval_seconds"] >= 15

    async def test_mark_read_decrements(self, svc, owner_id):
        n = await svc.notify(owner_id, type="t", title="one")
        async with _client() as c:
            resp = await c.post(f"/api/v1/notifications/{n['id']}/read")
            count = (await c.get("/api/v1/notifications/unread-count")).json()
        assert resp.status_code == 200
        assert count["unread"] == 0

    async def test_read_all(self, svc, owner_id):
        await svc.notify(owner_id, type="t", title="one")
        await svc.notify(owner_id, type="t", title="two")
        async with _client() as c:
            resp = await c.post("/api/v1/notifications/read-all")
            count = (await c.get("/api/v1/notifications/unread-count")).json()
        assert resp.json()["affected"] == 2
        assert count["unread"] == 0

    async def test_dismiss_removes_from_list(self, svc, owner_id):
        n = await svc.notify(owner_id, type="t", title="one")
        async with _client() as c:
            await c.delete(f"/api/v1/notifications/{n['id']}")
            listed = (await c.get("/api/v1/notifications")).json()
        assert listed["items"] == []

    async def test_dismiss_group(self, svc, owner_id):
        await svc.notify(owner_id, type="t", title="a", group_key="g1")
        await svc.notify(owner_id, type="t", title="b", group_key="g1")
        await svc.notify(owner_id, type="t", title="c", group_key="g2")
        async with _client() as c:
            resp = await c.post("/api/v1/notifications/dismiss-group", json={"group_key": "g1"})
            listed = (await c.get("/api/v1/notifications")).json()
        assert resp.json()["affected"] == 2
        assert len(listed["items"]) == 1

    async def test_filter_by_category_and_unread(self, svc, owner_id):
        await svc.notify(owner_id, type="t", title="sys", category="system")
        ai = await svc.notify(owner_id, type="t", title="ai", category="ai")
        async with _client() as c:
            only_ai = (await c.get("/api/v1/notifications?category=ai")).json()
        assert len(only_ai["items"]) == 1
        assert only_ai["items"][0]["id"] == ai["id"]

    async def test_prefs_get_and_update(self, svc, owner_id):
        async with _client() as c:
            defaults = (await c.get("/api/v1/notifications/prefs")).json()
            assert defaults["digest"] == "off"
            assert defaults["categories"]["security"]["email"] is True
            updated = (
                await c.put(
                    "/api/v1/notifications/prefs",
                    json={
                        "categories": [{"category": "ai", "in_app": False, "email": True}],
                        "digest": "daily",
                    },
                )
            ).json()
        assert updated["digest"] == "daily"
        assert updated["categories"]["ai"] == {"in_app": False, "email": True}

    async def test_feature_flag_off_returns_404(self, svc, owner_id, monkeypatch):
        monkeypatch.setattr(app_settings, "notifications_enabled", False)
        async with _client() as c:
            resp = await c.get("/api/v1/notifications")
        assert resp.status_code == 404

    async def test_mark_read_foreign_404(self, svc, owner_id):
        async with _client() as c:
            resp = await c.post("/api/v1/notifications/ghost/read")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Outbox → notification consumer pipeline
# ---------------------------------------------------------------------------


class TestConsumerPipeline:
    async def test_resume_parsed_event_creates_notification(self, isolated_db, owner_id):
        from app.auth.kvstore.local import LocalKVStore
        from app.events import EventType, emit
        from app.events.jobs import run_productivity_jobs

        await emit(EventType.RESUME_PARSED, {"resume_id": "r1"}, user_id=owner_id)
        await run_productivity_jobs(kvstore=LocalKVStore())

        svc = get_notification_service()
        rows = await svc._repo.list(owner_id, limit=10)
        assert any(r["type"] == EventType.RESUME_PARSED.value for r in rows)

    async def test_duplicate_event_deduped_across_replay(self, isolated_db, owner_id):
        from app.auth.kvstore.local import LocalKVStore
        from app.events import EventType, emit
        from app.events.jobs import run_productivity_jobs

        kv = LocalKVStore()
        # Same resource emitted twice → dedupe_key collapses to one notification.
        await emit(EventType.RESUME_PARSED, {"resume_id": "rX"}, user_id=owner_id)
        await run_productivity_jobs(kvstore=kv)
        await emit(EventType.RESUME_PARSED, {"resume_id": "rX"}, user_id=owner_id)
        await run_productivity_jobs(kvstore=kv)

        svc = get_notification_service()
        rows = [r for r in await svc._repo.list(owner_id, limit=10) if r["node_id"] == "rX"]
        assert len(rows) == 1


# ---------------------------------------------------------------------------
# Email worker
# ---------------------------------------------------------------------------


class TestEmailWorker:
    async def test_immediate_email_sent_for_email_on_category(self, svc, owner_id):
        # security defaults to email-on; digest off → immediate send.
        await svc.notify(owner_id, type="t", title="Security alert", category="security")
        result = await svc.process_pending_emails()
        assert result["sent"] == 1
        # Re-run → nothing pending (emailed_at stamped).
        assert (await svc.process_pending_emails())["sent"] == 0

    async def test_email_off_category_is_skipped(self, svc, owner_id):
        await svc.notify(owner_id, type="t", title="sys", category="system")  # email off
        result = await svc.process_pending_emails()
        assert result["sent"] == 0
        assert result["skipped"] == 1

    async def test_kill_switch_pauses_email(self, svc, owner_id, monkeypatch):
        monkeypatch.setattr(app_settings, "notifications_email_enabled", False)
        await svc.notify(owner_id, type="t", title="Security alert", category="security")
        result = await svc.process_pending_emails()
        assert result.get("disabled") == 1
        # Re-enable → now it flushes.
        monkeypatch.setattr(app_settings, "notifications_email_enabled", True)
        assert (await svc.process_pending_emails())["sent"] == 1

    async def test_digest_defers_then_batches(self, svc, owner_id):
        await svc._repo.set_pref(owner_id, "ai", in_app=True, email=True)
        await svc._repo.set_digest(owner_id, "daily")
        await svc.notify(owner_id, type="t", title="ai one", category="ai", priority="normal")
        await svc.notify(owner_id, type="t", title="ai two", category="ai", priority="normal")
        # Immediate pass defers low/normal digest items.
        immediate = await svc.process_pending_emails()
        assert immediate["sent"] == 0 and immediate["deferred"] == 2
        # Digest batches them into a single email.
        digest = await svc.process_digests()
        assert digest["digests_sent"] == 1


# ---------------------------------------------------------------------------
# Retention
# ---------------------------------------------------------------------------


class TestRetention:
    async def test_prunes_read_notifications(self, svc, owner_id, monkeypatch):
        from app.auth.kvstore.local import LocalKVStore
        from app.retention.jobs import run_retention_jobs

        n = await svc.notify(owner_id, type="t", title="old")
        await svc._repo.mark_read(owner_id, n["id"])
        # Force the row's created_at into the far past + zero-day window.
        monkeypatch.setattr(app_settings, "notification_retention_days", 0)
        from app import database
        from app.models import Notification
        from sqlalchemy import update

        async with database.db.session_factory() as s:
            await s.execute(
                update(Notification).where(Notification.id == n["id"]).values(
                    created_at="2000-01-01T00:00:00+00:00"
                )
            )
            await s.commit()
        result = await run_retention_jobs(kvstore=LocalKVStore())
        assert result["notifications_pruned"] == 1

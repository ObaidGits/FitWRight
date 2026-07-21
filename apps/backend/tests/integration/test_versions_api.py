"""Integration tests for resume version history (real isolated DB, P3 §A).

Exercises the full stack: capture (dedupe/debounce/cap-prune), the metadata-only
list, on-demand data fetch, non-destructive restore + CAS, undo-last-ai, compare,
ownership 404s, and the feature-flag kill-switch.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import settings as app_settings
from app.main import app
from app.versions import service as vs


def _client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _resume(isolated_db, owner_id, data):
    return await isolated_db.create_resume(
        owner_id, content="{}", content_type="json", processed_data=data,
        processing_status="ready",
    )


# ---------------------------------------------------------------------------
# Capture: dedupe, debounce, cap/prune
# ---------------------------------------------------------------------------


class TestCapture:
    async def test_capture_creates_snapshot(self, isolated_db, owner_id):
        r = await _resume(isolated_db, owner_id, {"summary": "v1"})
        created = await vs.capture_snapshot(owner_id, r["resume_id"], {"summary": "v1"}, "original")
        assert created is not None
        assert created["source"] == "original"
        assert created["size_bytes"] > 0

    async def test_identical_content_is_deduped(self, isolated_db, owner_id):
        r = await _resume(isolated_db, owner_id, {"summary": "same"})
        first = await vs.capture_snapshot(owner_id, r["resume_id"], {"summary": "same"}, "manual", debounce_seconds=0)
        second = await vs.capture_snapshot(owner_id, r["resume_id"], {"summary": "same"}, "manual", debounce_seconds=0)
        assert first is not None
        assert second is None  # no-op dedupe (R1.2)
        assert await isolated_db.count_resume_versions(owner_id, r["resume_id"]) == 1

    async def test_rapid_manual_saves_debounced(self, isolated_db, owner_id):
        r = await _resume(isolated_db, owner_id, {"summary": "a"})
        await vs.capture_snapshot(owner_id, r["resume_id"], {"summary": "a"}, "manual", debounce_seconds=300)
        # Different content but within the debounce window -> coalesced.
        second = await vs.capture_snapshot(owner_id, r["resume_id"], {"summary": "b"}, "manual", debounce_seconds=300)
        assert second is None
        assert await isolated_db.count_resume_versions(owner_id, r["resume_id"]) == 1

    async def test_ai_snapshot_not_debounced_after_manual(self, isolated_db, owner_id):
        r = await _resume(isolated_db, owner_id, {"summary": "a"})
        await vs.capture_snapshot(owner_id, r["resume_id"], {"summary": "a"}, "manual", debounce_seconds=300)
        ai = await vs.capture_snapshot(owner_id, r["resume_id"], {"summary": "ai"}, "ai", debounce_seconds=300)
        assert ai is not None  # only manual->manual is debounced

    async def test_cap_prunes_oldest_non_original(self, isolated_db, owner_id, monkeypatch):
        monkeypatch.setattr(app_settings, "version_history_cap", 3)
        r = await _resume(isolated_db, owner_id, {"v": 0})
        await vs.capture_snapshot(owner_id, r["resume_id"], {"v": 0}, "original", debounce_seconds=0)
        for i in range(1, 6):
            await vs.capture_snapshot(owner_id, r["resume_id"], {"v": i}, "manual", debounce_seconds=0)
        # Cap is 3 total; the original must always be retained.
        rows = await isolated_db.list_resume_versions(owner_id, r["resume_id"], limit=100)
        assert len(rows) == 3
        assert any(row["source"] == "original" for row in rows)

    async def test_none_processed_data_is_noop(self, isolated_db, owner_id):
        r = await _resume(isolated_db, owner_id, {"a": 1})
        assert await vs.capture_snapshot(owner_id, r["resume_id"], None, "manual") is None


# ---------------------------------------------------------------------------
# List / get endpoints
# ---------------------------------------------------------------------------


class TestListAndGet:
    async def test_list_is_metadata_only_newest_first(self, isolated_db, owner_id):
        r = await _resume(isolated_db, owner_id, {"v": 0})
        await vs.capture_snapshot(owner_id, r["resume_id"], {"v": 0}, "original", debounce_seconds=0)
        await vs.capture_snapshot(owner_id, r["resume_id"], {"v": 1}, "ai", debounce_seconds=0)
        async with _client() as c:
            resp = await c.get(f"/api/v1/resumes/{r['resume_id']}/versions")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 2
        assert items[0]["source"] == "ai"  # newest first
        assert "processed_data" not in items[0]  # metadata only (R3.1)

    async def test_get_single_returns_decompressed_data(self, isolated_db, owner_id):
        r = await _resume(isolated_db, owner_id, {"summary": "hello"})
        snap = await vs.capture_snapshot(owner_id, r["resume_id"], {"summary": "hello"}, "original")
        async with _client() as c:
            resp = await c.get(f"/api/v1/resumes/{r['resume_id']}/versions/{snap['id']}")
        assert resp.status_code == 200
        assert resp.json()["processed_data"] == {"summary": "hello"}

    async def test_pagination_cursor(self, isolated_db, owner_id):
        r = await _resume(isolated_db, owner_id, {"v": 0})
        for i in range(5):
            await vs.capture_snapshot(owner_id, r["resume_id"], {"v": i}, "manual", debounce_seconds=0)
        async with _client() as c:
            page1 = (await c.get(f"/api/v1/resumes/{r['resume_id']}/versions?limit=2")).json()
        assert len(page1["items"]) == 2
        assert page1["next_cursor"] is not None
        async with _client() as c:
            page2 = (await c.get(
                f"/api/v1/resumes/{r['resume_id']}/versions?limit=2&cursor={page1['next_cursor']}"
            )).json()
        assert len(page2["items"]) == 2
        # No overlap between pages.
        ids1 = {i["id"] for i in page1["items"]}
        ids2 = {i["id"] for i in page2["items"]}
        assert ids1.isdisjoint(ids2)


# ---------------------------------------------------------------------------
# Restore / undo (non-destructive)
# ---------------------------------------------------------------------------


class TestRestore:
    async def test_restore_is_reversible(self, isolated_db, owner_id):
        r = await _resume(isolated_db, owner_id, {"summary": "current"})
        old = await vs.capture_snapshot(owner_id, r["resume_id"], {"summary": "old"}, "original")
        async with _client() as c:
            resp = await c.post(
                f"/api/v1/resumes/{r['resume_id']}/versions/{old['id']}/restore", json={}
            )
        assert resp.status_code == 200
        assert resp.json()["processed_data"]["summary"] == "old"
        # The resume now holds the restored data.
        refreshed = await isolated_db.get_resume(owner_id, r["resume_id"])
        assert refreshed["processed_data"]["summary"] == "old"
        # Restore snapshotted the *current* state first -> it is recoverable.
        rows = await isolated_db.list_resume_versions(owner_id, r["resume_id"], limit=100)
        assert any(row["label"] == "Before restore" for row in rows)

    async def test_restore_cas_conflict_returns_409(self, isolated_db, owner_id):
        r = await _resume(isolated_db, owner_id, {"summary": "current"})
        snap = await vs.capture_snapshot(owner_id, r["resume_id"], {"summary": "old"}, "original")
        async with _client() as c:
            resp = await c.post(
                f"/api/v1/resumes/{r['resume_id']}/versions/{snap['id']}/restore",
                json={"expected_updated_at": "1999-01-01T00:00:00+00:00"},
            )
        assert resp.status_code == 409

    async def test_restore_foreign_version_404(self, isolated_db, owner_id):
        r = await _resume(isolated_db, owner_id, {"a": 1})
        async with _client() as c:
            resp = await c.post(
                f"/api/v1/resumes/{r['resume_id']}/versions/ghost/restore", json={}
            )
        assert resp.status_code == 404

    async def test_undo_last_ai(self, isolated_db, owner_id):
        r = await _resume(isolated_db, owner_id, {"v": "current"})
        await vs.capture_snapshot(owner_id, r["resume_id"], {"v": "pre-ai"}, "original", debounce_seconds=0)
        await vs.capture_snapshot(owner_id, r["resume_id"], {"v": "ai-output"}, "ai", debounce_seconds=0)
        async with _client() as c:
            resp = await c.post(f"/api/v1/resumes/{r['resume_id']}/undo-last-ai")
        assert resp.status_code == 200
        assert resp.json()["processed_data"]["v"] == "pre-ai"

    async def test_undo_last_ai_nothing_to_undo(self, isolated_db, owner_id):
        r = await _resume(isolated_db, owner_id, {"v": 1})
        await vs.capture_snapshot(owner_id, r["resume_id"], {"v": 1}, "original")
        async with _client() as c:
            resp = await c.post(f"/api/v1/resumes/{r['resume_id']}/undo-last-ai")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Compare
# ---------------------------------------------------------------------------


class TestCompare:
    async def test_compare_returns_field_diff(self, isolated_db, owner_id):
        r = await _resume(isolated_db, owner_id, {"summary": "z"})
        a = await vs.capture_snapshot(owner_id, r["resume_id"], {"summary": "old"}, "original", debounce_seconds=0)
        b = await vs.capture_snapshot(owner_id, r["resume_id"], {"summary": "new"}, "ai", debounce_seconds=0)
        async with _client() as c:
            resp = await c.get(
                f"/api/v1/resumes/{r['resume_id']}/versions/compare?a={a['id']}&b={b['id']}"
            )
        assert resp.status_code == 200
        changes = resp.json()["changes"]
        assert changes == [
            {"path": "summary", "action": "changed", "before": "old", "after": "new"}
        ]

    async def test_compare_foreign_version_404(self, isolated_db, owner_id):
        r = await _resume(isolated_db, owner_id, {"a": 1})
        snap = await vs.capture_snapshot(owner_id, r["resume_id"], {"a": 1}, "original")
        async with _client() as c:
            resp = await c.get(
                f"/api/v1/resumes/{r['resume_id']}/versions/compare?a={snap['id']}&b=ghost"
            )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Ownership + feature flag
# ---------------------------------------------------------------------------


class TestScopingAndFlag:
    async def test_foreign_resume_404(self, isolated_db, owner_id):
        async with _client() as c:
            resp = await c.get("/api/v1/resumes/does-not-exist/versions")
        assert resp.status_code == 404

    async def test_versions_scoped_to_other_user(self, isolated_db, owner_id):
        # A snapshot owned by another user must be invisible (404, not listed).
        from app.models import User

        async with isolated_db.session_factory() as session:
            session.add(User(id="other-user", email="other@localhost", name="Other"))
            await session.commit()
        r = await _resume(isolated_db, owner_id, {"a": 1})
        snap = await vs.capture_snapshot("other-user", r["resume_id"], {"a": 1}, "original")
        async with _client() as c:
            resp = await c.get(f"/api/v1/resumes/{r['resume_id']}/versions/{snap['id']}")
        assert resp.status_code == 404

    async def test_feature_flag_off_returns_404(self, isolated_db, owner_id, monkeypatch):
        monkeypatch.setattr(app_settings, "version_history_enabled", False)
        r = await _resume(isolated_db, owner_id, {"a": 1})
        async with _client() as c:
            resp = await c.get(f"/api/v1/resumes/{r['resume_id']}/versions")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "version_history_disabled"

"""Integration tests for global search (real isolated DB + SQLite FTS5, P3 §C).

Covers the outbox->index pipeline, FTS ranking + scoping-in-SQL (no cross-user
leakage), filters, cursor pagination, rebuild, drift detection, and the
feature-flag kill-switch.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.auth.kvstore.local import LocalKVStore
from app.config import settings as app_settings
from app.events.jobs import run_productivity_jobs
from app.main import app
from app.search.indexer import index_node, rebuild_user_index, search_drift
from app.search.repo import get_search_repo


def _client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _drain(kv=None):
    await run_productivity_jobs(kvstore=kv or LocalKVStore())


async def _resume(db, uid, data, **kw):
    return await db.create_resume(uid, content="{}", content_type="json", processed_data=data, **kw)


# ---------------------------------------------------------------------------
# Outbox -> index pipeline
# ---------------------------------------------------------------------------


class TestIndexingPipeline:
    async def test_created_resume_becomes_searchable(self, isolated_db, owner_id):
        await _resume(isolated_db, owner_id, {"summary": "Python FastAPI backend engineer"})
        await _drain()  # process the resume.upserted event emitted by the facade
        rows = await get_search_repo().search(owner_id, "FastAPI", limit=10)
        assert len(rows) == 1
        assert rows[0]["node_type"] == "resume"

    async def test_deleted_resume_removed_from_index(self, isolated_db, owner_id):
        r = await _resume(isolated_db, owner_id, {"summary": "unique_token_xyz"})
        await _drain()
        assert len(await get_search_repo().search(owner_id, "unique_token_xyz", limit=10)) == 1
        await isolated_db.delete_resume(owner_id, r["resume_id"])
        await _drain()
        assert await get_search_repo().search(owner_id, "unique_token_xyz", limit=10) == []

    async def test_application_indexed_by_company(self, isolated_db, owner_id):
        job = await isolated_db.create_job(owner_id, content="JD")
        await isolated_db.create_application(
            owner_id, job_id=job["job_id"], resume_id="r1", company="Acme Rockets", role="SRE"
        )
        await _drain()
        rows = await get_search_repo().search(owner_id, "Acme", limit=10)
        assert any(r["node_type"] == "application" for r in rows)


# ---------------------------------------------------------------------------
# Scoping (no cross-user leakage) + ranking + filters + pagination
# ---------------------------------------------------------------------------


class TestSearchQuery:
    async def test_scoped_to_user(self, isolated_db, owner_id):
        from app.models import User

        async with isolated_db.session_factory() as s:
            s.add(User(id="other", email="o@localhost", name="O"))
            await s.commit()
        # Index a doc for another user with the same term.
        await index_node("other", "resume", "foreign-1")
        await get_search_repo().upsert(
            user_id="other", node_type="resume", node_id="foreign-1",
            title="Secret", body="confidential_marker", status=None,
        )
        await _resume(isolated_db, owner_id, {"summary": "confidential_marker mine"})
        await _drain()
        rows = await get_search_repo().search(owner_id, "confidential_marker", limit=10)
        # Only the owner's doc - never the other user's.
        assert all(r["node_id"] != "foreign-1" for r in rows)
        assert len(rows) == 1

    async def test_api_search_endpoint(self, isolated_db, owner_id):
        await _resume(isolated_db, owner_id, {"summary": "Kubernetes platform engineer"})
        await _drain()
        async with _client() as c:
            resp = await c.get("/api/v1/search?q=Kubernetes")
        assert resp.status_code == 200
        body = resp.json()
        assert body["query"] == "Kubernetes"
        assert len(body["items"]) == 1

    async def test_type_filter(self, isolated_db, owner_id):
        await _resume(isolated_db, owner_id, {"summary": "shared_term resume"})
        job = await isolated_db.create_job(owner_id, content="shared_term in the job")
        await isolated_db.update_job(owner_id, job["job_id"], {"company": "shared_term Co"})
        await _drain()
        async with _client() as c:
            only_resume = (await c.get("/api/v1/search?q=shared_term&types=resume")).json()
        assert all(i["node_type"] == "resume" for i in only_resume["items"])
        assert len(only_resume["items"]) >= 1

    async def test_cursor_pagination(self, isolated_db, owner_id):
        for i in range(5):
            await _resume(isolated_db, owner_id, {"summary": f"commonword entry {i}"})
        await _drain()
        async with _client() as c:
            page1 = (await c.get("/api/v1/search?q=commonword&limit=2")).json()
            assert len(page1["items"]) == 2
            assert page1["next_cursor"]
            page2 = (
                await c.get(f"/api/v1/search?q=commonword&limit=2&cursor={page1['next_cursor']}")
            ).json()
        ids1 = {i["node_id"] for i in page1["items"]}
        ids2 = {i["node_id"] for i in page2["items"]}
        assert ids1.isdisjoint(ids2)

    async def test_no_match_empty(self, isolated_db, owner_id):
        await _resume(isolated_db, owner_id, {"summary": "alpha"})
        await _drain()
        async with _client() as c:
            resp = (await c.get("/api/v1/search?q=zzzznomatch")).json()
        assert resp["items"] == []

    async def test_injection_query_is_safe(self, isolated_db, owner_id):
        await _resume(isolated_db, owner_id, {"summary": "safe content"})
        await _drain()
        async with _client() as c:
            # FTS/SQL metacharacters must neither error nor widen scope.
            resp = await c.get('/api/v1/search?q=%22 OR 1=1 --')
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Rebuild + drift + flag
# ---------------------------------------------------------------------------


class TestRebuildAndDrift:
    async def test_reindex_endpoint_rebuilds(self, isolated_db, owner_id):
        await _resume(isolated_db, owner_id, {"summary": "rebuildme token"})
        # Do NOT drain - index is empty; reindex builds from source.
        async with _client() as c:
            resp = await c.post("/api/v1/search/reindex")
        assert resp.status_code == 200
        assert resp.json()["indexed"]["resume"] == 1
        rows = await get_search_repo().search(owner_id, "rebuildme", limit=10)
        assert len(rows) == 1

    async def test_drift_detects_unindexed(self, isolated_db, owner_id):
        await _resume(isolated_db, owner_id, {"summary": "x"})
        # Not drained -> source has 1, index has 0 -> missing 1.
        drift = await search_drift(owner_id)
        assert drift["missing"] == 1
        await rebuild_user_index(owner_id)
        assert (await search_drift(owner_id))["missing"] == 0

    async def test_feature_flag_off_404(self, isolated_db, owner_id, monkeypatch):
        monkeypatch.setattr(app_settings, "search_enabled", False)
        async with _client() as c:
            resp = await c.get("/api/v1/search?q=x")
        assert resp.status_code == 404

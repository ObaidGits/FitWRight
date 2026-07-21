"""Integration tests for the Professional Profile API (real isolated DB).

Exercises the full stack: lazy get-or-create (backfill from master resume),
version-CAS PATCH (updated / conflict), completeness, resume generation
(preview + persist), version snapshot list/get/restore, and the feature-flag
kill-switch.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import settings as app_settings
from app.main import app

BASE = "/api/v1/profile"


def _client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _master(isolated_db, owner_id, processed):
    return await isolated_db.create_resume(
        owner_id,
        content="{}",
        content_type="json",
        processed_data=processed,
        processing_status="ready",
        is_master=True,
    )


# ---------------------------------------------------------------------------
# Get / lazy create + backfill
# ---------------------------------------------------------------------------


class TestGetOrCreate:
    async def test_get_creates_empty_profile_when_no_resume(self, isolated_db, owner_id):
        async with _client() as c:
            resp = await c.get(BASE)
        assert resp.status_code == 200
        body = resp.json()
        assert body["version"] == 1
        # No master resume -> the document is empty apart from any identity
        # fallbacks carried from the user account (e.g. a bootstrap name).
        assert body["data"]["workExperience"] == []
        assert body["data"]["summary"] == ""

    async def test_get_backfills_from_master_resume(self, isolated_db, owner_id):
        await _master(
            isolated_db,
            owner_id,
            {
                "personalInfo": {"name": "Ada", "title": "Engineer", "email": "a@b.co"},
                "summary": "Builds systems.",
                "workExperience": [{"title": "SWE", "company": "Acme", "description": ["x"]}],
            },
        )
        async with _client() as c:
            resp = await c.get(BASE)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["identity"]["name"] == "Ada"
        assert data["summary"] == "Builds systems."
        assert data["workExperience"][0]["company"] == "Acme"
        assert resp.json()["completeness"] > 0

    async def test_get_is_idempotent(self, isolated_db, owner_id):
        async with _client() as c:
            first = (await c.get(BASE)).json()
            second = (await c.get(BASE)).json()
        # Same profile, unchanged version (no new create on second read).
        assert first["version"] == second["version"] == 1
        # A single profile row exists.
        assert await isolated_db.get_profile(owner_id) is not None


# ---------------------------------------------------------------------------
# Update (version CAS)
# ---------------------------------------------------------------------------


class TestUpdate:
    async def test_update_applies_and_bumps_version(self, isolated_db, owner_id):
        async with _client() as c:
            current = (await c.get(BASE)).json()
            data = current["data"]
            data["identity"]["name"] = "Grace Hopper"
            data["summary"] = "Compilers."
            resp = await c.patch(
                BASE, json={"data": data, "base_version": current["version"]}
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["identity"]["name"] == "Grace Hopper"
        assert body["version"] == current["version"] + 1
        assert body["completeness"] > 0

    async def test_stale_base_version_conflicts(self, isolated_db, owner_id):
        async with _client() as c:
            current = (await c.get(BASE)).json()
            data = current["data"]
            data["summary"] = "first"
            await c.patch(BASE, json={"data": data, "base_version": current["version"]})
            # Second write with the now-stale original base_version -> 409.
            data["summary"] = "second"
            resp = await c.patch(
                BASE, json={"data": data, "base_version": current["version"]}
            )
        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert detail["code"] == "version_conflict"
        assert detail["current_version"] == current["version"] + 1

    async def test_snapshot_captured_on_update(self, isolated_db, owner_id):
        async with _client() as c:
            current = (await c.get(BASE)).json()
            data = current["data"]
            data["summary"] = "snapshot me"
            await c.patch(BASE, json={"data": data, "base_version": current["version"]})
            versions = (await c.get(f"{BASE}/versions")).json()
        # Baseline (migration) + the manual update snapshot.
        assert len(versions["items"]) >= 2


# ---------------------------------------------------------------------------
# Completeness
# ---------------------------------------------------------------------------


class TestCompleteness:
    async def test_completeness_endpoint(self, isolated_db, owner_id):
        await _master(
            isolated_db, owner_id, {"personalInfo": {"name": "Ada"}}
        )
        async with _client() as c:
            resp = await c.get(f"{BASE}/completeness")
        assert resp.status_code == 200
        body = resp.json()
        assert 0 < body["score"] < 100
        assert any(s["key"] == "name" and s["done"] for s in body["suggestions"])
        assert any(s["key"] == "summary" and not s["done"] for s in body["suggestions"])


# ---------------------------------------------------------------------------
# Generate resume (Projection Engine)
# ---------------------------------------------------------------------------


class TestGenerateResume:
    async def test_preview_does_not_persist(self, isolated_db, owner_id):
        await _master(
            isolated_db,
            owner_id,
            {"personalInfo": {"name": "Ada", "title": "Eng"}, "summary": "S"},
        )
        before = len(await isolated_db.list_resumes(owner_id))
        async with _client() as c:
            resp = await c.post(f"{BASE}/generate-resume", json={"persist": False})
        assert resp.status_code == 200
        body = resp.json()
        assert body["resume_id"] is None
        assert body["resume_data"]["personalInfo"]["name"] == "Ada"
        after = len(await isolated_db.list_resumes(owner_id))
        assert after == before

    async def test_generate_with_template_and_section_visibility(self, isolated_db, owner_id):
        await _master(
            isolated_db,
            owner_id,
            {
                "personalInfo": {"name": "Ada", "title": "Eng"},
                "summary": "S",
                "education": [{"institution": "MIT", "degree": "BS"}],
            },
        )
        async with _client() as c:
            resp = await c.post(
                f"{BASE}/generate-resume",
                json={"persist": False, "template": "modern", "sections": {"education": False}},
            )
        assert resp.status_code == 200
        data = resp.json()["resume_data"]
        assert data["meta"]["template"] == "modern"
        edu_meta = next(m for m in data["sectionMeta"] if m["key"] == "education")
        assert edu_meta["isVisible"] is False

    async def test_persist_creates_resume(self, isolated_db, owner_id):
        await _master(
            isolated_db,
            owner_id,
            {"personalInfo": {"name": "Ada", "title": "Eng"}, "summary": "S"},
        )
        async with _client() as c:
            resp = await c.post(
                f"{BASE}/generate-resume",
                json={"persist": True, "title": "My Resume"},
            )
        assert resp.status_code == 200
        rid = resp.json()["resume_id"]
        assert rid is not None
        stored = await isolated_db.get_resume(owner_id, rid)
        assert stored is not None
        assert stored["processed_data"]["personalInfo"]["name"] == "Ada"
        assert stored["processed_data"]["meta"]["derivedFromProfile"] is True


# ---------------------------------------------------------------------------
# Versions (list / get / restore)
# ---------------------------------------------------------------------------


class TestVersions:
    async def test_list_get_and_restore(self, isolated_db, owner_id):
        async with _client() as c:
            current = (await c.get(BASE)).json()
            data = current["data"]
            data["summary"] = "version A"
            v2 = (
                await c.patch(
                    BASE, json={"data": data, "base_version": current["version"]}
                )
            ).json()

            # A later edit we will roll back.
            data2 = v2["data"]
            data2["summary"] = "version B"
            await c.patch(BASE, json={"data": data2, "base_version": v2["version"]})

            versions = (await c.get(f"{BASE}/versions")).json()["items"]
            assert len(versions) >= 2
            # Find the snapshot holding "version A".
            target = None
            for meta in versions:
                full = (await c.get(f"{BASE}/versions/{meta['id']}")).json()
                if full["data"]["summary"] == "version A":
                    target = meta["id"]
                    break
            assert target is not None

            restored = await c.post(f"{BASE}/versions/{target}/restore")
        assert restored.status_code == 200
        assert restored.json()["data"]["summary"] == "version A"

    async def test_get_foreign_version_404(self, isolated_db, owner_id):
        async with _client() as c:
            await c.get(BASE)  # ensure profile exists
            resp = await c.get(f"{BASE}/versions/does-not-exist")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Feature-flag kill switch
# ---------------------------------------------------------------------------


class TestKillSwitch:
    async def test_disabled_returns_404(self, isolated_db, owner_id, monkeypatch):
        monkeypatch.setattr(app_settings, "profile_enabled", False)
        async with _client() as c:
            assert (await c.get(BASE)).status_code == 404
            assert (await c.get(f"{BASE}/completeness")).status_code == 404
            assert (
                await c.post(f"{BASE}/generate-resume", json={})
            ).status_code == 404

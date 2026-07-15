"""Integration tests for Profile P3–P6 endpoints (real isolated DB).

Import (preview + apply), synchronization (draft apply + submitted-resume lock),
AI memory + suggestions + skill autocomplete, and the public/portfolio/JSON
Resume projections — all user-scoped and version-CAS guarded.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app

BASE = "/api/v1/profile"


def _client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _resume(isolated_db, owner_id, processed, *, is_master=False):
    return await isolated_db.create_resume(
        owner_id,
        content="{}",
        content_type="json",
        processed_data=processed,
        processing_status="ready",
        is_master=is_master,
    )


# ---------------------------------------------------------------------------
# Import (P3)
# ---------------------------------------------------------------------------


class TestImport:
    async def test_preview_and_apply_resume_import(self, isolated_db, owner_id):
        r = await _resume(
            isolated_db,
            owner_id,
            {
                "personalInfo": {"name": "Ada", "title": "Engineer"},
                "summary": "Imported summary.",
                "workExperience": [{"title": "SWE", "company": "Globex", "description": ["Shipped"]}],
            },
        )
        async with _client() as c:
            await c.get(BASE)  # ensure profile exists (empty)
            preview = await c.post(
                f"{BASE}/import/preview",
                json={"source": "resume", "payload": {"resume_id": r["resume_id"]}},
            )
            assert preview.status_code == 200
            body = preview.json()
            assert body["source"] == "resume"
            assert len(body["plan"]["operations"]) > 0

            current = (await c.get(BASE)).json()
            apply = await c.post(
                f"{BASE}/import/apply",
                json={
                    "incoming": body["incoming"],
                    "resolutions": {},
                    "base_version": current["version"],
                    "source": "import",
                },
            )
        assert apply.status_code == 200
        applied = apply.json()
        assert applied["applied"] >= 1
        titles = [e["title"] for e in applied["data"]["workExperience"]]
        assert "SWE" in titles

    async def test_import_missing_resume_404(self, isolated_db, owner_id):
        async with _client() as c:
            resp = await c.post(
                f"{BASE}/import/preview",
                json={"source": "resume", "payload": {"resume_id": "nope"}},
            )
        assert resp.status_code == 404

    async def test_json_resume_import(self, isolated_db, owner_id):
        async with _client() as c:
            preview = await c.post(
                f"{BASE}/import/preview",
                json={
                    "source": "json_resume",
                    "payload": {"data": {"basics": {"name": "Grace"}, "work": [{"name": "Navy", "position": "Officer"}]}},
                },
            )
        assert preview.status_code == 200
        assert preview.json()["incoming"]["identity"]["name"] == "Grace"

    async def test_preview_includes_statistics(self, isolated_db, owner_id):
        r = await _resume(
            isolated_db,
            owner_id,
            {
                "personalInfo": {"name": "Ada", "title": "Eng", "email": "a@b.co"},
                "summary": "A rich summary.",
                "workExperience": [{"title": "SWE", "company": "Globex", "description": ["Shipped", "Led"]}],
                "education": [{"institution": "MIT", "degree": "BS"}],
                "additional": {"technicalSkills": ["Python", "React"]},
            },
        )
        async with _client() as c:
            preview = await c.post(
                f"{BASE}/import/preview",
                json={"source": "resume", "payload": {"resume_id": r["resume_id"]}},
            )
        stats = preview.json()["statistics"]
        assert stats["quality_score"] > 0
        assert stats["sections"]["workExperience"] == 1
        assert stats["total_operations"] == stats["new_items"] + stats["updates"] + stats["conflicts"] + stats["duplicates"]


# ---------------------------------------------------------------------------
# Synchronization (P4)
# ---------------------------------------------------------------------------


class TestSync:
    async def test_sync_preview_and_apply_draft(self, isolated_db, owner_id):
        await _resume(isolated_db, owner_id, {"personalInfo": {"name": "Ada"}}, is_master=True)
        # Generate a draft resume from the profile.
        async with _client() as c:
            gen = await c.post(f"{BASE}/generate-resume", json={"persist": True})
            rid = gen.json()["resume_id"]

            # Change the profile so the projection differs from the draft.
            current = (await c.get(BASE)).json()
            data = current["data"]
            data["summary"] = "Now with a summary."
            await c.patch(BASE, json={"data": data, "base_version": current["version"]})

            preview = await c.get(f"{BASE}/sync/{rid}")
            assert preview.status_code == 200
            pbody = preview.json()
            assert pbody["immutable"] is False
            assert len(pbody["changes"]) > 0

            resume = await isolated_db.get_resume(owner_id, rid)
            apply = await c.post(
                f"{BASE}/sync/{rid}",
                json={"base_version": resume["version"]},
            )
        assert apply.status_code == 200
        synced = await isolated_db.get_resume(owner_id, rid)
        assert synced["processed_data"]["summary"] == "Now with a summary."

    async def test_submitted_resume_is_immutable(self, isolated_db, owner_id):
        await _resume(isolated_db, owner_id, {"personalInfo": {"name": "Ada"}}, is_master=True)
        async with _client() as c:
            gen = await c.post(f"{BASE}/generate-resume", json={"persist": True})
            rid = gen.json()["resume_id"]
            # Link a submitted application to the resume → locks it.
            await isolated_db.create_application(
                owner_id, job_id="j1", resume_id=rid, status="applied"
            )
            resume = await isolated_db.get_resume(owner_id, rid)
            apply = await c.post(f"{BASE}/sync/{rid}", json={"base_version": resume["version"]})
        assert apply.status_code == 409
        assert apply.json()["detail"]["code"] == "resume_locked"


# ---------------------------------------------------------------------------
# AI layer (P5)
# ---------------------------------------------------------------------------


class TestAiLayer:
    async def test_update_ai_memory(self, isolated_db, owner_id):
        async with _client() as c:
            current = (await c.get(BASE)).json()
            resp = await c.put(
                f"{BASE}/ai-memory",
                json={
                    "aiMemory": {**current["data"]["aiMemory"], "tone": "confident"},
                    "base_version": current["version"],
                },
            )
        assert resp.status_code == 200
        assert resp.json()["data"]["aiMemory"]["tone"] == "confident"

    async def test_skill_autocomplete(self, isolated_db, owner_id):
        async with _client() as c:
            resp = await c.get(f"{BASE}/skills/suggest?q=java")
        assert resp.status_code == 200
        names = [s["displayName"] for s in resp.json()["suggestions"]]
        assert "JavaScript" in names

    async def test_ai_suggest_skills_normalize(self, isolated_db, owner_id):
        # Seed a profile with duplicate skills, then normalize (pure, no LLM).
        async with _client() as c:
            current = (await c.get(BASE)).json()
            data = current["data"]
            data["skills"]["technical"] = [
                {
                    "uid": "s1", "canonical": "", "displayName": "js", "aliases": [],
                    "category": "technical", "subcategory": "", "yearsExperience": None,
                    "proficiency": "", "lastUsed": "", "confidence": None,
                    "verificationSource": "", "aiNormalizedName": "", "evidenceUids": [],
                },
                {
                    "uid": "s2", "canonical": "", "displayName": "JavaScript", "aliases": [],
                    "category": "technical", "subcategory": "", "yearsExperience": None,
                    "proficiency": "", "lastUsed": "", "confidence": None,
                    "verificationSource": "", "aiNormalizedName": "", "evidenceUids": [],
                },
            ]
            await c.patch(BASE, json={"data": data, "base_version": current["version"]})
            resp = await c.post(f"{BASE}/ai/suggest", json={"kind": "skills_normalize"})
        assert resp.status_code == 200
        suggestion = resp.json()["suggestion"]
        names = [s["displayName"] for s in suggestion["technical"]]
        assert names.count("JavaScript") == 1


# ---------------------------------------------------------------------------
# Public projection (P6)
# ---------------------------------------------------------------------------


class TestPublicSharing:
    async def _seed_profile(self, isolated_db, owner_id):
        await _resume(
            isolated_db,
            owner_id,
            {
                "personalInfo": {"name": "Ada Lovelace", "title": "Engineer", "phone": "555-000"},
                "summary": "Builds reliable systems.",
                "workExperience": [{"title": "Engineer", "company": "Acme"}],
            },
            is_master=True,
        )

    async def test_publish_creates_slug_and_public_page_loads(self, isolated_db, owner_id):
        await self._seed_profile(isolated_db, owner_id)
        async with _client() as c:
            await c.get(BASE)
            pub = await c.post(f"{BASE}/publish", json={"visibility": "public"})
            assert pub.status_code == 200
            slug = pub.json()["public_slug"]
            assert slug

            # Anonymous public page loads (no auth).
            page = await c.get(f"/api/v1/public/profiles/{slug}")
        assert page.status_code == 200
        body = page.json()
        assert body["profile"]["identity"]["name"] == "Ada Lovelace"
        assert body["indexable"] is True
        assert body["json_ld"]["@type"] == "Person"
        # Private field never leaked.
        assert "555-000" not in str(body)

    async def test_private_profile_returns_404(self, isolated_db, owner_id):
        await self._seed_profile(isolated_db, owner_id)
        async with _client() as c:
            pub = await c.post(f"{BASE}/publish", json={"visibility": "public"})
            slug = pub.json()["public_slug"]
            await c.post(f"{BASE}/unpublish")
            page = await c.get(f"/api/v1/public/profiles/{slug}")
        assert page.status_code == 404

    async def test_unlisted_loads_but_not_indexable(self, isolated_db, owner_id):
        await self._seed_profile(isolated_db, owner_id)
        async with _client() as c:
            pub = await c.post(f"{BASE}/publish", json={"visibility": "unlisted"})
            slug = pub.json()["public_slug"]
            page = await c.get(f"/api/v1/public/profiles/{slug}")
        assert page.status_code == 200
        assert page.json()["indexable"] is False

    async def test_slug_is_stable_across_republish(self, isolated_db, owner_id):
        await self._seed_profile(isolated_db, owner_id)
        async with _client() as c:
            first = (await c.post(f"{BASE}/publish", json={"visibility": "public"})).json()
            await c.post(f"{BASE}/unpublish")
            second = (await c.post(f"{BASE}/publish", json={"visibility": "public"})).json()
        assert first["public_slug"] == second["public_slug"]

    async def test_vcard_download(self, isolated_db, owner_id):
        await self._seed_profile(isolated_db, owner_id)
        async with _client() as c:
            pub = await c.post(f"{BASE}/publish", json={"visibility": "public"})
            slug = pub.json()["public_slug"]
            vcard = await c.get(f"/api/v1/public/profiles/{slug}/vcard")
        assert vcard.status_code == 200
        assert "text/vcard" in vcard.headers["content-type"]
        assert "BEGIN:VCARD" in vcard.text
        assert "Ada Lovelace" in vcard.text

    async def test_unknown_slug_404(self, isolated_db, owner_id):
        async with _client() as c:
            page = await c.get("/api/v1/public/profiles/nobody-here")
        assert page.status_code == 404


class TestSearchAnalyticsAndAiExpansion:
    async def _seed(self, isolated_db, owner_id):
        await _resume(
            isolated_db,
            owner_id,
            {
                "personalInfo": {"name": "Ada", "title": "Backend Engineer"},
                "summary": "Builds services.",
                "workExperience": [{"title": "Engineer", "company": "Acme", "description": ["Built Python APIs"]}],
                "additional": {"technicalSkills": ["Python", "PostgreSQL"]},
            },
            is_master=True,
        )

    async def test_profile_search(self, isolated_db, owner_id):
        await self._seed(isolated_db, owner_id)
        async with _client() as c:
            resp = await c.get(f"{BASE}/search?q=python")
        assert resp.status_code == 200
        results = resp.json()["results"]
        assert any(r["type"] == "skill" for r in results)

    async def test_search_empty_query(self, isolated_db, owner_id):
        await self._seed(isolated_db, owner_id)
        async with _client() as c:
            resp = await c.get(f"{BASE}/search?q=")
        assert resp.status_code == 200
        assert resp.json()["results"] == []

    async def test_ai_skills_gap(self, isolated_db, owner_id):
        await self._seed(isolated_db, owner_id)
        async with _client() as c:
            current = (await c.get(BASE)).json()
            data = current["data"]
            data["identity"]["targetRoles"] = ["backend"]
            await c.patch(BASE, json={"data": data, "base_version": current["version"]})
            resp = await c.post(f"{BASE}/ai/suggest", json={"kind": "skills_gap"})
        assert resp.status_code == 200
        assert resp.json()["kind"] == "skills_gap"
        assert "missing" in resp.json()["suggestion"]

    async def test_ai_keywords(self, isolated_db, owner_id):
        await self._seed(isolated_db, owner_id)
        async with _client() as c:
            resp = await c.post(f"{BASE}/ai/suggest", json={"kind": "keywords"})
        assert resp.status_code == 200
        assert isinstance(resp.json()["suggestion"], list)

    async def test_analytics_snapshot(self, isolated_db, owner_id):
        await self._seed(isolated_db, owner_id)
        # Simulate what the outbox analytics consumer does.
        from app.profile.analytics import get_analytics_service, reset_analytics_service

        reset_analytics_service()
        svc = get_analytics_service()
        await svc.record(owner_id, "resumes_generated")
        await svc.record(owner_id, "resumes_generated")
        await svc.record(owner_id, "imports")
        async with _client() as c:
            resp = await c.get(f"{BASE}/analytics")
        assert resp.status_code == 200
        body = resp.json()
        assert body["counters"]["resumes_generated"] == 2
        assert body["counters"]["imports"] == 1
        assert body["total_events"] >= 3

    async def test_public_portfolio_page(self, isolated_db, owner_id):
        await self._seed(isolated_db, owner_id)
        async with _client() as c:
            await c.get(BASE)
            pub = await c.post(f"{BASE}/publish", json={"visibility": "public"})
            slug = pub.json()["public_slug"]
            portfolio = await c.get(f"/api/v1/public/profiles/{slug}/portfolio")
        assert portfolio.status_code == 200
        assert portfolio.json()["identity"]["name"] == "Ada"


class TestAnalyticsConsumer:
    async def test_consumer_maps_events_to_counters(self, isolated_db, owner_id):
        from app.events import EventType, OutboxEvent
        from app.profile.analytics import get_analytics_service, reset_analytics_service
        from app.profile.analytics_consumer import _EVENT_METRIC, _make_handler

        reset_analytics_service()
        handler = _make_handler(_EVENT_METRIC[EventType.PROFILE_IMPORTED.value])
        event = OutboxEvent(
            id="e1",
            user_id=owner_id,
            event_type=EventType.PROFILE_IMPORTED.value,
            payload={},
            created_at="now",
            attempts=0,
        )
        await handler(event)
        snap = await get_analytics_service().snapshot(owner_id)
        assert snap["counters"]["imports"] == 1


class TestPublicProjection:
    async def test_public_and_portfolio_and_export(self, isolated_db, owner_id):
        await _resume(
            isolated_db,
            owner_id,
            {
                "personalInfo": {"name": "Ada", "title": "Eng", "phone": "555"},
                "summary": "S",
                "workExperience": [{"title": "SWE", "company": "X"}],
            },
            is_master=True,
        )
        async with _client() as c:
            public = await c.get(f"{BASE}/public")
            portfolio = await c.get(f"{BASE}/portfolio")
            export = await c.get(f"{BASE}/export/json-resume")
        assert public.status_code == 200
        assert public.json()["identity"]["name"] == "Ada"
        assert "555" not in str(public.json())  # phone not leaked publicly
        assert portfolio.status_code == 200
        assert export.status_code == 200
        assert export.json()["basics"]["name"] == "Ada"

"""Per-resume template (appearance) persistence.

Verifies that a resume's chosen template + customization is stored in the DB,
returned on fetch, survives across requests, and - because it is a rendering
artifact, not content - does NOT bump the optimistic-concurrency version.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app

SETTINGS = {
    "template": "latex",
    "pageSize": "A4",
    "margins": {"top": 12, "bottom": 12, "left": 12, "right": 12},
    "spacing": {"section": 4, "item": 3, "lineHeight": 4},
    "fontSize": {"base": 3, "headerScale": 4, "headerFont": "serif", "bodyFont": "serif"},
    "compactMode": False,
    "showContactIcons": True,
    "accentColor": "blue",
}


@pytest.fixture
def client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def _seed(db, owner_id: str) -> str:
    resume = await db.create_resume(
        owner_id,
        content="{}",
        content_type="json",
        processed_data={"personalInfo": {"name": "Ada"}, "summary": "s"},
        processing_status="ready",
    )
    return resume["resume_id"]


class TestTemplateSettingsPersistence:
    async def test_patch_persists_and_fetch_returns(self, isolated_db, owner_id, client):
        rid = await _seed(isolated_db, owner_id)
        async with client:
            r = await client.patch(
                f"/api/v1/resumes/{rid}/template-settings", json={"settings": SETTINGS}
            )
            assert r.status_code == 200
            fetched = await client.get(f"/api/v1/resumes?resume_id={rid}")
        assert fetched.status_code == 200
        data = fetched.json()["data"]
        assert data["template_settings"]["template"] == "latex"
        assert data["template_settings"]["showContactIcons"] is True

    async def test_template_write_does_not_bump_version(self, isolated_db, owner_id, client):
        rid = await _seed(isolated_db, owner_id)
        async with client:
            before = (await client.get(f"/api/v1/resumes?resume_id={rid}")).json()["data"]["version"]
            await client.patch(
                f"/api/v1/resumes/{rid}/template-settings", json={"settings": SETTINGS}
            )
            after = (await client.get(f"/api/v1/resumes?resume_id={rid}")).json()["data"]["version"]
        # Appearance is not content - the editor's optimistic lock is untouched.
        assert after == before

    async def test_unknown_resume_is_404(self, isolated_db, owner_id, client):
        async with client:
            r = await client.patch(
                "/api/v1/resumes/does-not-exist/template-settings", json={"settings": SETTINGS}
            )
        assert r.status_code == 404

    async def test_oversized_payload_rejected(self, isolated_db, owner_id, client):
        rid = await _seed(isolated_db, owner_id)
        huge = {"blob": "x" * 9000}
        async with client:
            r = await client.patch(
                f"/api/v1/resumes/{rid}/template-settings", json={"settings": huge}
            )
        assert r.status_code == 422

    async def test_legacy_resume_has_null_template_settings(self, isolated_db, owner_id, client):
        rid = await _seed(isolated_db, owner_id)
        async with client:
            data = (await client.get(f"/api/v1/resumes?resume_id={rid}")).json()["data"]
        # Backward compatible: a resume created without a template reads back None.
        assert data["template_settings"] is None


class TestPdfUsesStoredTemplate:
    """Audit Bug #1: a bare PDF request renders the resume's stored template."""

    async def test_bare_pdf_request_uses_stored_template(
        self, isolated_db, owner_id, client, monkeypatch
    ):
        import app.routers.resumes as resumes_mod
        from unittest.mock import AsyncMock

        captured: dict[str, str] = {}

        async def fake_render(url, page_size, margins=None):
            captured["url"] = url
            captured["page_size"] = page_size
            return b"%PDF-1.4 fake"

        monkeypatch.setattr(resumes_mod, "render_resume_pdf", AsyncMock(side_effect=fake_render))

        rid = await _seed(isolated_db, owner_id)
        await isolated_db.update_resume(owner_id, rid, {"template_settings": SETTINGS})

        async with client:
            # NO query params - must fall back to the stored template (latex/A4).
            resp = await client.get(f"/api/v1/resumes/{rid}/pdf")
        assert resp.status_code == 200
        assert "template=latex" in captured["url"]
        assert "showContactIcons=true" in captured["url"]

    async def test_query_param_overrides_stored_template(
        self, isolated_db, owner_id, client, monkeypatch
    ):
        import app.routers.resumes as resumes_mod
        from unittest.mock import AsyncMock

        captured: dict[str, str] = {}

        async def fake_render(url, page_size, margins=None):
            captured["url"] = url
            return b"%PDF-1.4 fake"

        monkeypatch.setattr(resumes_mod, "render_resume_pdf", AsyncMock(side_effect=fake_render))

        rid = await _seed(isolated_db, owner_id)
        await isolated_db.update_resume(owner_id, rid, {"template_settings": SETTINGS})

        async with client:
            resp = await client.get(f"/api/v1/resumes/{rid}/pdf?template=modern")
        assert resp.status_code == 200
        assert "template=modern" in captured["url"]


class TestCreateFromData:
    """POST /resumes/from-data - powers Use-Sample + duplication."""

    _DATA = {
        "personalInfo": {"name": "Sample User", "title": "Engineer", "email": "s@e.com"},
        "summary": "A crafted sample.",
        "workExperience": [
            {"id": 1, "title": "Engineer", "company": "Acme", "years": "2020 - 2024"}
        ],
        "education": [],
        "personalProjects": [],
        "additional": {"technicalSkills": ["Python"]},
    }

    async def test_creates_resume_with_template_and_returns_id(
        self, isolated_db, owner_id, client
    ):
        async with client:
            r = await client.post(
                "/api/v1/resumes/from-data",
                json={
                    "processed_data": self._DATA,
                    "title": "My Sample Resume",
                    "template_settings": SETTINGS,
                },
            )
            assert r.status_code == 200, r.text
            rid = r.json()["resume_id"]
            fetched = (await client.get(f"/api/v1/resumes?resume_id={rid}")).json()["data"]
        assert fetched["processed_resume"]["summary"] == "A crafted sample."
        assert fetched["template_settings"]["template"] == "latex"
        assert fetched["title"] == "My Sample Resume"

    async def test_rejects_garbage_payload(self, isolated_db, owner_id, client):
        async with client:
            r = await client.post(
                "/api/v1/resumes/from-data", json={"processed_data": "not-a-resume"}
            )
        assert r.status_code == 422


class TestTemplateAwareVersioning:
    """Audit Bug #3: restoring a version restores its template, not just content."""

    async def test_restore_reapplies_snapshot_template(self, isolated_db, owner_id):
        from app.versions import service as version_service

        # v1 content saved under template A (latex).
        resume = await isolated_db.create_resume(
            owner_id,
            content="{}",
            content_type="json",
            processed_data={"personalInfo": {"name": "Ada"}, "summary": "v1"},
            processing_status="ready",
            template_settings={**SETTINGS, "template": "latex"},
        )
        rid = resume["resume_id"]
        snap = await version_service.capture_snapshot(
            owner_id, rid, {"personalInfo": {"name": "Ada"}, "summary": "v1"}, "original"
        )
        assert snap is not None

        # User edits content to v2 AND switches template to B (modern).
        await isolated_db.update_resume(
            owner_id,
            rid,
            {"processed_data": {"personalInfo": {"name": "Ada"}, "summary": "v2"}},
        )
        await isolated_db.update_resume(
            owner_id, rid, {"template_settings": {**SETTINGS, "template": "modern"}}
        )

        # Restore v1 -> content AND template revert to the snapshot's (latex).
        await version_service.restore_version(owner_id, rid, snap["id"])
        restored = await isolated_db.get_resume(owner_id, rid)
        assert restored["processed_data"]["summary"] == "v1"
        assert restored["template_settings"]["template"] == "latex"

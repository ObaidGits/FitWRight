"""P4 Resilience - version-CAS conflict resolution on PATCH /resumes/{id}.

Exercises the atomic optimistic-concurrency path against a REAL (isolated)
database so the single-row conditional update and the 409 envelope are actually
verified end-to-end (Property 1: no write silently overwrites a newer version).
"""

import asyncio
import json

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.fixture
def client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


def _resume_payload(summary: str) -> dict:
    """A minimal-but-valid ResumeData body for PATCH (all other fields default)."""
    return {
        "personalInfo": {"name": "Jane Doe", "email": "jane@example.com"},
        "summary": summary,
    }


async def _seed_resume(db, owner_id: str) -> str:
    created = await db.create_resume(
        owner_id,
        content=json.dumps(_resume_payload("v1")),
        content_type="json",
        processed_data=_resume_payload("v1"),
        processing_status="ready",
    )
    return created["resume_id"]


class TestVersionCas:
    async def test_fresh_resume_starts_at_version_1(self, isolated_db, owner_id, client):
        resume_id = await _seed_resume(isolated_db, owner_id)
        async with client:
            resp = await client.get("/api/v1/resumes", params={"resume_id": resume_id})
        assert resp.status_code == 200
        assert resp.json()["data"]["version"] == 1

    async def test_responses_carry_api_version_header(self, isolated_db, owner_id, client):
        """P4 R9.8: every response advertises X-API-Version so the client can
        detect a deploy mid-session (API version skew) and enter Safe-Mode."""
        async with client:
            resp = await client.get("/api/v1/health")
        assert resp.headers.get("X-API-Version")

    async def test_matching_if_match_applies_and_bumps_version(
        self, isolated_db, owner_id, client
    ):
        resume_id = await _seed_resume(isolated_db, owner_id)
        async with client:
            resp = await client.patch(
                f"/api/v1/resumes/{resume_id}",
                json=_resume_payload("v2"),
                headers={"If-Match": "1"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["version"] == 2
        assert data["processed_resume"]["summary"] == "v2"

    async def test_stale_if_match_returns_409_envelope(self, isolated_db, owner_id, client):
        resume_id = await _seed_resume(isolated_db, owner_id)
        # First write moves version 1 -> 2.
        async with client:
            first = await client.patch(
                f"/api/v1/resumes/{resume_id}",
                json=_resume_payload("v2"),
                headers={"If-Match": "1"},
            )
            assert first.status_code == 200
            # Second write still claims base version 1 -> conflict.
            conflict = await client.patch(
                f"/api/v1/resumes/{resume_id}",
                json=_resume_payload("v2-other"),
                headers={"If-Match": "1"},
            )
        assert conflict.status_code == 409
        body = conflict.json()["error"]
        assert body["code"] == "version_conflict"
        details = body["details"]
        assert details["your_base_version"] == 1
        assert details["current_version"] == 2
        # The 409 carries the current server data so the client can diff.
        assert details["current_data"]["summary"] == "v2"

    async def test_no_if_match_is_normal_write_that_still_bumps(
        self, isolated_db, owner_id, client
    ):
        resume_id = await _seed_resume(isolated_db, owner_id)
        async with client:
            resp = await client.patch(
                f"/api/v1/resumes/{resume_id}", json=_resume_payload("v2")
            )
        assert resp.status_code == 200
        assert resp.json()["data"]["version"] == 2

    async def test_concurrent_same_base_exactly_one_wins(
        self, isolated_db, owner_id
    ):
        """Two CAS writes at the same base: exactly one 'updated', one 'conflict'."""
        resume_id = await _seed_resume(isolated_db, owner_id)
        results = await asyncio.gather(
            isolated_db.update_resume_cas(
                owner_id, resume_id, {"content": "a"}, base_version=1
            ),
            isolated_db.update_resume_cas(
                owner_id, resume_id, {"content": "b"}, base_version=1
            ),
        )
        statuses = sorted(status for status, _ in results)
        assert statuses == ["conflict", "updated"]

    async def test_ai_artifact_writes_do_not_bump_version(self, isolated_db, owner_id):
        """Persisting a cover letter must NOT invalidate the editor's optimistic
        lock (no spurious conflict on the next resume-body autosave)."""
        resume_id = await _seed_resume(isolated_db, owner_id)
        before = (await isolated_db.get_resume(owner_id, resume_id))["version"]
        await isolated_db.update_resume(owner_id, resume_id, {"cover_letter": "Dear..."})
        after = (await isolated_db.get_resume(owner_id, resume_id))["version"]
        assert after == before  # cover letter write left the version untouched

    async def test_content_writes_bump_version(self, isolated_db, owner_id):
        resume_id = await _seed_resume(isolated_db, owner_id)
        before = (await isolated_db.get_resume(owner_id, resume_id))["version"]
        await isolated_db.update_resume(
            owner_id, resume_id, {"processed_data": _resume_payload("changed")}
        )
        after = (await isolated_db.get_resume(owner_id, resume_id))["version"]
        assert after == before + 1

    async def test_patch_nonexistent_returns_404(self, isolated_db, owner_id, client):
        async with client:
            resp = await client.patch(
                "/api/v1/resumes/does-not-exist",
                json=_resume_payload("v2"),
                headers={"If-Match": "1"},
            )
        assert resp.status_code == 404


class TestIdempotency:
    async def test_replay_same_key_and_content_is_deduped(
        self, isolated_db, owner_id, client
    ):
        resume_id = await _seed_resume(isolated_db, owner_id)
        headers = {"If-Match": "1", "Idempotency-Key": "key-abc"}
        async with client:
            first = await client.patch(
                f"/api/v1/resumes/{resume_id}", json=_resume_payload("v2"), headers=headers
            )
            assert first.status_code == 200
            assert first.json()["data"]["version"] == 2
            # Replay with the SAME key + content: deduped, version does NOT bump
            # again (the client couldn't tell the first landed and retried).
            replay = await client.patch(
                f"/api/v1/resumes/{resume_id}", json=_resume_payload("v2"), headers=headers
            )
        assert replay.status_code == 200
        assert replay.json()["data"]["version"] == 2

    async def test_same_key_different_content_is_not_deduped(
        self, isolated_db, owner_id, client
    ):
        resume_id = await _seed_resume(isolated_db, owner_id)
        async with client:
            first = await client.patch(
                f"/api/v1/resumes/{resume_id}",
                json=_resume_payload("v2"),
                headers={"If-Match": "1", "Idempotency-Key": "key-xyz"},
            )
            assert first.status_code == 200
            # Same key, different content + fresh base -> a genuine new write.
            second = await client.patch(
                f"/api/v1/resumes/{resume_id}",
                json=_resume_payload("v3"),
                headers={"If-Match": "2", "Idempotency-Key": "key-xyz"},
            )
        assert second.status_code == 200
        assert second.json()["data"]["version"] == 3

"""Endpoint-level cross-user isolation (Property 1, R10.2/10.3/10.6).

Proves the isolation boundary *through the real routers*: a request scoped to
user B can never read or mutate user A's owned rows, and a foreign id returns
**404** (never 403 - no existence disclosure). Multi-user resolution is
simulated by overriding the ``get_effective_user_id`` dependency (real sessions
arrive in Task 4); the persistence and 404 logic exercised here are the actual
production paths.
"""

from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.auth import get_effective_user_id
from app.main import app
from app.models import User


def _client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _make_user(db, email: str) -> str:
    uid = str(uuid4())
    async with db.session_factory() as session:
        session.add(User(id=uid, email=email, name="U", role="user", status="active"))
        await session.commit()
    return uid


@pytest.fixture
async def two_users(isolated_db):
    a = await _make_user(isolated_db, "a@example.com")
    b = await _make_user(isolated_db, "b@example.com")
    yield a, b


def _as_user(user_id: str):
    """Override the effective-user dependency to a fixed authenticated user."""
    app.dependency_overrides[get_effective_user_id] = lambda: user_id


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.pop(get_effective_user_id, None)


class TestResumeScoping:
    async def test_foreign_resume_is_404_everywhere(self, isolated_db, two_users):
        user_a, user_b = two_users
        resume = await isolated_db.create_resume(user_a, content="A secret", is_master=False)
        rid = resume["resume_id"]

        _as_user(user_b)
        async with _client() as client:
            # Read
            assert (await client.get(f"/api/v1/resumes?resume_id={rid}")).status_code == 404
            # List does not leak A's resume to B.
            listed = await client.get("/api/v1/resumes/list?include_master=true")
            assert listed.status_code == 200
            assert listed.json()["data"] == []
            # Mutate
            assert (
                await client.patch(f"/api/v1/resumes/{rid}/title", json={"title": "hijack"})
            ).status_code == 404
            # Delete
            assert (await client.delete(f"/api/v1/resumes/{rid}")).status_code == 404

        # A's resume is untouched.
        assert (await isolated_db.get_resume(user_a, rid))["content"] == "A secret"

    async def test_owner_can_access_own_resume(self, isolated_db, two_users):
        user_a, _ = two_users
        resume = await isolated_db.create_resume(user_a, content="mine")
        _as_user(user_a)
        async with _client() as client:
            resp = await client.get(f"/api/v1/resumes?resume_id={resume['resume_id']}")
        assert resp.status_code == 200


class TestJobScoping:
    async def test_foreign_job_is_404(self, isolated_db, two_users):
        user_a, user_b = two_users
        job = await isolated_db.create_job(user_a, content="A's JD")
        _as_user(user_b)
        async with _client() as client:
            assert (await client.get(f"/api/v1/jobs/{job['job_id']}")).status_code == 404
        # Owner sees it.
        _as_user(user_a)
        async with _client() as client:
            assert (await client.get(f"/api/v1/jobs/{job['job_id']}")).status_code == 200


class TestApplicationScoping:
    async def test_foreign_application_is_404_and_hidden(self, isolated_db, two_users):
        user_a, user_b = two_users
        card = await isolated_db.create_application(user_a, job_id="j1", resume_id="r1")
        aid = card["application_id"]

        _as_user(user_b)
        async with _client() as client:
            assert (await client.get(f"/api/v1/applications/{aid}")).status_code == 404
            assert (
                await client.patch(f"/api/v1/applications/{aid}", json={"notes": "x"})
            ).status_code == 404
            assert (await client.delete(f"/api/v1/applications/{aid}")).status_code == 404
            # B's board never contains A's card.
            board = await client.get("/api/v1/applications")
            columns = board.json()["columns"]
            assert all(columns[s] == [] for s in columns)

        assert (await isolated_db.get_application(user_a, aid))["status"] == "applied"


class TestApiKeyScoping:
    async def test_api_keys_are_per_user(self, isolated_db, two_users):
        user_a, user_b = two_users
        # A stores an OpenAI key directly in the encrypted store.
        from app import crypto

        isolated_db.set_api_key_ciphertext(user_a, "openai", crypto.encrypt("A-key"))

        # B sees no configured keys through the API.
        _as_user(user_b)
        async with _client() as client:
            status = await client.get("/api/v1/config/api-keys")
        configured = {p["provider"]: p["configured"] for p in status.json()["providers"]}
        assert configured["openai"] is False

        # A sees their own key configured.
        _as_user(user_a)
        async with _client() as client:
            status = await client.get("/api/v1/config/api-keys")
        configured = {p["provider"]: p["configured"] for p in status.json()["providers"]}
        assert configured["openai"] is True

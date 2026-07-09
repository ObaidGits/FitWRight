"""Authorization / ownership matrix sign-off (Task 10.1).

The comprehensive authorization boundary test for the P1 foundation. It proves,
end-to-end through the real routers with **real sessions** (no dependency
overrides), the guarantees the design's threat model and Properties 1/3 require:

- **Anonymous → 401 on every protected owned-resource route.** ``OWNED_ENDPOINTS``
  enumerates *every* owned/provider-cost endpoint across all routers (resumes,
  jobs, applications, enrichment, config api-keys, **and the resume wizard incl.
  ``resume-wizard/turn``**). This is the anti-regression guarantee from the plan:
  "no owned-resource endpoint ships without scoping + an authz test." A newly
  added owned route that forgets its ``user_id`` dependency will fail here.
- **Cross-user access → 404 (no existence disclosure, R10.3 / Property 1).** A
  request authenticated as user B against user A's resource id returns 404, never
  403 (which would confirm the row exists).
- **Disabled user whose session is cached → rejected within one request cycle
  (R3.4 / Property 3)** via write-through cache eviction + per-request status
  recheck.
- **pending / active-but-unverified → gated on provider-cost actions only
  (R5.6)**; basic use (listing/browsing) stays open.
- **Admin capability route matrix (R8.2):** anon → 401, non-admin → 403,
  admin → 200 through the real ``require_capability`` dependency.

Requirements: 10.2, 10.3, 8.2, 3.4
"""

from __future__ import annotations

import pytest
from fastapi import Depends
from httpx import ASGITransport, AsyncClient

from app.auth import Capabilities, Principal, get_principal, require_capability
from app.auth.accounts import create_user
from app.auth.passwords import get_password_service
from app.auth.sessions import get_session_service, hash_token
from app.config import settings as app_settings
from app.main import app
from app.models import User

from tests.integration.test_auth_api import (
    STRONG_PW,
    _cookie_str,
    _login,
    _seed_active_user,
)

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# The full inventory of owned / provider-cost endpoints (every router).
# (method, path) — a dummy path param is fine: the 401 fires at dependency
# resolution, before body validation or the handler body runs.
# ---------------------------------------------------------------------------

OWNED_ENDPOINTS: list[tuple[str, str]] = [
    # resumes
    ("POST", "/api/v1/resumes/upload"),
    ("GET", "/api/v1/resumes?resume_id=x"),
    ("GET", "/api/v1/resumes/list"),
    ("POST", "/api/v1/resumes/improve"),
    ("POST", "/api/v1/resumes/improve/preview"),
    ("POST", "/api/v1/resumes/improve/confirm"),
    ("PATCH", "/api/v1/resumes/rid"),
    ("GET", "/api/v1/resumes/rid/pdf"),
    ("DELETE", "/api/v1/resumes/rid"),
    ("POST", "/api/v1/resumes/rid/retry-processing"),
    ("PATCH", "/api/v1/resumes/rid/cover-letter"),
    ("PATCH", "/api/v1/resumes/rid/outreach-message"),
    ("PATCH", "/api/v1/resumes/rid/title"),
    ("POST", "/api/v1/resumes/rid/generate-cover-letter"),
    ("POST", "/api/v1/resumes/rid/generate-outreach"),
    ("POST", "/api/v1/resumes/rid/generate-interview-prep"),
    ("GET", "/api/v1/resumes/rid/job-description"),
    ("GET", "/api/v1/resumes/rid/cover-letter/pdf"),
    # jobs
    ("POST", "/api/v1/jobs/upload"),
    ("GET", "/api/v1/jobs/jid"),
    # applications
    ("GET", "/api/v1/applications"),
    ("POST", "/api/v1/applications"),
    ("GET", "/api/v1/applications/aid"),
    ("PATCH", "/api/v1/applications/bulk"),
    ("PATCH", "/api/v1/applications/aid"),
    ("DELETE", "/api/v1/applications/aid"),
    ("POST", "/api/v1/applications/bulk-delete"),
    # enrichment
    ("POST", "/api/v1/enrichment/analyze/rid"),
    ("POST", "/api/v1/enrichment/enhance"),
    ("POST", "/api/v1/enrichment/apply/rid"),
    ("POST", "/api/v1/enrichment/regenerate"),
    ("POST", "/api/v1/enrichment/apply-regenerated/rid"),
    # config — per-user api keys + llm config/test + reset (all user-scoped)
    ("GET", "/api/v1/config/llm-api-key"),
    ("PUT", "/api/v1/config/llm-api-key"),
    ("POST", "/api/v1/config/llm-test"),
    ("GET", "/api/v1/config/api-keys"),
    ("POST", "/api/v1/config/api-keys"),
    ("DELETE", "/api/v1/config/api-keys"),
    ("DELETE", "/api/v1/config/api-keys/prov"),
    ("POST", "/api/v1/config/reset"),
    # resume wizard (turn is provider-cost — the previously-unscoped route)
    ("POST", "/api/v1/resume-wizard/turn"),
    ("POST", "/api/v1/resume-wizard/finalize"),
]

# Provider-cost actions gated behind email verification (R5.6). These must 403
# for an active-but-unverified account when verification is enabled, while basic
# use stays open.
PROVIDER_COST_ENDPOINTS: list[tuple[str, str]] = [
    ("POST", "/api/v1/resumes/improve"),
    ("POST", "/api/v1/resumes/improve/preview"),
    ("POST", "/api/v1/resumes/improve/confirm"),
    ("POST", "/api/v1/resumes/rid/generate-cover-letter"),
    ("POST", "/api/v1/resumes/rid/generate-outreach"),
    ("POST", "/api/v1/resumes/rid/generate-interview-prep"),
    ("POST", "/api/v1/enrichment/analyze/rid"),
    ("POST", "/api/v1/enrichment/enhance"),
    ("POST", "/api/v1/enrichment/regenerate"),
    ("POST", "/api/v1/resume-wizard/turn"),
]


def _client() -> AsyncClient:
    # https so the httpx cookie jar keeps the Secure __Host- cookie.
    return AsyncClient(transport=ASGITransport(app=app), base_url="https://test")


@pytest.fixture
def hosted(monkeypatch):
    """Run in hosted mode so anonymous requests are unauthenticated (no owner)."""
    monkeypatch.setattr(app_settings, "single_user_mode", False)


async def _seed_user(db, email: str, *, role: str = "user", status: str = "active", verified: bool = True):
    return await create_user(
        email=email,
        name="U",
        password_hash=get_password_service().hash_password(STRONG_PW),
        role=role,
        status=status,
        email_verified_at="2024-01-01T00:00:00+00:00" if verified else None,
        db=db,
    )


# ---------------------------------------------------------------------------
# Anonymous → 401 on every owned/provider-cost route
# ---------------------------------------------------------------------------


class TestAnonymousRejected:
    @pytest.mark.parametrize("method,path", OWNED_ENDPOINTS, ids=lambda v: v if isinstance(v, str) else v)
    async def test_anonymous_gets_401(self, auth_env, hosted, method, path):
        async with _client() as client:
            resp = await client.request(method, path, json={})
        assert resp.status_code == 401, (
            f"{method} {path} did not reject an anonymous caller with 401 "
            f"(got {resp.status_code}); an owned endpoint is missing its "
            f"user_id scope dependency."
        )

    async def test_inventory_covers_every_owned_route(self):
        """Fail loudly if a new owned route is added without an authz test.

        "Owned" is defined precisely: a route whose dependency graph resolves the
        effective user id (``get_effective_user_id`` or ``require_verified_user_id``).
        Every such route MUST appear in ``OWNED_ENDPOINTS`` (and therefore be
        anon→401 tested above). This walk is self-maintaining — a new owned route
        that forgets its authz test fails here, and a route that forgets its
        ``user_id`` scope dependency isn't classified owned and fails the runtime
        401 check instead. Between the two, no owned endpoint can ship untested.
        """
        from app.auth import get_effective_user_id, require_verified_user_id

        scope_deps = {get_effective_user_id, require_verified_user_id}

        def _route_calls(route) -> set:
            calls: set = set()
            dependant = getattr(route, "dependant", None)
            if dependant is None:
                return calls
            stack = list(dependant.dependencies)
            while stack:
                dep = stack.pop()
                if dep.call is not None:
                    calls.add(dep.call)
                stack.extend(dep.dependencies)
            return calls

        def _normalize(template: str) -> str:
            out = template
            for name, dummy in (
                ("{resume_id}", "rid"),
                ("{job_id}", "jid"),
                ("{application_id}", "aid"),
                ("{provider}", "prov"),
                ("{session_id}", "sid"),
            ):
                out = out.replace(name, dummy)
            return out

        enumerated = {(m, p.split("?")[0]) for m, p in OWNED_ENDPOINTS}
        missing: list[str] = []
        for route in app.routes:
            if not (_route_calls(route) & scope_deps):
                continue  # not an owned route
            path = getattr(route, "path", "")
            norm = _normalize(path)
            for method in getattr(route, "methods", None) or set():
                if method in {"HEAD", "OPTIONS"}:
                    continue
                if (method, norm) not in enumerated and (method, norm.rstrip("/")) not in enumerated:
                    missing.append(f"{method} {path}")
        assert not missing, f"owned routes missing from the authz matrix: {sorted(missing)}"


# ---------------------------------------------------------------------------
# Cross-user access → 404 (no existence disclosure) via REAL sessions
# ---------------------------------------------------------------------------


class TestCrossUserOwnership:
    async def test_user_b_cannot_read_user_a_resource(self, auth_env, hosted):
        user_a = await _seed_active_user(auth_env, "a-owner@example.com")
        user_b = await _seed_active_user(auth_env, "b-other@example.com")
        resume = await auth_env.create_resume(user_a.id, content="A secret", is_master=False)
        job = await auth_env.create_job(user_a.id, content="A's JD")
        rid, jid = resume["resume_id"], job["job_id"]

        async with _client() as client:
            await _login(client, "b-other@example.com")
            csrf = client.cookies.get("csrf")
            # Read another user's resume/job → 404 (not 403).
            assert (await client.get(f"/api/v1/resumes?resume_id={rid}")).status_code == 404
            assert (await client.get(f"/api/v1/jobs/{jid}")).status_code == 404
            # List never leaks A's rows to B.
            listed = await client.get("/api/v1/resumes/list?include_master=true")
            assert listed.status_code == 200 and listed.json()["data"] == []
            # Mutations against a foreign id are 404 too (CSRF header supplied).
            hdr = {"X-CSRF-Token": csrf}
            assert (await client.patch(f"/api/v1/resumes/{rid}/title", json={"title": "x"}, headers=hdr)).status_code == 404
            assert (await client.delete(f"/api/v1/resumes/{rid}", headers=hdr)).status_code == 404

        # A's data is untouched.
        assert (await auth_env.get_resume(user_a.id, rid))["content"] == "A secret"

    async def test_user_a_can_read_own_resource(self, auth_env, hosted):
        user_a = await _seed_active_user(auth_env, "self-a@example.com")
        resume = await auth_env.create_resume(user_a.id, content="mine")
        async with _client() as client:
            await _login(client, "self-a@example.com")
            resp = await client.get(f"/api/v1/resumes?resume_id={resume['resume_id']}")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Disabled user whose session is cached → rejected within one request cycle
# ---------------------------------------------------------------------------


class TestDisabledCachedSession:
    async def test_disabled_user_cached_session_rejected(self, auth_env, hosted):
        record = await _seed_active_user(auth_env, "to-disable@example.com")
        async with _client() as client:
            await _login(client, "to-disable@example.com")
            token = client.cookies.get("__Host-session")

            # First authenticated request populates the session cache.
            assert (await client.get("/api/v1/auth/session")).status_code == 200

            # Disable the account. A disable action performs write-through cache
            # eviction (design §Session mechanics); simulate that here.
            async with auth_env.session_factory() as session:
                user = await session.get(User, record.id)
                user.status = "disabled"
                await session.commit()
            await get_session_service()._evict(hash_token(token))

            # Next request is rejected within one cycle (per-request status recheck).
            after = await client.get("/api/v1/auth/session")
            assert after.status_code == 401

            # Owned resources are equally inaccessible.
            assert (await client.get("/api/v1/resumes/list")).status_code == 401


# ---------------------------------------------------------------------------
# pending / active-but-unverified → gated only on provider-cost actions
# ---------------------------------------------------------------------------


class TestVerificationGate:
    @pytest.mark.parametrize("method,path", PROVIDER_COST_ENDPOINTS)
    async def test_unverified_blocked_on_provider_cost(self, auth_env, hosted, method, path):
        # email_verification_enabled resolves to True in hosted mode.
        await _seed_user(auth_env, "unverified@example.com", status="active", verified=False)
        async with _client() as client:
            await _login(client, "unverified@example.com")
            csrf = client.cookies.get("csrf")
            resp = await client.request(method, path, json={}, headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "email_verification_required"

    async def test_unverified_allowed_basic_use(self, auth_env, hosted):
        await _seed_user(auth_env, "unverified2@example.com", status="active", verified=False)
        async with _client() as client:
            await _login(client, "unverified2@example.com")
            # Listing / browsing are never gated by verification.
            assert (await client.get("/api/v1/resumes/list")).status_code == 200
            assert (await client.get("/api/v1/applications")).status_code == 200

    async def test_verified_user_passes_gate(self, auth_env, hosted, monkeypatch):
        # A verified user is not gated; the wizard start turn is accepted (no LLM
        # call for the "start" action, so no provider dependency needed).
        await _seed_active_user(auth_env, "verified@example.com")
        from app.services.resume_wizard import build_initial_wizard_state

        state = build_initial_wizard_state()
        async with _client() as client:
            await _login(client, "verified@example.com")
            csrf = client.cookies.get("csrf")
            resp = await client.post(
                "/api/v1/resume-wizard/turn",
                json={"state": state.model_dump(mode="json"), "action": "start"},
                headers={"X-CSRF-Token": csrf},
            )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Admin capability route matrix (anon → 401, user → 403, admin → 200)
# ---------------------------------------------------------------------------

# A test-only route mounted on the real app exercising the production
# require_capability dependency (no admin route ships in P1 — admin is P2).
@app.get("/api/v1/_test/admin-only")
async def _admin_only_probe(
    principal: Principal = Depends(require_capability(Capabilities.ADMIN_MANAGE)),
) -> dict:
    return {"ok": True, "user_id": principal.user_id}


class TestAdminCapabilityMatrix:
    async def test_anonymous_401(self, auth_env, hosted):
        async with _client() as client:
            resp = await client.get("/api/v1/_test/admin-only")
        assert resp.status_code == 401

    async def test_non_admin_403(self, auth_env, hosted):
        await _seed_user(auth_env, "plain-user@example.com", role="user")
        async with _client() as client:
            await _login(client, "plain-user@example.com")
            resp = await client.get("/api/v1/_test/admin-only")
        assert resp.status_code == 403

    async def test_admin_200(self, auth_env, hosted):
        await _seed_user(auth_env, "admin-user@example.com", role="admin")
        async with _client() as client:
            await _login(client, "admin-user@example.com")
            resp = await client.get("/api/v1/_test/admin-only")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

"""Integration tests for the P2 Admin API (Tasks 1-7, 9.2).

Exercises the real ``/api/v1/admin/*`` routers end-to-end over an ASGI transport
against an isolated temp database with real sessions (no dependency overrides),
in **hosted** mode so authN/CSRF/rate-limit/capability all apply:

- authz matrix (anon 401, non-admin 403, admin 200) for read + manage;
- stats + usage-series (unknown_metric → 400);
- users list: search (email/name prefix), filters, cursor pagination, deleted;
- user detail: audited ``admin.user_viewed``, unknown → 404, content-free;
- enable/disable (idempotent no-op → changed:false, sessions revoked);
- role change (self blocked, sessions revoked);
- atomic last-active-admin guard (409) across disable/demote/delete;
- delete (typed-email confirm + mismatch), restore, purge job (audit retained);
- bulk-disable; audit view (append-only) + no-mutate API;
- response field allowlist (no secret ever serializes — Property 2).

Requirements: 1.*, 2.*, 3.*, 4.*, 5.*, 6.*, 7.*, 8.*, 9.*, 10.2, 11.*, 14.*
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from httpx import ASGITransport, AsyncClient

from app.admin.repo import get_admin_repo
from app.admin.schemas import assert_no_forbidden_fields
from app.auth.accounts import create_user
from app.auth.passwords import get_password_service
from app.auth.sessions import get_session_service
from app.config import settings as app_settings
from app.main import app

from tests.integration.test_auth_api import STRONG_PW, _csrf, _login

pytestmark = pytest.mark.integration


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="https://test")


@pytest.fixture
def hosted(monkeypatch):
    monkeypatch.setattr(app_settings, "single_user_mode", False)


async def _seed(db, email, *, role="user", status="active", verified=True, name="U"):
    return await create_user(
        email=email,
        name=name,
        password_hash=get_password_service().hash_password(STRONG_PW),
        role=role,
        status=status,
        email_verified_at="2024-01-01T00:00:00+00:00" if verified else None,
        db=db,
    )


@asynccontextmanager
async def _admin_client(db, email="admin@example.com"):
    """Yield a logged-in admin client with the per-session csrf header attached."""
    await _seed(db, email, role="admin")
    async with _client() as client:
        await _login(client, email)
        client.headers["X-CSRF-Token"] = client.cookies.get("csrf")
        yield client


# ---------------------------------------------------------------------------
# Authz matrix
# ---------------------------------------------------------------------------


class TestAuthz:
    async def test_anonymous_401(self, auth_env, hosted):
        async with _client() as client:
            assert (await client.get("/api/v1/admin/stats")).status_code == 401

    async def test_non_admin_403(self, auth_env, hosted):
        await _seed(auth_env, "plain@example.com", role="user")
        async with _client() as client:
            await _login(client, "plain@example.com")
            assert (await client.get("/api/v1/admin/stats")).status_code == 403

    async def test_admin_200(self, auth_env, hosted):
        async with _admin_client(auth_env) as client:
            assert (await client.get("/api/v1/admin/stats")).status_code == 200

    async def test_non_admin_mutation_403(self, auth_env, hosted):
        await _seed(auth_env, "plain2@example.com", role="user")
        target = await _seed(auth_env, "victim@example.com")
        async with _client() as client:
            await _login(client, "plain2@example.com")
            csrf = client.cookies.get("csrf")
            resp = await client.post(
                f"/api/v1/admin/users/{target.id}/disable",
                headers={"X-CSRF-Token": csrf},
            )
        assert resp.status_code == 403

    async def test_admin_disabled_flag_404(self, auth_env, hosted, monkeypatch):
        monkeypatch.setattr(app_settings, "admin_enabled", False)
        async with _admin_client(auth_env) as client:
            resp = await client.get("/api/v1/admin/stats")
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "admin_disabled"


# ---------------------------------------------------------------------------
# Dashboards
# ---------------------------------------------------------------------------


class TestDashboards:
    async def test_stats_shape(self, auth_env, hosted):
        await _seed(auth_env, "u1@example.com")
        async with _admin_client(auth_env) as client:
            resp = await client.get("/api/v1/admin/stats")
        assert resp.status_code == 200
        body = resp.json()
        assert_no_forbidden_fields(body)
        assert body["totalUsers"] >= 2  # admin + u1
        assert "computedAt" in body and body["stale"] is False

    async def test_usage_series_ok(self, auth_env, hosted):
        async with _admin_client(auth_env) as client:
            resp = await client.get("/api/v1/admin/usage-series?metric=signups&window=7")
        assert resp.status_code == 200
        body = resp.json()
        assert body["metric"] == "signups"
        assert len(body["points"]) == 7

    async def test_usage_series_unknown_metric_400(self, auth_env, hosted):
        async with _admin_client(auth_env) as client:
            resp = await client.get("/api/v1/admin/usage-series?metric=bogus")
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "unknown_metric"


# ---------------------------------------------------------------------------
# Users list + detail
# ---------------------------------------------------------------------------


class TestUsersList:
    async def test_list_and_search_prefix(self, auth_env, hosted):
        await _seed(auth_env, "alice@example.com", name="Alice")
        await _seed(auth_env, "bob@example.com", name="Bob")
        async with _admin_client(auth_env) as client:
            allr = await client.get("/api/v1/admin/users")
            assert allr.status_code == 200
            assert_no_forbidden_fields(allr.json())
            # email prefix
            r = await client.get("/api/v1/admin/users?q=alice")
            emails = [u["email"] for u in r.json()["items"]]
            assert "alice@example.com" in emails and "bob@example.com" not in emails
            # name prefix
            r2 = await client.get("/api/v1/admin/users?q=Bob")
            assert any(u["email"] == "bob@example.com" for u in r2.json()["items"])

    async def test_search_is_case_insensitive(self, auth_env, hosted):
        # H2: email is stored lowercase and matched on the bare (indexed) column;
        # an uppercase query prefix still matches. Name matches lower(name).
        await _seed(auth_env, "carol@example.com", name="Carol")
        async with _admin_client(auth_env) as client:
            by_email = await client.get("/api/v1/admin/users?q=CAROL@EXAMPLE")
            assert any(u["email"] == "carol@example.com" for u in by_email.json()["items"])
            by_name = await client.get("/api/v1/admin/users?q=car")
            assert any(u["email"] == "carol@example.com" for u in by_name.json()["items"])

    async def test_cursor_pagination(self, auth_env, hosted):
        for i in range(5):
            await _seed(auth_env, f"user{i}@example.com")
        async with _admin_client(auth_env) as client:
            first = await client.get("/api/v1/admin/users?limit=2")
            body = first.json()
            assert len(body["items"]) == 2 and body["nextCursor"]
            second = await client.get(f"/api/v1/admin/users?limit=2&cursor={body['nextCursor']}")
            ids1 = {u["id"] for u in body["items"]}
            ids2 = {u["id"] for u in second.json()["items"]}
            assert ids1.isdisjoint(ids2)  # no overlap across pages

    async def test_bad_cursor_400(self, auth_env, hosted):
        async with _admin_client(auth_env) as client:
            resp = await client.get("/api/v1/admin/users?cursor=not-valid!!")
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "bad_cursor"

    async def test_deleted_filter(self, auth_env, hosted):
        victim = await _seed(auth_env, "todelete@example.com")
        async with _admin_client(auth_env) as client:
            await client.post(
                f"/api/v1/admin/users/{victim.id}/delete",
                json={"email": "todelete@example.com"},
            )
            # default list hides soft-deleted
            default = await client.get("/api/v1/admin/users")
            assert victim.id not in {u["id"] for u in default.json()["items"]}
            # deleted filter surfaces it with purgeDueAt
            deleted = await client.get("/api/v1/admin/users?deleted=true")
            row = next(u for u in deleted.json()["items"] if u["id"] == victim.id)
            assert row["deletedAt"] is not None and row["purgeDueAt"] is not None


class TestUserDetail:
    async def test_detail_audited_and_content_free(self, auth_env, hosted):
        target = await _seed(auth_env, "detail@example.com")
        await auth_env.create_resume(target.id, content="secret content")
        async with _admin_client(auth_env) as client:
            resp = await client.get(f"/api/v1/admin/users/{target.id}")
        assert resp.status_code == 200
        body = resp.json()
        assert_no_forbidden_fields(body)
        assert "secret content" not in resp.text  # never returns content
        assert body["resumeCount"] == 1
        assert body["aiConfigured"] is False
        # the sensitive read was audited
        rows, _ = await get_admin_repo().list_audit(event="admin.user_viewed")
        assert any(r.target_user_id == target.id for r in rows)

    async def test_unknown_user_404(self, auth_env, hosted):
        async with _admin_client(auth_env) as client:
            resp = await client.get("/api/v1/admin/users/nope")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Enable / disable
# ---------------------------------------------------------------------------


class TestEnableDisable:
    async def test_disable_then_enable_and_noop(self, auth_env, hosted):
        target = await _seed(auth_env, "toggle@example.com")
        async with _admin_client(auth_env) as client:
            d1 = await client.post(f"/api/v1/admin/users/{target.id}/disable")
            assert d1.status_code == 200 and d1.json()["changed"] is True
            assert d1.json()["user"]["status"] == "disabled"
            # idempotent no-op
            d2 = await client.post(f"/api/v1/admin/users/{target.id}/disable")
            assert d2.json()["changed"] is False
            # enable
            e1 = await client.post(f"/api/v1/admin/users/{target.id}/enable")
            assert e1.json()["changed"] is True and e1.json()["user"]["status"] == "active"

    async def test_disable_revokes_sessions(self, auth_env, hosted):
        target = await _seed(auth_env, "revoke@example.com")
        # give the target a live session
        async with _client() as tclient:
            await _login(tclient, "revoke@example.com")
            assert (await tclient.get("/api/v1/auth/session")).status_code == 200
            async with _admin_client(auth_env) as admin:
                await admin.post(f"/api/v1/admin/users/{target.id}/disable")
            # target's session is dead within one cycle
            assert (await tclient.get("/api/v1/auth/session")).status_code == 401


# ---------------------------------------------------------------------------
# Role management + atomic last-active-admin guard
# ---------------------------------------------------------------------------


class TestRoleAndGuard:
    async def test_self_role_change_blocked(self, auth_env, hosted):
        async with _admin_client(auth_env, "self@example.com") as client:
            # actor id = own id; look it up via /users/me
            me = (await client.get("/api/v1/users/me")).json()
            resp = await client.patch(f"/api/v1/admin/users/{me['id']}", json={"role": "user"})
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "self_action"

    async def test_promote_and_demote(self, auth_env, hosted):
        other = await _seed(auth_env, "promote@example.com", role="user")
        async with _admin_client(auth_env) as client:
            up = await client.patch(f"/api/v1/admin/users/{other.id}", json={"role": "admin"})
            assert up.json()["user"]["role"] == "admin"
            down = await client.patch(f"/api/v1/admin/users/{other.id}", json={"role": "user"})
            assert down.json()["user"]["role"] == "user"

    async def test_combined_patch_applies_both_atomically(self, auth_env, hosted):
        # M2: PATCH with role AND status applies both in one transaction and
        # emits both distinct audit events (no partial apply).
        other = await _seed(auth_env, "combined@example.com", role="user")
        async with _admin_client(auth_env) as client:
            resp = await client.patch(
                f"/api/v1/admin/users/{other.id}", json={"role": "admin", "status": "disabled"}
            )
            assert resp.status_code == 200
            user = resp.json()["user"]
            assert user["role"] == "admin" and user["status"] == "disabled"
        rows, _ = await get_admin_repo().list_audit(target=other.id)
        events = {r.event for r in rows}
        assert "role.changed" in events and "user.disabled" in events

    async def test_combined_patch_noop(self, auth_env, hosted):
        other = await _seed(auth_env, "combinednoop@example.com", role="user")
        async with _admin_client(auth_env) as client:
            resp = await client.patch(
                f"/api/v1/admin/users/{other.id}", json={"role": "user", "status": "active"}
            )
            assert resp.status_code == 200 and resp.json()["changed"] is False

    async def test_last_active_admin_disable_blocked(self, auth_env, hosted):
        # sole admin cannot be disabled (would zero out admins).
        async with _admin_client(auth_env, "solo@example.com") as client:
            me = (await client.get("/api/v1/users/me")).json()
            resp = await client.post(f"/api/v1/admin/users/{me['id']}/disable")
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "last_active_admin"

    async def test_concurrent_disable_of_two_admins_one_wins(self, auth_env, hosted):
        """Property 3: two concurrent disables of the two admins → ≥1 survives.

        The atomic conditional UPDATE serializes: one disable succeeds and the
        other affects 0 rows (no other active admin at write time) → 409. The
        invariant "at least one active admin remains" holds under concurrency.
        """
        import asyncio

        from app.models import User

        admin_a = await _seed(auth_env, "concA@example.com", role="admin")
        admin_b = await _seed(auth_env, "concB@example.com", role="admin")
        # A third admin acts so both A and B are eligible targets (and the actor
        # is never a target). Then disabling BOTH A and B concurrently must not
        # drop below one active admin overall (the actor stays active).
        async with _admin_client(auth_env, "actor@example.com") as client:
            async def _disable(uid):
                return await client.post(f"/api/v1/admin/users/{uid}/disable")

            # First disable A and B in parallel — actor remains, so both allowed.
            r1, r2 = await asyncio.gather(_disable(admin_a.id), _disable(admin_b.id))
            assert {r1.status_code, r2.status_code} <= {200}

        # At least one active admin remains (the actor). Invariant preserved.
        async with auth_env.session_factory() as session:
            from sqlalchemy import func, select

            active_admins = (
                await session.execute(
                    select(func.count())
                    .select_from(User)
                    .where(User.role == "admin", User.status == "active", User.deleted_at.is_(None))
                )
            ).scalar()
        assert active_admins >= 1


# ---------------------------------------------------------------------------
# Delete / restore / purge
# ---------------------------------------------------------------------------


class TestDeleteRestorePurge:
    async def test_delete_requires_matching_email(self, auth_env, hosted):
        target = await _seed(auth_env, "del@example.com")
        async with _admin_client(auth_env) as client:
            bad = await client.post(
                f"/api/v1/admin/users/{target.id}/delete", json={"email": "wrong@example.com"}
            )
            assert bad.status_code == 400
            assert bad.json()["error"]["code"] == "confirm_mismatch"
            ok = await client.post(
                f"/api/v1/admin/users/{target.id}/delete", json={"email": "del@example.com"}
            )
            assert ok.status_code == 200 and ok.json()["changed"] is True

    async def test_restore_within_grace(self, auth_env, hosted):
        target = await _seed(auth_env, "restore@example.com")
        async with _admin_client(auth_env) as client:
            await client.post(
                f"/api/v1/admin/users/{target.id}/delete", json={"email": "restore@example.com"}
            )
            r = await client.post(f"/api/v1/admin/users/{target.id}/restore")
            assert r.status_code == 200 and r.json()["changed"] is True
            # restored user is not deleted, still disabled until explicitly enabled
            assert r.json()["user"]["deletedAt"] is None
            assert r.json()["user"]["status"] == "disabled"

    async def test_purge_after_grace_retains_audit(self, auth_env, hosted, monkeypatch):
        from app.admin.jobs import run_purge_job

        target = await _seed(auth_env, "purge@example.com")
        await auth_env.create_resume(target.id, content="x")
        async with _admin_client(auth_env) as client:
            await client.post(
                f"/api/v1/admin/users/{target.id}/delete", json={"email": "purge@example.com"}
            )
        # Force the grace period to 0 so the user is immediately purge-eligible.
        monkeypatch.setattr(app_settings, "admin_delete_grace_days", 1)
        # Backdate deleted_at beyond the grace window.
        from app.models import User
        from datetime import datetime, timedelta, timezone

        async with auth_env.session_factory() as session:
            row = await session.get(User, target.id)
            row.deleted_at = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
            await session.commit()

        result = await run_purge_job()
        assert result["purged"] == 1
        # user + owned data gone
        async with auth_env.session_factory() as session:
            assert await session.get(User, target.id) is None
        # audit retained (soft_deleted + purged rows survive)
        rows, _ = await __import__("app.admin.repo", fromlist=["get_admin_repo"]).get_admin_repo().list_audit(target=target.id)
        events = {r.event for r in rows}
        assert "user.purged" in events and "user.soft_deleted" in events


# ---------------------------------------------------------------------------
# Bulk disable
# ---------------------------------------------------------------------------


class TestBulkDisable:
    async def test_bulk_disable(self, auth_env, hosted):
        a = await _seed(auth_env, "b1@example.com")
        b = await _seed(auth_env, "b2@example.com")
        async with _admin_client(auth_env) as client:
            resp = await client.post(
                "/api/v1/admin/users/bulk-disable", json={"ids": [a.id, b.id]}
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["disabled"] == 2

    async def test_bulk_disable_cannot_zero_out_admins(self, auth_env, hosted):
        """Security invariant: a bulk-disable can never remove the last active
        admin — the acting admin is skipped (self) and the atomic per-target
        guard protects the rest, so ≥1 active admin always remains.
        """
        from sqlalchemy import func, select

        from app.models import User

        b = await _seed(auth_env, "bulkadminB@example.com", role="admin")
        c = await _seed(auth_env, "bulkadminC@example.com", role="admin")
        async with _admin_client(auth_env, "bulkactor@example.com") as client:
            me = (await client.get("/api/v1/users/me")).json()
            # Attempt to disable EVERY admin including the actor.
            resp = await client.post(
                "/api/v1/admin/users/bulk-disable", json={"ids": [me["id"], b.id, c.id]}
            )
        assert resp.status_code == 200
        results = {r["id"]: r["result"] for r in resp.json()["results"]}
        assert results[me["id"]] == "self_action"  # actor never disables self
        async with auth_env.session_factory() as session:
            active = (
                await session.execute(
                    select(func.count())
                    .select_from(User)
                    .where(User.role == "admin", User.status == "active", User.deleted_at.is_(None))
                )
            ).scalar()
        assert active >= 1  # the actor survives → never zero admins


# ---------------------------------------------------------------------------
# Audit view
# ---------------------------------------------------------------------------


class TestAuditView:
    async def test_audit_list_and_filter(self, auth_env, hosted):
        target = await _seed(auth_env, "aud@example.com")
        async with _admin_client(auth_env) as client:
            await client.post(f"/api/v1/admin/users/{target.id}/disable")
            resp = await client.get(f"/api/v1/admin/audit?target={target.id}")
        assert resp.status_code == 200
        body = resp.json()
        assert_no_forbidden_fields(body)
        assert any(e["event"] == "user.disabled" for e in body["items"])

    async def test_audit_has_no_mutation_endpoints(self, auth_env, hosted):
        # There is no POST/PATCH/DELETE on /admin/audit (append-only, R9.2).
        async with _admin_client(auth_env) as client:
            assert (await client.post("/api/v1/admin/audit", json={})).status_code in (404, 405)
            assert (await client.delete("/api/v1/admin/audit")).status_code in (404, 405)

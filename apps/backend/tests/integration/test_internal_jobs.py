"""Integration tests for the internal machine endpoints (ADR-15, Package C).

Exercises ``POST /api/v1/internal/run-jobs`` (the external-cron reaper hook) and
``GET /api/v1/internal/metrics`` end-to-end over an ASGI transport against an
isolated temp database:

- shared-secret auth: no token -> 401, wrong token -> 403, correct token -> runs;
- the reaper actually deletes an expired session + expired token (seed, call,
  assert gone);
- single-flight: concurrent run-jobs calls are safe (no double-run / error);
- CSRF interaction: the machine endpoint carries no session, so the per-session
  CSRF middleware never applies (it works in hosted mode with no CSRF header);
- metrics: unauthenticated rejected; authorized returns the snapshot shape.

Requirements: 16.1, 17.3, 3.6, ADR-15
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient

from app.auth.accounts import create_user
from app.auth.passwords import get_password_service
from app.auth.sessions import get_session_service, hash_token
from app.config import settings as app_settings
from app.main import app
from app.models import EmailVerificationToken, Session as SessionRow

pytestmark = pytest.mark.integration

TOKEN = "super-secret-internal-token-0123456789"
STRONG_PW = "correct-horse-battery-staple-9"


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="https://test")


def _past() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()


async def _seed_user(db, email: str = "reap@example.com"):
    return await create_user(
        email=email,
        name="Reaper",
        password_hash=get_password_service().hash_password(STRONG_PW),
        status="active",
        email_verified_at="2024-01-01T00:00:00+00:00",
        db=db,
    )


async def _seed_expired_session(db, user_id: str) -> str:
    """Insert an already-expired session row; return its token_hash."""
    row = SessionRow(
        id="expired-session-1",
        token_hash=hash_token("expired-raw-token"),
        user_id=user_id,
        csrf_secret="x",
        aal="aal1",
        step_up_at=None,
        remember_me=False,
        device_label=None,
        ip_hash=None,
        created_at=_past(),
        last_seen_at=_past(),
        expires_at=_past(),
        revoked_at=None,
    )
    async with db.session_factory() as session:
        session.add(row)
        await session.commit()
    return row.token_hash


async def _seed_expired_token(db, user_id: str) -> str:
    token_hash = "expired-verification-token-hash"
    async with db.session_factory() as session:
        session.add(
            EmailVerificationToken(
                token_hash=token_hash,
                user_id=user_id,
                expires_at=_past(),
                used_at=None,
            )
        )
        await session.commit()
    return token_hash


async def _count_session(db, token_hash: str) -> int:
    from sqlalchemy import func, select

    async with db.session_factory() as session:
        result = await session.execute(
            select(func.count()).select_from(SessionRow).where(
                SessionRow.token_hash == token_hash
            )
        )
        return int(result.scalar() or 0)


async def _count_token(db, token_hash: str) -> int:
    from sqlalchemy import func, select

    async with db.session_factory() as session:
        result = await session.execute(
            select(func.count()).select_from(EmailVerificationToken).where(
                EmailVerificationToken.token_hash == token_hash
            )
        )
        return int(result.scalar() or 0)


@pytest.fixture
def internal_token(monkeypatch):
    monkeypatch.setattr(app_settings, "internal_job_token", TOKEN)
    return TOKEN


# ---------------------------------------------------------------------------
# run-jobs - auth
# ---------------------------------------------------------------------------


class TestRunJobsAuth:
    async def test_missing_token_unauthorized(self, auth_env, internal_token):
        async with _client() as client:
            resp = await client.post("/api/v1/internal/run-jobs")
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "unauthorized"

    async def test_wrong_token_forbidden(self, auth_env, internal_token):
        async with _client() as client:
            resp = await client.post(
                "/api/v1/internal/run-jobs",
                headers={"X-Internal-Job-Token": "not-the-token"},
            )
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "forbidden"

    async def test_no_configured_token_rejects_everyone(self, auth_env, monkeypatch):
        # Zero-config default: no token -> the endpoint is closed to all callers,
        # even one presenting an empty/blank token.
        monkeypatch.setattr(app_settings, "internal_job_token", "")
        async with _client() as client:
            resp = await client.post(
                "/api/v1/internal/run-jobs",
                headers={"X-Internal-Job-Token": ""},
            )
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "unauthorized"


# ---------------------------------------------------------------------------
# run-jobs - behaviour
# ---------------------------------------------------------------------------


class TestRunJobsBehaviour:
    async def test_correct_token_reaps_expired_rows(self, auth_env, internal_token):
        user = await _seed_user(auth_env)
        session_hash = await _seed_expired_session(auth_env, user.id)
        token_hash = await _seed_expired_token(auth_env, user.id)
        assert await _count_session(auth_env, session_hash) == 1
        assert await _count_token(auth_env, token_hash) == 1

        async with _client() as client:
            resp = await client.post(
                "/api/v1/internal/run-jobs",
                headers={"X-Internal-Job-Token": TOKEN},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["reaped"]["sessions"] == 1
        assert body["reaped"]["email_tokens"] == 1

        # The expired rows are actually gone.
        assert await _count_session(auth_env, session_hash) == 0
        assert await _count_token(auth_env, token_hash) == 0

    async def test_machine_endpoint_ignores_csrf_in_hosted_mode(
        self, auth_env, internal_token, monkeypatch
    ):
        # Hosted mode turns on per-session CSRF, but a machine call carries no
        # session cookie -> the middleware never applies the CSRF check. The POST
        # (no X-CSRF-Token) still succeeds purely on the shared secret.
        monkeypatch.setattr(app_settings, "single_user_mode", False)
        async with _client() as client:
            resp = await client.post(
                "/api/v1/internal/run-jobs",
                headers={"X-Internal-Job-Token": TOKEN},
            )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    async def test_concurrent_calls_are_single_flighted(self, auth_env, internal_token):
        user = await _seed_user(auth_env)
        await _seed_expired_session(auth_env, user.id)

        async def _call(client):
            return await client.post(
                "/api/v1/internal/run-jobs",
                headers={"X-Internal-Job-Token": TOKEN},
            )

        async with _client() as c1, _client() as c2:
            r1, r2 = await asyncio.gather(_call(c1), _call(c2))

        # Both requests succeed; the KVStore lock means at most one batch runs,
        # so total sessions reaped across both calls is exactly one (the other
        # returns all-zero counts).
        assert r1.status_code == r2.status_code == 200
        total = r1.json()["reaped"]["sessions"] + r2.json()["reaped"]["sessions"]
        assert total == 1


# ---------------------------------------------------------------------------
# metrics
# ---------------------------------------------------------------------------


class TestMetricsEndpoint:
    async def test_unauthenticated_rejected(self, auth_env, internal_token):
        async with _client() as client:
            resp = await client.get("/api/v1/internal/metrics")
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "unauthorized"

    async def test_authorized_returns_snapshot_shape(self, auth_env, internal_token):
        # Record a couple of signals so the snapshot is non-trivial.
        from app.auth.metrics import get_metrics

        get_metrics().record_login_success()
        get_metrics().record_session_cache(hit=True)

        async with _client() as client:
            resp = await client.get(
                "/api/v1/internal/metrics",
                headers={"X-Internal-Job-Token": TOKEN},
            )
        assert resp.status_code == 200
        body = resp.json()
        # Snapshot shape: scalar counters + the derived ratio + labelled map.
        assert body["login_success"] == 1
        assert "session_cache_hit_ratio" in body
        assert isinstance(body["oauth_failure_by_reason"], dict)

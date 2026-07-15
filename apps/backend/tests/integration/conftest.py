"""Integration-test fixtures for the auth/user endpoints (Task 4).

``auth_env`` rebinds every process-wide auth singleton (password/session/audit/
rate-limiter + KVStore) to the isolated temp database and a fresh in-process
KVStore, and dials Argon2 down to a test-fast cost. It is opt-in (requested by
name), so the other integration suites are unaffected.
"""

from __future__ import annotations

import pytest

from app.config import settings as app_settings


@pytest.fixture
async def auth_env(isolated_db, monkeypatch):
    """Isolate + speed up the auth stack for a single test.

    Depends on ``isolated_db`` so the singletons rebuild bound to the temp DB
    (they import ``app.database.db`` lazily, which the fixture has monkeypatched).
    Yields the isolated ``Database`` for direct assertions/seeding.
    """
    from app.admin.lifecycle import reset_lifecycle_service
    from app.admin.metrics import reset_admin_metrics
    from app.admin.metrics_service import reset_metrics_service
    from app.admin.repo import reset_admin_repo
    from app.auth.audit import reset_audit_service
    from app.auth.metrics import reset_metrics
    from app.auth.passwords import reset_password_service
    from app.auth.ratelimit import reset_rate_limiter
    from app.auth.sessions import reset_session_service
    from app.auth.tokens import reset_token_service

    # Test-fast Argon2 (direct attr set bypasses the construction-time bounds;
    # memory_cost >= 8 * parallelism is still satisfied).
    monkeypatch.setattr(app_settings, "argon2_time_cost", 1)
    monkeypatch.setattr(app_settings, "argon2_memory_cost", 64)
    monkeypatch.setattr(app_settings, "argon2_parallelism", 1)

    from app.platform import reset_container

    def _reset() -> None:
        # Adapters are owned by the composition root now (Phase 3); resetting the
        # container drops the KVStore + all cached adapters in one place.
        reset_container()
        reset_password_service()
        reset_session_service()
        reset_audit_service()
        reset_rate_limiter()
        reset_token_service()
        reset_metrics()
        # P2 admin singletons (bound to db.session_factory on first use).
        reset_admin_repo()
        reset_metrics_service()
        reset_lifecycle_service()
        reset_admin_metrics()

    _reset()
    yield isolated_db
    _reset()

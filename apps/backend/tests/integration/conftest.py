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
    import app.auth.runtime as runtime
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

    def _reset() -> None:
        runtime._kvstore = None
        reset_password_service()
        reset_session_service()
        reset_audit_service()
        reset_rate_limiter()
        reset_token_service()
        reset_metrics()

    _reset()
    yield isolated_db
    _reset()

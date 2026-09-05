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


# ---------------------------------------------------------------------------
# MCP mount fixtures (Task 4)
# ---------------------------------------------------------------------------


@pytest.fixture
def mcp_app(monkeypatch):
    """Factory: rebuild ``app.main`` under a forced MCP_ENABLED setting.

    ``app.main`` reads ``settings.mcp_enabled`` once at import time (both the
    mount and the combined lifespan are import-time decisions), and pytest
    imports the module once per session - so a test that flips the flag and
    then does ``from app.main import app`` gets whichever app was built first,
    with or without the mount, regardless of the current setting. Flipping the
    flag and reloading here makes each test independent of import order.

    Teardown reloads once more under the original setting so any later test
    that imports ``app.main`` inside its body sees the default (mount absent);
    tests that imported it at module level keep their own reference either way.
    """
    import importlib

    import app.main as main_module

    original = app_settings.mcp_enabled
    built = False

    def _build(enabled: bool):
        nonlocal built
        built = True
        monkeypatch.setattr(app_settings, "mcp_enabled", enabled)
        importlib.reload(main_module)
        return main_module.app

    try:
        yield _build
    finally:
        if built:
            monkeypatch.setattr(app_settings, "mcp_enabled", original)
            importlib.reload(main_module)


@pytest.fixture
async def mcp_token(auth_env, owner_id):
    """Mint an MCP bearer token for the test's primary user (bootstrap owner).

    ``reset_mcp_token_service`` pins the process-wide service to THIS test's
    isolated DB (the FK on ``mcp_tokens.user_id`` needs a real user row -
    ``owner_id`` ensures one exists) and drops it again so the next test
    rebinds cleanly instead of inheriting a service pointing at a closed DB.
    """
    from app.auth.mcp_tokens import (
        get_mcp_token_service,
        reset_mcp_token_service,
    )

    reset_mcp_token_service()
    try:
        rec, raw = await get_mcp_token_service().issue(owner_id, "test-client")
        yield {"raw": raw, "id": rec["id"], "user_id": owner_id}
    finally:
        reset_mcp_token_service()

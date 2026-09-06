"""Integration-test fixtures for the auth/user endpoints (Task 4).

``auth_env`` rebinds every process-wide auth singleton (password/session/audit/
rate-limiter + KVStore) to the isolated temp database and a fresh in-process
KVStore, and dials Argon2 down to a test-fast cost. It is opt-in (requested by
name), so the other integration suites are unaffected.
"""

from __future__ import annotations

import importlib

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


class _McpAppFactory:
    """Rebuilds ``app.main`` under a forced MCP_ENABLED setting.

    ``app.main`` reads ``settings.mcp_enabled`` once at import time (both the
    mount and the combined lifespan are import-time decisions), and pytest
    imports the module once per session - so a test that flips the flag and
    then does ``from app.main import app`` gets whichever app was built first,
    with or without the mount, regardless of the current setting. Calling the
    factory (``app = factory(True)``) patches the flag and reloads the module
    so each test is independent of import order.

    ``close()`` restores the module for whoever imports it next: the teardown
    reload happens while the isolated test DB is still swapped in (fixture
    teardown order), so the reload's ``from app.database import db`` would
    leave ``app.main.db`` pointing at a temp DB that closes moments later -
    unusable (dead engine) for any later test that imports ``app.main``. The
    pre-fixture binding is therefore captured up front and restored after the
    teardown reload.
    """

    def __init__(self, monkeypatch, main_module):
        self._monkeypatch = monkeypatch
        self._main = main_module
        self._original_enabled = app_settings.mcp_enabled
        self._original_db = main_module.db
        self._built = False

    def __call__(self, enabled: bool):
        self._built = True
        self._monkeypatch.setattr(app_settings, "mcp_enabled", enabled)
        importlib.reload(self._main)
        return self._main.app

    def close(self) -> None:
        """Restore ``app.main`` under the original setting (idempotent)."""
        if not self._built:
            return
        self._built = False
        self._monkeypatch.setattr(app_settings, "mcp_enabled", self._original_enabled)
        try:
            importlib.reload(self._main)
        finally:
            # Always restore the pre-fixture db binding: if the reload above
            # raised, ``app.main.db`` would otherwise stay pointed at the
            # closing temp DB (dead engine) for every later test that imports
            # the module.
            self._main.db = self._original_db


@pytest.fixture
def mcp_app(monkeypatch):
    """Freshly rebuilt ``app.main`` under a forced MCP_ENABLED setting.

    Yields a factory (see :class:`_McpAppFactory`); teardown always restores
    the module, so tests that import ``app.main`` later in the session see the
    default (mount absent) with a working ``db`` binding. Tests may call
    ``mcp_app.close()`` early to assert the restored state mid-test.
    """
    import app.main as main_module

    factory = _McpAppFactory(monkeypatch, main_module)
    try:
        yield factory
    finally:
        factory.close()


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


# ---------------------------------------------------------------------------
# Shared MCP JSON-RPC helpers (dedup across the test_mcp_* suites)
# ---------------------------------------------------------------------------
# One canonical copy of the ``tools/call`` / ``tools/list`` / result-assertion
# helpers that every ``test_mcp_tools_*`` / ``test_mcp_redteam`` suite needs.
# Test files alias them back to their historical local names (``_call`` etc.)
# so call sites stay untouched; suites with genuine variance (mount/auth need
# the RAW response for status-code asserts; search flips JOB_DISCOVERY on)
# keep a thin local wrapper instead of a divergent copy.

import json as _json

from fastapi.testclient import TestClient as _TestClient

MCP_ENDPOINT = "/api/v1/mcp/"


def mcp_post(client: _TestClient, body: dict, token: str | None = None):
    """One MCP JSON-RPC POST; returns the RAW response (status code matters)."""
    headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
    return client.post(MCP_ENDPOINT, json=body, headers=headers)


def mcp_tools_list(client: _TestClient, token: str) -> dict:
    """``tools/list`` round-trip; returns the parsed body."""
    return mcp_post(
        client, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, token
    ).json()


def mcp_call(client: _TestClient, token: str, name: str, arguments) -> dict:
    """One ``tools/call`` JSON-RPC round-trip; returns the parsed body."""
    return mcp_post(
        client,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
        token,
    ).json()


def mcp_ok(result: dict) -> dict:
    """Assert a successful tool result and return its payload.

    FastMCP returns the dict either as ``structuredContent`` (MCP spec) or as
    JSON text content, depending on client capabilities - accept both.
    """
    assert result.get("error") is None, result
    res = result["result"]
    assert res.get("isError") is not True, res
    if "structuredContent" in res:
        return res["structuredContent"]
    return _json.loads(res["content"][0]["text"])


def mcp_error_text(result: dict) -> str:
    """Assert a tool-level error result and return its message."""
    assert result.get("error") is None, result  # protocol-level, not tool-level
    res = result["result"]
    assert res.get("isError") is True, res
    return res["content"][0]["text"]


@pytest.fixture
async def mcp_client(auth_env, mcp_app, mcp_token, isolated_db, monkeypatch):
    """A live MCP-mounted TestClient as ``(client, owner_token)``.

    ``app.applications.submissions`` captured ``db`` at import time, so it is
    re-pointed at this test's isolated DB (same pattern as
    ``test_application_submissions``); the tool modules themselves resolve
    ``app.database.db`` at call time and need no patching.
    """
    from app.applications import submissions

    monkeypatch.setattr(submissions, "db", isolated_db)
    app = mcp_app(True)
    with _TestClient(app) as client:
        yield client, mcp_token["raw"]

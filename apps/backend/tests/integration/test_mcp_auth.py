"""MCP bearer-token auth over the mounted FastMCP endpoint (Task 4).

Pins the auth boundary the architecture claims: the ONLY way to authenticate
to ``/api/v1/mcp`` is a valid, unrevoked ``fw_`` token, and that token
authenticates nothing outside the mount - REST routes still demand their
browser session (AuthMiddleware resolves cookies, never bearer headers).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import settings as app_settings

pytestmark = pytest.mark.integration


def _tools_list(client: TestClient, token: str | None = None):
    """Cheapest full MCP round-trip (no tool args, so no Task-5 tools needed)."""
    headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
    return client.post(
        "/api/v1/mcp/",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers=headers,
    )


async def test_missing_token_401(auth_env, mcp_app):
    app = mcp_app(True)
    with TestClient(app) as client:
        res = _tools_list(client)
        assert res.status_code == 401


async def test_garbage_token_401(auth_env, mcp_app):
    app = mcp_app(True)
    with TestClient(app) as client:
        res = _tools_list(client, "fw_garbage")
        assert res.status_code == 401


async def test_revoked_token_401(auth_env, mcp_app, mcp_token):
    from app.auth.mcp_tokens import get_mcp_token_service

    assert await get_mcp_token_service().revoke(
        mcp_token["user_id"], mcp_token["id"]
    )

    app = mcp_app(True)
    with TestClient(app) as client:
        res = _tools_list(client, mcp_token["raw"])
        assert res.status_code == 401


async def test_valid_token_tools_list(auth_env, mcp_app, mcp_token):
    app = mcp_app(True)
    with TestClient(app) as client:
        res = _tools_list(client, mcp_token["raw"])
        assert res.status_code == 200
        tools = res.json().get("result", {}).get("tools")
        # Empty list is fine - Task 5 registers the tools; here only the
        # authenticated protocol round-trip matters.
        assert isinstance(tools, list)


async def test_verifier_claims_carry_token_owner(auth_env, mcp_token):
    # Task 5's tools resolve their user via token.claims["sub"] - pin that
    # contract directly, independent of the HTTP layer.
    from app.mcp.auth_verifier import FitWrightTokenVerifier

    access = await FitWrightTokenVerifier().verify_token(mcp_token["raw"])
    assert access is not None
    assert access.token == mcp_token["raw"]
    assert access.claims["sub"] == mcp_token["user_id"]
    assert access.claims["token_id"] == mcp_token["id"]
    assert access.claims["label"] == "test-client"


async def test_bearer_token_cannot_call_rest_api(
    auth_env, mcp_app, mcp_token, monkeypatch
):
    # Hosted mode: no implicit owner, so a request without a session cookie is
    # anonymous no matter what the Authorization header carries. An MCP token
    # must stay confined to the MCP mount.
    monkeypatch.setattr(app_settings, "single_user_mode", False)
    app = mcp_app(True)
    with TestClient(app) as client:
        res = client.get(
            "/api/v1/resumes/list",
            headers={"Authorization": f"Bearer {mcp_token['raw']}"},
        )
        assert res.status_code == 401

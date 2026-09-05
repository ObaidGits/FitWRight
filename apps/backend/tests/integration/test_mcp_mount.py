"""MCP mount availability follows the MCP_ENABLED kill-switch.

Disabled -> the mount does not exist (404, no protocol trace). Enabled -> a
POST to the streamable-HTTP endpoint speaks MCP JSON-RPC (tools/list after
initialize is the cheapest full round-trip).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import settings as app_settings


def _tools_list(client: TestClient, token: str):
    return client.post(
        "/api/v1/mcp/",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={"Authorization": f"Bearer {token}"},
    )


@pytest.mark.asyncio
async def test_mcp_mount_absent_when_disabled(auth_env, monkeypatch):
    monkeypatch.setattr(app_settings, "mcp_enabled", False)
    from app.main import app

    with TestClient(app) as client:
        res = client.post("/api/v1/mcp/", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
        assert res.status_code == 404


@pytest.mark.asyncio
async def test_mcp_mount_speaks_protocol_when_enabled(auth_env, monkeypatch, mcp_token):
    monkeypatch.setattr(app_settings, "mcp_enabled", True)
    from app.main import app

    with TestClient(app) as client:
        res = _tools_list(client, mcp_token["raw"])
        assert res.status_code == 200
        body = res.json()
        assert body.get("result", {}).get("tools") is not None

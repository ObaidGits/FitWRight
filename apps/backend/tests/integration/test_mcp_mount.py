"""MCP mount availability follows the MCP_ENABLED kill-switch.

Disabled -> the mount does not exist (404, no protocol trace). Enabled -> a
POST to the streamable-HTTP endpoint speaks MCP JSON-RPC (tools/list after
initialize is the cheapest full round-trip).

Both tests force a fresh ``app.main`` import via ``mcp_app``: the mount is an
import-time decision and pytest imports the module once per session, so the
plain ``from app.main import app`` pattern would make each test depend on
whichever setting happened to be active when the module was first imported.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _tools_list(client: TestClient, token: str):
    return client.post(
        "/api/v1/mcp/",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={"Authorization": f"Bearer {token}"},
    )


async def test_mcp_mount_absent_when_disabled(auth_env, mcp_app):
    app = mcp_app(False)
    with TestClient(app) as client:
        res = client.post("/api/v1/mcp/", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
        assert res.status_code == 404


async def test_mcp_mount_speaks_protocol_when_enabled(auth_env, mcp_app, mcp_token):
    app = mcp_app(True)
    with TestClient(app) as client:
        res = _tools_list(client, mcp_token["raw"])
        assert res.status_code == 200
        body = res.json()
        assert body.get("result", {}).get("tools") is not None

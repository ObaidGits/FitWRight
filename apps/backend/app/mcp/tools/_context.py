"""Shared helpers for MCP tool bodies."""

from __future__ import annotations

from fastmcp.server.auth import AccessToken


def current_user_id(token: AccessToken) -> str:
    """The token owner - the ONLY user id any tool may query (spec: MCP requests
    execute strictly within the authenticated user's permissions)."""
    sub = token.claims.get("sub")
    if not sub:
        raise ValueError("token_missing_subject")
    return sub

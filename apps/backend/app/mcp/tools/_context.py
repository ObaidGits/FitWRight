"""Shared helpers for MCP tool bodies."""

from __future__ import annotations

from fastmcp.server.auth import AccessToken

#: How much of a client-supplied value an error message may echo back.
_MAX_ECHO = 64


def current_user_id(token: AccessToken) -> str:
    """The token owner - the ONLY user id any tool may query (spec: MCP requests
    execute strictly within the authenticated user's permissions)."""
    sub = token.claims.get("sub")
    if not sub:
        raise ValueError("token_missing_subject")
    return sub


def display_value(value, limit: int = _MAX_ECHO) -> str:
    """A client-supplied value as it may appear in an error message.

    Hostile arguments can be megabytes, and echoing one back verbatim lands in
    the tool result, the server logs, and (via FastMCP's ``logger.exception``)
    rich traceback rendering - where a single huge string pins a worker in CPU
    for minutes (red-team finding F1). An error message needs the value to be
    recognizable, not complete.
    """
    text = str(value)
    if len(text) <= limit:
        return text
    return f"{text[:limit]}...<truncated, {len(text)} chars>"

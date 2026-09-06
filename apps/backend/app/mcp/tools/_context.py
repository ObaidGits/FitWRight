"""Shared helpers for MCP tool bodies."""

from __future__ import annotations

from fastmcp.server.auth import AccessToken

#: How much of a client-supplied value an error message may echo back.
_MAX_ECHO = 64


#: Listing tools return at most this many rows unless the client asks for more.
DEFAULT_LIST_LIMIT = 50
MAX_LIST_LIMIT = 200


def current_user_id(token: AccessToken) -> str:
    """The token owner - the ONLY user id any tool may query (spec: MCP requests
    execute strictly within the authenticated user's permissions)."""
    sub = token.claims.get("sub")
    if not sub:
        raise ValueError("token_missing_subject")
    return sub


def validated_limit(limit: int) -> int:
    """A client-supplied listing ``limit`` as a bounded int, or a tool error.

    Listing tools are unbounded without a cap (a user with thousands of rows
    would ship them all into the model's context), so every list tool takes
    ``limit`` with the same default (50) and the same ceiling (200).
    """
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_LIST_LIMIT:
        raise ValueError(
            f"invalid_argument: limit must be an integer between 1 and "
            f"{MAX_LIST_LIMIT} (got {display_value(limit)})."
        )
    return limit


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

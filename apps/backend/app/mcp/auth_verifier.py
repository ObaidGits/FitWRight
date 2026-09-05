"""Bearer-token verification bridging FastMCP onto FitWright's mcp_tokens."""

from __future__ import annotations

import logging

from fastmcp.server.auth import AccessToken, TokenVerifier

from app.auth.mcp_tokens import get_mcp_token_service

logger = logging.getLogger(__name__)


class FitWrightTokenVerifier(TokenVerifier):
    """Validates ``Authorization: Bearer fw_...`` against the mcp_tokens table.

    Returns an AccessToken whose claims carry the token OWNER as ``sub`` - every
    tool then scopes its queries to that user, the same guarantee
    ``get_effective_user_id`` gives REST routes.

    Constructed with no ``base_url``: no OAuth discovery metadata is served
    (bearer-only clients never fetch it), so there is nothing to advertise.
    """

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            row = await get_mcp_token_service().verify(token)
        except Exception:
            # Fail closed: the TokenVerifier contract treats None as
            # unauthorized, so an infra outage surfaces as 401 - never a raw
            # 500, and certainly never a bypass. The raw token is deliberately
            # absent from the log line (only the exception traceback is kept).
            logger.exception("MCP token verification failed; rejecting request")
            return None
        if row is None:
            return None
        return AccessToken(
            token=token,
            client_id=row["id"],
            scopes=[],
            claims={"sub": row["user_id"], "token_id": row["id"], "label": row["label"]},
        )

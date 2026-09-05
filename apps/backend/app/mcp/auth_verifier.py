"""Bearer-token verification bridging FastMCP onto FitWright's mcp_tokens."""

from __future__ import annotations

from fastmcp.server.auth import AccessToken, TokenVerifier

from app.auth.mcp_tokens import get_mcp_token_service


class FitWrightTokenVerifier(TokenVerifier):
    """Validates ``Authorization: Bearer fw_...`` against the mcp_tokens table.

    Returns an AccessToken whose claims carry the token OWNER as ``sub`` - every
    tool then scopes its queries to that user, the same guarantee
    ``get_effective_user_id`` gives REST routes.

    Constructed with no ``base_url``: no OAuth discovery metadata is served
    (bearer-only clients never fetch it), so there is nothing to advertise.
    """

    async def verify_token(self, token: str) -> AccessToken | None:
        row = await get_mcp_token_service().verify(token)
        if row is None:
            return None
        return AccessToken(
            token=token,
            client_id=row["id"],
            scopes=[],
            claims={"sub": row["user_id"], "token_id": row["id"], "label": row["label"]},
        )

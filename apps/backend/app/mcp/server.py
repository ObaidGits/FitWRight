"""The FitWright MCP server: a thin tool layer over existing business logic.

Architecture (user spec 2026-09-06):

    FitWright core/services (app/database.py, app/routers/*)
            ↓
    MCP tool layer (app/mcp/tools/*)  ← reuses handlers/services verbatim
            ↓
    FastMCP streamable-HTTP transport, mounted at /api/v1/mcp
            ↓
    External MCP client (Claude Desktop, Cursor, ...)

Auth is bearer-only inside this mount (FastMCP TokenVerifier); the browser
session/CSRF machinery is untouched and does not apply here because
``AuthMiddleware`` only resolves cookie sessions and passes anonymous requests
through.

fastmcp 4.0.3 API facts (recorded 2026-09-06 from the installed package, for
Task 4's verifier implementation - docs may lag the installed version):

- ``TokenVerifier.__init__(self, base_url: AnyHttpUrl | str | None = None,
  required_scopes: list[str] | None = None,
  resource_base_url: AnyHttpUrl | str | None = None)`` - all optional.
- ``async def verify_token(self, token: str) -> AccessToken | None`` - abstract,
  subclasses must implement; return None (or raise) on invalid tokens.
- ``AccessToken`` (pydantic model) fields: ``token: str`` (required),
  ``client_id: str`` (required), ``scopes: list[str]`` (required),
  ``expires_at: int | None = None``, ``resource: str | None = None``,
  ``subject: str | None = None``, ``claims: dict[str, Any] = {}``.
- ``CurrentAccessToken`` is importable from ``fastmcp.dependencies``.
"""

from __future__ import annotations

from starlette.types import ASGIApp

# Memoized singletons. ``FastMCP.http_app()`` must be called exactly once per
# instance (the session manager is bound to the returned ASGI app), and both
# the mount in ``app.main`` and the combined lifespan need the SAME app or the
# streamable-HTTP transport never boots. Building either more than once leaks
# transports and breaks lifespan pairing, hence module-level memoization.
_mcp_instance = None
_mcp_app: ASGIApp | None = None


def get_mcp_instance():
    """Return the process-wide FastMCP instance (built once, memoized)."""
    global _mcp_instance
    if _mcp_instance is None:
        from fastmcp import FastMCP

        from app.mcp.auth_verifier import FitWrightTokenVerifier

        _mcp_instance = FastMCP(
            "FitWright",
            instructions=(
                "Tools for the user's FitWright account: resume management, "
                "job-application tracking, reminders, cover letters, and interview "
                "prep. All data belongs to the authenticated token owner."
            ),
            auth=FitWrightTokenVerifier(),
        )

        from app.mcp import tools  # noqa: F401  (registers tools via import)

    return _mcp_instance


def build_mcp_app() -> ASGIApp:
    """Build the mounted MCP ASGI app (memoized). Raises if MCP is disabled."""
    global _mcp_app
    from app.config import settings

    if not settings.mcp_enabled:
        raise RuntimeError("MCP_ENABLED is false; build_mcp_app must not be mounted")

    if _mcp_app is None:
        # stateless_http: no Mcp-Session-Id sessions - each POST is a complete
        # JSON-RPC round-trip (user spec: "streamable HTTP, sessionless").
        # json_response: plain application/json bodies, not SSE event streams,
        # so the test (and curl) can read tools/list with .json().
        _mcp_app = get_mcp_instance().http_app(
            path="/", stateless_http=True, json_response=True
        )

    return _mcp_app

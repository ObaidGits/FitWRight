"""Async job-search tools - start a search, poll its progress.

Thin wrappers over the REST handlers themselves (``start_manual_search`` /
``manual_search_progress`` in ``app/routers/discovery.py``), not a copy of
their logic. Calling the handler functions directly is what keeps the two
surfaces from drifting: the order of the guards (10s rate cooldown ->
one-search-at-a-time -> daily cap) and the already-running-no-charge rule are
the handler's own, so an MCP call and a REST call behave identically.

The REST handlers signal refusal with HTTP errors (429 cooldown, 429 daily
cap). MCP has no status codes, so those travel as one-line ``ValueError``
messages with stable machine codes - the same contract as the AI tools.
"""

from __future__ import annotations

from fastapi import HTTPException
from fastmcp.dependencies import CurrentAccessToken
from fastmcp.server.auth import AccessToken

from app.errors import ApiError
from app.mcp.server import get_mcp_instance
from app.mcp.tools._context import current_user_id, db_fail_closed
from app.mcp.tools.ai import _tool_error

mcp = get_mcp_instance()


def _require_job_discovery_enabled() -> None:
    """The JOB_DISCOVERY kill-switch (Req 10.1/10.2), for the tool surface.

    On REST this gate is a router-level dependency
    (``require_job_discovery_enabled``): every discovery route 404s when the
    feature is off. The MCP tools call the handlers directly, bypassing the
    router, so the same setting is checked here - without it, a deployment
    with the feature disabled would still run real board scrapes through MCP.
    """
    from app.config import settings

    if not settings.JOB_DISCOVERY:
        raise ValueError(
            "job_discovery_disabled: The job-discovery feature is turned off "
            "on this deployment."
        )


@mcp.tool
@db_fail_closed
async def start_job_search(
    query: str,
    sites: list[str] | None = None,
    token: AccessToken = CurrentAccessToken(),
) -> dict:
    """Start a background job search and return immediately with a search_id.

    The scrape across job boards takes 15-35 seconds, so this tool NEVER waits
    for it: the response lands in milliseconds with
    {"search_id", "status": "running", "already_running": false} and the search
    continues in the background. Poll get_job_search_status with the search_id
    until status is done or failed.

    query is raw search terms, e.g. "Backend Engineer Python" (max 256
    characters, same bound as the REST body). sites optionally picks the
    boards (e.g. ["indeed", "linkedin", "glassdoor", "naukri"]); when omitted
    the app's configured boards are used. Only one search runs per user
    at a time - starting another while one is in flight returns that search's
    id with already_running true (and does not use up a daily search).

    Refusal codes (one-line tool errors, same rules as REST):
    ``http_429`` (10-second cooldown between searches), ``search_limit_reached``
    (daily plan ceiling, resets at midnight UTC), and
    ``job_discovery_disabled`` (the deployment turned job discovery off).
    """

    user_id = current_user_id(token)
    # Kill-switch first, mirroring the router-level gate's outermost position.
    _require_job_discovery_enabled()

    # Same bounds as the REST body (ManualSearchRequest): a real search term
    # within the shared 256-character cap (a 1MB query otherwise echoes back
    # through the error path at ~2x).
    if not (query or "").strip():
        raise ValueError(
            "invalid_argument: query must be a non-empty search term, e.g. "
            "'Backend Engineer Python'."
        )
    if len(query) > 256:
        raise ValueError(
            "invalid_argument: query must be at most 256 characters "
            f"(got {len(query)})."
        )

    # Resolved inside the body (same rule as the other tool modules): the
    # memoized FastMCP instance outlives any test's DB swap.
    from app.config import settings
    from app.database import db
    from app.routers.discovery import ManualSearchRequest, start_manual_search

    payload = ManualSearchRequest(query=query, sites=sites)
    try:
        return await start_manual_search(payload, user_id, db, settings)
    except (ApiError, HTTPException) as exc:
        raise _tool_error(exc) from None


@mcp.tool
@db_fail_closed
async def get_job_search_status(
    search_id: str, token: AccessToken = CurrentAccessToken()
) -> dict:
    """Report how a background job search is going: status (running, done,
    failed, expired), per-board progress (sites_done of sites_total, found,
    saved), and the saved count / failures once finished.

    search_id comes from start_job_search. Status "expired" means the server no
    longer knows the search (e.g. after a restart, or an id from another user):
    reload the feed and stop polling. Searches never cost credits; the daily
    plan ceiling is separate.
    """
    user_id = current_user_id(token)
    # Kill-switch first, mirroring the router-level gate's outermost position.
    _require_job_discovery_enabled()

    from app.routers.discovery import manual_search_progress

    # Ownership is enforced on read inside search_jobs.get: a foreign or
    # unknown id reads as "expired" and leaks nothing.
    return await manual_search_progress(search_id, user_id)

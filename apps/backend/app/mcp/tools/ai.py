"""AI generation tools - cover letter and interview prep, billed like REST.

Both tools call the REST endpoint functions directly (the handlers, not a
copy), wrapped in the SAME two guards the REST routes declare, in the same
order:

1. ``enforce_llm_rate_limit(user_id)`` - the plain-helper form of
   ``llm_rate_limit_dep``.
2. ``metered_ai_call(user_id, feature)`` - the billing context the
   ``ai_metered(feature)`` route dependency enters.

The feature names are the billing identity from ``app/ai_feature_prices``
(``cover_letter`` / ``interview_prep``); reusing them verbatim is what keeps
an MCP call and a REST call on one ledger row per feature instead of two.
"""

from __future__ import annotations

from fastapi import HTTPException
from fastmcp.dependencies import CurrentAccessToken
from fastmcp.server.auth import AccessToken

from app.errors import ApiError
from app.mcp.server import get_mcp_instance
from app.mcp.tools._context import current_user_id

mcp = get_mcp_instance()


def _tool_error(exc: Exception) -> ValueError:
    """A REST-layer error as an actionable one-line tool error.

    The REST endpoints signal refusal with HTTP errors: 402
    ``insufficient_credits`` before any work, 429 ``rate_limited``, 404 for
    an unknown resume. MCP has no status codes, so the stable
    machine-readable code travels in the message - the same contract
    ``ValueError`` already has in the read/write tools.
    """
    if isinstance(exc, ApiError):
        return ValueError(f"{exc.code}: {exc.message}")
    if isinstance(exc, HTTPException):
        return ValueError(f"http_{exc.status_code}: {exc.detail}")
    return ValueError(f"generation_failed: {exc}")


@mcp.tool
async def generate_cover_letter(
    resume_id: str,
    regenerate: bool = False,
    token: AccessToken = CurrentAccessToken(),
) -> dict:
    """Generate a cover letter for a job-tailored resume (charges credits).

    resume_id must be a TAILORED resume (one produced by tailoring to a job
    description) - get one from list_resumes. A previously generated cover
    letter is returned as-is unless regenerate is true (no extra charge on
    reuse, same as the app).
    """
    user_id = current_user_id(token)
    from app.ai_metered import metered_ai_call
    from app.llm_ratelimit import enforce_llm_rate_limit
    from app.routers.resumes import generate_cover_letter_endpoint

    try:
        # Same guards as the REST route, same order: rate limit, then billing.
        await enforce_llm_rate_limit(user_id)
        async with metered_ai_call(user_id, "cover_letter"):
            response = await generate_cover_letter_endpoint(
                resume_id, regenerate, user_id
            )
    except (ApiError, HTTPException) as exc:
        raise _tool_error(exc) from None
    return response.model_dump()


@mcp.tool
async def generate_interview_prep(
    resume_id: str,
    regenerate: bool = False,
    token: AccessToken = CurrentAccessToken(),
) -> dict:
    """Generate interview preparation for a job-tailored resume (charges
    credits): role-fit analysis, likely questions with answer points, skill
    gaps to close, and talking points.

    resume_id must be a TAILORED resume - get one from list_resumes.
    Previously generated prep is returned as-is unless regenerate is true.
    """
    user_id = current_user_id(token)
    from app.ai_metered import metered_ai_call
    from app.llm_ratelimit import enforce_llm_rate_limit
    from app.routers.resumes import generate_interview_prep_endpoint

    try:
        # Same guards as the REST route, same order: rate limit, then billing.
        await enforce_llm_rate_limit(user_id)
        async with metered_ai_call(user_id, "interview_prep"):
            response = await generate_interview_prep_endpoint(
                resume_id, regenerate, user_id
            )
    except (ApiError, HTTPException) as exc:
        raise _tool_error(exc) from None
    return response.model_dump()

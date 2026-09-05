"""AI generation tools - cover letter and interview prep, billed like REST.

Both tools call the REST endpoint functions directly (the handlers, not a
copy), wrapped in the SAME guards the REST routes declare, in the same order:

1. The caller is published on the request-scoped user-id ContextVar (on REST
   this is what ``get_effective_user_id`` does) so ``get_llm_config``
   resolves the CALLER's provider key - an own-key user must never run on
   the operator's key (R10.6).
2. ``enforce_llm_rate_limit(user_id)`` - the plain-helper form of
   ``llm_rate_limit_dep``.
3. ``metered_ai_call(user_id, feature)`` - the billing context the
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


def _tool_error(exc: ApiError | HTTPException) -> ValueError:
    """A REST-layer error as an actionable one-line tool error.

    The REST endpoints signal refusal with HTTP errors: 402
    ``insufficient_credits`` before any work, 429 ``rate_limited``, 404 for
    an unknown resume. MCP has no status codes, so the stable
    machine-readable code travels in the message - the same contract
    ``ValueError`` already has in the read/write tools.
    """
    if isinstance(exc, ApiError):
        return ValueError(f"{exc.code}: {exc.message}")
    return ValueError(f"http_{exc.status_code}: {exc.detail}")


async def _billed_generation(
    user_id: str, feature: str, handler, *handler_args
):
    """Run one REST AI handler under the exact guards its route declares.

    Mirrors what FastAPI does for the REST route, in the route's order:

    1. Publish the caller on the request-scoped user-id ContextVar - on REST
       this is ``get_effective_user_id``, and without it ``get_llm_config``
       cannot know whose provider key to resolve, so an own-key user's call
       would silently run on the operator's key (R10.6).
    2. ``enforce_llm_rate_limit(user_id)`` - the plain-helper form of the
       ``llm_rate_limit_dep`` route dependency.
    3. ``metered_ai_call(user_id, feature)`` - the billing context the
       ``ai_metered(feature)`` route dependency enters.

    ``feature`` is the billing identity from ``app/ai_feature_prices``;
    reusing the REST name verbatim keeps an MCP call and a REST call on one
    ledger row per feature instead of two.
    """
    from app.ai_metered import metered_ai_call
    from app.auth.context import reset_current_user_id, set_current_user_id
    from app.llm_ratelimit import enforce_llm_rate_limit

    context_token = set_current_user_id(user_id)
    try:
        await enforce_llm_rate_limit(user_id)
        async with metered_ai_call(user_id, feature):
            return await handler(*handler_args)
    finally:
        reset_current_user_id(context_token)


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
    from app.routers.resumes import generate_cover_letter_endpoint

    try:
        response = await _billed_generation(
            user_id, "cover_letter", generate_cover_letter_endpoint,
            resume_id, regenerate, user_id,
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
    from app.routers.resumes import generate_interview_prep_endpoint

    try:
        response = await _billed_generation(
            user_id, "interview_prep", generate_interview_prep_endpoint,
            resume_id, regenerate, user_id,
        )
    except (ApiError, HTTPException) as exc:
        raise _tool_error(exc) from None
    return response.model_dump()

"""Resume read tools - thin wrappers over db service calls.

Registration pattern: each tool module grabs the memoized FastMCP instance at
import time (``mcp = get_mcp_instance()``). ``app.mcp.server.get_mcp_instance``
imports ``app.mcp.tools`` *after* the instance exists, so this never cycles.
"""

from __future__ import annotations

from fastmcp.dependencies import CurrentAccessToken
from fastmcp.server.auth import AccessToken

from app.mcp.server import get_mcp_instance
from app.mcp.tools._context import (
    DEFAULT_LIST_LIMIT,
    current_user_id,
    display_value,
    validated_limit,
)

mcp = get_mcp_instance()


@mcp.tool
async def list_resumes(
    limit: int = DEFAULT_LIST_LIMIT, token: AccessToken = CurrentAccessToken()
) -> dict:
    """List the user's resumes (job-tailored variants; the master resume is
    excluded) with id, filename, title, ATS score, processing status, and
    updated date, newest-first.

    limit (default 50, max 200) caps how many summaries come back - the
    newest-updated resumes first, so a small limit never hides recent work.

    Returns a lightweight summary per resume - never the resume content itself.
    Use get_resume with one of the returned resume_id values to read a resume.
    """
    user_id = current_user_id(token)
    bounded = validated_limit(limit)
    from app.database import db

    summaries = await db.list_resume_summaries(user_id, limit=bounded)
    return {"resumes": summaries}


@mcp.tool
async def get_resume(resume_id: str, token: AccessToken = CurrentAccessToken()) -> dict:
    """Get one resume's full content by id, including its parsed data.

    resume_id must come from list_resumes. The response includes the resume
    text (content), the parsed resume data (processed_data), and any generated
    cover letter / outreach message / interview prep. The pre-tailor markdown
    is NOT included: it is a near-duplicate of content and doubles the payload
    for no new information.
    """
    user_id = current_user_id(token)
    from app.database import db

    resume = await db.get_resume(user_id, resume_id)
    if resume is None:
        raise ValueError(
            f"resume_not_found: {display_value(resume_id)}. "
            "Call list_resumes to get valid resume ids."
        )
    # Deferred T5 finding: the pre-tailor markdown is ~a second copy of the
    # resume text; content already carries the current text.
    resume.pop("original_markdown", None)
    return resume

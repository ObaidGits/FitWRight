"""Resume read tools - thin wrappers over db service calls.

Registration pattern: each tool module grabs the memoized FastMCP instance at
import time (``mcp = get_mcp_instance()``). ``app.mcp.server.get_mcp_instance``
imports ``app.mcp.tools`` *after* the instance exists, so this never cycles.
"""

from __future__ import annotations

from fastmcp.dependencies import CurrentAccessToken
from fastmcp.server.auth import AccessToken

from app.mcp.server import get_mcp_instance
from app.mcp.tools._context import current_user_id, display_value

mcp = get_mcp_instance()


@mcp.tool
async def list_resumes(token: AccessToken = CurrentAccessToken()) -> dict:
    """List the user's resumes (job-tailored variants; the master resume is
    excluded) with id, filename, title, ATS score, processing status, and
    updated date.

    Returns a lightweight summary per resume - never the resume content itself.
    Use get_resume with one of the returned resume_id values to read a resume.
    """
    user_id = current_user_id(token)
    from app.database import db

    summaries = await db.list_resume_summaries(user_id)
    return {"resumes": summaries}


@mcp.tool
async def get_resume(resume_id: str, token: AccessToken = CurrentAccessToken()) -> dict:
    """Get one resume's full content by id, including its parsed data.

    resume_id must come from list_resumes. The response includes the resume
    text (content), the parsed resume data (processed_data), and any generated
    cover letter / outreach message / interview prep.
    """
    user_id = current_user_id(token)
    from app.database import db

    resume = await db.get_resume(user_id, resume_id)
    if resume is None:
        raise ValueError(
            f"resume_not_found: {display_value(resume_id)}. "
            "Call list_resumes to get valid resume ids."
        )
    return resume

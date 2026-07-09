"""Health check and status endpoints."""

import logging

from fastapi import APIRouter, Request

from app.auth import get_optional_principal
from app.config import settings
from app.database import db
from app.llm import check_llm_health, get_llm_config
from app.schemas import HealthResponse, StatusResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Health"])

# Returned for database_stats when the stats query itself fails, so /status can
# still respond (degraded) instead of 500-ing.
_EMPTY_DB_STATS = {
    "total_resumes": 0,
    "total_jobs": 0,
    "total_improvements": 0,
    "has_master_resume": False,
}


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Lightweight liveness check for Docker HEALTHCHECK.

    Does NOT call the LLM provider. Use GET /status for full LLM health.
    """
    return HealthResponse(status="healthy")


async def _resolve_status_user_id(request: Request) -> str | None:
    """Best-effort effective user id for the (public) status endpoint.

    Uses the authenticated principal when present, the bootstrap owner in
    single-user/local mode, and otherwise ``None`` (anonymous hosted request —
    the DB stats are simply reported empty rather than 401-ing the status page).
    """
    principal = get_optional_principal(request)
    if principal is not None:
        return principal.user_id
    if settings.single_user_mode:
        from app.auth.owner import ensure_owner

        return await ensure_owner()
    return None


@router.get("/status", response_model=StatusResponse)
async def get_status(request: Request) -> StatusResponse:
    """Get comprehensive application status.

    Each subsystem check is isolated: a failure in the LLM health probe or the
    database stats query degrades only its own field instead of 500-ing the
    whole endpoint, so the status page can still report partial/degraded state.
    """
    user_id = await _resolve_status_user_id(request)

    llm_configured = False
    llm_healthy = False
    try:
        config = get_llm_config(user_id)
        # ollama / openai_compatible run without a key, matching check_llm_health.
        llm_configured = bool(config.api_key) or config.provider in ("ollama", "openai_compatible")
        llm_status = await check_llm_health(config)
        llm_healthy = bool(llm_status.get("healthy"))
    except Exception:
        logger.exception("Status: LLM health check failed")

    db_stats: dict = dict(_EMPTY_DB_STATS)
    if user_id is not None:
        try:
            db_stats = await db.get_stats(user_id)
        except Exception:
            logger.exception("Status: database stats failed")

    has_master_resume = bool(db_stats.get("has_master_resume"))

    return StatusResponse(
        status="ready" if llm_healthy and has_master_resume else "setup_required",
        llm_configured=llm_configured,
        llm_healthy=llm_healthy,
        has_master_resume=has_master_resume,
        database_stats=db_stats,
    )

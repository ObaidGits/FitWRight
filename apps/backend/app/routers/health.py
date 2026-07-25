"""Health check and status endpoints."""

import asyncio
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.auth import get_optional_principal, require_verified_user_id
from app.database import db
from app.llm import get_llm_config
from app.schemas import HealthResponse, SetupStatusResponse, StatusResponse

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
    """Lightweight **liveness** check for Docker HEALTHCHECK.

    Intentionally dependency-free: it must NOT fail on a transient DB/Redis blip
    (that would trigger container restart loops). It only proves the process is
    up and serving. Use GET /health/ready for dependency readiness and the
    authenticated config test endpoint for an explicit provider check.
    """
    return HealthResponse(status="healthy")


@router.get("/health/ready")
async def readiness_check() -> JSONResponse:
    """**Readiness** probe: verifies the backing dependencies are reachable.

    Checks the database (``SELECT 1`` on the async engine) and the KVStore
    (a round-trip probe). Returns 200 only when every dependency is healthy,
    else 503 with a per-dependency breakdown - the correct signal for a load
    balancer / orchestrator readiness gate (Render, Kubernetes) so traffic is
    not routed to an instance that cannot serve requests. Each check is isolated
    and time-bounded so the probe itself can never hang.
    """
    checks: dict[str, str] = {}
    ok = True

    # -- database ----------------------------------------------------------
    try:
        async with db.async_engine.connect() as conn:
            await asyncio.wait_for(conn.execute(text("SELECT 1")), timeout=5.0)
        checks["database"] = "ok"
    except Exception as exc:  # noqa: BLE001
        ok = False
        checks["database"] = "unavailable"
        logger.warning("Readiness: database check failed: %s", exc)

    # -- KVStore (session cache / rate-limit / locks) ----------------------
    try:
        from app.auth.runtime import get_kvstore

        kv = get_kvstore()
        probe_key = "readiness:probe"
        await asyncio.wait_for(kv.set(probe_key, "1", ttl_seconds=5), timeout=5.0)
        await asyncio.wait_for(kv.get(probe_key), timeout=5.0)
        checks["kvstore"] = "ok"
    except Exception as exc:  # noqa: BLE001
        ok = False
        checks["kvstore"] = "unavailable"
        logger.warning("Readiness: KVStore check failed: %s", exc)

    return JSONResponse(
        status_code=200 if ok else 503,
        content={"status": "ready" if ok else "not_ready", "checks": checks},
    )


async def _resolve_status_user_id(request: Request) -> str | None:
    """Best-effort effective user id for the (public) status endpoint.

    Uses the authenticated principal when present, the bootstrap owner in
    single-user/local mode, and otherwise ``None`` (anonymous hosted request -
    the DB stats are simply reported empty rather than 401-ing the status page).
    """
    principal = get_optional_principal(request)
    if principal is not None:
        return principal.user_id
    # Owner fallback (local) vs None (hosted) is decided by the composition
    # root's IdentityProvider - no direct deployment-mode read here (Phase 5).
    from app.platform import get_container

    return await get_container().identity_provider().resolve_owner_fallback()


@router.get("/setup/status", response_model=SetupStatusResponse)
async def get_setup_status(
    user_id: str = Depends(require_verified_user_id),
) -> SetupStatusResponse:
    """Return deterministic persisted onboarding facts for the current user.

    This endpoint intentionally does NOT call the AI provider. Setup completion
    means a provider is configured and a master resume exists; provider health
    is operational state, not onboarding state. Keeping the two separate avoids
    slow health probes, cache races, and transient provider outages sending an
    established user back through first-time setup.
    """
    config = get_llm_config(user_id)
    llm_configured = bool(config.api_key) or config.provider in (
        "ollama",
        "openai_compatible",
    )
    stats = await db.get_stats(user_id)
    has_master_resume = bool(stats.get("has_master_resume"))
    return SetupStatusResponse(
        complete=llm_configured and has_master_resume,
        llm_configured=llm_configured,
        has_master_resume=has_master_resume,
    )


@router.get("/status", response_model=StatusResponse)
async def get_status(request: Request) -> StatusResponse:
    """Return persisted setup status without probing the AI provider.

    This public endpoint is safe to poll: it never performs an outbound LLM
    request. Authenticated users (and the implicit owner in local single-user
    mode) receive their persisted configuration and resume facts. Anonymous
    hosted callers do not resolve an owner and therefore cannot read or use an
    owner's provider key.

    ``llm_healthy`` is always ``None`` because health was not checked. Provider
    connectivity is tested only by the explicit authenticated configuration
    test endpoint.
    """
    user_id = await _resolve_status_user_id(request)

    llm_configured = False
    db_stats: dict = dict(_EMPTY_DB_STATS)

    # Crucially, do not call get_llm_config(None): that resolver can fall back to
    # a bootstrap owner. Hosted anonymous status requests have no setup facts.
    if user_id is not None:
        try:
            config = get_llm_config(user_id)
            llm_configured = bool(config.api_key) or config.provider in (
                "ollama",
                "openai_compatible",
            )
        except Exception:
            logger.exception("Status: persisted LLM configuration lookup failed")

        try:
            db_stats = await db.get_stats(user_id)
        except Exception:
            logger.exception("Status: database stats failed")

    has_master_resume = bool(db_stats.get("has_master_resume"))
    setup_complete = llm_configured and has_master_resume

    # Deployment mode via the composition seam (never a direct settings read -
    # ARCHITECTURE §18.5 keeps the deployment axis contained). ``is_local`` is
    # the local (single-user) profile.
    from app.platform import get_container

    is_local = get_container().profile().is_local

    return StatusResponse(
        status="ready" if setup_complete else "setup_required",
        llm_configured=llm_configured,
        llm_healthy=None,
        has_master_resume=has_master_resume,
        database_stats=db_stats,
        single_user=is_local,
    )

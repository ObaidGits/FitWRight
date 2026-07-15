"""Health check and status endpoints."""

import asyncio
import hashlib
import logging
import time
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.auth import get_optional_principal
from app.config import settings
from app.database import db
from app.llm import LLMConfig, check_llm_health, get_llm_config
from app.schemas import HealthResponse, StatusResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Health"])

# --- /status LLM-health probe cache -----------------------------------------
# GET /status is PUBLIC (optional principal; anonymous callers reach it in
# hosted mode) and is polled by the client status cache. A naive implementation
# fires a live LLM provider round-trip (litellm.acompletion, up to a 30s
# timeout) on EVERY call — which (a) collapses /status throughput under
# concurrency and (b) lets any unauthenticated caller trigger outbound, billable
# provider requests on the server's key (cost/abuse vector at scale). We cache
# the probe result briefly and single-flight concurrent probes so repeated
# /status hits reuse one recent result. The client already treats LLM health as
# slow-changing (it re-checks every 30 min), so a short server TTL is safe.
# The explicit "test this key" path (routers/config.py) stays UNCACHED.
_LLM_HEALTH_TTL_SECONDS = 60.0
_llm_health_cache: dict[str, Any] = {"key": None, "result": None, "at": 0.0}
_llm_health_lock = asyncio.Lock()


def _llm_config_fingerprint(config: LLMConfig) -> str:
    """Stable cache key for an effective LLM config.

    Includes a salted hash of the API key so rotating the key (or switching
    provider/model/base) invalidates the cached health result. The raw key is
    never stored in the cache dict.
    """
    api_key = getattr(config, "api_key", "") or ""
    key_hash = hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:16]
    return "|".join(
        [
            getattr(config, "provider", "") or "",
            getattr(config, "model", "") or "",
            getattr(config, "api_base", "") or "",
            key_hash,
        ]
    )


def reset_status_llm_health_cache() -> None:
    """Clear the cached /status LLM-health probe result.

    Used by tests for isolation, and available operationally if a config change
    must take effect before the TTL expires.
    """
    _llm_health_cache.update({"key": None, "result": None, "at": 0.0})


async def _cached_llm_health(config: LLMConfig) -> dict[str, Any]:
    """Return a recent LLM health result, probing at most once per TTL window.

    Single-flighted: concurrent /status requests that miss the cache wait on one
    in-flight probe instead of each firing their own provider round-trip.
    """
    fingerprint = _llm_config_fingerprint(config)
    now = time.monotonic()
    cached = _llm_health_cache
    if (
        cached["key"] == fingerprint
        and cached["result"] is not None
        and (now - cached["at"]) < _LLM_HEALTH_TTL_SECONDS
    ):
        return cached["result"]

    async with _llm_health_lock:
        # Re-check inside the lock: another coroutine may have just refreshed it.
        now = time.monotonic()
        if (
            cached["key"] == fingerprint
            and cached["result"] is not None
            and (now - cached["at"]) < _LLM_HEALTH_TTL_SECONDS
        ):
            return cached["result"]
        result = await check_llm_health(config)
        cached["key"] = fingerprint
        cached["result"] = result
        cached["at"] = time.monotonic()
        return result

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
    up and serving. Use GET /health/ready for dependency readiness and GET
    /status for full LLM health.
    """
    return HealthResponse(status="healthy")


@router.get("/health/ready")
async def readiness_check() -> JSONResponse:
    """**Readiness** probe: verifies the backing dependencies are reachable.

    Checks the database (``SELECT 1`` on the async engine) and the KVStore
    (a round-trip probe). Returns 200 only when every dependency is healthy,
    else 503 with a per-dependency breakdown — the correct signal for a load
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
    single-user/local mode, and otherwise ``None`` (anonymous hosted request —
    the DB stats are simply reported empty rather than 401-ing the status page).
    """
    principal = get_optional_principal(request)
    if principal is not None:
        return principal.user_id
    # Owner fallback (local) vs None (hosted) is decided by the composition
    # root's IdentityProvider — no direct deployment-mode read here (Phase 5).
    from app.platform import get_container

    return await get_container().identity_provider().resolve_owner_fallback()


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
        llm_status = await _cached_llm_health(config)
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

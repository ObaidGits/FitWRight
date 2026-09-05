"""FastAPI application entry point."""

import asyncio
import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.gzip import GZipMiddleware

# Fix for Windows: Use ProactorEventLoop for subprocess support (Playwright)
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

logger = logging.getLogger(__name__)
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.auth import AuthMiddleware, SecurityHeadersMiddleware, auth_csrf_router
from app.config import settings
from app.observability import RequestContextMiddleware, configure_json_logging
from app.database import db
from app.pdf import close_pdf_renderer, init_pdf_renderer
from app.errors import error_envelope, install_error_handlers
from app.routers import (
    admin_router,
    admin_ai_router,
    credits_router,
    purchases_router,
    agenda_router,
    applications_router,
    auth_router,
    config_router,
    contact_router,
    error_reports_router,
    reviews_router,
    enrichment_router,
    health_router,
    internal_router,
    interviews_ics_router,
    interviews_router,
    jd_router,
    jobs_router,
    media_router,
    notifications_router,
    profile_router,
    public_pricing_router,
    public_profile_router,
    reminders_router,
    resume_wizard_router,
    resumes_router,
    search_router,
    users_router,
    versions_router,
    discovery_router,
    application_fields_router,
    extension_router,
    mcp_tokens_router,
)


def _configure_application_logging() -> None:
    """Set application log level from configuration."""
    numeric_level = getattr(logging, settings.log_level, logging.INFO)
    logging.getLogger("app").setLevel(numeric_level)


_configure_application_logging()


# Substring -> plain-language cause, checked in order against str(exc). Startup
# DB failures (migration connect, or the app's own engine) otherwise surface as
# whatever asyncpg/psycopg/SQLAlchemy raised: correct, but 15+ internal frames
# with the actual cause buried in one clause of one line. This does not replace
# that traceback (still printed) - it adds one line naming the likely cause and
# the setting to check, so a missing/wrong DB doesn't require reading a driver's
# source to diagnose.
_DB_CONNECT_ERROR_HINTS: tuple[tuple[str, str], ...] = (
    ("password authentication failed", "DATABASE_URL has the wrong username/password"),
    ("does not exist", "the database/role in DATABASE_URL has not been created yet"),
    ("could not translate host name", "DATABASE_URL's host is wrong or unreachable"),
    ("name or service not known", "DATABASE_URL's host is wrong or unreachable"),
    ("connection refused", "nothing is listening on DATABASE_URL's host:port "
        "(is Postgres running? is the port right?)"),
    ("timeout", "DATABASE_URL's host is unreachable (network/firewall, or a "
        "paused hosted database)"),
    ("ssl", "DATABASE_URL's SSL mode doesn't match what the server requires"),
)


def _format_db_connect_error(exc: Exception) -> str:
    """One plain-language line naming the likely cause of a DB-connect failure."""
    text = str(exc).lower()
    for needle, cause in _DB_CONNECT_ERROR_HINTS:
        if needle in text:
            return (
                f"Database connection/migration failed - likely cause: {cause}. "
                f"Check DATABASE_URL (and MIGRATION_DATABASE_URL if set) in your "
                f".env. Raw error: {exc}"
            )
    return f"Database connection/migration failed: {exc}"


@asynccontextmanager
async def _core_lifespan(app: FastAPI):
    """Application lifespan manager."""
    # Startup
    # Structured JSON logs (request_id + user_id correlation, no secrets/PII -
    # R16.1). Done in lifespan (not at import) so importing the app for tests
    # never reconfigures the root logger under pytest.
    configure_json_logging(getattr(logging, settings.log_level, logging.INFO))
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    # Explicit deployment-profile capability validation (ARCHITECTURE §5;
    # IMPLEMENTATION_PLAN Phase 1). Fail-fast before serving traffic: a profile
    # whose required capabilities are absent, or an explicit profile that
    # contradicts SINGLE_USER_MODE, must never boot into a broken state. This
    # complements (does not replace) Settings._validate_auth_surface.
    from app.platform import startup_validation

    deployment_errors = startup_validation(settings)
    if deployment_errors:
        for err in deployment_errors:
            logger.error("Deployment validation: %s", err)
        raise RuntimeError(
            "Deployment profile validation failed: " + "; ".join(deployment_errors)
        )
    # Hosted Postgres: bring the schema to head under an advisory lock before we
    # serve traffic (SQLite uses create_all and is a no-op here). Fail-fast - a
    # migration/connection failure must abort startup, never serve a broken DB.
    from app.migrations_runtime import apply_migrations_if_configured

    try:
        migration_result = await apply_migrations_if_configured()
    except Exception as exc:
        # Alembic/the driver raise their own exception type unmodified (by
        # design - see migrations_runtime's docstring). That is a raw
        # "OperationalError: connection to server ... failed: ..." wrapped in
        # 15+ frames of asyncpg/SQLAlchemy internals - the actual cause (wrong
        # host, wrong port, DB not running, bad credentials, DB doesn't exist)
        # is one substring inside it. Surface that plainly, once, before the
        # traceback still prints for anyone who needs the full chain.
        logger.error(_format_db_connect_error(exc))
        raise
    if migration_result.get("status") not in ("skipped_sqlite",):
        logger.info("Startup schema check: %s", migration_result)
    # Secret-free provider report so operators can confirm which adapters
    # resolved (and spot silent dev-safe fallbacks) from one log line.
    from app.diagnostics import log_startup_report

    log_startup_report(settings)
    # Composition root (ARCHITECTURE §2; IMPLEMENTATION_PLAN Phase 3): the single
    # assembly seam. Warm the pure (no-I/O) adapters so a construction-time
    # misconfiguration fails fast at boot rather than on the first request.
    from app.platform import get_container

    logger.info("Composition warmup: %s", get_container().warmup())
    # Import a legacy TinyDB database into SQLite if present (idempotent).
    # Fail-fast on error: starting with an empty DB would look like data loss.
    from app.scripts.migrate_tinydb_to_sqlite import migrate as migrate_tinydb

    result = await migrate_tinydb()
    if result.get("status") == "migrated":
        logger.info("Startup data migration: %s", result)
    # Fold any legacy plaintext API keys into the encrypted store (idempotent,
    # non-clobbering), then strip them from config.json. Move legacy provider /
    # model selection into its durable per-user database row as well, so local
    # installs keep their existing settings and hosted dynos never depend on disk.
    from app.config import migrate_legacy_keys, migrate_legacy_llm_config

    migrate_legacy_keys()
    migrate_legacy_llm_config()
    # Say something when the extension allowlist looks wrong. A malformed origin
    # rejects every extension request through CORS and logs nothing, which is
    # indistinguishable from a broken extension from the user's side.
    for warning in settings.extension_origin_warnings:
        logger.warning("%s", warning)
    # Give older discovery rows the grouping key that collapses duplicate
    # listings. Idempotent and bounded - only rows missing one are touched - so
    # this is a no-op on every boot after the first. Without it, deduplication
    # would only help future searches while the feed the user already has stays
    # a quarter repeats.
    try:
        filled = await db.backfill_group_fingerprints()
        if filled:
            logger.info("Backfilled group fingerprints for %d discovery rows", filled)
    except Exception:
        # A feed that shows duplicates is a worse feed, not a broken app.
        logger.exception("Group fingerprint backfill failed; duplicates may remain")
    # Single-user/local: ensure the bootstrap owner exists and claim any owned
    # rows created by ``create_all`` before scoping was threaded (idempotent,
    # zero data loss). Hosted does this via Alembic migration 0004 instead.
    if settings.single_user_mode:
        from app.auth.owner import ensure_owner

        try:
            await ensure_owner()
        except Exception:
            logger.exception("Failed to ensure bootstrap owner")
            raise
    # PDF renderer is lazily initialized on first use. Optionally warm Chromium
    # in the BACKGROUND now so the first export doesn't pay the browser
    # cold-start (~1-3s). Fire-and-forget: never blocks boot and a failure here
    # is non-fatal (the lazy path still initializes on the first real render).
    prewarm_task = None
    if getattr(settings, "pdf_prewarm_enabled", True):

        async def _prewarm_pdf() -> None:
            try:
                await init_pdf_renderer()
                logger.info("PDF renderer pre-warmed (Chromium ready)")
            except Exception as exc:  # pragma: no cover - best-effort warmup
                logger.info("PDF renderer pre-warm skipped: %s", exc)

        prewarm_task = asyncio.create_task(_prewarm_pdf())

    # One-time import of LLM_API_KEY as a (disabled) AI channel, so the operator's
    # credential stops living in two conceptual places. No-ops unless credits are
    # enabled and no channel exists yet - see app/ai_channel_import.py for why it is
    # deliberately conservative. Awaited rather than fired-and-forgotten: it is a
    # single cheap query in the common case, and a race with the first request
    # resolving a route would be confusing to debug for no benefit.
    try:
        from app.ai_channel_import import adopt_env_key_as_channel

        await adopt_env_key_as_channel()
    except Exception as exc:  # pragma: no cover - never block boot
        logger.info("AI channel import skipped: %s", exc)
    # Session reaper (ADR-15). In ``internal`` (premium) mode a background loop
    # runs the single-flighted reaper on an interval; ``external_cron`` (free
    # tier default) instead relies on POST /api/v1/internal/run-jobs, so nothing
    # is started here and local zero-config boot is unaffected.
    reaper_task = None
    admin_jobs_task = None
    if settings.scheduler_mode == "internal":
        from app.scheduler import start_admin_jobs, start_reaper

        reaper_task = start_reaper(settings.reaper_interval_seconds)
        logger.info(
            "Started internal session reaper (interval=%ss)",
            settings.reaper_interval_seconds,
        )
        # P2 Admin scheduled jobs (rollup + purge) run on the same interval in
        # premium mode; the free tier drives them via the external-cron endpoint.
        if settings.admin_enabled:
            admin_jobs_task = start_admin_jobs(settings.reaper_interval_seconds)
            logger.info("Started internal admin jobs loop")

    # Start discovery background worker if the feature is enabled
    discovery_task = None
    if settings.JOB_DISCOVERY:
        from app.job_discovery.worker import start_discovery_worker
        discovery_task = start_discovery_worker()
        logger.info("Started discovery background worker")

    yield
    # Shutdown - wrap each cleanup in try-except to ensure all resources are released
    try:
        # Cancel the reaper first so it stops touching the DB/KVStore before
        # those are torn down (clean cancellation, no task leak).
        from app.scheduler import stop_admin_jobs, stop_reaper

        await stop_admin_jobs(admin_jobs_task)
        await stop_reaper(reaper_task)
    except Exception as e:
        logger.error(f"Error stopping scheduled jobs: {e}")

    try:
        if discovery_task and not discovery_task.done():
            from app.job_discovery.worker import stop_discovery_worker
            stop_discovery_worker()
    except Exception as e:
        logger.error(f"Error stopping discovery worker: {e}")

    try:
        # Ensure the background pre-warm isn't mid-launch while we tear down.
        if prewarm_task is not None:
            prewarm_task.cancel()
            try:
                await prewarm_task
            except (asyncio.CancelledError, Exception):
                pass
        await close_pdf_renderer()
    except Exception as e:
        logger.error(f"Error closing PDF renderer: {e}")

    try:
        from app.jd.browser.pool import close_browser_pool

        await close_browser_pool()
    except Exception as e:
        logger.error(f"Error closing JD browser pool: {e}")

    try:
        # Release the KVStore first (no-op for the DB-backed adapter, whose
        # engine is owned by the database layer closed just below).
        from app.auth.runtime import close_kvstore

        await close_kvstore()
    except Exception as e:
        logger.error(f"Error closing KVStore: {e}")

    try:
        await db.close()
    except Exception as e:
        logger.error(f"Error closing database: {e}")


def _final_lifespan():
    """Core lifespan, combined with the FastMCP session-manager lifespan when
    the MCP mount is enabled.

    The FastMCP streamable-HTTP transport only boots inside its own lifespan
    (it starts/stops the session manager). Without merging it into ours the
    mount would accept connections whose transport never started. Both the
    mount below and this lifespan share the memoized FastMCP instance/ASGI app
    from ``app.mcp.server`` - building either twice is not supported.
    """
    if settings.mcp_enabled:
        from fastmcp.utilities.lifespan import combine_lifespans

        from app.mcp.server import build_mcp_app

        return combine_lifespans(_core_lifespan, build_mcp_app().lifespan)
    return _core_lifespan


app = FastAPI(
    title="FitWright API",
    description="AI-powered resume tailoring for job descriptions",
    version=__version__,
    lifespan=_final_lifespan(),
)

# Maintenance mode is a conservative product-traffic gate, not an operator
# lockout. Keep this allowlist explicit and narrow: root/API documentation,
# liveness/readiness/status, every admin route, and only the auth endpoints an
# operator needs to establish/inspect/end a session. OAuth login paths are also
# admitted for deployments where administrators do not use passwords. OPTIONS
# remains available so browser CORS preflights do not hide the 503 response.
_MAINTENANCE_EXACT_ALLOWLIST = frozenset(
    {
        "/",
        "/openapi.json",
        "/redoc",
        "/api/v1/health",
        "/api/v1/health/ready",
        "/api/v1/status",
        "/api/v1/auth/csrf",
        "/api/v1/auth/login",
        "/api/v1/auth/logout",
        "/api/v1/auth/logout-all",
        "/api/v1/auth/session",
    }
)
_MAINTENANCE_PREFIX_ALLOWLIST = (
    "/docs/",
    "/api/v1/admin/",
    "/api/v1/auth/oauth/",
)


@app.middleware("http")
async def maintenance_gate(request: Request, call_next):
    """Return the normal API envelope for blocked product traffic; never mutate DB."""
    path = request.url.path.rstrip("/") or "/"
    allowed = (
        request.method == "OPTIONS"
        or path in _MAINTENANCE_EXACT_ALLOWLIST
        or any(
            path == prefix.rstrip("/") or path.startswith(prefix)
            for prefix in _MAINTENANCE_PREFIX_ALLOWLIST
        )
    )
    if settings.maintenance_mode and not allowed:
        return JSONResponse(
            status_code=503,
            content=error_envelope(
                "maintenance_mode",
                "The service is temporarily unavailable for maintenance.",
            ),
            headers={"Retry-After": "60"},
        )
    return await call_next(request)

# Auth + security middleware (P1 Multi-User Foundation).
#
# Order matters: Starlette runs the LAST-added middleware OUTERMOST. From the
# outside in we want: security headers (so even an inner rejection carries them)
# -> request-context/observability (mints the request_id before auth logs/audits
# fire, and reads the resolved principal *after* call_next for the access log +
# metrics) -> auth middleware -> CORS innermost. The auth middleware only performs
# a DB session lookup when a session cookie is present, and per-session CSRF
# enforcement is gated on SINGLE_USER_MODE, so local zero-config boot and the
# existing unauthenticated routes are unaffected.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.effective_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# Response compression (audit B5). The tailor JSON responses are large
# (resume_preview + markdownOriginal + markdownImproved + diff), and gzip cuts
# the transferred bytes substantially over the Heroku router hop. Starlette's
# GZipMiddleware auto-excludes ``text/event-stream``, so the SSE tailor stream
# (and every other stream) is never buffered/compressed. ``minimum_size`` avoids
# spending CPU on tiny bodies. Added here so it sits just outside CORS and
# compresses the route/body output before the outer auth/observability layers.
app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(AuthMiddleware, config=settings)
app.add_middleware(RequestContextMiddleware)
app.add_middleware(SecurityHeadersMiddleware, config=settings)

# Admin API latency + error-rate metrics (R12.1), scoped to /api/v1/admin. Added
# innermost-of-the-observability-stack so it wraps the admin routes directly and
# never touches the rest of the app.
from app.admin import AdminMetricsMiddleware  # noqa: E402

app.add_middleware(AdminMetricsMiddleware)

# ADR-7 error envelope for the versioned surface (opt-in via ApiError).
install_error_handlers(app)

# Include routers
app.include_router(auth_csrf_router, prefix="/api/v1")
app.include_router(auth_router, prefix="/api/v1")
app.include_router(users_router, prefix="/api/v1")
app.include_router(health_router, prefix="/api/v1")
app.include_router(internal_router, prefix="/api/v1")
app.include_router(admin_router, prefix="/api/v1")
app.include_router(admin_ai_router, prefix="/api/v1")
app.include_router(credits_router, prefix="/api/v1")
app.include_router(purchases_router, prefix="/api/v1")
app.include_router(config_router, prefix="/api/v1")
app.include_router(resumes_router, prefix="/api/v1")
app.include_router(versions_router, prefix="/api/v1")
app.include_router(jobs_router, prefix="/api/v1")
app.include_router(error_reports_router, prefix="/api/v1")
app.include_router(contact_router, prefix="/api/v1")
app.include_router(reviews_router, prefix="/api/v1")
app.include_router(enrichment_router, prefix="/api/v1")
app.include_router(applications_router, prefix="/api/v1")
app.include_router(notifications_router, prefix="/api/v1")
app.include_router(search_router, prefix="/api/v1")
app.include_router(reminders_router, prefix="/api/v1")
app.include_router(interviews_router, prefix="/api/v1")
app.include_router(interviews_ics_router, prefix="/api/v1")
app.include_router(agenda_router, prefix="/api/v1")
app.include_router(jd_router, prefix="/api/v1")
app.include_router(media_router, prefix="/api/v1")
app.include_router(resume_wizard_router, prefix="/api/v1")
app.include_router(profile_router, prefix="/api/v1")
app.include_router(public_pricing_router, prefix="/api/v1")
app.include_router(public_profile_router, prefix="/api/v1")
app.include_router(discovery_router, prefix="/api/v1")
app.include_router(extension_router, prefix="/api/v1")
app.include_router(application_fields_router, prefix="/api/v1")
# MCP token lifecycle (browser-authenticated). Shares the MCP_ENABLED
# kill-switch with the mount below, so the two read as one feature.
app.include_router(mcp_tokens_router, prefix="/api/v1")

# MCP mount (kill-switched). The FastMCP app needs its lifespan (session
# manager) merged with ours, or the streamable-HTTP transport never boots.
# When MCP_ENABLED is false the mount simply does not exist, so the whole MCP
# surface 404s and a disabled deployment leaks nothing about it.
#
# Deliberately mounted AFTER the routers: Starlette matches routes in
# registration order, and this prefix-mount would otherwise swallow the
# browser-authenticated /api/v1/mcp/tokens REST routes above (they 404ed inside
# the FastMCP app). The specific API routes win; anything else under
# /api/v1/mcp falls through to the mount.
if settings.mcp_enabled:
    from app.mcp.server import build_mcp_app

    _mcp_app = build_mcp_app()
    app.mount("/api/v1/mcp", _mcp_app)


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "name": "FitWright API",
        "version": __version__,
        "docs": "/docs",
    }


def main():
    """Entry point for the project.scripts console script."""
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.reload,
    )


if __name__ == "__main__":
    main()

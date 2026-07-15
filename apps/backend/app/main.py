"""FastAPI application entry point."""

import asyncio
import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI

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
from app.errors import install_error_handlers
from app.routers import (
    admin_router,
    agenda_router,
    applications_router,
    auth_router,
    config_router,
    contact_router,
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
    public_profile_router,
    reminders_router,
    resume_wizard_router,
    resumes_router,
    search_router,
    users_router,
    versions_router,
)


def _configure_application_logging() -> None:
    """Set application log level from configuration."""
    numeric_level = getattr(logging, settings.log_level, logging.INFO)
    logging.getLogger("app").setLevel(numeric_level)


_configure_application_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    # Startup
    # Structured JSON logs (request_id + user_id correlation, no secrets/PII —
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
    # serve traffic (SQLite uses create_all and is a no-op here). Fail-fast — a
    # migration/connection failure must abort startup, never serve a broken DB.
    from app.migrations_runtime import apply_migrations_if_configured

    migration_result = await apply_migrations_if_configured()
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
    # non-clobbering), then strip them from config.json.
    from app.config import migrate_legacy_keys

    migrate_legacy_keys()
    # Single-user/local: ensure the bootstrap owner exists and claim any owned
    # rows created by ``create_all`` before scoping was threaded (idempotent,
    # zero data loss). Hosted does this via Alembic migration 0004 instead.
    if settings.single_user_mode:
        from app.auth.owner import ensure_owner

        try:
            await ensure_owner()
        except Exception as e:
            logger.error("Failed to ensure bootstrap owner: %s", e)
    # PDF renderer uses lazy initialization - will initialize on first use
    # await init_pdf_renderer()
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


app = FastAPI(
    title="FitWright API",
    description="AI-powered resume tailoring for job descriptions",
    version=__version__,
    lifespan=lifespan,
)

# Auth + security middleware (P1 Multi-User Foundation).
#
# Order matters: Starlette runs the LAST-added middleware OUTERMOST. From the
# outside in we want: security headers (so even an inner rejection carries them)
# → request-context/observability (mints the request_id before auth logs/audits
# fire, and reads the resolved principal *after* call_next for the access log +
# metrics) → auth middleware → CORS innermost. The auth middleware only performs
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
app.include_router(config_router, prefix="/api/v1")
app.include_router(resumes_router, prefix="/api/v1")
app.include_router(versions_router, prefix="/api/v1")
app.include_router(jobs_router, prefix="/api/v1")
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
app.include_router(public_profile_router, prefix="/api/v1")


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

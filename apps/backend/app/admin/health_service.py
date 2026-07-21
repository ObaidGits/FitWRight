"""Compose the admin health panel from signals the backend already produces (Task 6.1).

``HealthService.compose_health()`` assembles the six-tile ``AdminHealth`` payload
(``GET /admin/health``, wired in Task 6.3) - Backend, Database, KVStore/Queue,
AI provider, Storage provider, Migrations - plus secret-free release metadata
(version / build / commit / migration / env - Req 17) and the process uptime.

**Bounded (Req 21.3/21.4/21.5):** this service composes ONLY signals the backend
already emits - the readiness DB/KVStore probes, the cached ``/status`` LLM
health, the storage provider configuration, the Alembic head-vs-applied
comparison, and the release constants. It NEVER adds a new per-request infra
probe: no CPU/RAM/disk/thread/container/k8s metrics, and no live object-storage
query (object-storage usage is sampled off the request path by the storage job,
Task 12).

**Per-source isolation (Req 3.1/3.6):** every subsystem is probed under its own
``asyncio.wait_for(..., 2.0)`` timeout inside its own ``try/except``. A source
that errors or exceeds 2s degrades ONLY its own tile to ``down`` - the remaining
tiles still compose from their reachable sources. The tiles are gathered
concurrently so the whole compose is bounded by ~2s rather than the sum.

**Secret-free (Req 17.3 / Property 3):** every tile detail and release field is a
count, identifier, short status string, or presence-derived label - never a
secret, key, URL, or host. ``AdminHealth`` passes ``assert_no_forbidden_fields``.

**Bounded-context purity (Req 19.2/19.3/19.5):** this Domain_Metrics_Service
depends only on shared primitives and existing app modules (config, database
engine, KVStore, ``app.llm``, the readiness/status helpers in
``app.routers.health``, Alembic). It imports no other Domain_Metrics_Service, so
the import-graph guard (``tests/architecture/test_admin_import_graph.py``) holds.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from app import __version__
from app.admin.schemas import AdminHealth, HealthTile, ReleaseInfo
from app.config import settings

logger = logging.getLogger(__name__)

__all__ = ["HealthService", "get_health_service", "reset_health_service"]

# Per-source probe budget (Req 3.1). Each subsystem must compose within this or
# its tile degrades to ``down`` (Req 3.6); tiles run concurrently so the whole
# compose stays ~2s.
_SOURCE_TIMEOUT_SECONDS = 2.0

# KVStore round-trip probe key (mirrors the readiness_check probe; short TTL so a
# stale value never lingers). Deliberately secret-free.
_KV_PROBE_KEY = "admin:health:probe"

# Process start reference for the Backend uptime gauge (Req 3.5). Captured at
# import time - the backend has no other boot timestamp, so this module-level
# monotonic anchor is the documented uptime source. ``monotonic`` is immune to
# wall-clock adjustments, which is exactly what an uptime measure wants.
_PROCESS_START_MONOTONIC = time.monotonic()

# Location of alembic.ini relative to this file: app/admin/health_service.py ->
# parents[2] is the backend root that holds alembic.ini + the alembic/ tree.
_ALEMBIC_INI_PATH = Path(__file__).resolve().parents[2] / "alembic.ini"


def _now_iso() -> str:
    """Current UTC time as an ISO-8601 string (matches the other admin models)."""
    return datetime.now(timezone.utc).isoformat()


class HealthService:
    """Compose the six health tiles + release fields + uptime (Req 3, 17).

    Dependencies are optionally injected (tests); otherwise the process-wide app
    database engine and KVStore are resolved lazily so importing this module
    never forces DB/engine initialization.
    """

    def __init__(self, *, async_engine=None, kvstore=None) -> None:
        self._engine = async_engine
        self._kv = kvstore

    # -- lazily-resolved shared primitives ----------------------------------

    def _async_engine(self):
        if self._engine is not None:
            return self._engine
        from app.database import db

        return db.async_engine

    def _kvstore(self):
        if self._kv is not None:
            return self._kv
        from app.auth.runtime import get_kvstore

        return get_kvstore()

    # -- public API ----------------------------------------------------------

    async def compose_health(self) -> AdminHealth:
        """Compose the full :class:`AdminHealth` payload from existing signals.

        The six tiles are probed concurrently, each under its own 2s timeout and
        error boundary (Req 3.6). The Migrations probe additionally yields the
        applied/head revision identifiers, which flow into ``ReleaseInfo`` (Req
        17.2). ``jobs`` is intentionally empty here - the jobs table is populated
        from KV run markers by a later task (6.2), a separate concern.
        """
        (
            backend_tile,
            database_tile,
            kvstore_tile,
            ai_tile,
            storage_tile,
            migrations_result,
        ) = await asyncio.gather(
            self._compose_backend(),
            self._compose_database(),
            self._compose_kvstore(),
            self._compose_ai(),
            self._compose_storage(),
            self._compose_migrations(),
        )
        migrations_tile, applied_rev, head_rev = migrations_result

        # EXACTLY six tiles, in the documented order (Req 3.2).
        tiles = [
            backend_tile,
            database_tile,
            kvstore_tile,
            ai_tile,
            storage_tile,
            migrations_tile,
        ]

        return AdminHealth(
            tiles=tiles,
            release=self._release_info(applied_rev, head_rev),
            backendUptimeSeconds=self._uptime_seconds(),
            jobs=[],  # populated from KV run markers by Task 6.2 (separate concern)
            computedAt=_now_iso(),
            stale=False,
        )

    # -- release / uptime ----------------------------------------------------

    @staticmethod
    def _uptime_seconds() -> int:
        """Whole seconds since process start (never negative)."""
        return max(0, int(time.monotonic() - _PROCESS_START_MONOTONIC))

    @staticmethod
    def _release_info(applied_rev: str | None, head_rev: str | None) -> ReleaseInfo:
        """Assemble the secret-free release metadata (Req 17.1/17.2/17.3).

        ``version`` is the app's own constant; ``env`` is the resolved deployment
        profile name; ``build``/``commit`` are read from optional deploy env vars
        (``APP_BUILD`` / ``GIT_COMMIT`` | ``GIT_SHA``) and are ``None`` when the
        deployment does not inject them. All values are plain identifiers.
        """
        build = os.environ.get("APP_BUILD") or None
        commit = os.environ.get("GIT_COMMIT") or os.environ.get("GIT_SHA") or None
        try:
            # Read the deployment profile through the composition root's seam
            # rather than the settings axis directly, keeping deployment-mode
            # reads contained (ARCHITECTURE §18.5 / test_profile_containment).
            from app.platform import get_container

            env = get_container().profile().value
        except Exception:  # pragma: no cover - defensive; never fail release info
            env = "unknown"
        return ReleaseInfo(
            version=__version__,
            build=build,
            commit=commit,
            migrationApplied=applied_rev,
            migrationHead=head_rev,
            env=env,
        )

    # -- tile: Backend -------------------------------------------------------

    async def _compose_backend(self) -> HealthTile:
        """Liveness: the process is serving (it is running to answer this).

        ``ok`` whenever we can compute the basic uptime figure; ``down`` only if
        even that fails (which would indicate a broken process). Uptime + version
        are surfaced on the dedicated ``AdminHealth``/``ReleaseInfo`` fields; the
        detail carries a short, secret-free summary.
        """
        try:
            uptime = self._uptime_seconds()
            return HealthTile(
                name="Backend",
                status="ok",
                detail=f"serving; uptime {uptime}s; version {__version__}",
            )
        except Exception:  # pragma: no cover - basics should never fail
            logger.exception("Health: backend tile could not compute basics")
            return HealthTile(name="Backend", status="down", detail="uptime unavailable")

    # -- tile: Database ------------------------------------------------------

    async def _compose_database(self) -> HealthTile:
        """DB reachability via the readiness probe (``SELECT 1``) under 2s.

        Reuses the exact readiness_check probe shape (async engine ``SELECT 1``);
        ``ok`` on success, ``down`` on any error or timeout (Req 3.6).
        """
        try:
            await asyncio.wait_for(self._probe_database(), timeout=_SOURCE_TIMEOUT_SECONDS)
            return HealthTile(name="Database", status="ok", detail=None)
        except Exception as exc:  # noqa: BLE001 - isolate to this tile
            logger.warning("Health: database probe failed: %s", exc)
            return HealthTile(name="Database", status="down", detail="unavailable")

    async def _probe_database(self) -> None:
        engine = self._async_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

    # -- tile: KVStore / Queue ----------------------------------------------

    async def _compose_kvstore(self) -> HealthTile:
        """KVStore reachability via a set/get round-trip under 2s (Req 3.6).

        Mirrors the readiness_check KVStore probe. ``ok`` on a successful
        round-trip, ``down`` on error/timeout. (Queue/outbox backlog may
        influence a future ``degraded`` state; for now this tile reports KV
        reachability only.)
        """
        try:
            await asyncio.wait_for(self._probe_kvstore(), timeout=_SOURCE_TIMEOUT_SECONDS)
            return HealthTile(name="KVStore/Queue", status="ok", detail=None)
        except Exception as exc:  # noqa: BLE001 - isolate to this tile
            logger.warning("Health: KVStore probe failed: %s", exc)
            return HealthTile(name="KVStore/Queue", status="down", detail="unavailable")

    async def _probe_kvstore(self) -> None:
        kv = self._kvstore()
        await kv.set(_KV_PROBE_KEY, "1", ttl_seconds=5)
        await kv.get(_KV_PROBE_KEY)

    # -- tile: AI provider ---------------------------------------------------

    async def _compose_ai(self) -> HealthTile:
        """AI provider health from the CACHED ``/status`` LLM probe (Req 3.2).

        Reuses ``app.routers.health._cached_llm_health`` so this never fires a
        new billable provider round-trip - it returns the recent cached result
        (or single-flights one probe per TTL window, exactly as ``/status``).

        Mapping: configured + healthy -> ``ok``; configured + unhealthy ->
        ``degraded``; not configured -> ``degraded`` (documented: the provider is
        simply unset, not broken). Any error/timeout -> ``down`` (Req 3.6).
        """
        try:
            return await asyncio.wait_for(self._probe_ai(), timeout=_SOURCE_TIMEOUT_SECONDS)
        except Exception as exc:  # noqa: BLE001 - isolate to this tile
            logger.warning("Health: AI provider probe failed: %s", exc)
            return HealthTile(name="AI provider", status="down", detail="unavailable")

    async def _probe_ai(self) -> HealthTile:
        # Lazy imports: keep litellm / the health router off this module's import
        # path and avoid any import cycle at load time.
        from app.llm import get_llm_config
        from app.routers.health import _cached_llm_health

        # No request context here - resolve the owner's effective config (the
        # same key-resolution path ``/status`` uses for an anonymous caller).
        config = get_llm_config(None)
        configured = bool(config.api_key) or config.provider in (
            "ollama",
            "openai_compatible",
        )
        if not configured:
            return HealthTile(
                name="AI provider",
                status="degraded",
                detail="not configured",
            )
        result = await _cached_llm_health(config)
        healthy = bool(result.get("healthy"))
        if healthy:
            return HealthTile(name="AI provider", status="ok", detail=f"provider {config.provider}")
        return HealthTile(
            name="AI provider",
            status="degraded",
            detail=f"provider {config.provider} unhealthy",
        )

    # -- tile: Storage provider ---------------------------------------------

    async def _compose_storage(self) -> HealthTile:
        """Storage provider health from configuration presence - no live query.

        Per Non-Goal Req 21.5 this NEVER performs a live object-storage request;
        object-storage usage is sampled off the request path by the storage job
        (Task 12). It reports on the *active* provider's configuration only:

        - ``local``      -> ``ok`` (the local filesystem is always available).
        - ``cloudinary`` -> ``ok`` when configured, else ``degraded`` (the
          provider is selected but its credentials are incomplete, so uploads
          fall back to local - a real degraded state).
        - ``s3``         -> ``ok`` (provider selected; there is no dedicated S3
          credential-presence signal to check here - documented gap).

        Secret-free: only the provider name + a short status label are exposed.
        """
        try:
            return await asyncio.wait_for(
                self._probe_storage(), timeout=_SOURCE_TIMEOUT_SECONDS
            )
        except Exception as exc:  # noqa: BLE001 - isolate to this tile
            logger.warning("Health: storage provider probe failed: %s", exc)
            return HealthTile(name="Storage provider", status="down", detail="unavailable")

    async def _probe_storage(self) -> HealthTile:
        provider = settings.storage_provider
        if provider == "cloudinary" and not settings.cloudinary_configured:
            return HealthTile(
                name="Storage provider",
                status="degraded",
                detail="cloudinary selected but not fully configured",
            )
        return HealthTile(
            name="Storage provider",
            status="ok",
            detail=f"provider {provider}",
        )

    # -- tile: Migrations ----------------------------------------------------

    async def _compose_migrations(self) -> tuple[HealthTile, str | None, str | None]:
        """Compare the applied Alembic revision to the latest head (Req 3.3).

        Returns ``(tile, applied_revision, head_revision)`` so the revision
        identifiers can also populate ``ReleaseInfo`` (Req 17.2).

        Mapping:
        - applied == head        -> ``ok``.
        - applied != head        -> ``degraded`` (both identifiers in the detail).
          This includes the local-SQLite case where the schema is managed via
          ``create_all`` and no ``alembic_version`` row exists (applied is
          ``None``): a documented, honest "not tracked here" degraded signal.
        - source error / timeout -> ``down`` (Req 3.6), e.g. the DB is
          unreachable or the migration scripts cannot be read.
        """
        try:
            head, applied = await asyncio.wait_for(
                self._migration_revisions(), timeout=_SOURCE_TIMEOUT_SECONDS
            )
        except Exception as exc:  # noqa: BLE001 - isolate to this tile
            logger.warning("Health: migrations probe failed: %s", exc)
            return HealthTile(name="Migrations", status="down", detail="unavailable"), None, None

        if head is None:
            # Could not determine the head revision from the script directory.
            return (
                HealthTile(name="Migrations", status="down", detail="head revision unreadable"),
                applied,
                None,
            )
        if applied == head:
            return (
                HealthTile(name="Migrations", status="ok", detail=f"at {head}"),
                applied,
                head,
            )
        detail = f"applied {applied or 'none'} != head {head}"
        return HealthTile(name="Migrations", status="degraded", detail=detail), applied, head

    async def _migration_revisions(self) -> tuple[str | None, str | None]:
        """Return ``(head_revision, applied_revision)``.

        The head is read from the Alembic script directory (pure file read, run
        in a worker thread so it never blocks the event loop). The applied
        revision is read from the ``alembic_version`` table on the async engine;
        when that table is absent (local SQLite ``create_all`` schema) the applied
        revision is ``None`` - that is not an error, just "not tracked here".
        """
        head = await asyncio.to_thread(self._read_alembic_head)
        applied = await self._read_applied_revision()
        return head, applied

    @staticmethod
    def _read_alembic_head() -> str | None:
        """Read the latest head revision id from the Alembic script directory."""
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        cfg = Config(str(_ALEMBIC_INI_PATH))
        script = ScriptDirectory.from_config(cfg)
        return script.get_current_head()

    async def _read_applied_revision(self) -> str | None:
        """Read the applied revision from ``alembic_version`` (None if untracked).

        A missing ``alembic_version`` table is treated as "no applied revision"
        (``None``) rather than an error, so the local SQLite ``create_all`` path
        reports a clean degraded state instead of a spurious ``down``. A genuine
        connection failure still raises and surfaces as ``down`` via the caller.
        """
        engine = self._async_engine()
        async with engine.connect() as conn:
            has_table = await conn.run_sync(
                lambda sync_conn: _has_alembic_version_table(sync_conn)
            )
            if not has_table:
                return None
            result = await conn.execute(text("SELECT version_num FROM alembic_version"))
            row = result.first()
            return row[0] if row else None


def _has_alembic_version_table(sync_conn) -> bool:
    """Whether the ``alembic_version`` table exists on this connection."""
    from sqlalchemy import inspect as sa_inspect

    return sa_inspect(sync_conn).has_table("alembic_version")


# ---------------------------------------------------------------------------
# Process-wide instance (mirrors the other admin service accessors)
# ---------------------------------------------------------------------------

_service: HealthService | None = None


def get_health_service() -> HealthService:
    """Return the process-wide :class:`HealthService`."""
    global _service
    if _service is None:
        _service = HealthService()
    return _service


def reset_health_service() -> None:
    """Drop the cached instance (test helper)."""
    global _service
    _service = None

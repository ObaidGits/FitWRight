"""Unit tests for the admin System Health composer (Task 6.6).

Exercises :class:`app.admin.health_service.HealthService.compose_health` and its
per-source probe methods in isolation — no real DB/KVStore/provider round-trip.
Dependencies are driven two ways, matching the design's injection points:

- **DB / KVStore** are driven by *injecting fakes* (``HealthService(async_engine=,
  kvstore=)``) so the real per-source ``try/except`` + ``asyncio.wait_for``
  isolation path actually runs (Req 3.6).
- **AI / Storage / Migrations** are driven by monkeypatching the source the
  private probe reads (the cached ``/status`` LLM health + ``get_llm_config``,
  the ``settings`` storage provider, and the Alembic head-vs-applied revisions)
  so each ``ok|degraded|down`` mapping is asserted deterministically.

Covers: composition + exactly six tiles in order (Req 3.2); degraded/down
mapping incl. migration-mismatch (Req 3.3); per-source timeout isolation (Req
3.6); null-safe + secret-free release fields (Req 17.2 / 17.3, Property 3);
non-negative uptime (Req 3.5).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import app.admin.health_service as health_service_mod
from app import __version__
from app.admin.health_service import HealthService
from app.admin.schemas import HealthTile, assert_no_forbidden_fields
from app.config import settings as app_settings

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Injectable fakes for the DB / KVStore sources (exercise the real isolation
# path in ``_compose_database`` / ``_compose_kvstore``).
# ---------------------------------------------------------------------------


class _OkConn:
    """Async-context DB connection whose ``SELECT 1`` succeeds."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, *a, **k):
        return None


class _OkEngine:
    def connect(self):
        return _OkConn()


class _RaisingEngine:
    """An engine whose ``connect()`` fails — drives the Database tile to down."""

    def connect(self):
        raise RuntimeError("database unavailable")


class _OkKV:
    async def set(self, *a, **k):
        return None

    async def get(self, *a, **k):
        return "1"


class _RaisingKV:
    """A KVStore whose round-trip fails — drives the KVStore tile to down."""

    async def set(self, *a, **k):
        raise RuntimeError("kvstore unavailable")

    async def get(self, *a, **k):
        raise RuntimeError("kvstore unavailable")


# ---------------------------------------------------------------------------
# Stub helpers for the AI + Migrations probes (so a full compose_health() runs
# without a live provider round-trip or a real DB revision read).
# ---------------------------------------------------------------------------


def _stub_ai(svc: HealthService, monkeypatch, *, status: str = "ok") -> None:
    async def _probe_ai():
        return HealthTile(name="AI provider", status=status, detail="provider test")

    monkeypatch.setattr(svc, "_probe_ai", _probe_ai)


def _stub_migrations(svc: HealthService, monkeypatch, *, head, applied) -> None:
    async def _migration_revisions():
        return head, applied

    monkeypatch.setattr(svc, "_migration_revisions", _migration_revisions)


def _healthy_service(monkeypatch, *, engine=None, kv=None) -> HealthService:
    """A service whose six tiles all compose green (local storage in hermetic env)."""
    svc = HealthService(async_engine=engine or _OkEngine(), kvstore=kv or _OkKV())
    _stub_ai(svc, monkeypatch, status="ok")
    _stub_migrations(svc, monkeypatch, head="0021", applied="0021")
    return svc


_TILE_ORDER = [
    "Backend",
    "Database",
    "KVStore/Queue",
    "AI provider",
    "Storage provider",
    "Migrations",
]
_VALID_STATUSES = {"ok", "degraded", "down"}


# ===========================================================================
# Composition — exactly six tiles, in the documented order (Req 3.2)
# ===========================================================================


class TestComposition:
    async def test_exactly_six_tiles_in_order(self, monkeypatch):
        health = await _healthy_service(monkeypatch).compose_health()
        assert [t.name for t in health.tiles] == _TILE_ORDER

    async def test_every_tile_status_is_valid(self, monkeypatch):
        health = await _healthy_service(monkeypatch).compose_health()
        assert all(t.status in _VALID_STATUSES for t in health.tiles)

    async def test_shape_defaults(self, monkeypatch):
        """jobs empty, not stale, computedAt present (jobs are a later concern)."""
        health = await _healthy_service(monkeypatch).compose_health()
        assert health.jobs == []
        assert health.stale is False
        assert isinstance(health.computedAt, str) and health.computedAt


# ===========================================================================
# Tile mapping — degraded / down driven by the underlying source (Req 3.6)
# ===========================================================================


class TestDatabaseTile:
    async def test_ok_when_probe_succeeds(self):
        tile = await HealthService(async_engine=_OkEngine())._compose_database()
        assert tile.name == "Database" and tile.status == "ok"

    async def test_down_when_connect_raises(self):
        # Real isolation path: connect() error is caught and degrades ONLY this tile.
        tile = await HealthService(async_engine=_RaisingEngine())._compose_database()
        assert tile.status == "down"


class TestKvStoreTile:
    async def test_ok_when_round_trip_succeeds(self):
        tile = await HealthService(kvstore=_OkKV())._compose_kvstore()
        assert tile.name == "KVStore/Queue" and tile.status == "ok"

    async def test_down_when_round_trip_raises(self):
        tile = await HealthService(kvstore=_RaisingKV())._compose_kvstore()
        assert tile.status == "down"


class TestAiTile:
    """AI mapping via the cached /status probe (never a new billable round-trip)."""

    def _patch_llm(self, monkeypatch, *, api_key: str, provider: str, healthy: bool):
        import app.llm as llm_mod
        import app.routers.health as health_router_mod

        cfg = SimpleNamespace(api_key=api_key, provider=provider, model="m", api_base="")
        monkeypatch.setattr(llm_mod, "get_llm_config", lambda _principal: cfg)

        async def _cached(config):
            return {"healthy": healthy}

        monkeypatch.setattr(health_router_mod, "_cached_llm_health", _cached)

    async def test_configured_healthy_is_ok(self, monkeypatch):
        self._patch_llm(monkeypatch, api_key="sk-x", provider="openai", healthy=True)
        tile = await HealthService()._compose_ai()
        assert tile.status == "ok"

    async def test_configured_unhealthy_is_degraded(self, monkeypatch):
        self._patch_llm(monkeypatch, api_key="sk-x", provider="openai", healthy=False)
        tile = await HealthService()._compose_ai()
        assert tile.status == "degraded"

    async def test_not_configured_is_degraded(self, monkeypatch):
        # No api_key + a key-requiring provider → "not configured" → degraded.
        self._patch_llm(monkeypatch, api_key="", provider="openai", healthy=False)
        tile = await HealthService()._compose_ai()
        assert tile.status == "degraded"
        assert tile.detail == "not configured"


class TestStorageTile:
    async def test_local_is_ok(self, monkeypatch):
        monkeypatch.setattr(app_settings, "storage_provider", "local")
        tile = await HealthService()._compose_storage()
        assert tile.name == "Storage provider" and tile.status == "ok"

    async def test_cloudinary_unconfigured_is_degraded(self, monkeypatch):
        # hermetic env leaves cloudinary_* blank → cloudinary_configured is False.
        monkeypatch.setattr(app_settings, "storage_provider", "cloudinary")
        assert app_settings.cloudinary_configured is False
        tile = await HealthService()._compose_storage()
        assert tile.status == "degraded"


# ===========================================================================
# Migrations — head-vs-applied mapping + release flow-through (Req 3.3 / 17.2)
# ===========================================================================


class TestMigrationsTile:
    async def test_mismatch_is_degraded_with_both_ids_and_release(self, monkeypatch):
        svc = _healthy_service(monkeypatch)
        _stub_migrations(svc, monkeypatch, head="9999", applied="0001")
        health = await svc.compose_health()

        tile = next(t for t in health.tiles if t.name == "Migrations")
        assert tile.status == "degraded"
        assert "9999" in tile.detail and "0001" in tile.detail
        # The revision identifiers also populate ReleaseInfo (Req 17.2).
        assert health.release.migrationApplied == "0001"
        assert health.release.migrationHead == "9999"

    async def test_equal_is_ok(self, monkeypatch):
        svc = _healthy_service(monkeypatch)
        _stub_migrations(svc, monkeypatch, head="0021", applied="0021")
        health = await svc.compose_health()
        tile = next(t for t in health.tiles if t.name == "Migrations")
        assert tile.status == "ok"

    async def test_head_unreadable_is_down(self, monkeypatch):
        svc = _healthy_service(monkeypatch)
        _stub_migrations(svc, monkeypatch, head=None, applied="0001")
        health = await svc.compose_health()
        tile = next(t for t in health.tiles if t.name == "Migrations")
        assert tile.status == "down"


# ===========================================================================
# Per-source timeout isolation (Req 3.6)
# ===========================================================================


class TestTimeoutIsolation:
    async def test_one_slow_probe_degrades_only_its_own_tile(self, monkeypatch):
        # Shrink the per-source budget so the test stays fast.
        monkeypatch.setattr(health_service_mod, "_SOURCE_TIMEOUT_SECONDS", 0.05)

        svc = _healthy_service(monkeypatch)

        async def _slow_probe():
            await asyncio.sleep(1.0)  # far beyond the 0.05s budget

        monkeypatch.setattr(svc, "_probe_database", _slow_probe)

        health = await svc.compose_health()

        by_name = {t.name: t.status for t in health.tiles}
        # The slow source's tile is isolated to `down`...
        assert by_name["Database"] == "down"
        # ...while every OTHER tile still composed from its reachable source.
        others = [status for name, status in by_name.items() if name != "Database"]
        assert all(status != "down" for status in others)
        assert sum(1 for s in by_name.values() if s == "down") == 1


# ===========================================================================
# Release fields (null-safe + secret-free) + uptime (Req 17.2 / 17.3 / 3.5)
# ===========================================================================


class TestReleaseAndUptime:
    async def test_release_is_null_safe_when_build_commit_unset(self, monkeypatch):
        for var in ("APP_BUILD", "GIT_COMMIT", "GIT_SHA"):
            monkeypatch.delenv(var, raising=False)
        health = await _healthy_service(monkeypatch).compose_health()
        assert health.release.build is None
        assert health.release.commit is None
        # version + env are always present.
        assert health.release.version == __version__
        assert isinstance(health.release.env, str) and health.release.env

    async def test_release_reads_build_and_commit_env(self, monkeypatch):
        monkeypatch.setenv("APP_BUILD", "build-123")
        monkeypatch.setenv("GIT_COMMIT", "abc1234")
        health = await _healthy_service(monkeypatch).compose_health()
        assert health.release.build == "build-123"
        assert health.release.commit == "abc1234"

    async def test_payload_is_secret_free(self, monkeypatch):
        health = await _healthy_service(monkeypatch).compose_health()
        # The response boundary guard must not raise (Req 17.3 / 15.7, Property 3).
        assert_no_forbidden_fields(health.model_dump(by_alias=True))

    async def test_uptime_is_non_negative_int(self, monkeypatch):
        health = await _healthy_service(monkeypatch).compose_health()
        assert isinstance(health.backendUptimeSeconds, int)
        assert health.backendUptimeSeconds >= 0

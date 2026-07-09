"""Boot checks for the app lifespan under both SCHEDULER_MODE values (ADR-15).

Confirms the FastAPI ``lifespan`` starts + shuts down cleanly whether the reaper
runs as an in-process loop (``internal``) or is left to an external cron
(``external_cron``), and that the ``internal`` loop task is created on startup and
cancelled (no leak) on shutdown. The heavy startup steps (legacy migrations,
owner bootstrap) are stubbed so this exercises only the scheduler wiring against
the isolated DB.

Requirements: 14.3, 17.3, ADR-15
"""

from __future__ import annotations

import asyncio

import pytest

import app.main as main_module
from app.config import settings as app_settings

pytestmark = pytest.mark.integration


@pytest.fixture
def _quiet_startup(monkeypatch, isolated_db):
    """Stub the heavy lifespan startup work + bind the app db to the temp DB."""
    monkeypatch.setattr(main_module, "db", isolated_db)

    async def _noop_migrate():
        return {"status": "noop"}

    monkeypatch.setattr(
        "app.scripts.migrate_tinydb_to_sqlite.migrate", _noop_migrate, raising=False
    )
    monkeypatch.setattr("app.config.migrate_legacy_keys", lambda: None, raising=False)
    monkeypatch.setattr(main_module, "configure_json_logging", lambda *a, **k: None)
    yield isolated_db


async def test_lifespan_internal_mode_starts_and_cancels_reaper(monkeypatch, _quiet_startup):
    monkeypatch.setattr(app_settings, "scheduler_mode", "internal")
    monkeypatch.setattr(app_settings, "single_user_mode", True)

    created: list[asyncio.Task] = []

    # Capture the task the lifespan creates via the scheduler module.
    import app.scheduler as scheduler_module

    orig_start = scheduler_module.start_reaper

    def _tracking_start(interval):
        task = orig_start(interval)
        created.append(task)
        return task

    monkeypatch.setattr(scheduler_module, "start_reaper", _tracking_start)

    async with main_module.app.router.lifespan_context(main_module.app):
        # Startup created exactly one reaper task, still running.
        assert len(created) == 1
        assert not created[0].done()

    # Shutdown cancelled it cleanly — no leak.
    assert created[0].done()


async def test_lifespan_external_cron_mode_starts_no_reaper(monkeypatch, _quiet_startup):
    monkeypatch.setattr(app_settings, "scheduler_mode", "external_cron")
    monkeypatch.setattr(app_settings, "single_user_mode", True)

    import app.scheduler as scheduler_module

    started: list[int] = []
    orig_start = scheduler_module.start_reaper

    def _tracking_start(interval):
        started.append(interval)
        return orig_start(interval)

    monkeypatch.setattr(scheduler_module, "start_reaper", _tracking_start)

    async with main_module.app.router.lifespan_context(main_module.app):
        # No in-process reaper in external-cron mode.
        assert started == []

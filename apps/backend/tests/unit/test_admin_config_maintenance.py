"""Unit tests for Config diagnostics + Maintenance actions (Task 8.4).

Covers the two read-only/thin-dispatcher services landed in tasks 8.1–8.2 at the
pure-logic level (no DB, no HTTP), complementing the end-to-end integration
suite in ``tests/integration/test_admin_config_maintenance_api.py``:

- ``app.admin.config_diag.ConfigService`` — the read-only, secret-free config
  diagnostics assembler. Tests prove the payload is **secret-free** (passes
  ``assert_no_forbidden_fields`` and exposes secrets only as presence booleans —
  Req 10.2/15.8) and that the service exposes **no mutation** surface at all
  (Req 10.3): its only public method is ``diagnostics``.
- ``app.admin.maintenance.MaintenanceService`` — the thin, frozen dispatcher.
  Tests prove there are **exactly four** allowed actions and the map is immutable
  (structurally "no other/destructive action exists" — Req 18.5) and that
  ``run(action)`` maps each underlying job's native status to the small
  maintenance vocabulary (Req 18.3).

Requirements: 10.2, 10.3, 18.3, 18.5, 15.8.
"""

from __future__ import annotations

import inspect

import pytest

from app.admin.config_diag import ConfigService, get_config_service
from app.admin.jobs import ROLLUP_LOCK_KEY
from app.admin.maintenance import (
    ALLOWED_ACTIONS,
    MaintenanceService,
)
from app.admin.schemas import assert_no_forbidden_fields
from app.auth.kvstore.local import LocalKVStore

pytestmark = pytest.mark.unit


# ===========================================================================
# ConfigService — secret-free (Req 10.2 / 15.8)
# ===========================================================================


class TestConfigSecretFree:
    """The diagnostics payload never leaks a secret name or value (Req 10.2/15.8)."""

    def test_payload_passes_forbidden_field_guard(self):
        model = get_config_service().diagnostics()
        # The serialization safeguard must not raise on any key in the payload.
        assert_no_forbidden_fields(model.model_dump(by_alias=True))

    def test_configured_values_are_presence_booleans_only(self):
        """``configured`` surfaces each secret ONLY as a bool — never its value."""
        model = get_config_service().diagnostics()
        assert model.configured, "expected at least one presence indicator"
        for key, value in model.configured.items():
            assert value in (True, False), f"{key} is not a presence boolean: {value!r}"
            assert isinstance(value, bool)

    def test_feature_flags_and_kill_switches_are_bools(self):
        model = get_config_service().diagnostics()
        for key, value in model.featureFlags.items():
            assert isinstance(value, bool), f"featureFlag {key} not a bool"
        for key, value in model.killSwitches.items():
            assert isinstance(value, bool), f"killSwitch {key} not a bool"

    def test_no_secret_looking_value_appears_in_payload(self):
        """Spot-check: no configured/versions/featureFlags value is a secret string.

        ``configured``/``featureFlags``/``killSwitches`` values are booleans and
        ``versions`` values are plain version identifiers — none should equal a
        secret-looking string (a long opaque token).
        """
        model = get_config_service().diagnostics()
        secret_looking = "sk-supersecretvalue1234567890"
        for value in model.configured.values():
            assert value is not secret_looking
        # versions are short identifiers, never secrets
        for key, value in model.versions.items():
            assert isinstance(value, str)
            assert secret_looking not in value


# ===========================================================================
# ConfigService — no mutation surface (Req 10.3)
# ===========================================================================


class TestConfigNoMutation:
    """ConfigService exposes exactly one public method: ``diagnostics`` (Req 10.3)."""

    _MUTATION_PREFIXES = ("set_", "update_", "delete_", "create_", "write_", "save_", "toggle_")

    def _public_methods(self) -> list[str]:
        return [
            name
            for name, member in inspect.getmembers(ConfigService, predicate=inspect.isfunction)
            if not name.startswith("_")
        ]

    def test_only_public_method_is_diagnostics(self):
        assert self._public_methods() == ["diagnostics"]

    def test_no_mutation_named_method_exists(self):
        for name in dir(ConfigService):
            assert not name.startswith(self._MUTATION_PREFIXES), (
                f"ConfigService exposes a mutation-like method: {name}"
            )


# ===========================================================================
# MaintenanceService — exactly four + immutable (Req 18.5)
# ===========================================================================


class TestMaintenanceActionSet:
    """The action set is exactly four and frozen (Req 18.5)."""

    _EXPECTED = {"refresh-metrics", "run-rollup", "run-cleanup", "run-retention"}

    def test_exactly_the_four_expected_actions(self):
        assert set(MaintenanceService.ACTIONS.keys()) == self._EXPECTED
        assert ALLOWED_ACTIONS == frozenset(self._EXPECTED)

    def test_actions_map_is_immutable(self):
        """``ACTIONS`` is a MappingProxyType — assignment/deletion raises TypeError."""
        from types import MappingProxyType

        assert isinstance(MaintenanceService.ACTIONS, MappingProxyType)
        with pytest.raises(TypeError):
            MaintenanceService.ACTIONS["evil"] = "drop_tables"  # type: ignore[index]
        with pytest.raises(TypeError):
            del MaintenanceService.ACTIONS["run-rollup"]  # type: ignore[attr-defined]

    def test_no_destructive_or_sql_or_config_edit_action_exposed(self):
        """No action name/target implies arbitrary SQL, config-edit, or deletion.

        Structurally proves Req 18.5: the four actions map only to the known
        idempotent job/refresh method names, and none of the action names or
        target method names contain destructive/SQL/config-edit verbs.
        """
        forbidden_tokens = (
            "sql",
            "query",
            "exec",
            "drop",
            "truncate",
            "delete",
            "flush",
            "deploy",
            "config",
            "flag",
            "setting",
            "migrate",
            "vacuum",
        )
        for action, method_name in MaintenanceService.ACTIONS.items():
            for token in forbidden_tokens:
                assert token not in action.lower(), f"action {action!r} implies {token}"
                assert token not in method_name.lower(), (
                    f"target {method_name!r} implies {token}"
                )
        # The four targets are exactly the known idempotent job/refresh methods.
        assert set(MaintenanceService.ACTIONS.values()) == {
            "refresh_metrics",
            "run_rollup",
            "run_cleanup",
            "run_retention",
        }

    async def test_run_rejects_unknown_action(self):
        svc = MaintenanceService()
        with pytest.raises(KeyError):
            await svc.run("run-arbitrary-sql")


# ===========================================================================
# MaintenanceService — status mapping (Req 18.3)
# ===========================================================================


class TestMaintenanceStatusMapping:
    """``run(action)`` maps each job's native status to the maintenance vocabulary."""

    @pytest.mark.parametrize(
        "action,job_symbol,native,expected",
        [
            ("run-rollup", "run_rollup_job", {"status": "locked"}, "already_running"),
            ("run-rollup", "run_rollup_job", {"status": "ok"}, "started"),
            ("run-cleanup", "run_purge_job", {"status": "disabled"}, "disabled"),
            ("run-cleanup", "run_purge_job", {"status": "ok"}, "started"),
            ("run-cleanup", "run_purge_job", {"status": "locked"}, "already_running"),
            ("run-retention", "run_audit_retention_job", {"status": "ok"}, "started"),
            ("run-retention", "run_audit_retention_job", {"status": "locked"}, "already_running"),
        ],
    )
    async def test_status_maps_from_underlying_job(
        self, monkeypatch, action, job_symbol, native, expected
    ):
        import app.admin.maintenance as maint

        async def _fake_job(*args, **kwargs):
            return native

        monkeypatch.setattr(maint, job_symbol, _fake_job)
        result = await MaintenanceService().run(action)
        assert result == {"status": expected}

    async def test_refresh_metrics_started_when_lock_free(self, monkeypatch):
        """refresh-metrics acquires the rollup lock, refreshes snapshot → started."""
        import app.admin.metrics_service as metrics_service

        calls = {"n": 0}

        class _FakeMetrics:
            async def refresh_totals_snapshot(self):
                calls["n"] += 1

        monkeypatch.setattr(metrics_service, "get_metrics_service", lambda: _FakeMetrics())

        kv = LocalKVStore()
        svc = MaintenanceService(kvstore=kv)
        result = await svc.run("refresh-metrics")
        assert result == {"status": "started"}
        assert calls["n"] == 1

    async def test_refresh_metrics_already_running_when_rollup_lock_held(self, monkeypatch):
        """With the rollup lock held, refresh-metrics returns already_running."""
        import app.admin.metrics_service as metrics_service

        class _FakeMetrics:
            async def refresh_totals_snapshot(self):  # pragma: no cover - must not run
                raise AssertionError("snapshot refresh must not run while lock held")

        monkeypatch.setattr(metrics_service, "get_metrics_service", lambda: _FakeMetrics())

        kv = LocalKVStore()
        svc = MaintenanceService(kvstore=kv)
        # Hold the rollup single-flight lock, then attempt the refresh.
        held = kv.lock(ROLLUP_LOCK_KEY, ttl_seconds=60, blocking=False)
        async with held as acquired:
            assert acquired is True
            result = await svc.run("refresh-metrics")
        assert result == {"status": "already_running"}

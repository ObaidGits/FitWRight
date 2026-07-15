"""Unit tests for the Storage panel domain (Task 12.4, Req 7).

Exercises the two job-time Rollup_Steps and the request-path read model in
:mod:`app.admin.storage_metrics`:

- :class:`DbSizeSampleStep` — the ≤-hourly KV guard (Req 7.1), the ``None``
  retain-and-retry path (Req 7.6), and the marker-advance-on-success behaviour.
- :class:`StorageSnapshotStep` — writing the named ``"storage"`` snapshot with
  the storage counts + object-storage usage (Req 7.2).
- :class:`StorageMetricsService.panel` — the CRITICAL cached-only read (Req
  7.4/21.5: never a live DB-size query or object walk on the request path), the
  30-day growth estimate + insufficient-samples handling (Req 7.8), and the DB-
  size / object-storage staleness markers (Req 7.6/7.7).
- Secret-free serialization (Req 15.8).

Collaborators are injected (an in-memory fake ``MetricStore`` + a spy
``AdminRepo``) so nothing here touches the process-wide singletons or the app
DB engine. The fake store records every method it is asked for so the
cached-only invariant can be asserted structurally.

Requirements: 7.1, 7.4, 7.7, 7.8, 21.5, 15.8.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.admin import storage_metrics
from app.admin.metric_registry import DB_SIZE_BYTES
from app.admin.schemas import StoragePanel, assert_no_forbidden_fields
from app.admin.storage_metrics import (
    DbSizeSampleStep,
    StorageMetricsService,
    StorageSnapshotStep,
)
from app.config import settings

pytestmark = pytest.mark.unit

# The KV marker + snapshot names the module uses (kept in sync with the module).
_DB_SIZE_LAST_SAMPLE = "db_size_last_sample"
_STORAGE_SNAPSHOT = "storage"


# ---------------------------------------------------------------------------
# Test doubles / helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _day(offset: int = 0) -> str:
    """UTC ``YYYY-MM-DD`` string ``offset`` days before now (offset ≥ 0)."""
    return (_now() - timedelta(days=offset)).strftime("%Y-%m-%d")


def _trailing_days(days: int) -> list[str]:
    now = _now()
    n = max(0, int(days))
    return [(now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(n - 1, -1, -1)]


class _FakeStore:
    """In-memory ``MetricStore`` stand-in that records every method it serves.

    - ``series(key, days)`` zero-fills the trailing window from ``_series`` — the
      exact contract the real store provides — so the panel's non-zero "latest"
      and growth rules are exercised faithfully.
    - ``snapshot_get`` / ``snapshot_put`` share a single ``snapshots`` dict.
    - ``upsert`` records ``(day, key, value)`` tuples.

    ``calls`` is the ordered list of method names invoked — the cached-only read
    invariant (Req 7.4/21.5) asserts panel() touches ONLY ``series`` +
    ``snapshot_get`` (never ``upsert`` / ``snapshot_put``).
    """

    def __init__(self, *, series=None, snapshots=None) -> None:
        # series: {metric_key: {day: value}}
        self._series: dict[str, dict[str, int]] = series or {}
        self.snapshots: dict[str, dict] = dict(snapshots or {})
        self.calls: list[str] = []
        self.upserts: list[tuple[str, str, int]] = []

    async def series(self, key: str, days: int) -> list[tuple[str, int]]:
        self.calls.append("series")
        stored = self._series.get(key, {})
        return [(day, int(stored.get(day, 0))) for day in _trailing_days(days)]

    async def upsert(self, day: str, key: str, value: int) -> None:
        self.calls.append("upsert")
        self.upserts.append((day, key, int(value)))

    async def snapshot_get(self, name: str):
        self.calls.append("snapshot_get")
        return self.snapshots.get(name)

    async def snapshot_put(self, name: str, payload: dict, *, ttl_seconds=None) -> None:
        self.calls.append("snapshot_put")
        self.snapshots[name] = payload


class _SpyRepo:
    """A spy ``AdminRepo`` counting ``db_size_bytes`` / ``storage_counts`` calls."""

    def __init__(self, *, db_size=None, counts=None) -> None:
        self._db_size = db_size
        self._counts = counts or {}
        self.db_size_calls = 0
        self.storage_counts_calls = 0

    async def db_size_bytes(self):
        self.db_size_calls += 1
        return self._db_size

    async def storage_counts(self):
        self.storage_counts_calls += 1
        return dict(self._counts)


# ===========================================================================
# 1. DbSizeSampleStep — hourly KV guard (Req 7.1)
# ===========================================================================


class TestHourlyGuard:
    """Validates: Requirements 7.1"""

    async def test_recent_marker_is_noop_success_no_query_no_upsert(self):
        # Marker taken *now* (< the 60m default interval ago) → guard holds:
        # neither the size query nor an upsert runs, and the step still succeeds.
        recent_ts = _iso(_now())
        store = _FakeStore(snapshots={_DB_SIZE_LAST_SAMPLE: {"ts": recent_ts}})
        repo = _SpyRepo(db_size=123456)
        step = DbSizeSampleStep(metric_store=store, repo=repo)

        result = await step.run(_day())

        assert result.ok is True
        assert repo.db_size_calls == 0  # no live size query
        assert store.upserts == []  # no DB_SIZE_BYTES write
        assert "snapshot_put" not in store.calls  # marker not rewritten
        # Guard did not advance the marker (unchanged).
        assert store.snapshots[_DB_SIZE_LAST_SAMPLE]["ts"] == recent_ts

    async def test_stale_marker_samples_upserts_and_advances(self, monkeypatch):
        monkeypatch.setattr(settings, "admin_db_size_sample_minutes", 60)
        old_ts = _iso(_now() - timedelta(hours=2))  # older than the 60m interval
        store = _FakeStore(snapshots={_DB_SIZE_LAST_SAMPLE: {"ts": old_ts}})
        repo = _SpyRepo(db_size=987654)
        step = DbSizeSampleStep(metric_store=store, repo=repo)

        result = await step.run(_day())

        assert result.ok is True
        assert repo.db_size_calls == 1  # sampled once
        assert store.upserts == [(_day(), DB_SIZE_BYTES, 987654)]  # today's value
        # Marker advanced to a fresh (later) timestamp.
        new_ts = store.snapshots[_DB_SIZE_LAST_SAMPLE]["ts"]
        assert new_ts != old_ts
        assert datetime.fromisoformat(new_ts) > datetime.fromisoformat(old_ts)

    async def test_no_marker_first_run_samples(self):
        store = _FakeStore()  # no marker at all
        repo = _SpyRepo(db_size=42)
        step = DbSizeSampleStep(metric_store=store, repo=repo)

        result = await step.run(_day())

        assert result.ok is True
        assert repo.db_size_calls == 1
        assert store.upserts == [(_day(), DB_SIZE_BYTES, 42)]
        assert _DB_SIZE_LAST_SAMPLE in store.snapshots  # marker created


# ===========================================================================
# 2. DbSizeSampleStep — None retains + does NOT advance marker (Req 7.6)
# ===========================================================================


class TestDbSizeNoneRetain:
    """Validates: Requirements 7.6"""

    async def test_none_size_no_upsert_marker_unchanged(self):
        old_ts = _iso(_now() - timedelta(hours=2))  # guard passes → sampling attempted
        store = _FakeStore(snapshots={_DB_SIZE_LAST_SAMPLE: {"ts": old_ts}})
        repo = _SpyRepo(db_size=None)  # query failed / unsupported dialect
        step = DbSizeSampleStep(metric_store=store, repo=repo)

        result = await step.run(_day())

        assert result.ok is True  # no-op success (retain last recorded size)
        assert repo.db_size_calls == 1  # it *did* attempt the sample
        assert store.upserts == []  # DB_SIZE_BYTES not overwritten (Req 7.6)
        # Marker NOT advanced → next run retries rather than waiting a full cycle.
        assert store.snapshots[_DB_SIZE_LAST_SAMPLE]["ts"] == old_ts


# ===========================================================================
# 3. StorageSnapshotStep — writes the named "storage" snapshot (Req 7.2)
# ===========================================================================


class TestStorageSnapshotStep:
    """Validates: Requirements 7.2"""

    async def test_writes_counts_and_object_usage(self, monkeypatch):
        # Force a deterministic object-storage usage sample (avoid a real walk).
        monkeypatch.setattr(storage_metrics, "_object_storage_usage", lambda: (54321, False))
        store = _FakeStore()
        repo = _SpyRepo(counts={"avatarCount": 3, "resumeCount": 10, "resumeVersionCount": 20})
        step = StorageSnapshotStep(metric_store=store, repo=repo)

        result = await step.run(_day())

        assert result.ok is True
        assert repo.storage_counts_calls == 1
        payload = store.snapshots[_STORAGE_SNAPSHOT]
        assert payload["avatarCount"] == 3
        assert payload["resumeCount"] == 10
        assert payload["resumeVersionCount"] == 20
        assert payload["objectStorageBytes"] == 54321
        assert payload["objectStorageStale"] is False
        assert "sampledAt" in payload

    async def test_unavailable_object_usage_marked_stale(self, monkeypatch):
        # Remote provider / walk failure → (None, True): step still succeeds.
        monkeypatch.setattr(storage_metrics, "_object_storage_usage", lambda: (None, True))
        store = _FakeStore()
        repo = _SpyRepo(counts={"avatarCount": 1, "resumeCount": 2, "resumeVersionCount": 4})
        step = StorageSnapshotStep(metric_store=store, repo=repo)

        result = await step.run(_day())

        assert result.ok is True
        payload = store.snapshots[_STORAGE_SNAPSHOT]
        assert payload["objectStorageBytes"] is None
        assert payload["objectStorageStale"] is True


# ===========================================================================
# 4. panel() cached-only — never a live query/walk (Req 7.4 / 21.5) — CRITICAL
# ===========================================================================


class TestPanelCachedOnly:
    """Validates: Requirements 7.4, 21.5"""

    async def test_panel_reads_only_store_never_live_object_query(self, monkeypatch):
        # Trip-wire: if panel() ever walks object storage it explodes loudly.
        walked = {"n": 0}

        def _boom():
            walked["n"] += 1
            raise AssertionError("object storage walked on the request path (Req 21.5)")

        monkeypatch.setattr(storage_metrics, "_object_storage_usage", _boom)

        store = _FakeStore(
            series={DB_SIZE_BYTES: {_day(1): 1000, _day(0): 2000}},
            snapshots={
                _DB_SIZE_LAST_SAMPLE: {"ts": _iso(_now())},
                _STORAGE_SNAPSHOT: {
                    "avatarCount": 3,
                    "resumeCount": 10,
                    "resumeVersionCount": 20,
                    "objectStorageBytes": 999,
                    "objectStorageStale": False,
                    "sampledAt": _iso(_now()),
                },
            },
        )
        # The service is constructed with a store ONLY — it structurally holds no
        # repo, so it *cannot* issue a live cross-user/DB-size query.
        service = StorageMetricsService(metric_store=store)
        assert "_repo" not in vars(service)
        assert "_admin_repo" not in vars(service)

        panel = await service.panel()

        # The object-storage walk was NEVER invoked on the request path.
        assert walked["n"] == 0
        # panel() issued ONLY cached reads: bounded series + KV point reads.
        assert set(store.calls) == {"series", "snapshot_get"}
        assert "upsert" not in store.calls
        assert "snapshot_put" not in store.calls
        # And it produced a well-formed panel from those cached values.
        assert isinstance(panel, StoragePanel)
        assert panel.dbSizeBytes == 2000
        assert panel.avatarCount == 3
        assert panel.objectStorageBytes == 999


# ===========================================================================
# 5. panel() — 30-day growth + insufficient samples (Req 7.8)
# ===========================================================================


class TestGrowth:
    """Validates: Requirements 7.8"""

    async def test_two_samples_linear_slope(self):
        # day-10 = 1000, day-0 = 4000 → slope (4000-1000)/10 = 300 bytes/day.
        store = _FakeStore(
            series={DB_SIZE_BYTES: {_day(10): 1000, _day(0): 4000}},
            snapshots={_DB_SIZE_LAST_SAMPLE: {"ts": _iso(_now())}},
        )
        panel = await StorageMetricsService(metric_store=store).panel()

        assert panel.growthUnavailable is False
        assert panel.growthUnavailableReason is None
        # (last - first) / days_between = (4000 - 1000) / 10
        assert panel.growthBytesPerDay == pytest.approx(300.0)
        assert panel.dbSizeBytes == 4000  # latest non-zero sample

    async def test_single_sample_insufficient(self):
        store = _FakeStore(
            series={DB_SIZE_BYTES: {_day(0): 4000}},
            snapshots={_DB_SIZE_LAST_SAMPLE: {"ts": _iso(_now())}},
        )
        panel = await StorageMetricsService(metric_store=store).panel()

        assert panel.growthBytesPerDay is None
        assert panel.growthUnavailable is True
        assert "insufficient" in (panel.growthUnavailableReason or "")

    async def test_no_samples_insufficient(self):
        store = _FakeStore()  # empty series
        panel = await StorageMetricsService(metric_store=store).panel()

        assert panel.growthBytesPerDay is None
        assert panel.growthUnavailable is True
        assert "insufficient" in (panel.growthUnavailableReason or "")


# ===========================================================================
# 6. panel() — stale markers (Req 7.6 / 7.7)
# ===========================================================================


class TestDbSizeStaleness:
    """Validates: Requirements 7.6"""

    async def test_recent_marker_with_sample_is_fresh(self, monkeypatch):
        monkeypatch.setattr(settings, "admin_db_size_sample_minutes", 60)
        store = _FakeStore(
            series={DB_SIZE_BYTES: {_day(0): 5000}},
            snapshots={_DB_SIZE_LAST_SAMPLE: {"ts": _iso(_now())}},
        )
        panel = await StorageMetricsService(metric_store=store).panel()
        assert panel.dbSizeBytes == 5000
        assert panel.dbSizeStale is False

    async def test_marker_older_than_two_intervals_is_stale(self, monkeypatch):
        monkeypatch.setattr(settings, "admin_db_size_sample_minutes", 60)
        # 3h ago > 2×60m → stale, even though a sample exists.
        store = _FakeStore(
            series={DB_SIZE_BYTES: {_day(0): 5000}},
            snapshots={_DB_SIZE_LAST_SAMPLE: {"ts": _iso(_now() - timedelta(hours=3))}},
        )
        panel = await StorageMetricsService(metric_store=store).panel()
        assert panel.dbSizeStale is True

    async def test_missing_marker_is_stale(self):
        store = _FakeStore(series={DB_SIZE_BYTES: {_day(0): 5000}})  # no marker
        panel = await StorageMetricsService(metric_store=store).panel()
        assert panel.dbSizeStale is True

    async def test_no_sample_gives_none_and_stale(self):
        store = _FakeStore(snapshots={_DB_SIZE_LAST_SAMPLE: {"ts": _iso(_now())}})
        panel = await StorageMetricsService(metric_store=store).panel()
        assert panel.dbSizeBytes is None
        assert panel.dbSizeStale is True


class TestObjectStorageStaleness:
    """Validates: Requirements 7.7"""

    async def test_snapshot_flag_propagates_stale(self):
        store = _FakeStore(
            snapshots={
                _STORAGE_SNAPSHOT: {
                    "avatarCount": 1,
                    "resumeCount": 2,
                    "resumeVersionCount": 3,
                    "objectStorageBytes": 100,
                    "objectStorageStale": True,
                    "sampledAt": _iso(_now()),
                }
            }
        )
        panel = await StorageMetricsService(metric_store=store).panel()
        assert panel.objectStorageStale is True

    async def test_missing_snapshot_none_bytes_and_stale(self):
        store = _FakeStore()  # no "storage" snapshot
        panel = await StorageMetricsService(metric_store=store).panel()
        assert panel.objectStorageBytes is None
        assert panel.objectStorageStale is True
        # Missing snapshot → counts default to zero.
        assert panel.avatarCount == 0
        assert panel.resumeCount == 0
        assert panel.resumeVersionCount == 0

    async def test_old_sampled_at_forces_stale(self):
        # sampledAt older than the 2-day snapshot-stale threshold → stale even
        # though the snapshot's own flag says fresh.
        store = _FakeStore(
            snapshots={
                _STORAGE_SNAPSHOT: {
                    "avatarCount": 1,
                    "resumeCount": 2,
                    "resumeVersionCount": 3,
                    "objectStorageBytes": 100,
                    "objectStorageStale": False,
                    "sampledAt": _iso(_now() - timedelta(days=3)),
                }
            }
        )
        panel = await StorageMetricsService(metric_store=store).panel()
        assert panel.objectStorageStale is True


# ===========================================================================
# 7. Secret-free serialization (Req 15.8)
# ===========================================================================


class TestSecretFree:
    """Validates: Requirements 15.8"""

    async def test_panel_is_secret_free(self):
        store = _FakeStore(
            series={DB_SIZE_BYTES: {_day(5): 1000, _day(0): 3000}},
            snapshots={
                _DB_SIZE_LAST_SAMPLE: {"ts": _iso(_now())},
                _STORAGE_SNAPSHOT: {
                    "avatarCount": 3,
                    "resumeCount": 10,
                    "resumeVersionCount": 20,
                    "objectStorageBytes": 555,
                    "objectStorageStale": False,
                    "sampledAt": _iso(_now()),
                },
            },
        )
        panel = await StorageMetricsService(metric_store=store).panel()
        assert_no_forbidden_fields(panel.model_dump(by_alias=True))

"""Storage-panel domain service + its rollup steps (Req 7).

This module is the storage bounded-context's owned home: it holds the two
job-time Rollup_Steps that *populate* the storage signals (this task, 12.1) and,
in Task 12.2, the ``StorageMetricsService.panel()`` read model that assembles
them. Co-locating the steps with their owning service mirrors ``ai_metrics.py``
(which holds ``AiFlushStep`` next to ``AiMetricsService``): a step lives beside
the domain it serves, and the pipeline only imports the step *singletons*.

**Bounded-context purity (Req 19.2/19.3/19.5).** As a Domain_Metrics_Service
module this depends ONLY on shared primitives — the Metric_Store, the
Metric_Registry (``DB_SIZE_BYTES``), the ``AdminRepo``, config, and the
object-storage provider port — never on another Domain_Metrics_Service. The
import-graph fitness test (Task 5.3) enforces this.

**Off the request path (Req 21.5).** Both steps run only inside the Rollup_Job.
They read cross-user aggregates via :class:`~app.admin.repo.AdminRepo` (allowed
at rollup time) and sample object-storage usage *by the job*, never on a request
path — see :class:`StorageSnapshotStep` for the local-disk-walk / remote-gap
resolution.

Two steps, two storage destinations:

- :class:`DbSizeSampleStep` writes the ``DB_SIZE_BYTES`` **daily** metric (one
  upserted value per UTC day = the latest sample that day). The 30-day growth in
  Task 12.2 reads this daily series (``MetricStore.series(DB_SIZE_BYTES, 30)``).
- :class:`StorageSnapshotStep` writes a single small **named KV snapshot**
  (``"storage"``) holding the storage counts + the best-effort object-storage
  usage. The service (12.2) reads it via ``MetricStore.snapshot_get("storage")``.

``StepResult`` is imported **lazily** inside each ``run`` (the cycle-safe pattern
used by ``AiFlushStep``): ``rollup_pipeline`` imports this module at load time to
assemble ``PIPELINE``, so this module must not import ``rollup_pipeline`` at the
top level.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

from app.admin.metric_registry import DB_SIZE_BYTES

logger = logging.getLogger(__name__)

__all__ = [
    "DbSizeSampleStep",
    "DB_SIZE_SAMPLE_STEP",
    "StorageSnapshotStep",
    "STORAGE_SNAPSHOT_STEP",
    "StorageMetricsService",
    "get_storage_metrics_service",
    "reset_storage_metrics_service",
]

# KV marker (a named Metric_Store snapshot) recording the ISO timestamp of the
# last DB-size sample, used to enforce the "≤ once per interval" guard (Req 7.1).
_DB_SIZE_LAST_SAMPLE = "db_size_last_sample"

# The named Metric_Store snapshot that holds the storage totals (counts +
# object-storage usage). The StorageMetricsService (12.2) reads it back.
_STORAGE_SNAPSHOT = "storage"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _today() -> str:
    """Current UTC calendar day as ``YYYY-MM-DD`` (the sampled day)."""
    return _now().strftime("%Y-%m-%d")


class DbSizeSampleStep:
    """Sample the database size at most once per rolling interval (Req 7.1/7.6).

    **Hourly guard.** The step samples the DB size at most once per
    ``settings.admin_db_size_sample_minutes`` (default 60) minutes. It reads a KV
    marker (a named Metric_Store snapshot ``"db_size_last_sample"`` storing the
    last-sample ISO timestamp); if the last sample was taken less than the
    configured interval ago, the step is a **no-op success** (skips sampling).
    This keeps the potentially heavier size query off the frequent rollup path
    (it belongs to the job, not to a request).

    **On sample.** It calls :meth:`AdminRepo.db_size_bytes` (dialect-aware, Task
    5.2), which returns ``int | None``:

    - a value → ``MetricStore.upsert(today, DB_SIZE_BYTES, value)`` (the daily
      metric: one value per UTC day = the latest sample that day) and the
      last-sample marker is advanced to *now* (so the next sample waits a full
      interval).
    - ``None`` (query failed / unsupported dialect) → the step does **NOT**
      overwrite ``DB_SIZE_BYTES`` (Req 7.6: retain the last recorded size) and
      does **NOT** advance the marker, so the next run retries the sample rather
      than waiting a full interval on a failed read. The staleness itself is
      surfaced by :class:`StorageMetricsService` (12.2) when the latest daily
      sample is older than expected — the step only skips the write and logs.

    Idempotent per day (the same day's value is re-upserted, never duplicated),
    resumable (a failed/None read retries next run), and failure-isolated (any
    unexpected error is returned as a failed ``StepResult``, never raised).
    """

    name = "db_size_sample"

    def __init__(self, *, metric_store=None, repo=None) -> None:
        # Optional injected collaborators (tests); otherwise the process-wide
        # singletons are resolved lazily at run time.
        self._store = metric_store
        self._repo = repo

    def _metric_store(self):
        if self._store is not None:
            return self._store
        from app.admin.metric_store import get_metric_store

        return get_metric_store()

    def _admin_repo(self):
        if self._repo is not None:
            return self._repo
        from app.admin.repo import get_admin_repo

        return get_admin_repo()

    async def run(self, day: str) -> "StepResult":  # noqa: ARG002 - see day note below
        # Lazy import breaks the load-time cycle: ``rollup_pipeline`` imports this
        # module to assemble PIPELINE, so we must not import it at module top.
        from app.admin.rollup_pipeline import StepResult
        from app.config import settings

        try:
            store = self._metric_store()

            # -- hourly guard: skip if sampled within the configured interval --
            try:
                interval_minutes = max(0, int(settings.admin_db_size_sample_minutes))
            except (TypeError, ValueError):
                interval_minutes = 60
            marker = await store.snapshot_get(_DB_SIZE_LAST_SAMPLE)
            last_ts = (marker or {}).get("ts") if isinstance(marker, dict) else None
            if last_ts and interval_minutes > 0:
                try:
                    last = datetime.fromisoformat(last_ts)
                except (TypeError, ValueError):
                    last = None
                if last is not None:
                    if last.tzinfo is None:
                        last = last.replace(tzinfo=timezone.utc)
                    if _now() - last < timedelta(minutes=interval_minutes):
                        # Sampled recently — no-op success (guard holds, Req 7.1).
                        return StepResult.success(self.name)

            # -- sample: dialect-aware size query (Task 5.2) --------------------
            size = await self._admin_repo().db_size_bytes()
            if size is None:
                # Unsupported dialect / query failure. Retain the last recorded
                # DB_SIZE_BYTES (no overwrite) and DO NOT advance the marker so the
                # next run retries. Staleness is surfaced by the service (12.2).
                logger.warning(
                    "DbSizeSampleStep: db_size_bytes() unavailable; retaining last "
                    "recorded size and marking (service-side) stale for %s",
                    _today(),
                )
                return StepResult.success(self.name)

            # Write the sampled size as today's daily value (latest wins) and
            # advance the guard marker so the next sample waits a full interval.
            today = _today()
            await store.upsert(today, DB_SIZE_BYTES, int(size))
            await store.snapshot_put(_DB_SIZE_LAST_SAMPLE, {"ts": _now().isoformat()})
        except Exception as exc:  # observable per-step failure (R2.5)
            return StepResult.failure(self.name, exc)
        return StepResult.success(self.name)


# Process-wide instance slotted into PIPELINE by ``rollup_pipeline``. Single-
# flighted by the Rollup_Job's KVStore lock, so it is driven by one run at a time.
DB_SIZE_SAMPLE_STEP = DbSizeSampleStep()


def _object_storage_usage() -> "tuple[int | None, bool]":
    """Best-effort object-storage usage in bytes for the active provider.

    Returns ``(bytes, stale)``:

    - **LocalStorageProvider** → sum every file size under the storage root via a
      filesystem walk (bounded, acceptable at job time, off the request path):
      ``(total_bytes, False)``.
    - **Cloudinary / any remote provider** → the ``StorageProvider`` port exposes
      only ``put``/``delete`` (see ``app/storage/provider.py``); there is **no**
      usage/size API in the adapter today. Adding a live Cloudinary usage call
      would both require a new provider API and violate Req 21.5 (no live
      request-path/remote sampling), so object-storage usage is **unavailable**:
      ``(None, True)``. Documented gap.
    - Any error during detection/walk → ``(None, True)`` (never blocks the step).
    """
    try:
        from app.storage.provider import LocalStorageProvider, get_storage_provider

        provider = get_storage_provider()
        if isinstance(provider, LocalStorageProvider):
            root = provider._root  # storage root Path (Local adapter's own dir)
            total = 0
            for dirpath, _dirnames, filenames in os.walk(root):
                for filename in filenames:
                    fpath = os.path.join(dirpath, filename)
                    try:
                        total += os.path.getsize(fpath)
                    except OSError:
                        # A file vanished mid-walk / unreadable — skip it.
                        continue
            return total, False
        # Remote provider (Cloudinary/S3): no usage API → unavailable (documented).
        return None, True
    except Exception:
        logger.debug("Object-storage usage sample failed", exc_info=True)
        return None, True


class StorageSnapshotStep:
    """Snapshot storage counts + object-storage usage into the totals (Req 7.2/7.3/21.5).

    Computes the storage counts via :meth:`AdminRepo.storage_counts` (Task 5.2 →
    ``{avatarCount, resumeCount, resumeVersionCount}``) and samples object-storage
    usage **by the job** (never on a request path — Req 21.5) via
    :func:`_object_storage_usage` (local disk walk when the active provider is
    local; unavailable for remote providers — see that helper for the documented
    Cloudinary gap).

    It persists a single small dict into a **named Metric_Store snapshot**
    (``"storage"``) rather than extending the ``_TOTALS_DAY`` metrics_daily
    snapshot: ``snapshot_put`` is the shared KV primitive and a named snapshot
    keeps the storage data cohesive and lets the service (12.2) read it directly
    via ``snapshot_get("storage")``. The payload is
    ``{avatarCount, resumeCount, resumeVersionCount, objectStorageBytes|None,
    objectStorageStale, sampledAt}``.

    Failure-isolated: any error is returned as a failed ``StepResult`` (never
    raised), and a usage sample that cannot be taken never fails the step — it is
    recorded as ``objectStorageBytes=None`` + ``objectStorageStale=True``.
    """

    name = "storage_snapshot"

    def __init__(self, *, metric_store=None, repo=None) -> None:
        self._store = metric_store
        self._repo = repo

    def _metric_store(self):
        if self._store is not None:
            return self._store
        from app.admin.metric_store import get_metric_store

        return get_metric_store()

    def _admin_repo(self):
        if self._repo is not None:
            return self._repo
        from app.admin.repo import get_admin_repo

        return get_admin_repo()

    async def run(self, day: str) -> "StepResult":  # noqa: ARG002 - snapshot is "as of now"
        # Lazy import breaks the load-time cycle (see module docstring).
        from app.admin.rollup_pipeline import StepResult

        try:
            counts = await self._admin_repo().storage_counts()
            object_storage_bytes, object_storage_stale = _object_storage_usage()

            payload = {
                "avatarCount": int(counts.get("avatarCount", 0)),
                "resumeCount": int(counts.get("resumeCount", 0)),
                "resumeVersionCount": int(counts.get("resumeVersionCount", 0)),
                "objectStorageBytes": (
                    int(object_storage_bytes) if object_storage_bytes is not None else None
                ),
                "objectStorageStale": bool(object_storage_stale),
                "sampledAt": _now().isoformat(),
            }
            await self._metric_store().snapshot_put(_STORAGE_SNAPSHOT, payload)
        except Exception as exc:  # observable per-step failure (R2.5)
            return StepResult.failure(self.name, exc)
        return StepResult.success(self.name)


# Process-wide instance slotted into PIPELINE by ``rollup_pipeline`` (single-
# flighted by the Rollup_Job's KVStore lock — one run at a time).
STORAGE_SNAPSHOT_STEP = StorageSnapshotStep()


# ---------------------------------------------------------------------------
# Storage panel read model — the StorageMetricsService (Req 7.2–7.8, 21.5)
# ---------------------------------------------------------------------------


class StorageMetricsService:
    """Storage-panel read model assembled from cached/pre-aggregated values only.

    ``panel()`` builds the :class:`~app.admin.schemas.StoragePanel` served by
    ``GET /api/v1/admin/storage``. It is a cohesive, single-responsibility
    Domain_Metrics_Service that depends **only** on shared primitives — the
    shared :class:`~app.admin.metric_store.MetricStore`, the static
    :mod:`app.admin.metric_registry` (``DB_SIZE_BYTES``), config, and the
    response schema — never on another Domain_Metrics_Service (import-graph
    guard, Req 19.2/19.3/19.5).

    **NEVER a live query on the request path (Req 7.4/21.5).** Every value is
    read from what the two Rollup_Steps (Task 12.1) already persisted:

    - the ``DB_SIZE_BYTES`` **daily** metric (``MetricStore.series`` — a bounded
      30-day read) and the ``db_size_last_sample`` freshness marker
      (``snapshot_get``), both written by :class:`DbSizeSampleStep`;
    - the named ``"storage"`` snapshot (``snapshot_get``) written by
      :class:`StorageSnapshotStep`, holding the counts + object-storage usage.

    No live ``pg_database_size`` call and no object enumeration ever runs here —
    the DB-size query lives in the job's :class:`DbSizeSampleStep`, and the
    disk-walk lives in the job's :class:`StorageSnapshotStep`.

    **O(1) read (Req 7.5).** A fixed, bounded number of reads runs regardless of
    user/row count: one bounded ``series(DB_SIZE_BYTES, 30)`` (at most 30 rows by
    an indexed ``(metric, day)`` lookup) plus two KV point reads (the freshness
    marker and the ``"storage"`` snapshot). Nothing grows with data volume.

    **Degrade gracefully (Req 7.6/7.7).** Each source read is wrapped: a failed
    read surfaces the last cached value plus a stale/unavailable marker rather
    than erroring the whole panel.

    ---

    ## Exact source mapping (and every documented rule)

    ``dbSizeBytes`` / ``dbSizeStale`` (Req 7.2/7.6)
        The most-recent **non-zero** value in ``series(DB_SIZE_BYTES, 30)`` is the
        DB size. ``MetricStore.series`` zero-fills days that have no stored row,
        and a real database size is always ``> 0``, so a ``0`` day is treated as
        "no sample that day" — this is how a missing sample is distinguished from
        a stored value. If **no** non-zero sample exists at all → ``dbSizeBytes``
        is ``None`` and ``dbSizeStale`` is ``True``.

        Staleness rule (Req 7.6): the ``db_size_last_sample`` marker records the
        ISO timestamp of the last **successful** sample (it is *not* advanced on a
        failed/unsupported size query — see :class:`DbSizeSampleStep`). The panel
        marks the DB size **stale** when there is no sample, or the marker is
        missing/unparseable (freshness cannot be confirmed), or the marker is
        older than ``2 × admin_db_size_sample_minutes`` (a full missed sampling
        cycle — default ``2 × 60 = 120`` minutes). A persistently failing size
        query therefore surfaces as stale because the marker stops advancing.

    ``avatarCount`` / ``resumeCount`` / ``resumeVersionCount``,
    ``objectStorageBytes`` / ``objectStorageStale`` (Req 7.2/7.7)
        From the named ``"storage"`` snapshot. When the snapshot is **present**:
        the three counts are mapped directly (non-negative ints) and
        ``objectStorageBytes`` + ``objectStorageStale`` come from the snapshot,
        with ``objectStorageStale`` additionally forced ``True`` when the
        snapshot's ``sampledAt`` is older than the stale threshold (``> 2`` days —
        more than one missed nightly rollup). When the snapshot is **missing**
        (the rollup never ran) or its read fails → counts default to ``0``,
        ``objectStorageBytes`` is ``None``, and ``objectStorageStale`` is ``True``
        (Req 7.7).

    ``growthBytesPerDay`` / ``growthUnavailable`` / ``growthUnavailableReason``
    (Req 7.3/7.8)
        Derived from the non-zero samples in ``series(DB_SIZE_BYTES, 30)``. With
        **fewer than two** non-zero samples → ``growthBytesPerDay`` is ``None``,
        ``growthUnavailable`` is ``True``, and ``growthUnavailableReason`` is
        ``"insufficient samples"`` (Req 7.8). Otherwise the estimate is the simple
        linear slope between the first and last available sample:
        ``(last_value - first_value) / days_between``, where ``days_between`` is
        the calendar-day gap between the oldest and newest non-zero sample days in
        the rolling 30-day window. (A defensive ``days_between <= 0`` — not
        reachable for two distinct days — is also treated as unavailable.)

    ``retentionStatus`` (Req 7.3)
        A short, secret-free descriptive string built from retention config only
        (never a live query): ``"metrics retained {N}d; audit hot {M}d"`` from
        ``admin_metrics_retention_days`` and ``admin_audit_hot_days``. It surfaces
        the configured retention windows an operator would check alongside growth.

    ``computedAt``
        Current UTC time as an ISO-8601 string.
    """

    # Snapshot staleness threshold: object-storage usage is stale when its
    # ``sampledAt`` is older than this (more than one missed nightly rollup).
    _SNAPSHOT_STALE_DAYS = 2

    def __init__(self, *, metric_store=None) -> None:
        # Optional injected read collaborator (tests); otherwise the process-wide
        # MetricStore singleton is resolved lazily. Depends ONLY on the shared
        # MetricStore + Metric_Registry + config + schema — never on another
        # Domain_Metrics_Service (import-graph guard, Req 19.2/19.3/19.5).
        self._metric_store = metric_store

    def _get_metric_store(self):
        if self._metric_store is not None:
            return self._metric_store
        from app.admin.metric_store import get_metric_store

        return get_metric_store()

    async def panel(self) -> "StoragePanel":
        """Return the storage panel from cached/pre-aggregated values only (Req 7).

        Assembles the DB size + staleness, storage counts, object-storage usage +
        staleness, the 30-day growth estimate, and the retention status. See the
        class docstring for the exact source mapping and every documented rule.

        **No live query (Req 7.4/21.5), O(1) (Req 7.5), degrades on failure
        (Req 7.6/7.7).**
        """
        from app.admin.schemas import StoragePanel
        from app.config import settings

        store = self._get_metric_store()
        now = _now()

        # -- DB-size daily series (bounded 30-day read) -----------------------
        # ``series`` zero-fills missing days; a real DB size is > 0, so we keep
        # only non-zero (day, value) pairs as the actual samples.
        try:
            raw_series = await store.series(DB_SIZE_BYTES, 30)
        except Exception:
            logger.debug("Storage panel: DB_SIZE_BYTES series read failed", exc_info=True)
            raw_series = []
        samples = [(day, value) for day, value in raw_series if value > 0]

        db_size_bytes = samples[-1][1] if samples else None

        # -- DB-size staleness from the last-successful-sample marker ---------
        db_size_stale = self._db_size_stale(
            store_marker=await self._read_marker(store),
            has_sample=bool(samples),
            interval_minutes=self._interval_minutes(settings),
            now=now,
        )

        # -- 30-day growth estimate (Req 7.3/7.8) -----------------------------
        growth_bytes_per_day, growth_unavailable, growth_reason = self._growth(samples)

        # -- storage counts + object-storage usage from the "storage" snapshot
        (
            avatar_count,
            resume_count,
            resume_version_count,
            object_storage_bytes,
            object_storage_stale,
        ) = self._storage_snapshot(await self._read_storage_snapshot(store), now)

        # -- retention status: config-only descriptive string (no live query) -
        retention_status = self._retention_status(settings)

        return StoragePanel(
            dbSizeBytes=db_size_bytes,
            dbSizeStale=db_size_stale,
            objectStorageBytes=object_storage_bytes,
            objectStorageStale=object_storage_stale,
            avatarCount=avatar_count,
            resumeCount=resume_count,
            resumeVersionCount=resume_version_count,
            retentionStatus=retention_status,
            growthBytesPerDay=growth_bytes_per_day,
            growthUnavailable=growth_unavailable,
            growthUnavailableReason=growth_reason,
            computedAt=now.isoformat(),
        )

    # -- helpers -------------------------------------------------------------

    async def _read_marker(self, store) -> dict | None:
        """Read the ``db_size_last_sample`` freshness marker; None on failure."""
        try:
            return await store.snapshot_get(_DB_SIZE_LAST_SAMPLE)
        except Exception:
            logger.debug("Storage panel: db_size_last_sample read failed", exc_info=True)
            return None

    async def _read_storage_snapshot(self, store) -> dict | None:
        """Read the named ``"storage"`` snapshot; None on failure (Req 7.7)."""
        try:
            return await store.snapshot_get(_STORAGE_SNAPSHOT)
        except Exception:
            logger.debug("Storage panel: storage snapshot read failed", exc_info=True)
            return None

    @staticmethod
    def _interval_minutes(settings) -> int:
        """The configured DB-size sampling interval in minutes (default 60)."""
        try:
            return max(0, int(settings.admin_db_size_sample_minutes))
        except (TypeError, ValueError):
            return 60

    def _db_size_stale(
        self, *, store_marker: dict | None, has_sample: bool, interval_minutes: int, now: datetime
    ) -> bool:
        """Decide DB-size staleness (Req 7.6).

        Stale when there is no sample at all, the marker is missing/unparseable
        (freshness unknown), or the last successful sample is older than
        ``2 × interval`` (a full missed sampling cycle).
        """
        if not has_sample:
            return True
        last_ts = (store_marker or {}).get("ts") if isinstance(store_marker, dict) else None
        if not last_ts:
            return True
        try:
            last = datetime.fromisoformat(last_ts)
        except (TypeError, ValueError):
            return True
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        # interval_minutes == 0 (sampling disabled) → never age-out on the marker.
        if interval_minutes <= 0:
            return False
        return now - last > timedelta(minutes=2 * interval_minutes)

    @staticmethod
    def _growth(samples: list[tuple[str, int]]) -> "tuple[float | None, bool, str | None]":
        """Estimate bytes/day growth from the non-zero DB-size samples (Req 7.3/7.8).

        Returns ``(growthBytesPerDay, growthUnavailable, growthUnavailableReason)``.
        Fewer than two samples → unavailable with "insufficient samples" (Req 7.8);
        otherwise a simple linear slope between the first and last sample.
        """
        if len(samples) < 2:
            return None, True, "insufficient samples"
        first_day, first_value = samples[0]
        last_day, last_value = samples[-1]
        try:
            days_between = (
                datetime.strptime(last_day, "%Y-%m-%d")
                - datetime.strptime(first_day, "%Y-%m-%d")
            ).days
        except (TypeError, ValueError):
            return None, True, "insufficient samples"
        if days_between <= 0:
            return None, True, "insufficient samples"
        return (last_value - first_value) / days_between, False, None

    def _storage_snapshot(
        self, snapshot: dict | None, now: datetime
    ) -> "tuple[int, int, int, int | None, bool]":
        """Map the ``"storage"`` snapshot → counts + object-storage usage (Req 7.2/7.7).

        Missing snapshot → counts 0, object-storage None + stale True. Present
        snapshot → mapped values, with object-storage additionally marked stale
        when its ``sampledAt`` is older than :attr:`_SNAPSHOT_STALE_DAYS`.
        """
        if not isinstance(snapshot, dict):
            # Rollup never ran (or read failed): no cached counts/usage yet.
            return 0, 0, 0, None, True

        def _count(field: str) -> int:
            try:
                return max(0, int(snapshot.get(field, 0)))
            except (TypeError, ValueError):
                return 0

        raw_bytes = snapshot.get("objectStorageBytes")
        try:
            object_storage_bytes = int(raw_bytes) if raw_bytes is not None else None
        except (TypeError, ValueError):
            object_storage_bytes = None

        object_storage_stale = bool(snapshot.get("objectStorageStale", False))
        if self._snapshot_expired(snapshot.get("sampledAt"), now):
            object_storage_stale = True

        return (
            _count("avatarCount"),
            _count("resumeCount"),
            _count("resumeVersionCount"),
            object_storage_bytes,
            object_storage_stale,
        )

    def _snapshot_expired(self, sampled_at, now: datetime) -> bool:
        """Whether the snapshot's ``sampledAt`` is older than the stale threshold."""
        if not sampled_at:
            return True
        try:
            sampled = datetime.fromisoformat(sampled_at)
        except (TypeError, ValueError):
            return True
        if sampled.tzinfo is None:
            sampled = sampled.replace(tzinfo=timezone.utc)
        return now - sampled > timedelta(days=self._SNAPSHOT_STALE_DAYS)

    @staticmethod
    def _retention_status(settings) -> str | None:
        """Config-derived retention/cleanup status string (Req 7.3; no live query)."""
        try:
            metrics_days = int(settings.admin_metrics_retention_days)
            audit_hot_days = int(settings.admin_audit_hot_days)
        except (TypeError, ValueError, AttributeError):
            return None
        return f"metrics retained {metrics_days}d; audit hot {audit_hot_days}d"


# ---------------------------------------------------------------------------
# Process-wide singleton (mirrors app.admin.errors_metrics.get_errors_metrics_service)
# ---------------------------------------------------------------------------

_service: "StorageMetricsService | None" = None


def get_storage_metrics_service() -> StorageMetricsService:
    """Return the process-wide :class:`StorageMetricsService` (built on first use)."""
    global _service
    if _service is None:
        _service = StorageMetricsService()
    return _service


def reset_storage_metrics_service() -> None:
    """Drop the cached instance (test helper)."""
    global _service
    _service = None

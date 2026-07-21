"""Metric_Store - the single low-level access point over ``metrics_daily`` (+ KV).

This is the *only* place ``metrics_daily`` I/O and the named-snapshot KV access
live. Every Domain_Metrics_Service and every Rollup_Step reuses it, so no service
re-implements the daily-metric read/write path (design §"Why not extend
MetricsService"; Req 19.3/19.4). It holds **no domain logic** - only generic,
key-agnostic primitives:

- :meth:`upsert` - set the absolute ``value`` for one ``(day, key)`` row.
- :meth:`add` - atomically increment one ``(day, key)`` row by ``delta``.
- :meth:`sum` - sum the given keys over an inclusive UTC day range.
- :meth:`series` - per-day values for the trailing ``N`` days of one key.
- :meth:`prune_before` - delete rows older than a cutoff day (exclusions kept).
- :meth:`snapshot_get` / :meth:`snapshot_put` - read/write a named KV snapshot.

Both writers are **idempotent-safe and race-free** via a dialect-aware
``INSERT ... ON CONFLICT (day_utc, metric) DO UPDATE`` (SQLite ``3.24+`` and
Postgres share the same ON CONFLICT shape - see the DB-portability gate):

- ``upsert`` sets an absolute value, so re-running a closed-day flush never
  changes the already-written value (backs Property 1 - idempotent per closed
  day, Req 2.3/2.6).
- ``add`` performs the increment inside the UPSERT (``value = value + :delta``),
  so concurrent inline call-site increments across workers never lose a delta -
  no read-modify-write race and no per-worker rows (feature-usage counts,
  Req 16.1).

The store is constructed with the app's async ``session_factory`` (and, lazily,
the process ``KVStore``) exactly like :class:`app.admin.metrics_service.MetricsService`,
so tests can inject an isolated factory. Named snapshots live in the KVStore as
JSON blobs (the ``_TOTALS_DAY`` O(1) totals snapshot remains owned by
``MetricsService`` - this class only handles arbitrary named snapshots).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as _pg_insert
from sqlalchemy.dialects.sqlite import insert as _sqlite_insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import MetricsDaily

logger = logging.getLogger(__name__)

__all__ = [
    "MetricStore",
    "get_metric_store",
    "reset_metric_store",
]

# Named snapshots are namespaced KV keys so they never collide with the auth /
# rate-limit / job-marker keyspaces sharing the same KVStore.
_SNAPSHOT_PREFIX = "admin:snapshot:"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _trailing_days(days: int) -> list[str]:
    """Return the trailing ``days`` UTC ``YYYY-MM-DD`` strings, oldest->newest."""
    now = datetime.now(timezone.utc)
    n = max(0, int(days))
    return [(now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(n - 1, -1, -1)]


class MetricStore:
    """Thin, logic-free read/write path over ``metrics_daily`` + a KV snapshot."""

    def __init__(self, session_factory: async_sessionmaker, *, kvstore=None) -> None:
        self._session_factory = session_factory
        self._kv = kvstore

    def _kvstore(self):
        if self._kv is not None:
            return self._kv
        from app.auth.runtime import get_kvstore

        return get_kvstore()

    @staticmethod
    def _insert_for(session):
        """Return the dialect-specific ``insert`` construct for ``session``.

        SQLite and Postgres both expose ``ON CONFLICT ... DO UPDATE``; we pick the
        matching dialect builder so the single UPSERT path is portable across the
        local (SQLite) and hosted (Postgres) engines.
        """
        dialect = session.get_bind().dialect.name
        return _pg_insert if dialect == "postgresql" else _sqlite_insert

    # -- writes --------------------------------------------------------------

    async def upsert(self, day: str, key: str, value: int) -> None:
        """Set the absolute ``value`` for the ``(day, key)`` row (idempotent).

        Re-running with the same value is a no-op change; this is the closed-day
        flush primitive whose repeatability backs Property 1 (Req 2.3/2.6).
        """
        now_iso = _now_iso()
        value = int(value)
        async with self._session_factory() as session:
            insert = self._insert_for(session)
            stmt = insert(MetricsDaily).values(
                day_utc=day, metric=key, value=value, computed_at=now_iso
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["day_utc", "metric"],
                set_={"value": value, "computed_at": now_iso},
            )
            await session.execute(stmt)
            await session.commit()

    async def add(self, day: str, key: str, delta: int) -> None:
        """Atomically increment the ``(day, key)`` row by ``delta``.

        The increment happens inside the UPSERT (``value = value + :delta``), so
        concurrent inline increments from multiple workers never lose a delta and
        never create per-worker rows.
        """
        now_iso = _now_iso()
        delta = int(delta)
        async with self._session_factory() as session:
            insert = self._insert_for(session)
            stmt = insert(MetricsDaily).values(
                day_utc=day, metric=key, value=delta, computed_at=now_iso
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["day_utc", "metric"],
                set_={
                    "value": MetricsDaily.__table__.c.value + delta,
                    "computed_at": now_iso,
                },
            )
            await session.execute(stmt)
            await session.commit()

    async def prune_before(self, cutoff_day: str, *, exclude_days=()) -> int:
        """Delete every ``metrics_daily`` row with ``day_utc < cutoff_day``.

        A generic, key-agnostic retention primitive: rows whose ``day_utc``
        sorts lexicographically before ``cutoff_day`` (``YYYY-MM-DD``, so the
        string order is the chronological order) are deleted, except any
        ``day_utc`` listed in ``exclude_days`` - used to spare the reserved
        ``_TOTALS_DAY`` totals-snapshot sentinel. Idempotent: a re-run after the
        old rows are gone deletes nothing. Returns the number of rows removed.
        """
        exclusions = [d for d in exclude_days]
        async with self._session_factory() as session:
            stmt = delete(MetricsDaily).where(MetricsDaily.day_utc < cutoff_day)
            if exclusions:
                stmt = stmt.where(MetricsDaily.day_utc.notin_(exclusions))
            result = await session.execute(stmt)
            await session.commit()
        return int(result.rowcount or 0)

    # -- reads ---------------------------------------------------------------

    async def sum(self, keys, day_from: str, day_to: str) -> int:
        """Sum the given ``keys`` over the inclusive UTC day range ``[from, to]``.

        Day strings are ``YYYY-MM-DD``; the inclusive range is expressed with a
        lexicographic ``BETWEEN`` (correct for zero-padded ISO days). Returns 0
        for an empty key set or a range with no rows.
        """
        key_list = list(keys)
        if not key_list:
            return 0
        async with self._session_factory() as session:
            total = (
                await session.execute(
                    select(func.coalesce(func.sum(MetricsDaily.value), 0)).where(
                        MetricsDaily.metric.in_(key_list),
                        MetricsDaily.day_utc >= day_from,
                        MetricsDaily.day_utc <= day_to,
                    )
                )
            ).scalar_one()
        return int(total or 0)

    async def series(self, key: str, days: int) -> list[tuple[str, int]]:
        """Return ``(day, value)`` for the trailing ``days`` days of ``key``.

        The result is ordered oldest->newest and every day in the window is
        present, with ``0`` filled for days that have no stored row. Domain
        services layer any live current-day compute on top of this raw read.
        """
        window = _trailing_days(days)
        if not window:
            return []
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(MetricsDaily.day_utc, MetricsDaily.value).where(
                        MetricsDaily.metric == key,
                        MetricsDaily.day_utc.in_(window),
                    )
                )
            ).all()
        stored = {day: int(value) for day, value in rows}
        return [(day, stored.get(day, 0)) for day in window]

    # -- named snapshots (KV) ------------------------------------------------

    async def snapshot_get(self, name: str) -> dict | None:
        """Return the named JSON snapshot from the KVStore, or ``None``."""
        kv = self._kvstore()
        try:
            raw = await kv.get(f"{_SNAPSHOT_PREFIX}{name}")
        except Exception:
            logger.debug("Snapshot read failed for %s", name, exc_info=True)
            return None
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            return None

    async def snapshot_put(
        self, name: str, payload: dict, *, ttl_seconds: float | None = None
    ) -> None:
        """Write the named JSON snapshot to the KVStore (optional TTL)."""
        kv = self._kvstore()
        await kv.set(f"{_SNAPSHOT_PREFIX}{name}", json.dumps(payload), ttl_seconds=ttl_seconds)


# ---------------------------------------------------------------------------
# Process-wide instance
# ---------------------------------------------------------------------------

_store: MetricStore | None = None


def get_metric_store() -> MetricStore:
    """Return the process-wide :class:`MetricStore` (bound to the app DB)."""
    global _store
    if _store is None:
        from app.database import db

        _store = MetricStore(db.session_factory)
    return _store


def reset_metric_store() -> None:
    """Drop the cached instance (test helper)."""
    global _store
    _store = None

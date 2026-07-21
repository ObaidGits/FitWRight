"""Scheduled admin jobs: RollupJob + PurgeJob (Tasks 0.3, 2.1, 6.2).

Both jobs are **single-flighted** via the KVStore lock (TTL + auto-expiry so a
crashed holder can't wedge them) and **resumable** - each run re-scans the work
from scratch, so a crash mid-batch is recovered on the next run. They run under
``SCHEDULER_MODE`` (ADR-15): the free tier's external cron calls
``POST /api/v1/internal/run-jobs`` (which invokes :func:`run_admin_jobs`); the
premium in-process scheduler calls the same functions on an interval. The job
logic and lock are identical across modes - only the trigger differs.

- :func:`run_rollup_job` - UPSERT closed-day metrics + reconcile the denormalized
  usage counters; refresh the purge-backlog gauge.
- :func:`run_purge_job` - purge users whose grace period has elapsed: delete
  owned data (FK-safe, via the user-scoped facade) then the non-owned rows
  (sessions/oauth/tokens) and finally the user row, in one transaction per user;
  ``audit_log`` is **never** touched (R8.4). Idempotent, chunked, resumable.
  Gated by the ``ADMIN_DESTRUCTIVE_ACTIONS`` kill-switch.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from app.admin.metric_registry import DownsamplableEvent, audit_downsample_key
from app.admin.metric_store import get_metric_store
from app.admin.metrics import get_admin_metrics
from app.admin.repo import get_admin_repo
from app.auth.audit import AuditEvent, get_audit_service
from app.config import settings
from app.models import (
    AuditLog,
    EmailChangeToken,
    EmailVerificationToken,
    OAuthIdentity,
    PasswordResetToken,
    Session as SessionRow,
    User,
)

logger = logging.getLogger(__name__)

__all__ = [
    "run_rollup_job",
    "run_purge_job",
    "run_audit_retention_job",
    "run_alerting_job",
    "run_admin_jobs",
    "ROLLUP_LOCK_KEY",
    "PURGE_LOCK_KEY",
    "AUDIT_RETENTION_LOCK_KEY",
    "ALERTING_LOCK_KEY",
    "ALERT_CONDITIONS",
    "SECURITY_CRITICAL_EVENTS",
    "DOWNSAMPLABLE_EVENTS",
    "NEVER_DROPPED_EVENTS",
]

ROLLUP_LOCK_KEY = "admin:rollup"
PURGE_LOCK_KEY = "admin:purge"
AUDIT_RETENTION_LOCK_KEY = "admin:audit_retention"
ALERTING_LOCK_KEY = "admin:alerting"

# --- Audit retention tiers (R1.3/1.4/1.5) -----------------------------------
# Single source of truth for the audit-log retention job (task 3.x). Each tier
# is a frozenset derived from the ``AuditEvent`` catalog (app/auth/audit.py) -
# never hardcode raw event strings here, so the catalog stays authoritative.
#
# - SECURITY_CRITICAL_EVENTS: retained on the long (security) horizon.
# - DOWNSAMPLABLE_EVENTS: high-volume, safe to thin over time.
# - NEVER_DROPPED_EVENTS: a subset that must be excluded from BOTH the delete
#   path and the downsample path - these rows survive all retention pruning.
SECURITY_CRITICAL_EVENTS: frozenset[str] = frozenset(
    {
        AuditEvent.LOGIN_FAILED,
        AuditEvent.LOGIN,
        AuditEvent.AUTHZ_DENIED,
        AuditEvent.STEP_UP,
        AuditEvent.SESSION_REVOKED,
        AuditEvent.PASSWORD_CHANGED,
        AuditEvent.PASSWORD_RESET,
        AuditEvent.ROLE_CHANGED,
        AuditEvent.ADMIN_USER_DISABLED,
        AuditEvent.ADMIN_USER_SOFT_DELETED,
        AuditEvent.ADMIN_USER_RESTORED,
        AuditEvent.ADMIN_USER_PURGED,
    }
)

DOWNSAMPLABLE_EVENTS: frozenset[str] = frozenset(
    {
        AuditEvent.ADMIN_USER_VIEWED,
    }
)

NEVER_DROPPED_EVENTS: frozenset[str] = frozenset(
    {
        AuditEvent.ADMIN_USER_PURGED,
        AuditEvent.ADMIN_USER_SOFT_DELETED,
        AuditEvent.ROLE_CHANGED,
    }
)

# Max users purged per invocation (bounds a single run; the next run resumes).
_PURGE_BATCH = 50
_ROLLUP_LOCK_TTL = 300
_PURGE_LOCK_TTL = 600
# Retention prunes/aggregates the largest table in bounded batches; give it the
# same 10-minute headroom as the purge job so a slow batch can't self-evict its
# lock, while auto-expiry still frees a crashed holder before the next run.
_AUDIT_RETENTION_LOCK_TTL = 600
# The alerting job is a fast, read-only evaluation over already-computed
# signals (no batches, no scans), so a short TTL is enough while auto-expiry
# still frees a crashed holder before the next tick.
_ALERTING_LOCK_TTL = 120

# --- Minimal threshold alerting (Req 12) ------------------------------------
# The FIXED, closed set of operational conditions the Alerting_Job evaluates
# (Req 12.2). This is intentionally NOT dynamically extensible - the ≤8 names
# below are the entire alert surface; adding one is a deliberate edit here.
# Each is evaluated independently over ALREADY-COMPUTED health tiles / gauges /
# durable metrics - the job performs NO new data collection (Req 12.1/21.8).
ALERT_CONDITIONS: tuple[str, ...] = (
    "db_unhealthy",           # Database health tile is down/degraded
    "kv_unavailable",         # KVStore/Queue health tile is down/degraded
    "ai_provider_unavailable",  # AI provider health tile is down (unreachable)
    "storage_near_full",      # storage usage ≥ configured near-full pct (see gap note)
    "rollup_failed",          # rollup job's last run marker recorded a failure
    "migration_mismatch",     # Migrations health tile is down/degraded
    "high_error_rate",        # trailing-24h 5xx rate ≥ configured pct
    "background_job_stuck",   # any tracked job's run marker looks potentially stuck
)

# Per-alert KV state lives under this MetricStore snapshot-name prefix, so the
# full underlying KVStore key is ``admin:snapshot:alert:{name}`` - namespaced
# away from job markers / auth / rate-limit keys sharing the same KVStore.
_ALERT_STATE_PREFIX = "alert"

# Health-tile statuses that count as "bad" for a health-derived condition.
_HEALTH_BAD_STATUSES = frozenset({"down", "degraded"})


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _grace_cutoff_iso() -> str:
    """Users soft-deleted before this instant are purge-eligible."""
    return (_now() - timedelta(days=settings.admin_delete_grace_days)).isoformat()


async def run_rollup_job(*, kvstore=None, lookback_days: int = 3) -> dict:
    """Run the rollup + counter reconciliation, single-flighted (R10.3).

    The rollup work now runs **through the Rollup_Pipeline** rather than inline:
    the job acquires the single-flight lock, computes the closed day, and executes
    :func:`~app.admin.rollup_pipeline.run_rollup_pipeline`, whose first step
    (:class:`~app.admin.rollup_pipeline.ExistingRollupStep`) performs the exact
    same generic ``metrics_daily`` UPSERTs + ``_TOTALS_DAY`` totals snapshot +
    counter reconciliation as before. This is a pure refactor - the produced rows
    and this function's return shape are unchanged; the pipeline only adds ordered,
    per-step failure isolation (R2.5) that later steps build on.
    """
    if kvstore is None:
        from app.auth.runtime import get_kvstore

        kvstore = get_kvstore()

    lock = kvstore.lock(ROLLUP_LOCK_KEY, ttl_seconds=_ROLLUP_LOCK_TTL, blocking=False)
    async with lock as acquired:
        if not acquired:
            return {"status": "locked"}
        from app.admin.rollup_pipeline import EXISTING_ROLLUP_STEP, run_rollup_pipeline

        # The just-closed UTC day the pipeline coordinates over (yesterday). The
        # existing rollup step still recovers ``lookback_days`` closed days
        # internally for missed-run recovery, so behavior is unchanged.
        day = (_now() - timedelta(days=1)).strftime("%Y-%m-%d")
        EXISTING_ROLLUP_STEP.lookback_days = lookback_days
        await run_rollup_pipeline(day)
        rollup = EXISTING_ROLLUP_STEP.rollup
        reconcile = EXISTING_ROLLUP_STEP.reconcile
        # Refresh the purge-backlog gauge for observability/alerting (R12.1).
        try:
            backlog = await get_admin_repo().purge_backlog(_grace_cutoff_iso())
            get_admin_metrics().set_purge_backlog(backlog)
        except Exception:  # pragma: no cover - gauge is best-effort
            logger.debug("Failed to refresh purge backlog gauge", exc_info=True)
        return {"status": "ok", "rollup": rollup, "reconcile": reconcile}


async def run_purge_job(*, kvstore=None) -> dict:
    """Purge grace-elapsed soft-deleted users, single-flighted + resumable (R8.3/8.5).

    No-op (and logged) when ``ADMIN_DESTRUCTIVE_ACTIONS`` is off - the kill-switch
    stops all irreversible erasure without a code change.
    """
    if not settings.admin_destructive_actions:
        logger.info("Purge skipped: ADMIN_DESTRUCTIVE_ACTIONS is off")
        return {"status": "disabled", "purged": 0}
    if kvstore is None:
        from app.auth.runtime import get_kvstore

        kvstore = get_kvstore()

    from app.database import db

    lock = kvstore.lock(PURGE_LOCK_KEY, ttl_seconds=_PURGE_LOCK_TTL, blocking=False)
    async with lock as acquired:
        if not acquired:
            return {"status": "locked", "purged": 0}

        cutoff = _grace_cutoff_iso()
        # Re-scan every run (resumable): pick a bounded batch of purge-eligible ids.
        async with db.session_factory() as session:
            due_ids = list(
                (
                    await session.execute(
                        select(User.id)
                        .where(User.deleted_at.is_not(None), User.deleted_at < cutoff)
                        .order_by(User.deleted_at)
                        .limit(_PURGE_BATCH)
                    )
                ).scalars().all()
            )

        purged = 0
        audit = get_audit_service()
        for user_id in due_ids:
            try:
                owned_counts = await db.purge_user_owned_data(user_id)
                # Erase user-scoped JD extraction state (cost/rate counters, §27).
                # Best-effort: KV purge must never block the DB purge transaction.
                try:
                    from app.jd.monitoring.cost import purge_user_jd_data
                    await purge_user_jd_data(user_id, kvstore)
                except Exception:  # pragma: no cover - KV purge is best-effort
                    logger.debug("JD KV purge failed for %s", user_id, exc_info=True)
                # Delete the non-owned rows (FK-safe) then the user row itself.
                # audit_log is intentionally NOT touched (R8.4) - it has no FK and
                # is excluded here, so the security trail survives erasure.
                # Capture the avatar storage key BEFORE deleting the row so we can
                # GC the stored master (Photo System: erasure must not orphan the
                # image object in Cloudinary/local storage).
                async with db.session_factory() as session:
                    avatar_key = (
                        await session.execute(
                            select(User.avatar_key).where(User.id == user_id)
                        )
                    ).scalar_one_or_none()
                    for model in (
                        SessionRow,
                        OAuthIdentity,
                        EmailVerificationToken,
                        PasswordResetToken,
                        EmailChangeToken,
                    ):
                        await session.execute(delete(model).where(model.user_id == user_id))
                    await session.execute(delete(User).where(User.id == user_id))
                    await session.commit()
                # GC the avatar object from storage (best-effort; a failed delete
                # only leaves an orphan, never blocks erasure).
                if avatar_key:
                    try:
                        from app.storage.provider import get_storage_provider

                        await get_storage_provider().delete(avatar_key)
                    except Exception:  # pragma: no cover - best-effort cleanup
                        logger.debug("Avatar GC failed for purged user %s", user_id, exc_info=True)
                await audit.record(
                    AuditEvent.ADMIN_USER_PURGED,
                    target_user_id=user_id,
                    meta={"owned": owned_counts},
                )
                purged += 1
            except Exception:  # pragma: no cover - one failure must not stop the batch
                logger.exception("Purge failed for user %s; will retry next run", user_id)

        # Refresh the backlog gauge post-run.
        try:
            backlog = await get_admin_repo().purge_backlog(cutoff)
            get_admin_metrics().set_purge_backlog(backlog)
        except Exception:  # pragma: no cover
            logger.debug("Failed to refresh purge backlog gauge", exc_info=True)
        return {"status": "ok", "purged": purged, "scanned": len(due_ids)}


async def _downsample_aged_audit_rows(*, downsample_cutoff: str, limit: int) -> int:
    """Aggregate-then-delete aged Downsamplable rows into ``metrics_daily`` (Req 1.4/1.9).

    The downsample path for the Audit_Retention_Job. It thins high-volume,
    low-value Downsamplable_Event rows older than ``downsample_cutoff`` by folding
    each ``(event, day)`` group's count into that event's fixed ``AUDIT_DOWNSAMPLED_*``
    Metric_Key (via :func:`~app.admin.metric_registry.audit_downsample_key` +
    :class:`~app.admin.metric_store.MetricStore`) and only then deleting the exact
    rows that were counted. Returns the number of rows downsampled (aggregated then
    deleted) this call.

    ``limit`` is the **remaining per-invocation budget** (Req 1.7) handed down by
    :func:`run_audit_retention_job`, not the raw config batch size - the caller
    owns the shared budget and each path consumes only its slice, so the total
    rows processed across both paths never exceeds the configured maximum. A
    ``limit`` of ``0`` (or less) is a no-op.

    Aggregate-then-delete ordering + no-double-count strategy (Req 1.4/1.6):

    1. Select a *bounded* set (≤ ``limit``) of aged Downsamplable row **IDs**
       (with their ``ts`` so we can derive the ``day = ts[:10]``), oldest first
       (``ORDER BY ts``) so repeated invocations drain from the oldest backlog and
       resume where the previous run stopped. Never_Dropped events are excluded
       defensively even though none are downsamplable today (Req 1.5).
    2. Group those exact IDs in Python by ``(event, day)``.
    3. For each group, ``MetricStore.add`` the group's row **count** into the
       fixed key at that ``day``. ``add`` runs its own UPSERT + commit, so the
       aggregate is **durably committed before any delete**.
    4. Delete **exactly** the IDs that were just aggregated - never a broader
       predicate. Because we only ever delete rows we have already counted, and
       we only ever count rows that still exist, a normal run can neither
       double-count nor drop an uncounted row. (The sole residual double-count
       window - a crash after the aggregate commit but before the delete commit -
       is the inherent, accepted semantics of aggregate-then-delete; the exact-ID
       coupling keeps that window as small as possible and its full mitigation is
       task 3.4/Req 1.6.)
    5. Per-group failure isolation (Req 1.9): if a group's aggregate ``add``
       raises, retain that group's rows (skip the delete), leave the Metric_Key
       unchanged, log an error identifying the failed ``(event, day)`` group, and
       continue with the remaining groups.
    """
    # Nothing to do if the shared per-invocation budget is already exhausted.
    if limit <= 0:
        return 0

    # Downsamplable events actually eligible for pruning: exclude any that are
    # also Never_Dropped (defensive - the two sets are disjoint today, Req 1.5).
    eligible_events = DOWNSAMPLABLE_EVENTS - NEVER_DROPPED_EVENTS
    if not eligible_events:
        return 0

    from app.database import db

    # (1) Bounded select of the exact aged Downsamplable rows to process this run.
    async with db.session_factory() as session:
        aged_rows = (
            await session.execute(
                select(AuditLog.id, AuditLog.event, AuditLog.ts)
                .where(
                    AuditLog.event.in_(eligible_events),
                    AuditLog.ts < downsample_cutoff,
                )
                .order_by(AuditLog.ts)
                .limit(limit)
            )
        ).all()

    if not aged_rows:
        return 0

    # (2) Group the exact selected IDs by (event, day = ts[:10]).
    groups: dict[tuple[str, str], list[str]] = {}
    for row_id, event, ts in aged_rows:
        day = ts[:10]
        groups.setdefault((event, day), []).append(row_id)

    store = get_metric_store()
    downsampled = 0
    for (event, day), ids in groups.items():
        try:
            # (3) Aggregate: durably commit the group's count into the fixed key
            #     BEFORE any row is deleted.
            key = audit_downsample_key(DownsamplableEvent(event))
            await store.add(day, key, len(ids))
        except Exception:
            # (5) Aggregate failed: retain the rows, leave the key unchanged, log.
            logger.exception(
                "Audit downsample aggregate failed for event=%s day=%s (%d rows); "
                "retaining rows and leaving metric unchanged",
                event,
                day,
                len(ids),
            )
            continue

        # (4) Delete exactly the aggregated IDs, only after the aggregate commit.
        async with db.session_factory() as session:
            await session.execute(delete(AuditLog).where(AuditLog.id.in_(ids)))
            await session.commit()
        downsampled += len(ids)

    return downsampled


async def _delete_aged_security_critical_rows(*, hot_cutoff: str, limit: int) -> int:
    """Delete Security_Critical rows older than the hot window (Req 1.3/1.5).

    The delete path for the Audit_Retention_Job. Unlike the downsample path,
    Security_Critical rows are **not** aggregated - once they age past the
    configured hot-retention window they are simply removed (their long-horizon
    value has expired), so there is no ``metrics_daily`` write here, only a
    bounded delete. Returns the number of rows deleted this call.

    ``limit`` is the **remaining per-invocation budget** (Req 1.7) left over after
    the downsample path has consumed its slice (``batch - downsampled``), handed
    down by :func:`run_audit_retention_job`. This is what enforces a single shared
    budget across both paths so their combined work never exceeds the configured
    maximum. A ``limit`` of ``0`` (or less) is a no-op - the budget is spent.

    Never_Dropped exclusion via set subtraction (Req 1.5): three Never_Dropped
    events (``role.changed``, ``user.soft_deleted``, ``user.purged``) are also
    Security_Critical, so we prune the intersection by deleting only
    ``SECURITY_CRITICAL_EVENTS - NEVER_DROPPED_EVENTS``. Those subtracted rows are
    never selected by this predicate and therefore survive indefinitely,
    regardless of the hot window.

    Bounded like the downsample helper (Req 1.7): select at most ``limit`` aged
    row **IDs** (ordered by ``ts`` so the oldest go first - resumable draining),
    then delete exactly those IDs.
    """
    # Nothing to do if the shared per-invocation budget is already exhausted.
    if limit <= 0:
        return 0

    # Security_Critical events eligible for deletion: exclude any that are also
    # Never_Dropped so those rows are retained indefinitely (Req 1.5).
    eligible_events = SECURITY_CRITICAL_EVENTS - NEVER_DROPPED_EVENTS
    if not eligible_events:
        return 0

    from app.database import db

    # Bounded select of the exact aged Security_Critical row IDs to delete.
    async with db.session_factory() as session:
        aged_ids = list(
            (
                await session.execute(
                    select(AuditLog.id)
                    .where(
                        AuditLog.event.in_(eligible_events),
                        AuditLog.ts < hot_cutoff,
                    )
                    .order_by(AuditLog.ts)
                    .limit(limit)
                )
            ).scalars().all()
        )

    if not aged_ids:
        return 0

    # Delete exactly the selected IDs (mirrors the downsample helper's delete).
    async with db.session_factory() as session:
        await session.execute(delete(AuditLog).where(AuditLog.id.in_(aged_ids)))
        await session.commit()

    return len(aged_ids)


async def run_audit_retention_job(*, kvstore=None) -> dict:
    """Enforce tiered retention/pruning on ``audit_log``, single-flighted + resumable.

    Mirrors the :func:`run_rollup_job`/:func:`run_purge_job` pattern: acquire the
    job's own ``admin:audit_retention`` KVStore lock **non-blocking** before doing
    any work, and if it is already held, terminate the invocation WITHOUT any
    deletion or aggregation (Req 1.1). The lock has a TTL so a crashed holder can't
    wedge the job - the next run recovers it.

    It establishes the lock + reads the configured retention windows and batch
    size (Req 1.8), then runs the two pruning paths under a single shared
    per-invocation budget:

    - the aggregate-then-delete downsample path (Req 1.4/1.9),
    - the Security_Critical delete path past the hot window (Req 1.3/1.5).

    Windows are measured from each row's recorded event timestamp:
    - ``hot_days`` (default 365): Security_Critical rows older than this are deleted.
    - ``downsample_days`` (default 90): Downsamplable rows older than this are
      aggregated into ``metrics_daily`` then deleted.
    - ``batch`` (default 1000, range 1-100,000): max rows processed per invocation.
    Never_Dropped_Event rows are excluded from BOTH paths (Req 1.5).

    **Shared per-invocation budget (Req 1.7).** ``batch`` is one overall budget
    across BOTH paths, not a per-path cap. If each path independently used
    ``LIMIT batch`` a single invocation could process up to ``2*batch`` rows. To
    honor "no more than the configured maximum batch size per invocation", the
    downsample path runs first with the full ``batch`` budget and reports how many
    rows it processed; the Security_Critical delete path then runs with only the
    leftover budget (``batch - downsampled``, floored at 0 - skipped when 0). Thus
    ``downsampled + deleted <= batch`` always holds.

    **Resumability + no double-count (Req 1.6).** Each invocation is bounded, and
    both paths select their oldest eligible rows first (``ORDER BY ts``), so
    repeated invocations drain the backlog oldest-first and each run resumes where
    the last one stopped - no cursor state is needed. Correctness under
    interruption comes from the aggregate-then-delete-exact-IDs design in the
    downsample path: a group's count is durably committed to its Metric_Key
    *before* the exact aggregated rows are deleted, and only rows that still exist
    are ever aggregated. A re-run therefore never re-aggregates a row that a prior
    run already counted-and-deleted, so no aggregated event count is added to
    ``metrics_daily`` more than once. (The sole residual window - a crash after the
    aggregate commit but before the delete commit - is the inherent, accepted
    semantics of aggregate-then-delete; exact-ID coupling keeps it minimal.)
    """
    if kvstore is None:
        from app.auth.runtime import get_kvstore

        kvstore = get_kvstore()

    lock = kvstore.lock(
        AUDIT_RETENTION_LOCK_KEY, ttl_seconds=_AUDIT_RETENTION_LOCK_TTL, blocking=False
    )
    async with lock as acquired:
        if not acquired:
            return {"status": "locked"}

        # Config windows/batch (task 1.3 settings; validators guarantee sane values).
        hot_days = settings.admin_audit_hot_days
        downsample_days = settings.admin_audit_downsample_days
        batch = settings.admin_audit_retention_batch

        # Age cutoffs (ISO, UTC) - rows with an event timestamp OLDER than these
        # are eligible for their tier's pruning. Computed once per run.
        now = _now()
        hot_cutoff = (now - timedelta(days=hot_days)).isoformat()
        downsample_cutoff = (now - timedelta(days=downsample_days)).isoformat()

        # Shared per-invocation budget (Req 1.7): `batch` is the TOTAL rows this
        # invocation may process across BOTH paths, not a per-path cap. Run the
        # downsample path first with the full budget (it both aggregates and frees
        # rows), then run the Security_Critical delete path with only the leftover
        # budget so that downsampled + deleted <= batch.
        #
        # Downsample path (Req 1.4/1.9): fold aged Downsamplable rows into the fixed
        #   AUDIT_DOWNSAMPLED_* Metric_Key per (event, day), then delete exactly the
        #   aggregated rows (aggregate-then-delete, Req 1.4); per-group failures are
        #   isolated (retain + key unchanged + log, Req 1.9).
        downsampled = await _downsample_aged_audit_rows(
            downsample_cutoff=downsample_cutoff, limit=batch
        )
        # Delete path (Req 1.3): delete Security_Critical rows older than
        #   `hot_cutoff` with the REMAINING budget (`batch - downsampled`, floored
        #   at 0 - skipped when the downsample path already spent the budget).
        #   Never_Dropped rows are excluded via set subtraction
        #   (SECURITY_CRITICAL_EVENTS - NEVER_DROPPED_EVENTS), so the three
        #   overlapping Never_Dropped events are retained indefinitely (Req 1.5).
        #   No aggregate - aged security rows are simply removed.
        remaining = max(batch - downsampled, 0)
        deleted = await _delete_aged_security_critical_rows(
            hot_cutoff=hot_cutoff, limit=remaining
        )

        logger.info(
            "Audit retention ran (hot_days=%s, downsample_days=%s, batch=%s, "
            "downsampled=%s, deleted=%s)",
            hot_days,
            downsample_days,
            batch,
            downsampled,
            deleted,
        )
        return {
            "status": "ok",
            "downsampled": downsampled,
            "deleted": deleted,
            "hot_days": hot_days,
            "downsample_days": downsample_days,
            "batch": batch,
            "hot_cutoff": hot_cutoff,
            "downsample_cutoff": downsample_cutoff,
        }


class _ConditionUnavailable(Exception):
    """A condition's source/config is missing or unreadable this run (Req 12.6).

    Raised by an individual condition evaluator when the signal it needs cannot
    be obtained (e.g. health compose failed, a required gauge is unreadable, or a
    threshold is misconfigured). The run loop catches it, records the alert as
    *skipped* with a logged reason, and continues evaluating the other conditions
    - one bad condition never aborts the rest (per-condition isolation).
    """


def _alert_state_name(name: str) -> str:
    """MetricStore snapshot name holding ``name``'s per-alert cooldown state."""
    return f"{_ALERT_STATE_PREFIX}:{name}"


# Operational alerts are delivered at this log severity - the "existing log
# path" that operators/log-based alerting already watch. Kept as a WARNING so it
# stands out from routine INFO run summaries without the crash-level noise of
# ERROR (these are conditions, not process failures).
_ALERT_SEVERITY = "warning"


def _deliver_alert(name: str, message: str) -> None:
    """Deliver a freshly-raised alert via the existing LOG path (Req 12.2).

    **Delivery decision (log-only, deliberate).** Delivery uses the standard
    structured WARNING log - the honest "existing log path" the design allows and
    that operators / log-based alerting already tail. It emits the alert ``name``,
    its ``message``, and an explicit ``severity`` so the raise is greppable and
    machine-parseable, and is single-lined + control-char-safe against log
    injection (``name``/``message`` come from the fixed ``ALERT_CONDITIONS`` set,
    but we sanitize defensively).

    The platform :class:`~app.notifications.service.NotificationService` is
    intentionally **not** used here: it is strictly *user-scoped* (``notify``
    requires a ``user_id`` and resolves per-user delivery prefs), so fleet-level
    operational alerts have no natural single-user target. Fanning out to every
    admin user would need a cross-user admin lookup and would be noisy - and the
    spec's minimal-alerting Non-Goal forbids routing/escalation (Req 21.8). So we
    keep the log path as the single, sufficient operational channel; adding admin
    notification fan-out is a deliberate omission, not an oversight. There is no
    routing, priority, escalation, incident, or pager behavior here (Req 21.8).
    """
    safe_name = _sanitize_log_field(name)
    safe_message = _sanitize_log_field(message)
    logger.warning(
        "ALERT raised [%s] severity=%s: %s", safe_name, _ALERT_SEVERITY, safe_message
    )


def _sanitize_log_field(value: object) -> str:
    """Single-line a value for safe structured logging (log-injection defense)."""
    text = str(value)
    return " ".join(text.split())


def _parse_iso(value: object) -> datetime | None:
    """Parse a stored UTC ISO timestamp, or ``None`` if absent/unparseable."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _cooldown_seconds() -> int:
    """Read + validate the alert cooldown window from config (Req 12.5).

    Raises :class:`_ConditionUnavailable` if the configured value is missing or
    not a positive integer, so a misconfigured cooldown skips the affected
    cooldown-gated raises (Req 12.6) rather than silently using a bogus window.
    (The settings validator already coerces this to a positive int; the guard is
    defensive and keeps the "read from config, never hard-coded" contract honest.)
    """
    value = getattr(settings, "alert_cooldown_seconds", None)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise _ConditionUnavailable(f"alert_cooldown_seconds invalid: {value!r}")
    return value


async def _apply_alert(
    store,
    name: str,
    triggered: bool,
    *,
    cooldown: int,
    now: datetime,
    message: str,
    raised: list[str],
    resolved: list[str],
) -> None:
    """Transition one alert's KV cooldown state for this run (Req 12.2/12.3/12.4).

    Per-alert state is a tiny JSON snapshot ``{name, state, last_raised_at,
    updated_at}`` under ``admin:snapshot:alert:{name}``:

    - **Raise ≤ once per cooldown (Req 12.3).** When ``triggered`` and there is no
      prior ``raised`` state (fresh, or previously ``resolved``), raise now. When
      already ``raised``, only re-raise once ``now - last_raised_at >= cooldown``;
      otherwise suppress (no delivery, no state change) so a continuously-true
      condition alerts at most once per window.
    - **Resolve -> re-raise (Req 12.4).** When NOT ``triggered`` and the prior
      state was ``raised``, record it ``resolved`` (preserving ``last_raised_at``
      for history). Because a later recurrence sees state ``resolved`` (not
      ``raised``), it raises a *fresh* alert immediately rather than being
      suppressed by the old cooldown.
    """
    prior = await store.snapshot_get(_alert_state_name(name)) or {}
    prior_state = prior.get("state")

    if triggered:
        should_raise = prior_state != "raised"
        if not should_raise:
            last = _parse_iso(prior.get("last_raised_at"))
            should_raise = last is None or (now - last).total_seconds() >= cooldown
        if should_raise:
            _deliver_alert(name, message)
            await store.snapshot_put(
                _alert_state_name(name),
                {
                    "name": name,
                    "state": "raised",
                    "last_raised_at": now.isoformat(),
                    "updated_at": now.isoformat(),
                },
            )
            raised.append(name)
        # else: within cooldown - suppress, leave state untouched.
    elif prior_state == "raised":
        await store.snapshot_put(
            _alert_state_name(name),
            {
                "name": name,
                "state": "resolved",
                "last_raised_at": prior.get("last_raised_at"),
                "updated_at": now.isoformat(),
            },
        )
        resolved.append(name)
    # else: not triggered and not previously raised - nothing to record.


async def _high_error_rate(store, now: datetime) -> bool:
    """Trailing-24h server-error rate ≥ ``alert_error_rate_pct`` (Req 12.2).

    Mirrors the Overview ``errorRate24h`` derivation exactly - the trailing-24h
    proxy is the last two UTC days, ``errors = REQUEST_5XX`` and
    ``total = REQUEST_2XX + REQUEST_4XX + REQUEST_5XX`` summed from the durable
    Metric_Keys via ``MetricStore.sum`` (no new collection). ``total == 0`` => 0%
    (not triggered). Raises :class:`_ConditionUnavailable` if the threshold is
    misconfigured (Req 12.6) or the durable read fails.
    """
    threshold = getattr(settings, "alert_error_rate_pct", None)
    if not isinstance(threshold, int) or isinstance(threshold, bool) or not (0 <= threshold <= 100):
        raise _ConditionUnavailable(f"alert_error_rate_pct invalid: {threshold!r}")

    from app.admin.metric_registry import REQUEST_2XX, REQUEST_4XX, REQUEST_5XX

    try:
        day_to = now.strftime("%Y-%m-%d")
        day_from = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        errors = await store.sum([REQUEST_5XX], day_from, day_to)
        total = await store.sum([REQUEST_2XX, REQUEST_4XX, REQUEST_5XX], day_from, day_to)
    except Exception as exc:  # noqa: BLE001 - unavailable source, not a crash
        raise _ConditionUnavailable(f"error-rate source unreadable: {exc}") from exc

    if total <= 0:
        return False
    rate = errors / total * 100
    return rate >= threshold


def _job_marker_stuck(marker: dict | None, now: datetime) -> bool:
    """Whether a job run marker looks potentially stuck (Req 12.2, mirrors Req 8.10).

    Reuses the Jobs-panel stuck math from *already-recorded* markers only (no new
    monitoring): a job is only stuck while running (``running_since`` set); when
    an expected duration exists, stuck <=> current > expected * multiplier, else
    stuck <=> current > the absolute ceiling.
    """
    if not marker:
        return False
    running_since = _parse_iso(marker.get("running_since"))
    if running_since is None:
        return False
    current = (now - running_since).total_seconds()
    if current < 0:
        return False
    expected = marker.get("expected_duration_seconds")
    try:
        expected_val = float(expected) if expected is not None else None
    except (TypeError, ValueError):
        expected_val = None
    if expected_val is not None and expected_val > 0:
        return current > expected_val * settings.admin_job_stuck_multiplier
    return current > settings.admin_job_stuck_ceiling_seconds


async def run_alerting_job(*, kvstore=None) -> dict:
    """Evaluate the fixed minimal alert set over already-computed signals (Req 12).

    Single-flighted via its OWN ``admin:alerting`` KVStore lock (non-blocking; the
    TTL frees a crashed holder), exactly like the rollup/purge/retention jobs. On
    a held lock it returns ``{"status": "locked"}`` without evaluating.

    It performs **no new data collection** (Req 12.1/21.8): it reads the six
    health tiles + migration status from ``HealthService.compose_health()`` (the
    same bounded, cached signals the Health page already composes), the per-job KV
    run markers, and the durable ``REQUEST_*`` Metric_Keys - then evaluates the
    FIXED ``ALERT_CONDITIONS`` set (≤8), each **independently** (Req 12.2/12.6):

    - ``db_unhealthy`` / ``kv_unavailable`` / ``migration_mismatch`` <- the
      Database / KVStore-Queue / Migrations health tiles being ``down``/``degraded``.
    - ``ai_provider_unavailable`` <- the AI-provider tile being ``down`` (i.e.
      unreachable; ``degraded`` means merely unconfigured/unhealthy, not down).
    - ``storage_near_full`` <- **documented gap**: there is no total-capacity signal
      to compute a near-full percentage against, so this condition is always
      skipped-unavailable + logged (Req 12.5 misconfig/unavailable-skip). It never
      fabricates a capacity ceiling.
    - ``rollup_failed`` <- the ``rollup`` run marker's ``last_outcome == "failure"``.
    - ``high_error_rate`` <- trailing-24h 5xx rate ≥ ``alert_error_rate_pct``.
    - ``background_job_stuck`` <- any tracked job marker looking potentially stuck.

    Every threshold/cooldown is read from config (Req 12.5). A condition whose
    source or config is missing/invalid is **skipped + logged** and the remaining
    conditions still evaluate (Req 12.6). For each true condition an alert is
    raised at most once per ``alert_cooldown_seconds`` (Req 12.3); a condition that
    turns false is recorded ``resolved`` so a later recurrence raises afresh
    (Req 12.4). Delivery is via :func:`_deliver_alert` - the existing structured
    WARNING log path (see that function for why the user-scoped platform
    notification channel is intentionally not used). This job is wired into
    :func:`run_admin_jobs`, so it runs through ``POST /api/v1/internal/run-jobs``
    and the scheduler alongside rollup/purge/audit_retention. No routing /
    priority / escalation / incident / pager behavior exists here (Req 21.8).

    Returns ``{"status": "ok", "raised": [...], "resolved": [...], "skipped": [...]}``.
    """
    if kvstore is None:
        from app.auth.runtime import get_kvstore

        kvstore = get_kvstore()

    lock = kvstore.lock(ALERTING_LOCK_KEY, ttl_seconds=_ALERTING_LOCK_TTL, blocking=False)
    async with lock as acquired:
        if not acquired:
            return {"status": "locked"}

        from app.admin.job_markers import job_marker_name
        from app.admin.metric_store import get_metric_store

        store = get_metric_store()
        now = _now()

        # -- gather already-computed signals ONCE (no per-condition recompute) --
        # Health tiles (+ migration status). A compose failure degrades ONLY the
        # health-derived conditions to skipped; the others still evaluate.
        tiles: dict[str, str] = {}
        health_available = False
        try:
            from app.admin.health_service import get_health_service

            health = await get_health_service().compose_health()
            tiles = {tile.name: tile.status for tile in health.tiles}
            health_available = True
        except Exception:  # noqa: BLE001 - isolate: health-derived conditions skip
            logger.warning(
                "Alerting: health compose failed; health-derived conditions skipped",
                exc_info=True,
            )

        # Per-job run markers (best-effort; a missing marker is treated as
        # "no signal", never as a failure/stuck).
        markers: dict[str, dict | None] = {}
        for job_name in ("rollup", "purge", "audit_retention"):
            try:
                markers[job_name] = await store.snapshot_get(job_marker_name(job_name))
            except Exception:  # noqa: BLE001 - best-effort marker read
                logger.debug("Alerting: marker read failed for %s", job_name, exc_info=True)
                markers[job_name] = None

        def _tile(name: str) -> str:
            if not health_available:
                raise _ConditionUnavailable("health signal unavailable")
            return tiles.get(name, "unknown")

        # Fixed condition evaluators (closed set - mirrors ALERT_CONDITIONS).
        async def _eval(name: str) -> bool:
            if name == "db_unhealthy":
                return _tile("Database") in _HEALTH_BAD_STATUSES
            if name == "kv_unavailable":
                return _tile("KVStore/Queue") in _HEALTH_BAD_STATUSES
            if name == "ai_provider_unavailable":
                # "unreachable" => the tile is down (degraded = unconfigured/unhealthy).
                return _tile("AI provider") == "down"
            if name == "migration_mismatch":
                return _tile("Migrations") in _HEALTH_BAD_STATUSES
            if name == "storage_near_full":
                # Documented gap: no total-capacity signal exists to compute a
                # near-full percentage, so this is skipped-unavailable rather than
                # fabricating a ceiling (Req 12.5). alert_storage_full_pct is read
                # but there is nothing valid to compare it against.
                raise _ConditionUnavailable(
                    "storage capacity signal unavailable (no total-capacity source)"
                )
            if name == "rollup_failed":
                return (markers.get("rollup") or {}).get("last_outcome") == "failure"
            if name == "high_error_rate":
                return await _high_error_rate(store, now)
            if name == "background_job_stuck":
                return any(_job_marker_stuck(m, now) for m in markers.values())
            # Unreachable for the fixed set, but fail safe as skipped.
            raise _ConditionUnavailable(f"unknown condition {name!r}")

        # Cooldown is shared across raises (Req 12.3/12.5). If it is misconfigured
        # we cannot honor "raise ≤ once per cooldown", so every true condition is
        # skipped this run (Req 12.6) - but resolves still process below.
        try:
            cooldown = _cooldown_seconds()
            cooldown_ok = True
        except _ConditionUnavailable as exc:
            cooldown = 0
            cooldown_ok = False
            logger.error("Alerting: cooldown misconfigured, raises skipped: %s", exc)

        raised: list[str] = []
        resolved: list[str] = []
        skipped: list[str] = []

        for name in ALERT_CONDITIONS:
            try:
                triggered = await _eval(name)
            except _ConditionUnavailable as exc:
                skipped.append(name)
                logger.warning("Alerting: condition %s skipped: %s", name, exc)
                continue
            except Exception:  # noqa: BLE001 - isolate: one bad condition never aborts the rest
                skipped.append(name)
                logger.warning("Alerting: condition %s errored; skipped", name, exc_info=True)
                continue

            # A true condition needs the cooldown to gate its raise; if cooldown is
            # misconfigured, skip the raise (but still allow a false->resolved
            # transition to be recorded).
            if triggered and not cooldown_ok:
                skipped.append(name)
                continue

            try:
                await _apply_alert(
                    store,
                    name,
                    triggered,
                    cooldown=cooldown,
                    now=now,
                    message=f"condition {name} is true",
                    raised=raised,
                    resolved=resolved,
                )
            except Exception:  # noqa: BLE001 - isolate KV state failure to this alert
                skipped.append(name)
                logger.warning("Alerting: state update for %s failed; skipped", name, exc_info=True)

        logger.info(
            "Alerting ran (raised=%s, resolved=%s, skipped=%s)", raised, resolved, skipped
        )
        return {"status": "ok", "raised": raised, "resolved": resolved, "skipped": skipped}


async def _run_job_with_markers(job_name: str, coro_factory) -> dict:
    """Run one admin job, persisting its per-job run markers to KV (Req 3.4/8.8/8.9).

    Wraps a single job call so the System Health page and Jobs panel can read
    *when it ran*, *its outcome*, *whether it is running*, and *its typical
    duration* - all from KV run markers written here, never per-event storage.

    Sequence: write the start marker (sets ``running_since``), run the job, then
    write the completion marker with the outcome derived from the job's return
    dict (:func:`~app.admin.job_markers.outcome_from_result`). A raised exception
    is caught and recorded as a ``failure`` outcome so one failing job neither
    prevents its own marker nor blocks the remaining jobs (best-effort, mirroring
    the existing gauge updates); the failure is surfaced in the returned dict as
    ``{"status": "error"}``. Marker writes are themselves best-effort and never
    abort the job.
    """
    from app.admin.job_markers import mark_job_started, outcome_from_result, record_job_run
    from app.admin.metric_store import get_metric_store

    store = get_metric_store()
    start_iso = _now().isoformat()
    await mark_job_started(store, job_name, start_iso=start_iso)

    try:
        result = await coro_factory()
        outcome = outcome_from_result(result)
    except Exception:
        logger.exception("Admin job %s raised; recording failure marker", job_name)
        result = {"status": "error"}
        outcome = "failure"

    await record_job_run(store, job_name, start_iso=start_iso, outcome=outcome)
    return result


async def run_admin_jobs(*, kvstore=None) -> dict:
    """Run the admin jobs once (invoked by the internal run-jobs endpoint).

    Runs the rollup, purge, audit-retention, and alerting jobs. Each is
    single-flighted via its own KVStore lock and idempotent/resumable, so running
    them on every ``POST /api/v1/internal/run-jobs`` call (or scheduler tick)
    never double-counts or double-processes. Wiring :func:`run_audit_retention_job`
    here is what makes the Audit_Retention_Job run through the existing job runner
    (Req 1.2), and wiring :func:`run_alerting_job` likewise makes the minimal
    threshold Alerting_Job run on every tick (Req 12.2) - no separate scheduling
    path is introduced for either.

    The alerting job is a fast, read-only evaluation over already-computed signals
    and is not marker-wrapped: unlike rollup/purge/audit_retention it has no run
    marker that the Health page / Jobs panel surfaces, and the alerting evaluator
    only inspects those three jobs' markers for its ``rollup_failed`` /
    ``background_job_stuck`` conditions - so a self-marker would be unread noise.
    Its own single-flight lock still prevents overlapping runs. Its failure is
    isolated best-effort here so it can never stop the other jobs' results.

    Each job call is wrapped in :func:`_run_job_with_markers`, which persists that
    job's per-job run marker to KV (last run/outcome/running-since/last-success/
    duration/expected-duration - Req 3.4/8.8/8.9) so the Health page and Jobs panel
    can read job status without any per-event storage. Marker persistence and a
    single job's failure are both isolated best-effort, so neither can stop the
    other jobs from running and recording their own markers. Markers are recorded
    under the stable ``rollup``/``purge``/``audit_retention`` names (the jobs run
    here); ``reaper``/``outbox`` markers come from their own separate paths.
    """
    rollup = await _run_job_with_markers("rollup", lambda: run_rollup_job(kvstore=kvstore))
    purge = await _run_job_with_markers("purge", lambda: run_purge_job(kvstore=kvstore))
    audit_retention = await _run_job_with_markers(
        "audit_retention", lambda: run_audit_retention_job(kvstore=kvstore)
    )
    # Alerting runs unwrapped (no run marker - see docstring) but still isolated:
    # a failure here must not discard the other jobs' results.
    try:
        alerting = await run_alerting_job(kvstore=kvstore)
    except Exception:
        logger.exception("Alerting job raised; other job results preserved")
        alerting = {"status": "error"}
    return {
        "rollup": rollup,
        "purge": purge,
        "audit_retention": audit_retention,
        "alerting": alerting,
    }

"""Cost monitoring + budget enforcement for JD extraction (§25 of enhancement plan).

Attributes a per-operation cost to each extraction and enforces two budgets via
KVStore counters (atomic ``incr`` with TTL windows):

- Per-user daily cap (default $0.50) — protects against a single abusive account.
- Global hourly cap (default alert $50, circuit-break $100) — protects the fleet.

Costs are estimates in USD-cents-scaled integers (we store *microdollars* — i.e.
millionths of a dollar — as integers to keep KV counters integer-only and avoid
float drift). 1 dollar = 1_000_000 microdollars.
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

__all__ = ["CostMonitor", "OperationCost", "MICRO"]

MICRO = 1_000_000  # microdollars per dollar

# Per-operation cost estimates in microdollars (§25 cost table midpoints).
class OperationCost:
    CACHE_HIT = 0
    STATIC_FETCH = 100          # ~$0.0001 (bandwidth)
    PLATFORM_API = 0
    JSON_LD = 0
    DOM = 0
    PLAYWRIGHT = 12_000         # ~$0.012 (compute)
    AI_CLEANUP = 5_000          # ~$0.005 (tokens)
    OCR_PER_PAGE = 1_000        # ~$0.001/page (self-hosted compute)


async def purge_user_jd_data(user_id: str, kv=None) -> int:
    """Erase all user-scoped JD state for ``user_id`` (§27 GDPR right-to-erasure).

    Called from the admin PurgeJob. Extraction results/HTML are URL-hash keyed
    and TTL-expiring (contain no user identity), so this removes only the
    user-scoped cost + rate-limit counters. Returns the number of keys removed.
    """
    if kv is None:
        from app.auth.runtime import get_kvstore
        kv = get_kvstore()
    return await CostMonitor(kv).purge_user(user_id)


class BudgetExceeded(Exception):
    """Raised (or signalled) when a budget cap would be exceeded."""

    def __init__(self, scope: str, spent: int, cap: int):
        self.scope = scope
        self.spent = spent
        self.cap = cap
        super().__init__(f"{scope} budget exceeded: {spent}/{cap} microdollars")


class CostMonitor:
    """Per-user + global cost accounting backed by KVStore."""

    def __init__(self, kv, *, per_user_daily=None, global_hourly_break=None):
        self._kv = kv
        from app.config import settings

        # Defaults come from config; explicit args override (for tests).
        self._user_cap = (
            per_user_daily if per_user_daily is not None
            else int(getattr(settings, "jd_cost_user_daily_cap_usd", 0.5) * MICRO)
        )
        self._global_cap = (
            global_hourly_break if global_hourly_break is not None
            else int(getattr(settings, "jd_cost_global_hourly_break_usd", 100.0) * MICRO)
        )

    def _user_key(self, user_id: str) -> str:
        # Day bucket (UTC) so the counter naturally resets each day via TTL.
        day = int(time.time() // 86400)
        return f"jd:cost:user:{user_id}:{day}"

    def _global_key(self) -> str:
        hour = int(time.time() // 3600)
        return f"jd:cost:global:{hour}"

    async def check_budget(self, user_id: str) -> bool:
        """Return True if the user is under both caps (cheap read, no increment)."""
        try:
            user_raw = await self._kv.get(self._user_key(user_id))
            global_raw = await self._kv.get(self._global_key())
        except Exception:
            return True  # fail-open on KV error
        user_spent = int(user_raw) if user_raw else 0
        global_spent = int(global_raw) if global_raw else 0
        if user_spent >= self._user_cap:
            logger.warning("JD cost: user %s over daily cap (%d/%d)", user_id, user_spent, self._user_cap)
            self._metric("jd_budget_exceeded", "user")
            return False
        if global_spent >= self._global_cap:
            logger.error("JD cost: GLOBAL hourly circuit-break (%d/%d)", global_spent, self._global_cap)
            self._metric("jd_budget_exceeded", "global")
            return False
        return True

    async def record(self, user_id: str, microdollars: int) -> None:
        """Add ``microdollars`` to the user daily + global hourly counters."""
        if microdollars <= 0:
            return
        try:
            await self._kv.incr(self._user_key(user_id), amount=microdollars, ttl_seconds=86400)
            await self._kv.incr(self._global_key(), amount=microdollars, ttl_seconds=3600)
        except Exception:
            logger.debug("JD cost record failed", exc_info=True)
        # Fleet-wide cost gauge for the dashboard (§25, §34).
        try:
            from app.productivity.metrics import get_productivity_metrics
            get_productivity_metrics().jd_cost(microdollars)
        except Exception:
            pass

    async def spent_today(self, user_id: str) -> int:
        """Return the user's microdollar spend today (for observability)."""
        try:
            raw = await self._kv.get(self._user_key(user_id))
            return int(raw) if raw else 0
        except Exception:
            return 0

    async def global_spent(self) -> int:
        """Return the current global-hour microdollar counter (observability read).

        A cheap, non-incrementing read of the existing ``jd:cost:global:<hour>``
        KV counter — the only durable KVStore microdollar signal the system
        records today. Note it is a **rolling one-hour** window (the counter has
        a 1h TTL) scoped to the JD extraction pipeline; there is no durable
        arbitrary-window AI microdollar total. Callers that surface a windowed
        cost estimate (e.g. the AI Analytics panel) use this as the best
        available signal and must document that limitation. Fails soft to 0.
        """
        try:
            raw = await self._kv.get(self._global_key())
            return int(raw) if raw else 0
        except Exception:
            return 0

    async def purge_user(self, user_id: str) -> int:
        """Delete the user's cost counters (GDPR erasure, §27). Returns keys removed.

        Extraction *results* are URL-hash keyed and TTL-expiring (no PII), so the
        only user-scoped JD state is the cost + rate-limit counters. We delete the
        current and previous day buckets (older buckets have already TTL-expired).
        """
        import time as _time
        removed = 0
        day = int(_time.time() // 86400)
        keys = [
            f"jd:cost:user:{user_id}:{day}",
            f"jd:cost:user:{user_id}:{day - 1}",
            f"jd:rl:user:{user_id}",
        ]
        for key in keys:
            try:
                existed = await self._kv.get(key)
                await self._kv.delete(key)
                if existed is not None:
                    removed += 1
            except Exception:
                pass
        return removed

    @staticmethod
    def _metric(name: str, scope: str) -> None:
        try:
            from app.productivity.metrics import get_productivity_metrics
            get_productivity_metrics().incr(f"{name}_total.{scope}")
        except Exception:
            pass

"""Drift detection and circuit breaker for JD extraction (§32 of enhancement plan).

Monitors per-platform success rates using KVStore counters. When a platform's
failure rate exceeds thresholds, the circuit breaker trips and the orchestrator
skips that adapter (cascading to generic extraction).

Uses rolling 1-hour windows via TTL-based counters (approximate but adequate
for drift detection — true time-series requires Prometheus, a P3 concern).
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

__all__ = ["DriftMonitor"]

_WINDOW_SECONDS = 3600  # 1-hour rolling window
_TRIP_THRESHOLD = 0.4   # Trip if > 40% failure rate
_RESET_AFTER = 300      # Re-try after 5 min (half-open)
_MIN_SAMPLES = 5        # Don't trip on < 5 samples


class DriftMonitor:
    """Per-platform drift detection with circuit breaker.

    Each platform has:
    - success counter (1hr TTL)
    - failure counter (1hr TTL)
    - circuit state: closed (normal) / open (tripped) / half-open (probing)
    """

    def __init__(self, kv):
        self._kv = kv
        self._tripped: dict[str, float] = {}  # platform → trip timestamp
        self._probing: set[str] = set()       # platforms in half-open self-heal probe

    def _success_key(self, platform: str) -> str:
        return f"jd:drift:{platform}:ok"

    def _failure_key(self, platform: str) -> str:
        return f"jd:drift:{platform}:fail"

    async def record_success(self, platform: str) -> None:
        """Record a successful extraction for a platform.

        Self-healing: if the platform was in a half-open probe, a success fully
        heals the circuit — we reset the rolling counters so a handful of stale
        failures from before the outage don't immediately re-trip it.
        """
        if platform in self._probing:
            self._probing.discard(platform)
            try:
                await self._kv.delete(self._failure_key(platform))
                await self._kv.delete(self._success_key(platform))
            except Exception:
                pass
            logger.info("JD drift: circuit CLOSED for %s (self-healed after probe)", platform)
        try:
            await self._kv.incr(self._success_key(platform), ttl_seconds=_WINDOW_SECONDS)
        except Exception:
            pass

    async def record_failure(self, platform: str) -> None:
        """Record a failed extraction. May trip the circuit breaker.

        If the platform was in a half-open probe, a failure immediately re-trips
        the circuit (don't wait to re-accumulate the failure rate).
        """
        try:
            await self._kv.incr(self._failure_key(platform), ttl_seconds=_WINDOW_SECONDS)
        except Exception:
            pass

        if platform in self._probing:
            self._probing.discard(platform)
            self._tripped[platform] = time.time()
            logger.warning("JD drift: circuit RE-OPENED for %s (probe failed)", platform)
            return

        # Check if we should trip
        await self._evaluate(platform)

    async def is_healthy(self, platform: str) -> bool:
        """Check if a platform's circuit is closed (healthy).

        Returns False if the circuit is open (skip this adapter).
        """
        if platform not in self._tripped:
            return True

        # Check if enough time passed for half-open probe
        elapsed = time.time() - self._tripped[platform]
        if elapsed > _RESET_AFTER:
            # Half-open: allow one probe (self-heal confirmed by the next success)
            del self._tripped[platform]
            self._probing.add(platform)
            logger.info("JD drift: circuit half-open for %s (probing)", platform)
            return True

        return False

    async def stats(self, platform: str) -> dict:
        """Rolling success/failure counts + circuit state for a platform."""
        try:
            ok_raw = await self._kv.get(self._success_key(platform))
            fail_raw = await self._kv.get(self._failure_key(platform))
            ok = int(ok_raw) if ok_raw else 0
            fail = int(fail_raw) if fail_raw else 0
        except (TypeError, ValueError):
            ok, fail = 0, 0
        total = ok + fail
        if platform in self._tripped:
            state = "open"
        elif platform in self._probing:
            state = "half-open"
        else:
            state = "closed"
        return {
            "state": state,
            "success": ok,
            "failure": fail,
            "success_rate": (ok / total) if total else 1.0,
            "samples": total,
        }

    async def _evaluate(self, platform: str) -> None:
        """Evaluate failure rate and trip if threshold exceeded."""
        try:
            ok_raw = await self._kv.get(self._success_key(platform))
            fail_raw = await self._kv.get(self._failure_key(platform))
            ok = int(ok_raw) if ok_raw else 0
            fail = int(fail_raw) if fail_raw else 0
        except (TypeError, ValueError):
            return

        total = ok + fail
        if total < _MIN_SAMPLES:
            return

        failure_rate = fail / total
        if failure_rate > _TRIP_THRESHOLD:
            self._tripped[platform] = time.time()
            logger.warning(
                "JD drift: circuit OPEN for %s (failure rate %.0f%%, %d/%d)",
                platform, failure_rate * 100, fail, total,
            )

    async def get_status(self) -> dict[str, dict]:
        """Return current circuit status for monitoring/health endpoint."""
        status = {}
        for platform, trip_time in self._tripped.items():
            elapsed = time.time() - trip_time
            status[platform] = {
                "state": "open" if elapsed < _RESET_AFTER else "half-open",
                "tripped_seconds_ago": elapsed,
                "resets_in": max(0, _RESET_AFTER - elapsed),
            }
        return status

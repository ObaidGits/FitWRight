"""In-process auth metrics registry (Task 9.2, R16.1).

The design (`§Observability & operations`, R16.1) requires the auth surface to
emit metrics: login success/failure, signups, verification send/verify, reset
requests, OAuth outcomes *by reason*, lockouts, rate-limit denials, step-up
challenges, active-session count, and the **session-cache hit ratio**.

This module is a tiny, dependency-free, process-wide counter registry. It is a
sink, not a scraper: the auth flows call the ``record_*`` helpers, and an
operator (or a test, or the structured access log) reads :meth:`AuthMetrics.snapshot`.
Keeping it in-proc means it works identically on the free tier (no Prometheus
sidecar) and the premium tier; a real exporter can later read the same snapshot
without touching the call sites. Values are monotonic counters except the
cache-hit ratio, which is derived from its hit/miss counters on read.

It is deliberately not tied to any web framework so it is trivially unit-testable
and safe to call from middleware, services, and routers alike. Counter mutation
is guarded by a lock so concurrent workers in the same process do not race.
"""

from __future__ import annotations

import threading
from collections import defaultdict

__all__ = [
    "AuthMetrics",
    "get_metrics",
    "reset_metrics",
]


class AuthMetrics:
    """Process-wide monotonic counters for the auth surface (R16.1).

    Every counter is a non-negative integer keyed by a stable name. Labelled
    counters (OAuth failures *by reason*) live in their own name->count maps so a
    high-cardinality label can never collide with a scalar counter.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, int] = defaultdict(int)
        # Labelled counters: metric name -> {label: count}.
        self._labelled: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    # -- primitive mutators --------------------------------------------------

    def incr(self, name: str, amount: int = 1) -> None:
        """Increment a scalar counter (never goes negative below zero)."""
        with self._lock:
            self._counters[name] = max(0, self._counters[name] + amount)

    def incr_labelled(self, name: str, label: str, amount: int = 1) -> None:
        """Increment a labelled counter (e.g. ``oauth_failure`` by reason)."""
        with self._lock:
            bucket = self._labelled[name]
            bucket[label] = max(0, bucket.get(label, 0) + amount)

    # -- named auth events (R16.1) ------------------------------------------

    def record_login_success(self) -> None:
        self.incr("login_success")

    def record_login_failure(self) -> None:
        self.incr("login_failure")

    def record_signup(self) -> None:
        self.incr("signup")

    def record_verification_sent(self) -> None:
        self.incr("verification_sent")

    def record_verification_confirmed(self) -> None:
        self.incr("verification_confirmed")

    def record_reset_requested(self) -> None:
        self.incr("reset_requested")

    def record_reset_completed(self) -> None:
        self.incr("reset_completed")

    def record_lockout(self) -> None:
        self.incr("lockout")

    def record_rate_limited(self) -> None:
        self.incr("rate_limited")

    def record_captcha_required(self) -> None:
        self.incr("captcha_required")

    def record_step_up(self, *, success: bool) -> None:
        """One step-up (sudo) challenge, split by outcome."""
        self.incr("step_up_success" if success else "step_up_failure")

    def record_oauth_success(self) -> None:
        self.incr("oauth_success")

    def record_oauth_failure(self, reason: str) -> None:
        """One failed OAuth flow, labelled by reason (oauth-failure-by-reason)."""
        self.incr("oauth_failure")
        self.incr_labelled("oauth_failure_by_reason", reason or "unknown")

    def record_session_cache(self, *, hit: bool) -> None:
        """One session-resolution cache lookup, split hit/miss (for the ratio)."""
        self.incr("session_cache_hit" if hit else "session_cache_miss")

    # -- read ----------------------------------------------------------------

    @property
    def session_cache_hit_ratio(self) -> float:
        """Cache hit ratio in ``[0, 1]`` (0.0 when there have been no lookups)."""
        with self._lock:
            hits = self._counters.get("session_cache_hit", 0)
            misses = self._counters.get("session_cache_miss", 0)
        total = hits + misses
        return (hits / total) if total else 0.0

    def snapshot(self) -> dict[str, object]:
        """Return a JSON-serializable copy of all counters + the derived ratio."""
        with self._lock:
            counters = dict(self._counters)
            labelled = {name: dict(bucket) for name, bucket in self._labelled.items()}
        hits = counters.get("session_cache_hit", 0)
        misses = counters.get("session_cache_miss", 0)
        total = hits + misses
        counters["session_cache_hit_ratio"] = round((hits / total) if total else 0.0, 4)
        counters["oauth_failure_by_reason"] = labelled.get("oauth_failure_by_reason", {})
        return counters


# ---------------------------------------------------------------------------
# Process-wide instance
# ---------------------------------------------------------------------------

_metrics: AuthMetrics | None = None


def get_metrics() -> AuthMetrics:
    """Return the process-wide :class:`AuthMetrics` (built on first use)."""
    global _metrics
    if _metrics is None:
        _metrics = AuthMetrics()
    return _metrics


def reset_metrics() -> None:
    """Drop the cached instance (test helper)."""
    global _metrics
    _metrics = None

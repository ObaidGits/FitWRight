"""Bounded, in-process admin instrumentation (R12.1).

Request latency keeps lifetime sum/count plus the latest 512 observations for
an exact nearest-rank p95 within that bounded sample. Route classes are capped,
with excess classes coalesced into ``_other``, so both memory and label
cardinality stay bounded. Per-class failures count responses with status >= 400.
All values are current-process signals and every mutation/snapshot is protected
by one lock.
"""

from __future__ import annotations

import math
import threading
from collections import defaultdict, deque
from dataclasses import dataclass, field

__all__ = ["AdminMetrics", "get_admin_metrics", "reset_admin_metrics"]

_LATENCY_SAMPLE_LIMIT = 512
_MAX_ROUTE_CLASSES = 64
_OVERFLOW_ROUTE_CLASS = "_other"


@dataclass
class _RouteMetrics:
    total_ms: float = 0.0
    observation_count: int = 0
    failure_count: int = 0
    samples_ms: deque[float] = field(
        default_factory=lambda: deque(maxlen=_LATENCY_SAMPLE_LIMIT)
    )


class AdminMetrics:
    """Process-wide counters, bounded route metrics, and gauges."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, int] = defaultdict(int)
        self._actions: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self._latency: dict[str, _RouteMetrics] = {}
        self._gauges: dict[str, float] = {}

    def incr(self, name: str, amount: int = 1) -> None:
        with self._lock:
            self._counters[name] = max(0, self._counters[name] + amount)

    def record_action(self, action: str, result: str) -> None:
        """Record one admin mutation labelled by action and result."""
        with self._lock:
            self._actions[action][result] += 1

    def _bounded_route_class(self, route_class: str) -> str:
        route_class = str(route_class) or "root"
        if route_class in self._latency:
            return route_class
        # Reserve the final cardinality slot for all excess/unknown classes.
        if len(self._latency) < _MAX_ROUTE_CLASSES - 1:
            return route_class
        return _OVERFLOW_ROUTE_CLASS

    def record_request(self, route_class: str, status_code: int, duration_ms: float) -> None:
        """Record latency and status for one bounded admin route class."""
        duration = max(0.0, float(duration_ms))
        with self._lock:
            bounded_class = self._bounded_route_class(route_class)
            bucket = self._latency.setdefault(bounded_class, _RouteMetrics())
            bucket.total_ms += duration
            bucket.observation_count += 1
            bucket.samples_ms.append(duration)
            if status_code >= 400:
                bucket.failure_count += 1
            if status_code >= 500:
                self._counters["request_5xx"] += 1
            elif status_code >= 400:
                self._counters["request_4xx"] += 1
            else:
                self._counters["request_2xx"] += 1

    def record_authz_denied(self) -> None:
        self.incr("authz_denied")

    def record_cache(self, *, hit: bool) -> None:
        self.incr("dashboard_cache_hit" if hit else "dashboard_cache_miss")

    def set_gauge(self, name: str, value: float) -> None:
        with self._lock:
            self._gauges[name] = value

    def set_purge_backlog(self, count: int) -> None:
        self.set_gauge("purge_backlog", float(count))

    def set_dashboard_staleness(self, seconds: float) -> None:
        self.set_gauge("dashboard_staleness_seconds", float(seconds))

    def set_rollup_lag_days(self, days: float) -> None:
        self.set_gauge("rollup_lag_days", float(days))

    @property
    def dashboard_cache_hit_ratio(self) -> float:
        with self._lock:
            hits = self._counters.get("dashboard_cache_hit", 0)
            misses = self._counters.get("dashboard_cache_miss", 0)
        total = hits + misses
        return (hits / total) if total else 0.0

    def snapshot(self) -> dict[str, object]:
        """Return a consistent JSON-serializable current-process snapshot."""
        with self._lock:
            counters = dict(self._counters)
            actions = {action: dict(results) for action, results in self._actions.items()}
            latency: dict[str, dict[str, float | int]] = {}
            for route_class, bucket in self._latency.items():
                samples = sorted(bucket.samples_ms)
                rank = math.ceil(0.95 * len(samples))
                p95_ms = samples[rank - 1] if rank else 0.0
                latency[route_class] = {
                    "count": bucket.observation_count,  # backward-compatible alias
                    "observation_count": bucket.observation_count,
                    "failure_count": bucket.failure_count,
                    "avg_ms": round(bucket.total_ms / bucket.observation_count, 2),
                    "p95_ms": p95_ms,
                }
            gauges = dict(self._gauges)

        hits = counters.get("dashboard_cache_hit", 0)
        misses = counters.get("dashboard_cache_miss", 0)
        cache_observations = hits + misses
        counters["dashboard_cache_hit_ratio"] = round(
            (hits / cache_observations) if cache_observations else 0.0, 4
        )
        counters["dashboard_cache_observation_count"] = cache_observations
        return {
            "counters": counters,
            "admin_action_total": actions,
            "latency": latency,
            "gauges": gauges,
            "cache_observation_count": cache_observations,
        }


# ---------------------------------------------------------------------------
# Process-wide instance
# ---------------------------------------------------------------------------

_metrics: AdminMetrics | None = None


def get_admin_metrics() -> AdminMetrics:
    """Return the process-wide :class:`AdminMetrics` (built on first use)."""
    global _metrics
    if _metrics is None:
        _metrics = AdminMetrics()
    return _metrics


# ---------------------------------------------------------------------------
# Middleware - admin API latency + status-bucket metrics (R12.1)
# ---------------------------------------------------------------------------


def _route_class(path: str) -> str:
    """Coarse route-class for the latency histogram (bounded cardinality)."""
    rest = path.split("/api/v1/admin", 1)[-1].strip("/")
    if not rest:
        return "root"
    head = rest.split("/", 1)[0].split("?", 1)[0]
    if head == "users" and "/" in rest:
        return "users_item"
    return head or "root"


class AdminMetricsMiddleware:
    """Pure-ASGI middleware recording admin API latency + status buckets.

    Scoped to ``/api/v1/admin`` paths so it adds zero overhead to the rest of
    the app. Records per-route-class latency + a 2xx/4xx/5xx status bucket
    (admin error rate, R12.1). Implemented as raw ASGI (not BaseHTTPMiddleware)
    to observe the real final status code without buffering the response.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        import time

        if scope.get("type") != "http" or "/api/v1/admin" not in scope.get("path", ""):
            await self.app(scope, receive, send)
            return

        start = time.perf_counter()
        status_holder = {"code": 500}

        async def _send(message):
            if message.get("type") == "http.response.start":
                status_holder["code"] = message.get("status", 500)
            await send(message)

        try:
            await self.app(scope, receive, _send)
        finally:
            duration_ms = (time.perf_counter() - start) * 1000
            try:
                get_admin_metrics().record_request(
                    _route_class(scope.get("path", "")), status_holder["code"], duration_ms
                )
            except Exception:  # pragma: no cover - metrics must never break a request
                pass


def reset_admin_metrics() -> None:
    """Drop the cached instance (test helper)."""
    global _metrics
    _metrics = None

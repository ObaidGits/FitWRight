"""In-process admin metrics registry (R12.1).

Mirrors :mod:`app.auth.metrics`: a tiny, dependency-free, process-wide counter
sink the admin services/router call, readable via :meth:`AdminMetrics.snapshot`
(exposed through the internal metrics endpoint). It carries exactly the signals
the design's "Observability & operations" section (R12.1) requires:

- ``admin_action_total{action,result}`` - every admin mutation, labelled by
  action (disable/enable/role_change/delete/restore/bulk_disable/purge) and
  result (ok/no_op/denied/error/last_active_admin).
- admin API latency (sum/count per route-class -> derived average) and error rate
  (``admin_request_total`` split by 2xx/4xx/5xx).
- dashboard cache hit ratio + staleness age, rollup lag, purge backlog gauge,
  and the ``authz.denied`` counter (compromised-admin / scraping signal).

Gauges (purge backlog, staleness, rollup lag) are last-write-wins; everything
else is a monotonic counter. Mutation is lock-guarded so concurrent workers in
one process do not race.
"""

from __future__ import annotations

import threading
from collections import defaultdict

__all__ = ["AdminMetrics", "get_admin_metrics", "reset_admin_metrics"]


class AdminMetrics:
    """Process-wide counters + gauges for the admin surface (R12.1)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, int] = defaultdict(int)
        # action -> result -> count
        self._actions: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        # route-class -> [sum_ms, count]
        self._latency: dict[str, list[float]] = defaultdict(lambda: [0.0, 0])
        self._gauges: dict[str, float] = {}

    # -- mutators ------------------------------------------------------------

    def incr(self, name: str, amount: int = 1) -> None:
        with self._lock:
            self._counters[name] = max(0, self._counters[name] + amount)

    def record_action(self, action: str, result: str) -> None:
        """One admin mutation, labelled by ``action`` and ``result`` (R12.1)."""
        with self._lock:
            self._actions[action][result] += 1

    def record_request(self, route_class: str, status_code: int, duration_ms: float) -> None:
        """One admin API call: latency (per route-class) + status bucket."""
        with self._lock:
            bucket = self._latency[route_class]
            bucket[0] += max(0.0, duration_ms)
            bucket[1] += 1
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

    # -- read ----------------------------------------------------------------

    @property
    def dashboard_cache_hit_ratio(self) -> float:
        with self._lock:
            hits = self._counters.get("dashboard_cache_hit", 0)
            misses = self._counters.get("dashboard_cache_miss", 0)
        total = hits + misses
        return (hits / total) if total else 0.0

    def snapshot(self) -> dict[str, object]:
        """JSON-serializable copy of all counters, action labels, and gauges."""
        with self._lock:
            counters = dict(self._counters)
            actions = {a: dict(r) for a, r in self._actions.items()}
            latency = {
                rc: {
                    "count": int(v[1]),
                    "avg_ms": round(v[0] / v[1], 2) if v[1] else 0.0,
                }
                for rc, v in self._latency.items()
            }
            gauges = dict(self._gauges)
        hits = counters.get("dashboard_cache_hit", 0)
        misses = counters.get("dashboard_cache_miss", 0)
        total = hits + misses
        counters["dashboard_cache_hit_ratio"] = round((hits / total) if total else 0.0, 4)
        return {
            "counters": counters,
            "admin_action_total": actions,
            "latency": latency,
            "gauges": gauges,
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

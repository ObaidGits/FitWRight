"""P3 Productivity — cross-cutting observability (design §Observability, R17.5).

In-process counters for the productivity subsystem (search / notifications /
scheduler / JD-fetch / avatar), exposed via the internal ``/metrics`` endpoint
alongside live DB-computed gauges (outbox backlog + DLQ depth). Per-worker
counters are an accepted operational boundary (scrape every worker); the
operationally-critical backlog/DLQ gauges are computed live so they are
worker-independent.
"""

from app.productivity.metrics import get_productivity_metrics, reset_productivity_metrics

__all__ = ["get_productivity_metrics", "reset_productivity_metrics"]

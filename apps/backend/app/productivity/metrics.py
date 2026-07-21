"""In-process productivity metrics (design §Observability, R17.5).

Thread-safe-enough for asyncio (single-threaded event loop) counters covering
the signals the design's alerts key off:
- ``jd_fetch_total{result}`` + ``jd_blocked_ssrf_total`` (SSRF probe signal);
- ``avatar_upload_total{result}``;
- ``notification_created_total`` / ``notification_emailed_total`` /
  ``notification_deduped_total`` (duplicate-delivery *prevented* - the
  exactly-once signal, stays the counterpart of double_fire=0);
- ``scheduler_reminders_fired_total`` / ``scheduler_interview_leads_fired_total``;
- ``ai_cleanup_total`` (JD LLM cleanup - cost-aware usage tracking, R15).

Live gauges (outbox backlog + DLQ depth) are computed from the DB at scrape time
in the internal ``/metrics`` endpoint, not held here.
"""

from __future__ import annotations

from collections import defaultdict

__all__ = ["ProductivityMetrics", "get_productivity_metrics", "reset_productivity_metrics"]


class ProductivityMetrics:
    def __init__(self) -> None:
        self._counters: dict[str, int] = defaultdict(int)

    def incr(self, name: str, amount: int = 1) -> None:
        self._counters[name] += amount

    # Convenience wrappers (documented, typo-proof call sites).
    def jd_fetch(self, result: str) -> None:
        self.incr(f"jd_fetch_total.{result}")

    def jd_blocked_ssrf(self) -> None:
        self.incr("jd_blocked_ssrf_total")

    def avatar_upload(self, result: str) -> None:
        self.incr(f"avatar_upload_total.{result}")

    def notification_created(self) -> None:
        self.incr("notification_created_total")

    def notification_emailed(self) -> None:
        self.incr("notification_emailed_total")

    def notification_deduped(self) -> None:
        self.incr("notification_deduped_total")

    def reminders_fired(self, n: int = 1) -> None:
        self.incr("scheduler_reminders_fired_total", n)

    def interview_leads_fired(self, n: int = 1) -> None:
        self.incr("scheduler_interview_leads_fired_total", n)

    def ai_cleanup(self, result: str) -> None:
        self.incr(f"ai_cleanup_total.{result}")

    # --- JD v2 cost + pipeline observability (§25, §34) ---
    def jd_cost(self, microdollars: int) -> None:
        """Accumulate estimated extraction cost (microdollars = millionths of $)."""
        if microdollars > 0:
            self.incr("jd_cost_microdollars_total", microdollars)

    def jd_extract(self, source: str) -> None:
        """Count a successful extraction by source (platform_api/json_ld/dom/...)."""
        self.incr(f"jd_extract_total.{source}")

    def jd_render(self, result: str) -> None:
        """Count a Playwright render attempt by result (ok/failed/skipped)."""
        self.incr(f"jd_render_total.{result}")

    def jd_pdf(self, result: str) -> None:
        """Count a PDF extraction attempt by result (ok/failed/unsupported)."""
        self.incr(f"jd_pdf_total.{result}")

    def jd_budget_exceeded(self, scope: str) -> None:
        """Count a budget cap hit (scope=user|global)."""
        self.incr(f"jd_budget_exceeded_total.{scope}")

    def jd_robots_blocked(self) -> None:
        self.incr("jd_robots_blocked_total")

    def jd_near_duplicate(self) -> None:
        self.incr("jd_near_duplicate_total")

    def snapshot(self) -> dict[str, int]:
        return dict(self._counters)


_metrics: ProductivityMetrics | None = None


def get_productivity_metrics() -> ProductivityMetrics:
    global _metrics
    if _metrics is None:
        _metrics = ProductivityMetrics()
    return _metrics


def reset_productivity_metrics() -> None:
    global _metrics
    _metrics = ProductivityMetrics()

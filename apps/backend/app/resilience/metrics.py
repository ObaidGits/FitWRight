"""In-process P4 resilience metrics (design §Observability).

Counters/gauges backing the design's alerts:
- ``stream_first_token_ms`` (histogram-ish: sum+count for a mean), ``stream_*_total``,
  ``stream_active_gauge`` (per snapshot), ``stream_reaped_total`` (abandoned-stream
  reaper signal), ``stream_tokens_total`` (cost accounting, R1.7);
- ``autosave_conflict_total`` (409 rate - concurrent-edit signal);
- ``autosave_idempotent_replay_total`` (dedupe working).

Gauges that are naturally point-in-time (active streams) are tracked with an
in/out counter pair so a scrape can report the current value. Single-threaded
asyncio, so plain ints are safe.
"""

from __future__ import annotations

from collections import defaultdict

__all__ = ["ResilienceMetrics", "get_resilience_metrics", "reset_resilience_metrics"]


class ResilienceMetrics:
    def __init__(self) -> None:
        self._counters: dict[str, int] = defaultdict(int)
        self._first_token_ms_sum: float = 0.0
        self._first_token_ms_count: int = 0
        self._active_streams: int = 0

    def incr(self, name: str, amount: int = 1) -> None:
        self._counters[name] += amount

    # -- streaming ----------------------------------------------------------
    def stream_started(self) -> None:
        self.incr("stream_started_total")
        self._active_streams += 1

    def stream_ended(self) -> None:
        self.incr("stream_ended_total")
        if self._active_streams > 0:
            self._active_streams -= 1

    def stream_cancelled(self) -> None:
        self.incr("stream_cancel_total")

    def stream_reaped(self) -> None:
        self.incr("stream_reaped_total")

    def stream_fallback(self) -> None:
        self.incr("stream_fallback_total")

    def stream_rejected_cap(self) -> None:
        self.incr("stream_rejected_cap_total")

    def stream_error(self) -> None:
        self.incr("stream_error_total")

    def record_first_token_ms(self, ms: float) -> None:
        self._first_token_ms_sum += ms
        self._first_token_ms_count += 1

    def record_tokens(self, total_tokens: int) -> None:
        self.incr("stream_tokens_total", max(0, int(total_tokens)))

    # -- autosave / conflict ------------------------------------------------
    def autosave_conflict(self) -> None:
        self.incr("autosave_conflict_total")

    def autosave_idempotent_replay(self) -> None:
        self.incr("autosave_idempotent_replay_total")

    def snapshot(self) -> dict[str, float]:
        snap: dict[str, float] = dict(self._counters)
        snap["stream_active_gauge"] = self._active_streams
        snap["stream_first_token_ms_avg"] = (
            self._first_token_ms_sum / self._first_token_ms_count
            if self._first_token_ms_count
            else 0.0
        )
        return snap


_metrics: ResilienceMetrics | None = None


def get_resilience_metrics() -> ResilienceMetrics:
    global _metrics
    if _metrics is None:
        _metrics = ResilienceMetrics()
    return _metrics


def reset_resilience_metrics() -> None:
    global _metrics
    _metrics = None

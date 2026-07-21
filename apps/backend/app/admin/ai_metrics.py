"""In-process AI-call accumulators - the ``AiMetricsService`` (Req 4.1).

Mirrors :class:`app.admin.metrics.AdminMetrics`: a tiny, process-wide,
lock-guarded counter sink that the LLM call site updates via
:meth:`AiMetricsService.record_call` after every provider round-trip. The
``AiFlushStep`` (Task 9.2) reads :meth:`snapshot` and, on a successful durable
persist, calls :meth:`reset`.

**Allowlist (Req 4, design philosophy §4) - EXACTLY these signals, nothing more:**
total calls, success, failure, timeouts, retries, per-provider call counts,
summed tokens, and summed latency (ms). Cost is derived separately by the
``CostMonitor`` at flush time (Task 9.3) and is **not** accumulated here.

**Explicitly rejected - never accepted, accumulated, or stored here:**
temperature, prompt/completion length, model version, system prompt, tool
calls, reasoning tokens, and conversation/request ids. There are deliberately
no parameters or fields for any of these.

**Bounded cardinality (Req 20, Property 8).** Per-provider counts are keyed by
the closed :class:`~app.admin.metric_registry.AiProvider` enum. The per-provider
dict is fixed at construction - exactly one counter per enum member - so no key
is ever composed from a runtime value. A provider *string* handed to
:meth:`record_call` is mapped to an ``AiProvider`` via a static alias table; an
**unknown** provider is handled safely by still counting the global accumulators
while **skipping** the per-provider counter (never creating a runtime key).

This module depends only on :mod:`app.admin.metric_registry` and the stdlib -
never on another Domain_Metrics_Service (Req 19.2/19.3/19.5).
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone

from app.admin.metric_registry import (
    AI_CALLS,
    AI_FAILURE,
    AI_LATENCY_MS_SUM,
    AI_RETRIES,
    AI_SUCCESS,
    AI_TIMEOUTS,
    AI_TOKENS_SUM,
    AiProvider,
    ai_calls_key,
)
from app.admin.schemas import AiAnalytics, ProviderCount, SeriesPoint

logger = logging.getLogger(__name__)

__all__ = [
    "AiMetricsService",
    "get_ai_metrics_service",
    "reset_ai_metrics_service",
    "provider_to_enum",
    "AiFlushStep",
    "AI_FLUSH_STEP",
]


# ---------------------------------------------------------------------------
# provider string -> closed AiProvider enum (static; no runtime-composed keys)
# ---------------------------------------------------------------------------
#
# The LLM layer (``app/llm.py``) uses provider strings that are a superset of
# the closed AiProvider set: it also supports ``openrouter``, ``deepseek`` and
# ``groq`` (OpenAI-shaped aggregators) and spells the compatible provider
# ``openai_compatible`` (the enum value is ``openai_compat``). This table maps
# every *known* spelling to its enum member. Anything not present is treated as
# an unknown provider (see :func:`provider_to_enum`).
_PROVIDER_ALIASES: dict[str, AiProvider] = {
    "openai": AiProvider.OPENAI,
    "gemini": AiProvider.GEMINI,
    "anthropic": AiProvider.ANTHROPIC,
    "ollama": AiProvider.OLLAMA,
    "openai_compat": AiProvider.OPENAI_COMPAT,
    "openai_compatible": AiProvider.OPENAI_COMPAT,
}


def provider_to_enum(provider: "str | AiProvider | None") -> AiProvider | None:
    """Map a provider (str or enum) to a closed :class:`AiProvider`.

    Returns the matching enum member, or ``None`` for an unknown/blank provider.
    A ``None`` result is the documented signal to count the global accumulators
    but **skip** the per-provider counter - never to invent a runtime key.
    """
    if isinstance(provider, AiProvider):
        return provider
    if not provider:
        return None
    return _PROVIDER_ALIASES.get(str(provider).strip().lower())


class AiMetricsService:
    """Process-wide, lock-guarded AI-call accumulators (Req 4.1).

    All counters are monotonic within a flush window; :meth:`reset` zeroes them
    (called by ``AiFlushStep`` only after a successful durable persist).
    """

    def __init__(self, *, metric_store=None, cost_monitor=None) -> None:
        self._lock = threading.Lock()
        self._calls = 0
        self._success = 0
        self._failure = 0
        self._timeouts = 0
        self._retries = 0
        self._tokens_sum = 0
        self._latency_ms_sum = 0.0
        # Fixed per-provider counters: exactly one per closed enum member. This
        # dict's keyset never changes at runtime (Req 20 / Property 8).
        self._by_provider: dict[AiProvider, int] = {p: 0 for p in AiProvider}
        # Optional injected read collaborators for :meth:`analytics` (tests);
        # otherwise the process-wide singletons are resolved lazily. The service
        # depends ONLY on the shared MetricStore + Metric_Registry + schemas and
        # the CostMonitor (in ``app.jd.monitoring`` - not a Domain_Metrics_Service),
        # never on another domain service (import-graph guard, Req 19.2/19.3/19.5).
        self._metric_store = metric_store
        self._cost_monitor = cost_monitor

    # -- injected-collaborator accessors (lazy singletons in production) -----

    def _get_metric_store(self):
        if self._metric_store is not None:
            return self._metric_store
        from app.admin.metric_store import get_metric_store

        return get_metric_store()

    def _get_cost_monitor(self):
        if self._cost_monitor is not None:
            return self._cost_monitor
        # CostMonitor is a plain KV-backed helper (not a Domain_Metrics_Service),
        # so importing it here is allowed by the import-graph fitness test.
        from app.auth.runtime import get_kvstore
        from app.jd.monitoring.cost import CostMonitor

        return CostMonitor(get_kvstore())

    # -- mutation ------------------------------------------------------------

    def record_call(
        self,
        provider: "str | AiProvider | None",
        ok: bool,
        timed_out: bool = False,
        retried: "bool | int" = False,
        tokens: int = 0,
        latency_ms: float = 0.0,
    ) -> None:
        """Record one AI provider round-trip (allowlisted signals only).

        Args:
            provider: The configured provider (str or :class:`AiProvider`).
                Unknown/blank -> global counters still update, per-provider
                counter is skipped (no runtime key created).
            ok: Whether the call ultimately succeeded.
            timed_out: Whether the failure was a timeout.
            retried: Retry indicator. A ``bool`` contributes +1 when ``True``;
                an ``int`` contributes its (non-negative) value. Best-effort:
                the LiteLLM Router's own transport retries are not observable
                here, so this reflects only app-level retries.
            tokens: Aggregate ``usage.total_tokens`` for the call (never the
                prompt/completion breakdown - that is a rejected field).
            latency_ms: Wall-clock duration of the call in milliseconds.
        """
        mapped = provider_to_enum(provider)

        # Coerce numeric inputs defensively so a bad value can never raise here
        # (record_call must never break an LLM call).
        try:
            tok = max(0, int(tokens or 0))
        except (TypeError, ValueError):
            tok = 0
        try:
            lat = max(0.0, float(latency_ms or 0.0))
        except (TypeError, ValueError):
            lat = 0.0

        # retries += (count, or +1 if bool). ``bool`` is a subclass of ``int``,
        # so check it first.
        if isinstance(retried, bool):
            retry_inc = 1 if retried else 0
        else:
            try:
                retry_inc = max(0, int(retried))
            except (TypeError, ValueError):
                retry_inc = 0

        with self._lock:
            self._calls += 1
            if ok:
                self._success += 1
            else:
                self._failure += 1
            if timed_out:
                self._timeouts += 1
            self._retries += retry_inc
            self._tokens_sum += tok
            self._latency_ms_sum += lat
            if mapped is not None:
                self._by_provider[mapped] += 1
            # Unknown provider: intentionally NOT counted per-provider (the
            # global call is still counted above). Documented in module header.

    # -- read / lifecycle ----------------------------------------------------

    def snapshot(self) -> dict[str, object]:
        """Return a copy of the current accumulator values.

        ``by_provider`` is keyed by the :class:`AiProvider` enum member so the
        ``AiFlushStep`` (Task 9.2) can map each directly to its static
        per-provider Metric_Key via ``metric_registry.ai_calls_key``.
        """
        with self._lock:
            return {
                "calls": self._calls,
                "success": self._success,
                "failure": self._failure,
                "timeouts": self._timeouts,
                "retries": self._retries,
                "tokens_sum": self._tokens_sum,
                "latency_ms_sum": self._latency_ms_sum,
                "by_provider": dict(self._by_provider),
            }

    def reset(self) -> None:
        """Zero every accumulator (the design's success-path clear for Req 4.2).

        Retained as the specified clear-to-zero primitive. ``AiFlushStep`` uses
        :meth:`subtract` instead so increments that arrive *during* the flush are
        not silently dropped; :meth:`subtract` of the flushed snapshot is exactly
        equivalent to :meth:`reset` when there is no concurrent activity.
        """
        with self._lock:
            self._calls = 0
            self._success = 0
            self._failure = 0
            self._timeouts = 0
            self._retries = 0
            self._tokens_sum = 0
            self._latency_ms_sum = 0.0
            self._by_provider = {p: 0 for p in AiProvider}

    def subtract(self, values: dict[str, object]) -> None:
        """Subtract already-persisted amounts from each accumulator (Req 4.2).

        Called by ``AiFlushStep`` after a durable persist to *consume* exactly the
        amounts written to ``metrics_daily``. Consuming (rather than resetting to
        zero) preserves two things that a blind reset would lose:

        - **concurrent increments** that arrived between the flush snapshot and
          this call (only the snapshotted amount is removed; newer calls remain);
        - the **sub-integer latency remainder** - the step persists the truncated
          integer millisecond sum, so consuming that same integer leaves the
          fractional millisecond carry in the accumulator for the next flush.

        On a fully successful flush with no concurrent activity this is identical
        to :meth:`reset`. On a partial flush the step consumes only the keys that
        persisted, so the un-persisted counts are retained for the next run (no
        double count, no loss). Every field floors at 0 defensively; a missing
        field subtracts nothing. ``by_provider`` is keyed by :class:`AiProvider`.
        """
        by_provider = values.get("by_provider") or {}

        def _i(name: str) -> int:
            try:
                return max(0, int(values.get(name, 0) or 0))
            except (TypeError, ValueError):
                return 0

        try:
            lat = max(0.0, float(values.get("latency_ms_sum", 0.0) or 0.0))
        except (TypeError, ValueError):
            lat = 0.0

        with self._lock:
            self._calls = max(0, self._calls - _i("calls"))
            self._success = max(0, self._success - _i("success"))
            self._failure = max(0, self._failure - _i("failure"))
            self._timeouts = max(0, self._timeouts - _i("timeouts"))
            self._retries = max(0, self._retries - _i("retries"))
            self._tokens_sum = max(0, self._tokens_sum - _i("tokens_sum"))
            self._latency_ms_sum = max(0.0, self._latency_ms_sum - lat)
            for provider, count in by_provider.items():
                if provider in self._by_provider:
                    try:
                        dec = max(0, int(count or 0))
                    except (TypeError, ValueError):
                        dec = 0
                    self._by_provider[provider] = max(
                        0, self._by_provider[provider] - dec
                    )

    # -- analytics (read model) ---------------------------------------------

    async def analytics(self, window: int) -> AiAnalytics:
        """Return the AI Analytics read model for the trailing ``window`` days.

        Assembles the allowlisted AI aggregates (Req 4.3/4.5/4.6/4.7) from the
        durable ``AI_*`` ``metrics_daily`` keys via the shared ``MetricStore``,
        plus the current in-process accumulator so today's not-yet-flushed
        activity is included ("today is live" - matches the usage-series partial
        current-day convention and keeps the dashboard fresh). ``window`` is
        assumed to be a sane int (the endpoint validates 1-365; default 30).

        **O(1) read (Req 4.9).** Only a bounded, fixed number of indexed
        ``(metric, day)`` reads run: one ``MetricStore.sum`` per durable ``AI_*``
        key (7) + one per provider (5) + one bounded ``series`` for the daily
        chart. No row scan and no cost that grows with users/rows.

        **Rate invariant (Req 4.6/4.7).** ``successRate`` is ``successes/total``
        rounded to 4dp and ``failureRate`` is the 4dp complement
        (``round(1.0 - successRate, 4)``), so the two ALWAYS sum to exactly 1.0
        (in float) when ``total > 0``; both are 0.0 when ``total == 0``.

        **Cost (Req 4.5).** ``estimatedCostDollars`` = the best-available KVStore
        microdollar counter (``CostMonitor.global_spent``) truncated by integer
        division by 1,000,000. See the limitation note below.
        """
        store = self._get_metric_store()
        now = datetime.now(timezone.utc)
        day_to = now.strftime("%Y-%m-%d")
        day_from = (now - timedelta(days=max(1, int(window)) - 1)).strftime("%Y-%m-%d")

        # Live current-day snapshot: today's counts have not been flushed to
        # metrics_daily yet, so add them on top of the durable window sums so the
        # dashboard reflects today's activity ("today is live"). ``snapshot`` is
        # the accumulator "since last flush" ≈ today's not-yet-persisted counts.
        live = self.snapshot()
        live_by_provider = live.get("by_provider", {}) or {}

        total_calls = await store.sum([AI_CALLS], day_from, day_to) + int(live["calls"])
        successes = await store.sum([AI_SUCCESS], day_from, day_to) + int(live["success"])
        failures = await store.sum([AI_FAILURE], day_from, day_to) + int(live["failure"])
        timeouts = await store.sum([AI_TIMEOUTS], day_from, day_to) + int(live["timeouts"])
        retries = await store.sum([AI_RETRIES], day_from, day_to) + int(live["retries"])
        tokens_sum = await store.sum([AI_TOKENS_SUM], day_from, day_to) + int(live["tokens_sum"])
        latency_ms_sum = (
            await store.sum([AI_LATENCY_MS_SUM], day_from, day_to)
            + float(live["latency_ms_sum"])
        )

        # Rates - complement approach guarantees success + failure == 1.0 at 4dp
        # when total > 0 (rounding both independently could drift to 0.9999/1.0001).
        if total_calls > 0:
            success_rate = round(successes / total_calls, 4)
            failure_rate = round(1.0 - success_rate, 4)
            avg_latency_ms = round(latency_ms_sum / total_calls, 4)
            avg_units_per_call = round(tokens_sum / total_calls, 4)
        else:
            success_rate = 0.0
            failure_rate = 0.0
            avg_latency_ms = 0.0
            avg_units_per_call = 0.0

        # Per-provider breakdown: all five closed providers are returned (even
        # zero-count ones) for a stable, fixed-shape table across windows.
        providers: list[ProviderCount] = []
        for provider in AiProvider:
            calls = await store.sum([ai_calls_key(provider)], day_from, day_to) + int(
                live_by_provider.get(provider, 0)
            )
            providers.append(ProviderCount(provider=provider.value, calls=calls))

        # Estimated cost (Req 4.5). LIMITATION: the only durable KVStore
        # microdollar counter today is CostMonitor's rolling one-hour global
        # counter, scoped to the JD extraction pipeline - there is NO durable
        # arbitrary-window AI-cost total (adding one would be scope creep beyond
        # the metric registry). We therefore truncate the best-available signal;
        # the value is an operational estimate / "ready for future billing", not
        # a precise windowed AI spend. Fails soft to 0 on any read error.
        try:
            microdollars = await self._get_cost_monitor().global_spent()
        except Exception:
            logger.debug("AI analytics cost read failed", exc_info=True)
            microdollars = 0
        estimated_cost_dollars = int(max(0, int(microdollars)) // 1_000_000)

        # Daily AI_CALLS series for the chart; the trailing window's last point is
        # today, so fold in the live accumulator there to stay consistent.
        raw_series = await store.series(AI_CALLS, max(1, int(window)))
        last_idx = len(raw_series) - 1
        daily: list[SeriesPoint] = []
        for i, (day, value) in enumerate(raw_series):
            v = value + int(live["calls"]) if i == last_idx else value
            daily.append(SeriesPoint(date=day, value=v))

        return AiAnalytics(
            window=int(window),
            totalCalls=total_calls,
            successRate=success_rate,
            failureRate=failure_rate,
            avgLatencyMs=avg_latency_ms,
            avgUnitsPerCall=avg_units_per_call,
            timeouts=timeouts,
            retries=retries,
            estimatedCostDollars=estimated_cost_dollars,
            providers=providers,
            daily=daily,
            computedAt=now.isoformat(),
        )


# ---------------------------------------------------------------------------
# Process-wide singleton (mirrors app.admin.metrics.get_admin_metrics)
# ---------------------------------------------------------------------------

_service: AiMetricsService | None = None


def get_ai_metrics_service() -> AiMetricsService:
    """Return the process-wide :class:`AiMetricsService` (built on first use)."""
    global _service
    if _service is None:
        _service = AiMetricsService()
    return _service


def reset_ai_metrics_service() -> None:
    """Drop the cached instance (test helper)."""
    global _service
    _service = None


# ---------------------------------------------------------------------------
# AiFlushStep - durable flush of the in-process AI accumulators (Req 4.2 / 4.8)
# ---------------------------------------------------------------------------
#
# Unlike ``AdminMetrics`` (cumulative-since-process-start, flushed via a running
# per-worker delta baseline), the :class:`AiMetricsService` accumulates only
# *since the last successful flush* and supports :meth:`AiMetricsService.subtract`
# / :meth:`AiMetricsService.reset`. So the flush is a straight
# **snapshot -> add -> consume** cycle rather than a delta computation:
#
# 1. Take ONE snapshot of the current accumulators.
# 2. UPSERT-add each non-zero amount to the *current* accumulating UTC day
#    (``today``) via ``MetricStore.add`` (an atomic in-UPSERT increment, so
#    multiple workers sum into the single ``(day, key)`` row - no per-worker rows).
# 3. On a fully successful persist, ``subtract`` exactly the persisted amounts
#    from the accumulators (Req 4.2 "reset ... upon successful persistence" -
#    refined to a consume so concurrent increments during the flush are kept).
#
# Failure semantics (Req 4.8 - "retain the accumulators without resetting ...
# surface an error"): each key's ``add`` is isolated. A key whose ``add`` raises
# is NOT consumed (its count is retained for the next run) and is collected; the
# remaining keys are still attempted. The step then ``subtract``s ONLY the keys
# that durably persisted and returns a failed ``StepResult`` naming the failed
# keys. This is a strict, no-double-count refinement of "retain": no count is ever
# lost, and a count is never both persisted and left in the accumulator (which
# would double-count on the next run). If EVERY key fails, nothing is consumed and
# the accumulators are fully retained - matching the literal requirement.
#
# Day/idempotency: like ``MetricsFlushStep`` the amounts are added to ``today``
# (the accumulating day), never the just-closed ``day`` the pipeline passes, so a
# re-run never rewrites a closed day (the counts for a day were all added while it
# was current). ``AI_CALLS`` + the five per-provider ``AI_CALLS_*`` keys are owned
# EXCLUSIVELY here (``MetricsFlushStep`` intentionally does not flush them), so no
# AI key is written by two steps.

# Durable AI Metric_Key -> AiMetricsService.snapshot() scalar field name. The
# amount added for each is the integer of the snapshot value (the float
# ``latency_ms_sum`` is truncated to whole milliseconds; the fractional remainder
# is preserved in the accumulator by consuming only the truncated integer).
_AI_FLUSH_SCALARS: tuple[tuple[str, str], ...] = (
    (AI_CALLS, "calls"),
    (AI_SUCCESS, "success"),
    (AI_FAILURE, "failure"),
    (AI_TIMEOUTS, "timeouts"),
    (AI_RETRIES, "retries"),
    (AI_TOKENS_SUM, "tokens_sum"),
    (AI_LATENCY_MS_SUM, "latency_ms_sum"),
)


def _today() -> str:
    """Current UTC calendar day as ``YYYY-MM-DD`` (the accumulating day)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class AiFlushStep:
    """Rollup_Step persisting the in-process AI accumulators (Req 4.2 / 4.8).

    Independent, idempotent per closed UTC day (only ``today`` is ever written),
    resumable (an un-persisted key retries next run), and failure-isolated per
    key. See the module-level notes above for the snapshot->add->consume reasoning
    and the AI-key single-ownership decision.
    """

    name = "ai_flush"

    def __init__(self, *, metric_store=None, service: "AiMetricsService | None" = None) -> None:
        # Optional injected collaborators (tests); otherwise the process-wide
        # singletons resolved lazily at run time.
        self._store = metric_store
        self._service = service

    def _metric_store(self):
        if self._store is not None:
            return self._store
        # Lazy import keeps the module import-light and avoids binding the store
        # at import time (mirrors MetricsFlushStep).
        from app.admin.metric_store import get_metric_store

        return get_metric_store()

    def _ai_service(self) -> "AiMetricsService":
        return self._service if self._service is not None else get_ai_metrics_service()

    async def run(self, day: str) -> "StepResult":  # noqa: ARG002 - see day note above
        # Lazy import breaks the load-time cycle: ``rollup_pipeline`` imports this
        # module to assemble PIPELINE, so we must not import it at module top.
        from app.admin.rollup_pipeline import StepResult

        store = self._metric_store()
        service = self._ai_service()
        today = _today()  # accumulating day (NOT the passed just-closed day)

        snapshot = service.snapshot()
        by_provider = snapshot.get("by_provider", {}) or {}

        # ``consumed`` records exactly what durably persisted; only these amounts
        # are subtracted from the accumulators (so failed keys are retained and no
        # count is ever both persisted and retained).
        consumed: dict[str, object] = {
            "calls": 0,
            "success": 0,
            "failure": 0,
            "timeouts": 0,
            "retries": 0,
            "tokens_sum": 0,
            "latency_ms_sum": 0,
            "by_provider": {},
        }
        failed_keys: list[str] = []

        # -- scalar AI_* keys --------------------------------------------------
        for metric_key, field in _AI_FLUSH_SCALARS:
            try:
                amount = int(snapshot.get(field, 0) or 0)  # latency float -> truncated int
            except (TypeError, ValueError):
                amount = 0
            if amount <= 0:
                continue
            try:
                await store.add(today, metric_key, amount)
            except Exception:  # per-key failure isolation (Req 4.8)
                logger.exception("AiFlushStep failed to flush %s for %s", metric_key, today)
                failed_keys.append(metric_key)
                continue
            consumed[field] = amount

        # -- the five static per-provider AI_CALLS_* keys ----------------------
        consumed_providers: dict[AiProvider, int] = {}
        for provider, count in by_provider.items():
            try:
                amount = int(count or 0)
            except (TypeError, ValueError):
                amount = 0
            if amount <= 0:
                continue
            metric_key = ai_calls_key(provider)
            try:
                await store.add(today, metric_key, amount)
            except Exception:  # per-key failure isolation (Req 4.8)
                logger.exception("AiFlushStep failed to flush %s for %s", metric_key, today)
                failed_keys.append(metric_key)
                continue
            consumed_providers[provider] = amount
        consumed["by_provider"] = consumed_providers

        # Consume exactly the persisted amounts. On a fully clean flush with no
        # concurrent activity this zeroes the accumulators (Req 4.2); on a partial
        # failure only the persisted keys are removed and the rest are retained
        # (Req 4.8) - a strict, no-double-count refinement of "retain".
        service.subtract(consumed)

        if failed_keys:
            return StepResult.failure(
                self.name, f"ai flush failed for keys: {', '.join(failed_keys)}"
            )
        return StepResult.success(self.name)


# Process-wide instance slotted into PIPELINE by ``rollup_pipeline`` (after the
# MetricsFlushStep, before the prune). Single-flighted by the Rollup_Job's
# KVStore lock, so the shared accumulators are driven by one run at a time.
AI_FLUSH_STEP = AiFlushStep()

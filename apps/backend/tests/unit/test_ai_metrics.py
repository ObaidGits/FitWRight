"""Unit tests for the ``AiMetricsService`` + ``AiFlushStep`` (Task 9.5).

Covers the Requirement-4 guarantees of the in-process AI-call accumulators and
their durable flush (see :mod:`app.admin.ai_metrics`):

- **Rate invariant (Req 4.6).** ``successRate + failureRate == 1.0`` *exactly*
  (both rounded to 4dp) whenever ``totalCalls > 0``, across arbitrary splits.
- **Zero-calls (Req 4.7).** An empty store yields all-zero aggregates with no
  division-by-zero, all five providers present at zero, and cost 0.
- **Cost truncation (Req 4.5).** ``estimatedCostDollars`` is the injected
  ``CostMonitor.global_spent()`` microdollar counter floored by 1,000,000.
- **Allowlist (Req 4 §4 / 15.8).** ``record_call``'s signature, ``snapshot``'s
  keys, and the ``AiAnalytics`` model fields expose ONLY the allowlisted signals
  - never temperature/prompt-length/model/system-prompt/tool-calls/reasoning/ids
  - and the ``llm.py`` call sites pass only allowlisted kwargs.
- **Flush reset-on-success / retain-on-failure / idempotent (Req 4.2 / 4.8).**
  A clean flush consumes exactly the persisted amounts; a per-key ``add`` failure
  retains that key's accumulator (retried next run, no double-count); a re-flush
  with no new activity adds nothing.
- **provider_to_enum.** Known spellings map to the closed enum; unknown/blank ->
  ``None`` (global counters still move, no per-provider key invented).

Requirements: 4.2, 4.5, 4.6, 4.7, 4.8, 15.8.
"""

from __future__ import annotations

import ast
import inspect
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.admin.ai_metrics import (
    AiFlushStep,
    AiMetricsService,
    provider_to_enum,
)
from app.admin.metric_registry import (
    AI_CALLS,
    AI_FAILURE,
    AI_LATENCY_MS_SUM,
    AI_SUCCESS,
    AI_TOKENS_SUM,
    AiProvider,
    ai_calls_key,
)
from app.admin.metric_store import MetricStore
from app.admin.schemas import AiAnalytics

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers / fakes
# ---------------------------------------------------------------------------


class _FakeKV:
    """In-memory KVStore stand-in (the store's optional snapshot sink)."""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def get(self, key: str):
        return self.data.get(key)

    async def set(self, key: str, value: str, ttl_seconds=None) -> None:
        self.data[key] = value


class _FakeCostMonitor:
    """A ``CostMonitor``-shaped fake returning a fixed microdollar counter."""

    def __init__(self, microdollars: int) -> None:
        self._micro = microdollars

    async def global_spent(self) -> int:
        return self._micro


class _FailingAddStore:
    """A ``MetricStore``-shaped fake whose ``add`` raises for configured keys.

    Records healthy ``add`` calls into an in-memory ``(day, key) -> value`` map
    and raises for any key in ``fail_keys`` (clearable to simulate recovery).
    Only ``add`` is exercised by :class:`AiFlushStep`.
    """

    def __init__(self, *fail_keys: str) -> None:
        self.values: dict[tuple[str, str], int] = defaultdict(int)
        self.fail_keys: set[str] = set(fail_keys)

    async def add(self, day: str, key: str, delta: int) -> None:
        if key in self.fail_keys:
            raise RuntimeError(f"add failed for {key}")
        self.values[(day, key)] += int(delta)


def _store(isolated_db) -> MetricStore:
    """A DB-backed MetricStore on the isolated engine (with an in-memory KV)."""
    return MetricStore(isolated_db.session_factory, kvstore=_FakeKV())


def _service(store=None, *, cost=0) -> AiMetricsService:
    """An ``AiMetricsService`` with injected store + a fixed-cost fake monitor."""
    return AiMetricsService(metric_store=store, cost_monitor=_FakeCostMonitor(cost))


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _yesterday() -> str:
    return (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")


# The exact allowlisted signals (Req 4 §4). Nothing else may ever be accepted,
# accumulated, or serialized.
_ALLOWED_RECORD_PARAMS = {"provider", "ok", "timed_out", "retried", "tokens", "latency_ms"}
_ALLOWED_SNAPSHOT_KEYS = {
    "calls", "success", "failure", "timeouts", "retries",
    "tokens_sum", "latency_ms_sum", "by_provider",
}
# Fields that must NEVER appear on record_call, snapshot, or the AiAnalytics model.
_REJECTED_FIELDS = {
    "temperature", "prompt", "promptLength", "prompt_length", "completion",
    "model", "systemPrompt", "system_prompt", "toolCalls", "tool_calls",
    "reasoning", "reasoningTokens", "reasoning_tokens", "id", "ids",
    "requestId", "request_id", "conversationId", "conversation_id",
}


# ===========================================================================
# 1. Rate invariant (Req 4.6)
# ===========================================================================


class TestRateInvariant:
    """Validates: Requirements 4.6"""

    @pytest.mark.parametrize(
        "success,failure",
        [
            (1, 3),        # 1/4
            (7, 993),      # tiny success fraction
            (993, 7),      # tiny failure fraction
            (1, 1),        # even split
            (333, 667),    # thirds-ish
            (1, 999_999),  # extreme
            (12345, 67890),
        ],
    )
    async def test_success_plus_failure_rate_equals_one(
        self, isolated_db, success, failure
    ):
        """For any split with total > 0, the two 4dp rates sum to exactly 1.0.

        Seeds the durable ``AI_*`` keys for a day inside the window (no live
        activity) so ``analytics`` reads exactly these totals; the complement
        construction guarantees ``successRate + failureRate == 1.0``.
        """
        store = _store(isolated_db)
        day = _yesterday()
        total = success + failure
        await store.upsert(day, AI_CALLS, total)
        await store.upsert(day, AI_SUCCESS, success)
        await store.upsert(day, AI_FAILURE, failure)

        result = await _service(store).analytics(window=30)

        assert result.totalCalls == total
        # EXACT equality at 4dp - the design's complement guarantee.
        assert result.successRate + result.failureRate == 1.0
        assert result.successRate == round(success / total, 4)
        assert result.failureRate == round(1.0 - result.successRate, 4)

    async def test_rates_reflect_live_today_plus_durable_window(self, isolated_db):
        """The invariant still holds when today's live accumulator is folded in."""
        store = _store(isolated_db)
        svc = _service(store)
        # durable prior-day activity + live today activity
        await store.upsert(_yesterday(), AI_CALLS, 10)
        await store.upsert(_yesterday(), AI_SUCCESS, 6)
        await store.upsert(_yesterday(), AI_FAILURE, 4)
        svc.record_call("openai", ok=True)
        svc.record_call("openai", ok=False)

        result = await svc.analytics(window=30)

        assert result.totalCalls == 12  # 10 durable + 2 live
        assert result.successRate + result.failureRate == 1.0


# ===========================================================================
# 2. Zero-calls (Req 4.7)
# ===========================================================================


class TestZeroCalls:
    """Validates: Requirements 4.7"""

    async def test_empty_store_yields_all_zero_no_division_error(self, isolated_db):
        result = await _service(_store(isolated_db), cost=0).analytics(window=30)

        assert result.totalCalls == 0
        assert result.successRate == 0.0
        assert result.failureRate == 0.0
        assert result.avgLatencyMs == 0.0
        assert result.avgUnitsPerCall == 0.0
        assert result.timeouts == 0
        assert result.retries == 0
        assert result.estimatedCostDollars == 0
        # All five closed providers present, each at zero.
        assert len(result.providers) == len(AiProvider)
        assert {p.provider for p in result.providers} == {p.value for p in AiProvider}
        assert all(p.calls == 0 for p in result.providers)


# ===========================================================================
# 3. Cost truncation (Req 4.5)
# ===========================================================================


class TestCostTruncation:
    """Validates: Requirements 4.5"""

    @pytest.mark.parametrize(
        "microdollars,expected_dollars",
        [
            (3_500_000, 3),    # 3.5 -> 3 (truncated, not rounded)
            (999_999, 0),      # < $1 -> 0
            (0, 0),
            (1_000_000, 1),    # exactly $1
            (1_999_999, 1),    # 1.999... -> 1
            (100_000_000, 100),
        ],
    )
    async def test_cost_is_floored_microdollars(
        self, isolated_db, microdollars, expected_dollars
    ):
        svc = _service(_store(isolated_db), cost=microdollars)
        result = await svc.analytics(window=30)
        assert result.estimatedCostDollars == expected_dollars


# ===========================================================================
# 4. Allowlist - rejected fields never accepted / stored / serialized (15.8)
# ===========================================================================


class TestAllowlist:
    """Validates: Requirements 15.8"""

    def test_record_call_signature_is_allowlist_only(self):
        """``record_call`` accepts ONLY the six allowlisted params - no rejected
        field (temperature/prompt/model/system_prompt/tool_calls/reasoning/id)."""
        params = set(inspect.signature(AiMetricsService.record_call).parameters)
        params.discard("self")
        assert params == _ALLOWED_RECORD_PARAMS
        assert params.isdisjoint(_REJECTED_FIELDS)

    def test_snapshot_keys_are_allowlist_only(self):
        snap = AiMetricsService().snapshot()
        assert set(snap) == _ALLOWED_SNAPSHOT_KEYS
        assert set(snap).isdisjoint(_REJECTED_FIELDS)

    def test_ai_analytics_model_has_no_rejected_fields(self):
        fields = set(AiAnalytics.model_fields)
        assert fields.isdisjoint(_REJECTED_FIELDS)

    def test_llm_record_call_sites_pass_only_allowlisted_kwargs(self):
        """AST-scan ``app/llm.py``: every ``_record_ai_call(...)`` /
        ``record_call(...)`` invocation passes only allowlisted kwargs (the sole
        positional arg is the provider). A rejected kwarg would fail loudly."""
        source = Path(inspect.getfile(__import__("app.llm", fromlist=["_"]))).read_text()
        tree = ast.parse(source)
        # allow the provider positional plus the five keyword-only allowlist names
        allowed_kwargs = _ALLOWED_RECORD_PARAMS - {"provider"}
        seen_calls = 0
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = getattr(fn, "attr", None) or getattr(fn, "id", None)
            if name not in {"_record_ai_call", "record_call"}:
                continue
            seen_calls += 1
            kwarg_names = {kw.arg for kw in node.keywords if kw.arg is not None}
            assert kwarg_names <= allowed_kwargs, (
                f"non-allowlisted kwarg passed to {name}: {kwarg_names - allowed_kwargs}"
            )
            assert kwarg_names.isdisjoint(_REJECTED_FIELDS)
            assert len(node.args) <= 1  # only the provider positional
        assert seen_calls >= 1  # sanity: the scan actually found the call sites


# ===========================================================================
# 5. Flush reset-on-success (Req 4.2)
# ===========================================================================


class TestFlushResetOnSuccess:
    """Validates: Requirements 4.2"""

    async def test_success_consumes_accumulators_and_persists_values(self, isolated_db):
        store = _store(isolated_db)
        svc = _service(store)
        today = _today()

        svc.record_call("openai", ok=True, tokens=100, latency_ms=250.0)
        svc.record_call("openai", ok=False, timed_out=True, tokens=50, latency_ms=150.0)
        svc.record_call("gemini", ok=True, retried=2, tokens=30, latency_ms=90.0)

        before = svc.snapshot()
        assert before["calls"] == 3 and before["tokens_sum"] == 180

        step = AiFlushStep(metric_store=store, service=svc)
        result = await step.run(today)
        assert result.ok is True

        # Accumulators consumed back to zero (no concurrent activity).
        after = svc.snapshot()
        assert after["calls"] == 0
        assert after["success"] == 0
        assert after["failure"] == 0
        assert after["tokens_sum"] == 0
        assert all(v == 0 for v in after["by_provider"].values())

        # Durable metrics_daily holds exactly the flushed values for today.
        assert await store.sum([AI_CALLS], today, today) == 3
        assert await store.sum([AI_SUCCESS], today, today) == 2
        assert await store.sum([AI_FAILURE], today, today) == 1
        assert await store.sum([AI_TOKENS_SUM], today, today) == 180
        assert await store.sum([AI_LATENCY_MS_SUM], today, today) == 490  # truncated int
        assert await store.sum([ai_calls_key(AiProvider.OPENAI)], today, today) == 2
        assert await store.sum([ai_calls_key(AiProvider.GEMINI)], today, today) == 1


# ===========================================================================
# 6. Flush retain-on-failure (Req 4.8)
# ===========================================================================


class TestFlushRetainOnFailure:
    """Validates: Requirements 4.8"""

    async def test_failed_key_retained_and_retried_without_double_count(self):
        store = _FailingAddStore(AI_TOKENS_SUM)
        svc = _service()  # store injected per-run via the step
        today = _today()

        svc.record_call("openai", ok=True, tokens=100, latency_ms=200.0)
        svc.record_call("openai", ok=True, tokens=40, latency_ms=60.0)

        step = AiFlushStep(metric_store=store, service=svc)
        result = await step.run(today)

        # The failing key is named; the healthy keys persisted.
        assert result.ok is False
        assert AI_TOKENS_SUM in (result.error or "")
        assert store.values[(today, AI_CALLS)] == 2
        assert store.values[(today, AI_SUCCESS)] == 2
        assert (today, AI_TOKENS_SUM) not in store.values

        # The failed key's amount is RETAINED; consumed keys were subtracted.
        snap = svc.snapshot()
        assert snap["calls"] == 0
        assert snap["success"] == 0
        assert snap["tokens_sum"] == 140  # retained (100 + 40)

        # Recover the store and re-flush: the retained tokens persist exactly
        # once, and the already-consumed keys are NOT re-added (no double-count).
        store.fail_keys.clear()
        retry = await step.run(today)
        assert retry.ok is True
        assert store.values[(today, AI_TOKENS_SUM)] == 140  # retried once
        assert store.values[(today, AI_CALLS)] == 2  # unchanged
        assert store.values[(today, AI_SUCCESS)] == 2  # unchanged
        assert svc.snapshot()["tokens_sum"] == 0


# ===========================================================================
# 7. Idempotent re-flush (Req 4.8)
# ===========================================================================


class TestFlushIdempotent:
    """Validates: Requirements 4.8"""

    async def test_second_flush_with_no_new_activity_adds_nothing(self, isolated_db):
        store = _store(isolated_db)
        svc = _service(store)
        today = _today()

        svc.record_call("anthropic", ok=True, tokens=10, latency_ms=20.0)
        step = AiFlushStep(metric_store=store, service=svc)
        assert (await step.run(today)).ok is True
        assert await store.sum([AI_CALLS], today, today) == 1

        # No new calls -> snapshot empty -> the second flush adds nothing.
        assert svc.snapshot()["calls"] == 0
        assert (await step.run(today)).ok is True
        assert await store.sum([AI_CALLS], today, today) == 1
        assert await store.sum([AI_TOKENS_SUM], today, today) == 10


# ===========================================================================
# 8. provider_to_enum + unknown-provider handling
# ===========================================================================


class TestProviderMapping:
    """Validates: Requirements 4.1 (bounded cardinality - supports 15.8)"""

    @pytest.mark.parametrize(
        "spelling,expected",
        [
            ("openai", AiProvider.OPENAI),
            ("gemini", AiProvider.GEMINI),
            ("anthropic", AiProvider.ANTHROPIC),
            ("ollama", AiProvider.OLLAMA),
            ("openai_compat", AiProvider.OPENAI_COMPAT),
            ("openai_compatible", AiProvider.OPENAI_COMPAT),
            ("OpenAI", AiProvider.OPENAI),  # case-insensitive
            ("  gemini  ", AiProvider.GEMINI),  # trimmed
        ],
    )
    def test_known_providers_map_to_enum(self, spelling, expected):
        assert provider_to_enum(spelling) is expected

    @pytest.mark.parametrize("spelling", ["openrouter", "deepseek", "groq", "", None])
    def test_unknown_or_blank_providers_map_to_none(self, spelling):
        assert provider_to_enum(spelling) is None

    def test_enum_input_passes_through(self):
        assert provider_to_enum(AiProvider.OLLAMA) is AiProvider.OLLAMA

    def test_unknown_provider_counts_global_but_no_per_provider_counter(self):
        svc = AiMetricsService()
        svc.record_call("openrouter", ok=True, tokens=5)
        snap = svc.snapshot()
        assert snap["calls"] == 1  # global counter still moves
        assert snap["success"] == 1
        assert snap["tokens_sum"] == 5
        # No per-provider counter was invented (all closed counters stay zero).
        assert all(v == 0 for v in snap["by_provider"].values())

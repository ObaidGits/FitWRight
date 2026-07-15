"""Unit tests for the admin-panel-upgrade foundation modules (Task 1.5).

Covers the three foundation building blocks landed in tasks 1.1–1.3:

- ``app.admin.metric_registry`` — the static Metric_Registry. A source-level
  (``ast``) lint test proves **no Metric_Key is composed at runtime** (every
  registered key is a plain ``str`` literal — Req 20.2), plus **bounded
  cardinality** and closed/exhaustive dimension maps (Req 20.3/20.4).
- ``app.admin.metric_store`` — the shared low-level ``metrics_daily`` (+ KV)
  path. Tests the UPSERT/add idempotency + accumulation, windowed ``sum``,
  0-filled trailing ``series``, and JSON snapshot round-trip.
- ``app.config.Settings`` — the 10 new ``admin_*`` / ``alert_*`` settings:
  documented defaults, blank-env→default, and range clamping/validators.

Requirements: 20.2, 20.4, 15.8.
"""

from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import app.admin.metric_registry as reg
from app.admin.metric_registry import (
    AI_CALLS,
    AI_CALLS_BY_PROVIDER,
    AUDIT_DOWNSAMPLE_BY_EVENT,
    FEAT_BUILDER,
    METRIC_REGISTRY,
    REQUEST_5XX,
    RESUMES_GENERATED,
    RESUMES_IMPORTED,
    RESUMES_TAILORED,
    RESUMES_DELETED,
    FEAT_TAILOR,
    FEAT_PARSER,
    FEAT_IMPORT,
    FEAT_COVER_LETTER,
    FEAT_PROFILE_GEN,
    FEAT_PORTFOLIO,
    FEAT_JD_PARSE,
    SEC_LOGIN_FAILED,
    AiProvider,
    DownsamplableEvent,
    MetricCategory,
    all_keys,
    ai_calls_key,
    audit_downsample_key,
    category_of,
    is_registered,
    keys_for_category,
    usage_series_keys,
)
from app.config import Settings

pytestmark = pytest.mark.unit


# ===========================================================================
# Metric_Registry — static/lint: NO runtime-composed keys (Req 20.2)
# ===========================================================================


def _registry_ast() -> ast.Module:
    """Parse the metric_registry.py source into an AST (static analysis only)."""
    source = Path(reg.__file__).read_text(encoding="utf-8")
    return ast.parse(source)


class TestNoRuntimeComposedKeys:
    """Property 8 / Req 20.2: every Metric_Key is a plain string *literal*.

    A key produced by ``+`` concatenation, ``%`` formatting, ``str.format`` or an
    interpolating f-string would allow unbounded, runtime-derived keys. We parse
    the module source and assert none of those constructs can produce a key.
    """

    def test_module_contains_no_fstrings(self):
        """No f-string anywhere in the registry — keys can't be interpolated."""
        tree = _registry_ast()
        joined = [n for n in ast.walk(tree) if isinstance(n, ast.JoinedStr)]
        assert joined == [], "metric_registry must contain no f-strings (runtime-composed keys)"

    def test_module_contains_no_format_calls(self):
        """No ``.format(...)`` call — keys can't be format-composed."""
        tree = _registry_ast()
        offenders = [
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "format"
        ]
        assert offenders == [], "metric_registry must not use str.format for keys"

    def test_every_registered_key_symbol_is_a_plain_literal(self):
        """Each key referenced by METRIC_REGISTRY resolves to a bare str literal.

        We collect the module-level ``NAME = "literal"`` assignments, then verify
        that every symbol passed as the first argument of a ``MetricKeySpec(...)``
        (and every value in the two closed dimension maps) is one of those plain
        literals — never a name bound to a concatenation/format/f-string.
        """
        tree = _registry_ast()

        # 1. Module-level string-literal constants: NAME -> value.
        literal_consts: dict[str, str] = {}
        composed_names: set[str] = set()
        for node in tree.body:
            if not (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
            ):
                continue
            name = node.targets[0].id
            value = node.value
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                literal_consts[name] = value.value
            elif isinstance(value, (ast.JoinedStr, ast.BinOp, ast.Call)):
                # A string built at runtime (concat/format/f-string/call).
                composed_names.add(name)

        # 2. Find the METRIC_REGISTRY tuple and collect each MetricKeySpec's
        #    first positional arg (the key symbol). It is an *annotated*
        #    assignment (``METRIC_REGISTRY: tuple[...] = (...)``), so handle both
        #    plain (``ast.Assign``) and annotated (``ast.AnnAssign``) forms.
        key_symbols: list[str] = []
        for node in tree.body:
            if isinstance(node, ast.Assign):
                names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                names = [node.target.id]
            else:
                continue
            if "METRIC_REGISTRY" not in names:
                continue
            registry_value = node.value
            assert isinstance(registry_value, ast.Tuple), "METRIC_REGISTRY must be a static tuple"
            for element in registry_value.elts:
                assert isinstance(element, ast.Call), "registry entries must be MetricKeySpec(...) calls"
                assert element.args, "MetricKeySpec must be called with the key symbol positionally"
                first = element.args[0]
                assert isinstance(first, ast.Name), "the key must be a bare constant name, not an expression"
                key_symbols.append(first.id)

        assert key_symbols, "no MetricKeySpec entries found — parse failed"

        # 3. Every key symbol must be a plain literal constant, never composed.
        for symbol in key_symbols:
            assert symbol not in composed_names, f"{symbol} is a runtime-composed key"
            assert symbol in literal_consts, f"{symbol} is not a plain string-literal constant"

        # 4. The static literals must exactly match the runtime key set — ties
        #    the source-level guarantee to the live registry.
        literal_values = {literal_consts[s] for s in key_symbols}
        assert literal_values == set(all_keys())

    def test_dimension_map_values_are_registered_literals(self):
        """Closed-map values (AI_CALLS_BY_PROVIDER / AUDIT_*) are registered keys."""
        for value in AI_CALLS_BY_PROVIDER.values():
            assert is_registered(value)
        for value in AUDIT_DOWNSAMPLE_BY_EVENT.values():
            assert is_registered(value)


# ===========================================================================
# Metric_Registry — bounded cardinality + closed dimensions (Req 20.3/20.4)
# ===========================================================================


# Fixed cardinality broken out by owning category (self-documenting). Bumping
# any of these is an explicit, reviewed edit — never a runtime side effect.
_EXPECTED_PER_CATEGORY = {
    MetricCategory.AI: 12,
    MetricCategory.ERRORS: 3,
    MetricCategory.SECURITY: 5,
    MetricCategory.STORAGE: 1,
    MetricCategory.RESUME: 4,
    MetricCategory.FEATURE_USAGE: 8,
    MetricCategory.AUDIT_DOWNSAMPLE: 1,
}
_EXPECTED_TOTAL = sum(_EXPECTED_PER_CATEGORY.values())  # 34


class TestBoundedCardinality:
    def test_total_key_count_is_fixed(self):
        assert len(all_keys()) == _EXPECTED_TOTAL
        assert len(all_keys()) == len(METRIC_REGISTRY)

    def test_no_duplicate_key_strings(self):
        keys = [spec.key for spec in METRIC_REGISTRY]
        assert len(keys) == len(set(keys)), "duplicate Metric_Key string in registry"

    def test_per_category_counts_are_fixed(self):
        for category, expected in _EXPECTED_PER_CATEGORY.items():
            assert len(keys_for_category(category)) == expected, category

    def test_provider_map_is_exhaustive_over_enum(self):
        """The AI-provider dimension is closed: one static key per enum member."""
        assert set(AI_CALLS_BY_PROVIDER) == set(AiProvider)

    def test_downsample_map_is_exhaustive_over_enum(self):
        """The downsamplable-event dimension is closed: one static key per member."""
        assert set(AUDIT_DOWNSAMPLE_BY_EVENT) == set(DownsamplableEvent)

    def test_all_dimension_values_are_registered(self):
        for value in AI_CALLS_BY_PROVIDER.values():
            assert value in all_keys()
        for value in AUDIT_DOWNSAMPLE_BY_EVENT.values():
            assert value in all_keys()


class TestUsageSeriesKeys:
    def test_exact_expected_subset(self):
        expected = {
            AI_CALLS,
            REQUEST_5XX,
            SEC_LOGIN_FAILED,
            RESUMES_GENERATED,
            RESUMES_IMPORTED,
            RESUMES_TAILORED,
            RESUMES_DELETED,
            FEAT_BUILDER,
            FEAT_TAILOR,
            FEAT_PARSER,
            FEAT_IMPORT,
            FEAT_COVER_LETTER,
            FEAT_PROFILE_GEN,
            FEAT_PORTFOLIO,
            FEAT_JD_PARSE,
        }
        assert usage_series_keys() == expected

    def test_usage_series_is_subset_of_all_keys(self):
        assert usage_series_keys() <= all_keys()


class TestRegistryHelpers:
    def test_category_of_known_key(self):
        assert category_of(AI_CALLS) is MetricCategory.AI
        assert category_of(REQUEST_5XX) is MetricCategory.ERRORS
        assert category_of(FEAT_BUILDER) is MetricCategory.FEATURE_USAGE

    def test_category_of_unknown_raises(self):
        with pytest.raises(KeyError):
            category_of("not_a_real_key")

    def test_keys_for_category_matches_category_of(self):
        for category in MetricCategory:
            for key in keys_for_category(category):
                assert category_of(key) is category

    def test_is_registered(self):
        assert is_registered(AI_CALLS) is True
        assert is_registered("bogus_key") is False

    def test_ai_calls_key_returns_registered_static_key(self):
        for provider in AiProvider:
            key = ai_calls_key(provider)
            assert key == AI_CALLS_BY_PROVIDER[provider]
            assert is_registered(key)

    def test_audit_downsample_key_returns_registered_static_key(self):
        for event in DownsamplableEvent:
            key = audit_downsample_key(event)
            assert key == AUDIT_DOWNSAMPLE_BY_EVENT[event]
            assert is_registered(key)


# ===========================================================================
# Metric_Store — I/O idempotency + accumulation over an isolated DB
# ===========================================================================


class _FakeKV:
    """Minimal in-memory KVStore stand-in for the snapshot round-trip tests."""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def get(self, key: str):
        return self.data.get(key)

    async def set(self, key: str, value: str, ttl_seconds=None) -> None:
        self.data[key] = value


def _day(offset: int) -> str:
    """UTC ``YYYY-MM-DD`` string ``offset`` days before today."""
    return (datetime.now(timezone.utc) - timedelta(days=offset)).strftime("%Y-%m-%d")


async def _row_count(session_factory, day: str, key: str) -> int:
    from sqlalchemy import func, select

    from app.models import MetricsDaily

    async with session_factory() as session:
        return int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(MetricsDaily)
                    .where(MetricsDaily.day_utc == day, MetricsDaily.metric == key)
                )
            ).scalar()
        )


def _store(isolated_db, kv=None):
    from app.admin.metric_store import MetricStore

    return MetricStore(isolated_db.session_factory, kvstore=kv or _FakeKV())


class TestMetricStoreUpsert:
    async def test_upsert_sets_absolute_value(self, isolated_db):
        store = _store(isolated_db)
        await store.upsert(_day(1), "k1", 10)
        assert await store.sum(["k1"], _day(1), _day(1)) == 10

    async def test_reupsert_same_value_is_noop(self, isolated_db):
        store = _store(isolated_db)
        day = _day(1)
        await store.upsert(day, "k1", 10)
        await store.upsert(day, "k1", 10)  # idempotent per closed day
        assert await store.sum(["k1"], day, day) == 10
        assert await _row_count(isolated_db.session_factory, day, "k1") == 1

    async def test_upsert_new_value_overwrites(self, isolated_db):
        store = _store(isolated_db)
        day = _day(1)
        await store.upsert(day, "k1", 10)
        await store.upsert(day, "k1", 25)
        assert await store.sum(["k1"], day, day) == 25
        assert await _row_count(isolated_db.session_factory, day, "k1") == 1


class TestMetricStoreAdd:
    async def test_add_increments(self, isolated_db):
        store = _store(isolated_db)
        day = _day(1)
        await store.add(day, "k1", 5)
        await store.add(day, "k1", 3)
        assert await store.sum(["k1"], day, day) == 8

    async def test_repeated_adds_accumulate_no_lost_deltas(self, isolated_db):
        store = _store(isolated_db)
        day = _day(1)
        for _ in range(10):
            await store.add(day, "k1", 2)
        assert await store.sum(["k1"], day, day) == 20

    async def test_add_creates_single_row_no_per_worker_rows(self, isolated_db):
        store = _store(isolated_db)
        day = _day(1)
        for _ in range(5):
            await store.add(day, "k1", 1)
        assert await _row_count(isolated_db.session_factory, day, "k1") == 1


class TestMetricStoreSum:
    async def test_sum_over_inclusive_range_across_keys(self, isolated_db):
        store = _store(isolated_db)
        await store.upsert(_day(3), "k1", 1)
        await store.upsert(_day(2), "k1", 2)
        await store.upsert(_day(2), "k2", 10)
        await store.upsert(_day(1), "k2", 20)
        # Inclusive [day(3) .. day(1)] over both keys: 1 + 2 + 10 + 20 = 33.
        assert await store.sum(["k1", "k2"], _day(3), _day(1)) == 33

    async def test_sum_respects_range_bounds(self, isolated_db):
        store = _store(isolated_db)
        await store.upsert(_day(5), "k1", 100)  # outside the window
        await store.upsert(_day(2), "k1", 7)
        assert await store.sum(["k1"], _day(3), _day(1)) == 7

    async def test_empty_key_set_is_zero(self, isolated_db):
        store = _store(isolated_db)
        assert await store.sum([], _day(3), _day(1)) == 0

    async def test_missing_rows_sum_to_zero(self, isolated_db):
        store = _store(isolated_db)
        assert await store.sum(["nope"], _day(3), _day(1)) == 0


class TestMetricStoreSeries:
    async def test_trailing_days_oldest_to_newest_zero_filled(self, isolated_db):
        store = _store(isolated_db)
        await store.upsert(_day(0), "k1", 7)
        await store.upsert(_day(2), "k1", 3)
        result = await store.series("k1", 5)
        expected = [
            (_day(4), 0),
            (_day(3), 0),
            (_day(2), 3),
            (_day(1), 0),
            (_day(0), 7),
        ]
        assert result == expected

    async def test_series_length_matches_window(self, isolated_db):
        store = _store(isolated_db)
        assert len(await store.series("k1", 7)) == 7

    async def test_series_zero_days_is_empty(self, isolated_db):
        store = _store(isolated_db)
        assert await store.series("k1", 0) == []


class TestMetricStoreSnapshot:
    async def test_put_get_round_trip(self, isolated_db):
        kv = _FakeKV()
        store = _store(isolated_db, kv=kv)
        payload = {"totals": {"users": 5, "resumes": 12}, "day": "2026-01-01"}
        await store.snapshot_put("totals", payload)
        assert await store.snapshot_get("totals") == payload

    async def test_missing_snapshot_is_none(self, isolated_db):
        store = _store(isolated_db)
        assert await store.snapshot_get("does-not-exist") is None


# ===========================================================================
# Config — defaults / blank-env→default / range validators
# ===========================================================================


def _settings(**overrides) -> Settings:
    """Construct Settings hermetically (no .env bleed) in local single-user mode."""
    return Settings(single_user_mode=True, _env_file=None, **overrides)


# (field name, documented default) for all 10 new admin_* / alert_* settings.
_DEFAULTS = [
    ("admin_audit_hot_days", 365),
    ("admin_audit_downsample_days", 90),
    ("admin_audit_retention_batch", 1000),
    ("admin_metrics_retention_days", 400),
    ("admin_db_size_sample_minutes", 60),
    ("admin_job_stuck_multiplier", 3),
    ("admin_job_stuck_ceiling_seconds", 3600),
    ("alert_storage_full_pct", 90),
    ("alert_error_rate_pct", 5),
    ("alert_cooldown_seconds", 3600),
]

# Positive-int fields that floor at 1 with no upper bound.
_POSITIVE_INT_FIELDS = [
    "admin_audit_hot_days",
    "admin_audit_downsample_days",
    "admin_metrics_retention_days",
    "admin_db_size_sample_minutes",
    "admin_job_stuck_multiplier",
    "admin_job_stuck_ceiling_seconds",
    "alert_cooldown_seconds",
]

_PERCENT_FIELDS = ["alert_storage_full_pct", "alert_error_rate_pct"]


class TestConfigDefaults:
    @pytest.mark.parametrize("field,expected", _DEFAULTS)
    def test_default_when_env_unset(self, field, expected):
        assert getattr(_settings(), field) == expected

    @pytest.mark.parametrize("field,expected", _DEFAULTS)
    def test_blank_env_falls_back_to_default(self, field, expected):
        assert getattr(_settings(**{field: ""}), field) == expected

    @pytest.mark.parametrize("field,expected", _DEFAULTS)
    def test_whitespace_env_falls_back_to_default(self, field, expected):
        assert getattr(_settings(**{field: "   "}), field) == expected

    @pytest.mark.parametrize("field,expected", _DEFAULTS)
    def test_unparseable_env_falls_back_to_default(self, field, expected):
        assert getattr(_settings(**{field: "not-a-number"}), field) == expected


class TestRetentionBatchClamp:
    def test_clamps_low_to_one(self):
        assert _settings(admin_audit_retention_batch="0").admin_audit_retention_batch == 1
        assert _settings(admin_audit_retention_batch="-100").admin_audit_retention_batch == 1

    def test_clamps_high_to_ceiling(self):
        assert _settings(admin_audit_retention_batch="999999999").admin_audit_retention_batch == 100_000

    def test_in_range_value_preserved(self):
        assert _settings(admin_audit_retention_batch="5000").admin_audit_retention_batch == 5000


class TestAlertPercentClamp:
    @pytest.mark.parametrize("field", _PERCENT_FIELDS)
    def test_clamps_below_zero(self, field):
        assert getattr(_settings(**{field: "-10"}), field) == 0

    @pytest.mark.parametrize("field", _PERCENT_FIELDS)
    def test_clamps_above_hundred(self, field):
        assert getattr(_settings(**{field: "250"}), field) == 100

    @pytest.mark.parametrize("field", _PERCENT_FIELDS)
    def test_in_range_value_preserved(self, field):
        assert getattr(_settings(**{field: "42"}), field) == 42


class TestPositiveIntFloor:
    @pytest.mark.parametrize("field", _POSITIVE_INT_FIELDS)
    def test_floors_at_one(self, field):
        assert getattr(_settings(**{field: "0"}), field) == 1
        assert getattr(_settings(**{field: "-5"}), field) == 1

    @pytest.mark.parametrize("field", _POSITIVE_INT_FIELDS)
    def test_large_value_preserved(self, field):
        assert getattr(_settings(**{field: "100000"}), field) == 100000

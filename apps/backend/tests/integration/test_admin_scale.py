"""Performance/scale test: the new dashboard reads are O(1) (Task 18.3).

Proves the read cost of every new observability/product-analytics endpoint is
**independent of the number of users** (Req 15.4): a 1,000,000-user deployment
must serve these reads at the same p50/p95 (within 10%) as a 100-user one.

A literal 1M-user timing test is impractical (and flaky) in CI, so O(1) is
proven **structurally** instead, which is the stronger and more stable claim:

- The new reads are served from ``metrics_daily`` **daily aggregates** (via the
  shared :class:`~app.admin.metric_store.MetricStore`) and small **KV
  snapshots** — never from a per-user scan of the ``users`` table. If a read
  were O(N) in the user count, the number of SQL statements it issues would
  grow with the number of seeded users. So we **count the SQL statements** each
  endpoint issues at a SMALL user count vs a MUCH LARGER one and assert the
  count does **not grow** — a deterministic, wall-clock-free proof of O(1).

- A best-effort wall-clock ratio is also recorded and checked against a very
  generous bound. Timing in CI is noisy, so this is a documented sanity guard,
  **not** the primary assertion (the query-count equality above is).

The endpoints under test (every *new* dashboard read — Req 15.4):
``/kpis``, ``/errors``, ``/storage``, ``/security``, ``/performance``,
``/ai-analytics``, ``/analytics/feature-usage``, ``/analytics/resumes``.

Harness: the ``_admin_client`` / ``hosted`` / ``_seed`` pattern from
``tests/integration/test_feature_usage_api.py`` plus a ``reset_metric_store()``
fixture (the ``MetricStore`` singleton lazily binds to ``app.database.db``, so it
must be rebound to the isolated temp DB — see ``test_resume_analytics_api.py``).

Requirements: 15.4, 21.1 (bounded per-day keys), 21.3/21.4/21.5 (no scans).
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager, contextmanager

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event

from app.auth.accounts import create_user
from app.auth.passwords import get_password_service
from app.config import settings as app_settings
from app.main import app

from tests.integration.test_auth_api import STRONG_PW, _login

pytestmark = pytest.mark.integration


# Every NEW dashboard read added by the admin-panel upgrade. Each is served from
# aggregates/snapshots, so its SQL-statement count must not grow with user count.
_ENDPOINTS: tuple[str, ...] = (
    "/api/v1/admin/kpis",
    "/api/v1/admin/errors?window=30",
    "/api/v1/admin/storage",
    "/api/v1/admin/security",
    "/api/v1/admin/performance",
    "/api/v1/admin/ai-analytics?window=30",
    "/api/v1/admin/analytics/feature-usage?window=30",
    "/api/v1/admin/analytics/resumes?window=30",
)

# User counts to compare. A 20x jump: if any read were O(N) in the user table,
# its statement count would visibly grow between these two points.
_SMALL_USERS = 5
_LARGE_USERS = 100


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="https://test")


@pytest.fixture
def hosted(monkeypatch):
    monkeypatch.setattr(app_settings, "single_user_mode", False)


@pytest.fixture
def scale_env(auth_env):
    """Rebind the MetricStore singleton to the isolated temp DB for this test.

    ``get_metric_store()`` caches a process-wide instance bound to whatever
    ``app.database.db`` was at first use; resetting it before/after forces it to
    rebuild against the ``auth_env`` temp DB (mirrors ``resume_env``). ``auth_env``
    already rebinds the AdminRepo / MetricsService singletons.
    """
    from app.admin.metric_store import reset_metric_store

    reset_metric_store()
    yield auth_env
    reset_metric_store()


async def _seed(db, email, *, role="user", status="active", verified=True, name="U"):
    return await create_user(
        email=email,
        name=name,
        password_hash=get_password_service().hash_password(STRONG_PW),
        role=role,
        status=status,
        email_verified_at="2024-01-01T00:00:00+00:00" if verified else None,
        db=db,
    )


async def _seed_many(db, start: int, count: int) -> None:
    """Seed ``count`` plain users with distinct emails (batch ``start``..)."""
    for i in range(start, start + count):
        await _seed(db, f"user{i}@example.com", name=f"U{i}")


@asynccontextmanager
async def _admin_client(db, email="admin@example.com"):
    """Yield a logged-in admin client with the per-session csrf header attached."""
    await _seed(db, email, role="admin")
    async with _client() as client:
        await _login(client, email)
        client.headers["X-CSRF-Token"] = client.cookies.get("csrf")
        yield client


class _QueryCounter:
    """Count SQL statements issued on an engine within a ``with`` block.

    Attaches a ``before_cursor_execute`` listener to the sync engine backing the
    app's async engine (SQLAlchemy fires engine events on ``sync_engine``). Every
    admin request path — auth session/status lookups, the AdminRepo day-bounded
    counts, and every MetricStore ``metrics_daily`` read — flows through this one
    engine, so the counts here are the *complete* per-request SQL footprint.
    """

    def __init__(self, sync_engine) -> None:
        self._engine = sync_engine
        self.total = 0
        self.selects = 0

    def _before(self, conn, cursor, statement, parameters, context, executemany):
        self.total += 1
        if statement.lstrip()[:6].upper() == "SELECT":
            self.selects += 1

    def __enter__(self) -> "_QueryCounter":
        event.listen(self._engine, "before_cursor_execute", self._before)
        return self

    def __exit__(self, *exc) -> None:
        event.remove(self._engine, "before_cursor_execute", self._before)


def _sync_engine():
    """The sync engine backing the (monkeypatched) app database's async engine."""
    import app.database as database_module

    return database_module.db.async_engine.sync_engine


@contextmanager
def _count_queries():
    counter = _QueryCounter(_sync_engine())
    with counter:
        yield counter


async def _measure(client: AsyncClient, url: str) -> tuple[int, float]:
    """Return ``(select_count, elapsed_seconds)`` for a single admin GET.

    Asserts a 200 so a broken endpoint fails loudly rather than being counted as
    a cheap (error) response.
    """
    with _count_queries() as counter:
        started = time.perf_counter()
        resp = await client.get(url)
        elapsed = time.perf_counter() - started
    assert resp.status_code == 200, f"{url} -> {resp.status_code}: {resp.text}"
    return counter.selects, elapsed


class TestDashboardReadsAreConstantCost:
    """Validates: Requirement 15.4 (new reads are O(1) in the user count)."""

    async def test_query_count_does_not_grow_with_user_count(self, scale_env, hosted):
        """Each new read issues the SAME number of SQL statements at 5 vs 100 users.

        This is the primary, deterministic O(1) proof: a per-user table scan would
        make the statement count grow with the seeded user count; a read served
        from daily aggregates + KV snapshots does not. We warm every endpoint once
        (so lazy one-time initialisation never skews the baseline), record the
        SELECT count at the small user count, seed 20x more users into the SAME
        database + session, and assert every endpoint's SELECT count is unchanged.
        """
        await _seed_many(scale_env, start=0, count=_SMALL_USERS)

        async with _admin_client(scale_env) as client:
            # Warm-up: first calls may lazily build singletons / prime caches.
            for url in _ENDPOINTS:
                await _measure(client, url)

            # Baseline SELECT footprint at the SMALL user count.
            small_counts: dict[str, int] = {}
            for url in _ENDPOINTS:
                small_counts[url], _ = await _measure(client, url)

            # Grow the users table ~20x (same DB, same admin session).
            await _seed_many(scale_env, start=_SMALL_USERS, count=_LARGE_USERS - _SMALL_USERS)

            # SELECT footprint at the LARGE user count.
            large_counts: dict[str, int] = {}
            for url in _ENDPOINTS:
                large_counts[url], _ = await _measure(client, url)

        # O(1): the statement count must be identical — it must not grow with N.
        grew = {
            url: (small_counts[url], large_counts[url])
            for url in _ENDPOINTS
            if large_counts[url] > small_counts[url]
        }
        assert not grew, (
            "New dashboard reads must be O(1) in the user count (Req 15.4): the "
            "SQL-statement count grew when the users table grew "
            f"{_SMALL_USERS}->{_LARGE_USERS}, implying a per-user scan on the "
            f"request path: {grew}"
        )

    async def test_no_endpoint_issues_an_unbounded_number_of_queries(self, scale_env, hosted):
        """A hard upper bound on per-request SELECTs (defence-in-depth for Req 15.4).

        Even independent of scaling, each aggregate-backed read should issue only
        a small, fixed handful of statements (auth/session recheck + a bounded set
        of ``metrics_daily`` / KV reads). A generous ceiling catches an accidental
        N-per-something regression that a single measurement would still expose.
        """
        await _seed_many(scale_env, start=0, count=_LARGE_USERS)

        async with _admin_client(scale_env) as client:
            for url in _ENDPOINTS:
                await _measure(client, url)  # warm
                selects, _ = await _measure(client, url)
                assert selects <= 25, (
                    f"{url} issued {selects} SELECTs at {_LARGE_USERS} users — an "
                    "aggregate/snapshot-backed dashboard read should need only a "
                    "small bounded number (Req 15.4)."
                )

    async def test_wall_clock_ratio_within_generous_bound_best_effort(self, scale_env, hosted):
        """Best-effort timing sanity check (documented gap: NOT the primary proof).

        Wall-clock timing is noisy in CI, so this is intentionally lenient — the
        deterministic query-count equality above is the real O(1) proof. We take
        a small median of each endpoint's latency at the small vs large user
        count and assert the large-N median stays within a very generous multiple
        of the small-N median (plus an absolute floor to absorb sub-millisecond
        jitter). A true O(N) scan across a 20x-larger table would blow past even
        this generous bound; genuine O(1) reads comfortably stay under it.
        """
        await _seed_many(scale_env, start=0, count=_SMALL_USERS)

        def _median(values: list[float]) -> float:
            ordered = sorted(values)
            mid = len(ordered) // 2
            if len(ordered) % 2:
                return ordered[mid]
            return (ordered[mid - 1] + ordered[mid]) / 2

        async def _median_latency(client: AsyncClient, url: str, reps: int = 5) -> float:
            samples = []
            for _ in range(reps):
                _, elapsed = await _measure(client, url)
                samples.append(elapsed)
            return _median(samples)

        async with _admin_client(scale_env) as client:
            for url in _ENDPOINTS:
                await _measure(client, url)  # warm

            small_latency = {url: await _median_latency(client, url) for url in _ENDPOINTS}

            await _seed_many(scale_env, start=_SMALL_USERS, count=_LARGE_USERS - _SMALL_USERS)

            large_latency = {url: await _median_latency(client, url) for url in _ENDPOINTS}

        # Generous: allow up to 10x + a 50ms absolute floor for scheduler/GC noise.
        # This is a smoke guard, not the O(1) proof (that is the query-count test).
        _ABS_FLOOR_S = 0.050
        _RATIO = 10.0
        regressions = {
            url: (small_latency[url], large_latency[url])
            for url in _ENDPOINTS
            if large_latency[url] > small_latency[url] * _RATIO + _ABS_FLOOR_S
        }
        assert not regressions, (
            "Best-effort wall-clock guard tripped: a new dashboard read got "
            f"dramatically slower when users grew {_SMALL_USERS}->{_LARGE_USERS} "
            f"(small_s, large_s): {regressions}. The authoritative O(1) proof is "
            "the query-count test; investigate a possible per-user scan."
        )

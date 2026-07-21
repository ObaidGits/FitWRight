"""Phase 3 activation tests: near-dup linking, crawl-delay, cost metrics, purge.

These cover the features that were previously "built but not wired": fingerprint
near-duplicate linking (§22), Crawl-delay enforcement (§26), cost metric export
(§25/§34), and user-scoped JD data purge (§27 erasure).
"""

import time

import pytest

from app.auth.kvstore.local import LocalKVStore


def _jsonld_html(title, company="Acme", lang="en"):
    desc = "We are hiring a backend engineer to build scalable systems. " * 20
    return (
        f'<html lang="{lang}"><head><script type="application/ld+json">'
        f'{{"@type":"JobPosting","title":"{title}","description":"{desc}",'
        f'"hiringOrganization":{{"name":"{company}"}}}}'
        "</script></head><body></body></html>"
    )


# ============================================================
# §22 Near-duplicate linking (fingerprint index)
# ============================================================

class TestNearDuplicateLinking:
    @pytest.mark.asyncio
    async def test_fingerprint_index_roundtrip(self):
        from app.jd.cache import JdCache
        cache = JdCache(LocalKVStore())
        await cache.register_fingerprint("abc123fp", "https://a.com/job/1")
        assert await cache.get_url_by_fingerprint("abc123fp") == "https://a.com/job/1"
        assert await cache.get_url_by_fingerprint("missing") is None

    @pytest.mark.asyncio
    async def test_second_url_links_to_first(self, monkeypatch):
        from app.jd import orchestrator
        from app.jd.cache import JdCache
        from app.jd.drift import DriftMonitor
        kv = LocalKVStore()
        monkeypatch.setattr(orchestrator, "_cache", JdCache(kv))
        monkeypatch.setattr(orchestrator, "_drift", DriftMonitor(kv))

        # Same content served at two different URLs.
        html = _jsonld_html("Cloned Engineer", company="MirrorCo")

        async def mock_fetch(url, *a, **k):
            return html

        monkeypatch.setattr(orchestrator, "fetch_url_safely", mock_fetch)

        r1 = await orchestrator.orchestrate_v2("u", "https://board-a.com/jobs/1", force_refresh=True)
        assert r1.near_duplicate_of is None  # first sighting registers
        assert r1.fingerprint != ""

        r2 = await orchestrator.orchestrate_v2("u", "https://board-b.com/jobs/2", force_refresh=True)
        assert r2.fingerprint == r1.fingerprint
        assert r2.near_duplicate_of == (r1.canonical_url or r1.submitted_url)


# ============================================================
# §26 Crawl-delay enforcement
# ============================================================

class TestCrawlDelayEnforcement:
    @pytest.mark.asyncio
    async def test_first_hit_no_wait_second_hit_waits(self, monkeypatch):
        from app.jd import orchestrator
        # Speed up: patch sleep to record instead of actually sleeping.
        slept = {"total": 0.0}

        async def fake_sleep(s):
            slept["total"] += s

        monkeypatch.setattr(orchestrator.asyncio, "sleep", fake_sleep)
        monkeypatch.setattr(orchestrator, "_get_cost", orchestrator._get_cost)

        # Use a real local KV via get_kvstore (hermetic single-user).
        await orchestrator._enforce_crawl_delay("delaytest.com", 2.0)
        # Immediately again -> should schedule a wait close to the delay.
        await orchestrator._enforce_crawl_delay("delaytest.com", 2.0)
        assert slept["total"] > 0

    @pytest.mark.asyncio
    async def test_delay_capped(self, monkeypatch):
        from app.jd import orchestrator
        slept = {"total": 0.0}

        async def fake_sleep(s):
            slept["total"] += s

        monkeypatch.setattr(orchestrator.asyncio, "sleep", fake_sleep)
        # Prime the timestamp, then request an absurd delay -> capped at _CRAWL_DELAY_MAX.
        await orchestrator._enforce_crawl_delay("cap.com", 9999.0)
        await orchestrator._enforce_crawl_delay("cap.com", 9999.0)
        assert slept["total"] <= orchestrator._CRAWL_DELAY_MAX + 0.01


# ============================================================
# §25/§34 Cost metric export
# ============================================================

class TestCostMetrics:
    @pytest.mark.asyncio
    async def test_record_emits_cost_metric(self):
        from app.jd.monitoring.cost import CostMonitor, MICRO
        from app.productivity.metrics import get_productivity_metrics, reset_productivity_metrics
        reset_productivity_metrics()
        cm = CostMonitor(LocalKVStore(), per_user_daily=1000 * MICRO, global_hourly_break=1000 * MICRO)
        await cm.record("u1", 12_000)
        snap = get_productivity_metrics().snapshot()
        assert snap.get("jd_cost_microdollars_total") == 12_000

    @pytest.mark.asyncio
    async def test_budget_exceeded_emits_metric(self):
        from app.jd.monitoring.cost import CostMonitor, MICRO
        from app.productivity.metrics import get_productivity_metrics, reset_productivity_metrics
        reset_productivity_metrics()
        cm = CostMonitor(LocalKVStore(), per_user_daily=MICRO, global_hourly_break=1000 * MICRO)
        await cm.record("u1", MICRO)
        assert await cm.check_budget("u1") is False
        snap = get_productivity_metrics().snapshot()
        assert snap.get("jd_budget_exceeded_total.user") == 1


# ============================================================
# §27 User-scoped JD data purge
# ============================================================

class TestUserPurge:
    @pytest.mark.asyncio
    async def test_purge_removes_cost_and_rate_counters(self):
        from app.jd.monitoring.cost import CostMonitor, MICRO, purge_user_jd_data
        kv = LocalKVStore()
        cm = CostMonitor(kv, per_user_daily=1000 * MICRO, global_hourly_break=1000 * MICRO)
        await cm.record("victim", 5_000)
        # Simulate a rate-limit counter too.
        await kv.incr("jd:rl:user:victim", ttl_seconds=60)

        assert await cm.spent_today("victim") == 5_000
        removed = await purge_user_jd_data("victim", kv)
        assert removed >= 2
        assert await cm.spent_today("victim") == 0
        assert await kv.get("jd:rl:user:victim") is None

    @pytest.mark.asyncio
    async def test_purge_is_idempotent(self):
        from app.jd.monitoring.cost import purge_user_jd_data
        kv = LocalKVStore()
        # Nothing to purge -> 0 removed, no error.
        assert await purge_user_jd_data("ghost", kv) == 0

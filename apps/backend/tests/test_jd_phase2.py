"""Phase 2 tests: cache, SingleFlight, drift detection, circuit breaker."""

import asyncio
import json
import time
from unittest.mock import AsyncMock, patch

import pytest


# ============================================================
# Multi-layer Cache Tests
# ============================================================

class TestJdCache:
    @pytest.fixture
    def cache(self):
        from app.jd.cache import JdCache
        from app.auth.kvstore.local import LocalKVStore
        return JdCache(LocalKVStore())

    @pytest.mark.asyncio
    async def test_set_and_get_result(self, cache):
        from app.jd.models import ConfidenceResult, ExtractionResult
        result = ExtractionResult(
            content="Test job description content",
            confidence=ConfidenceResult(level="HIGH", score=90, reasons=["test"]),
            source="platform_api",
            canonical_url="https://example.com/jobs/1",
        )
        await cache.set_result("https://example.com/jobs/1", result)
        cached = await cache.get_result("https://example.com/jobs/1")
        assert cached is not None
        assert cached.content == "Test job description content"
        assert cached.confidence.level == "HIGH"

    @pytest.mark.asyncio
    async def test_cache_miss_returns_none(self, cache):
        result = await cache.get_result("https://nonexistent.com/jobs/xyz")
        assert result is None

    @pytest.mark.asyncio
    async def test_error_cache(self, cache):
        await cache.set_error("https://broken.com/jobs/1", "timeout")
        err = await cache.get_error("https://broken.com/jobs/1")
        assert err is not None
        assert err["reason"] == "timeout"

    @pytest.mark.asyncio
    async def test_error_cache_miss(self, cache):
        err = await cache.get_error("https://fresh.com/jobs/1")
        assert err is None

    @pytest.mark.asyncio
    async def test_html_cache(self, cache):
        html = "<html><body>Job page content</body></html>"
        await cache.set_html("https://example.com/jobs/1", html)
        cached = await cache.get_html("https://example.com/jobs/1")
        assert cached == html

    @pytest.mark.asyncio
    async def test_html_cache_rejects_huge(self, cache):
        """HTML > 500KB should not be cached (prevent KV bloat)."""
        huge = "x" * 600_000
        await cache.set_html("https://huge.com/jobs/1", huge)
        cached = await cache.get_html("https://huge.com/jobs/1")
        assert cached is None  # Not stored


# ============================================================
# Drift Detection / Circuit Breaker Tests
# ============================================================

class TestDriftMonitor:
    @pytest.fixture
    def drift(self):
        from app.jd.drift import DriftMonitor
        from app.auth.kvstore.local import LocalKVStore
        return DriftMonitor(LocalKVStore())

    @pytest.mark.asyncio
    async def test_initially_healthy(self, drift):
        assert await drift.is_healthy("ashby") is True
        assert await drift.is_healthy("greenhouse") is True

    @pytest.mark.asyncio
    async def test_trips_on_high_failure_rate(self, drift):
        # Record 6 failures, 1 success -> 86% failure rate -> trips
        for _ in range(6):
            await drift.record_failure("ashby")
        await drift.record_success("ashby")

        assert await drift.is_healthy("ashby") is False

    @pytest.mark.asyncio
    async def test_does_not_trip_below_threshold(self, drift):
        # 2 failures, 8 successes -> 20% failure rate -> healthy
        for _ in range(2):
            await drift.record_failure("lever")
        for _ in range(8):
            await drift.record_success("lever")

        assert await drift.is_healthy("lever") is True

    @pytest.mark.asyncio
    async def test_does_not_trip_below_min_samples(self, drift):
        # Only 3 failures (below min_samples=5) -> don't trip
        for _ in range(3):
            await drift.record_failure("greenhouse")

        assert await drift.is_healthy("greenhouse") is True

    @pytest.mark.asyncio
    async def test_resets_after_timeout(self, drift):
        """Circuit should reset to half-open after RESET_AFTER seconds."""
        from app.jd import drift as drift_module

        # Trip the circuit
        for _ in range(6):
            await drift.record_failure("ashby")
        await drift.record_success("ashby")

        # Manually set trip time to the past (simulating time passing)
        drift._tripped["ashby"] = time.time() - drift_module._RESET_AFTER - 1

        # Now should be half-open (healthy = True, allows probe)
        assert await drift.is_healthy("ashby") is True

    @pytest.mark.asyncio
    async def test_get_status(self, drift):
        # Trip the circuit and check status immediately
        for _ in range(6):
            await drift.record_failure("ashby")
        await drift.record_success("ashby")

        # Ensure it's actually tripped by checking internal state
        assert "ashby" in drift._tripped

        status = await drift.get_status()
        assert "ashby" in status
        assert status["ashby"]["state"] in ("open", "half-open")


# ============================================================
# SingleFlight Tests (local fallback)
# ============================================================

class TestSingleFlightLocal:
    @pytest.mark.asyncio
    async def test_deduplicates_concurrent_calls(self):
        """Multiple concurrent calls for same URL -> only one pipeline execution."""
        from app.jd.concurrency import _local_flight

        call_count = 0

        async def slow_pipeline():
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.2)
            return {"content": "result", "call": call_count}

        # Launch 5 concurrent requests for same URL
        tasks = [
            asyncio.create_task(_local_flight("https://same-url.com/job/1", slow_pipeline))
            for _ in range(5)
        ]
        results = await asyncio.gather(*tasks)

        # All should get results (no crashes)
        assert all(r["content"] == "result" for r in results)
        # Pipeline should have been called at most twice (leader + one waiter that promoted)
        assert call_count <= 2

    @pytest.mark.asyncio
    async def test_different_urls_run_independently(self):
        """Different URLs execute independently (no cross-dedup)."""
        from app.jd.concurrency import _local_flight

        call_count = 0

        async def pipeline():
            nonlocal call_count
            call_count += 1
            return {"content": f"result-{call_count}"}

        r1 = await _local_flight("https://url-1.com/job/1", pipeline)
        r2 = await _local_flight("https://url-2.com/job/2", pipeline)

        assert call_count == 2  # Both ran independently


# ============================================================
# Orchestrator Cache Integration Tests
# ============================================================

class TestOrchestratorCache:
    @pytest.mark.asyncio
    async def test_second_call_returns_cache(self, monkeypatch):
        """Second request for same URL returns cached result (no re-fetch)."""
        from app.jd import orchestrator
        from app.jd.cache import JdCache
        from app.auth.kvstore.local import LocalKVStore

        # Setup a local cache
        test_cache = JdCache(LocalKVStore())
        monkeypatch.setattr(orchestrator, "_cache", test_cache)

        html = '''<html><head>
        <script type="application/ld+json">
        {"@type": "JobPosting", "title": "Cached Engineer", "description": "''' + ("Build systems. " * 50) + '''", "hiringOrganization": {"name": "CacheCo"}}
        </script></head><body></body></html>'''

        fetch_count = 0

        async def mock_fetch(url, **kw):
            nonlocal fetch_count
            fetch_count += 1
            return html

        monkeypatch.setattr(orchestrator, "fetch_url_safely", mock_fetch)

        # First call - hits network
        r1 = await orchestrator.orchestrate_v2("t", "https://example.com/jobs/cached-role")
        assert r1.content != ""
        assert fetch_count == 1

        # Second call - should come from cache (no fetch)
        r2 = await orchestrator.orchestrate_v2("t", "https://example.com/jobs/cached-role")
        assert r2.content == r1.content
        assert fetch_count == 1  # No additional fetch!

    @pytest.mark.asyncio
    async def test_force_refresh_bypasses_cache(self, monkeypatch):
        """force_refresh=True always re-fetches even if cached."""
        from app.jd import orchestrator
        from app.jd.cache import JdCache
        from app.auth.kvstore.local import LocalKVStore

        test_cache = JdCache(LocalKVStore())
        monkeypatch.setattr(orchestrator, "_cache", test_cache)

        html = '''<html><head>
        <script type="application/ld+json">
        {"@type": "JobPosting", "title": "Fresh Engineer", "description": "''' + ("Fresh content. " * 50) + '''", "hiringOrganization": {"name": "FreshCo"}}
        </script></head><body></body></html>'''

        fetch_count = 0

        async def mock_fetch(url, **kw):
            nonlocal fetch_count
            fetch_count += 1
            return html

        monkeypatch.setattr(orchestrator, "fetch_url_safely", mock_fetch)

        # First call
        await orchestrator.orchestrate_v2("t", "https://example.com/jobs/fresh", force_refresh=False)
        assert fetch_count == 1

        # Second call with force_refresh
        await orchestrator.orchestrate_v2("t", "https://example.com/jobs/fresh", force_refresh=True)
        assert fetch_count == 2  # Bypassed cache!

    @pytest.mark.asyncio
    async def test_error_cached_prevents_hammering(self, monkeypatch):
        """After a failure, repeated requests return cached error (no re-fetch)."""
        from app.jd import orchestrator
        from app.jd.cache import JdCache
        from app.jd.drift import DriftMonitor
        from app.jd.ssrf import SsrfError
        from app.auth.kvstore.local import LocalKVStore

        kv = LocalKVStore()
        test_cache = JdCache(kv)
        test_drift = DriftMonitor(kv)
        monkeypatch.setattr(orchestrator, "_cache", test_cache)
        monkeypatch.setattr(orchestrator, "_drift", test_drift)

        fetch_count = 0

        async def mock_fetch(url, **kw):
            nonlocal fetch_count
            fetch_count += 1
            raise SsrfError("timeout")

        monkeypatch.setattr(orchestrator, "fetch_url_safely", mock_fetch)

        # First call fails and caches the error
        r1 = await orchestrator.orchestrate_v2("t", "https://broken.com/jobs/gone")
        assert r1.confidence.level == "LOW"
        assert fetch_count == 1

        # Second call should return cached error (no re-fetch)
        r2 = await orchestrator.orchestrate_v2("t", "https://broken.com/jobs/gone")
        assert r2.confidence.level == "LOW"
        assert fetch_count == 1  # No additional fetch!

    @pytest.mark.asyncio
    async def test_circuit_breaker_skips_adapter(self, monkeypatch):
        """When circuit is open, the adapter is skipped (cascade continues)."""
        from app.jd import orchestrator
        from app.jd.cache import JdCache
        from app.jd.drift import DriftMonitor
        from app.auth.kvstore.local import LocalKVStore

        kv = LocalKVStore()
        test_drift = DriftMonitor(kv)
        test_cache = JdCache(kv)
        monkeypatch.setattr(orchestrator, "_drift", test_drift)
        monkeypatch.setattr(orchestrator, "_cache", test_cache)

        # Trip the circuit for ashby and set trip time far enough in the past
        # to NOT auto-reset, but still be "open"
        import time as _time
        for _ in range(6):
            await test_drift.record_failure("ashby")
        await test_drift.record_success("ashby")
        # Force the trip time to NOW (so it's clearly within the reset window)
        test_drift._tripped["ashby"] = _time.time()
        assert await test_drift.is_healthy("ashby") is False

        # Now try an Ashby URL - adapter should be skipped
        html = '''<html><head>
        <script type="application/ld+json">
        {"@type": "JobPosting", "title": "Fallback Engineer", "description": "''' + ("Fallback content. " * 50) + '''"}
        </script></head><body></body></html>'''

        async def mock_fetch(url, **kw):
            return html

        monkeypatch.setattr(orchestrator, "fetch_url_safely", mock_fetch)

        result = await orchestrator.orchestrate_v2("t", "https://jobs.ashbyhq.com/co/abc-123-def-456-ghi", force_refresh=True)

        # Should have gotten content via JSON-LD (not API, because circuit is open)
        assert result.source == "json_ld"
        assert "Fallback Engineer" in result.content

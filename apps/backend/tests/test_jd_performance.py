"""Performance validation for JD extraction v2 (Phase 1 SLO targets).

Runs the extraction pipeline against a distribution of fixture types and
measures latency percentiles. SLO targets:
  - p50 < 3s
  - p95 < 12s
  - Playwright launches ONLY when levels 1-4 fail

These tests use mocked network to measure pipeline overhead deterministically.
Live latency depends on target site response time (not our control).
"""

from __future__ import annotations

import asyncio
import statistics
import time
from unittest.mock import AsyncMock, patch

import pytest

from app.jd.models import ConfidenceResult, ExtractionResult

# --- Fixtures representing different path types ---

_API_HTML = "<html><body>empty shell</body></html>"  # SPA that triggers API path

_JSONLD_HTML = '''<html><head>
<script type="application/ld+json">
{"@type": "JobPosting", "title": "Engineer", "description": "''' + ("Build systems. " * 50) + '''", "hiringOrganization": {"name": "Co"}}
</script></head><body><div id="root"></div></body></html>'''

_DOM_HTML = '''<html><body>
<article class="job-description">
<h1>Product Manager</h1>
<h2>Responsibilities</h2>
''' + "<li>Do important work</li>\n" * 30 + '''
<h2>Qualifications</h2>
''' + "<li>Have experience</li>\n" * 20 + '''
</article></body></html>'''

_SPA_HTML = '<html><body><div id="root"></div><script src="/app.js"></script></body></html>'


class TestPerformanceSLO:
    """Validate that pipeline latency meets SLO targets."""

    @pytest.mark.asyncio
    async def test_api_path_under_1s(self, monkeypatch):
        """API adapter path should complete in < 1s (mock network = instant)."""
        from app.jd import orchestrator
        import json

        api_response = json.dumps({
            "info": {"title": "Eng", "descriptionHtml": "<p>" + "x " * 300 + "</p>",
                     "organizationName": "Co", "location": "NYC"}
        })

        async def mock_fetch(url, **kw):
            if "api.ashbyhq.com" in url:
                return api_response
            return _API_HTML

        monkeypatch.setattr(orchestrator, "fetch_url_safely", mock_fetch)

        start = time.perf_counter()
        result = await orchestrator.orchestrate_v2("t", "https://jobs.ashbyhq.com/co/abc-123-def-456-ghi")
        elapsed = time.perf_counter() - start

        assert result.source == "platform_api"
        assert elapsed < 1.0, f"API path took {elapsed:.2f}s (SLO: < 1s)"

    @pytest.mark.asyncio
    async def test_jsonld_path_under_1s(self, monkeypatch):
        """JSON-LD path should complete in < 1s."""
        from app.jd import orchestrator

        async def mock_fetch(url, **kw):
            return _JSONLD_HTML

        monkeypatch.setattr(orchestrator, "fetch_url_safely", mock_fetch)

        start = time.perf_counter()
        result = await orchestrator.orchestrate_v2("t", "https://example.com/jobs/eng")
        elapsed = time.perf_counter() - start

        assert result.source == "json_ld"
        assert elapsed < 1.0, f"JSON-LD path took {elapsed:.2f}s (SLO: < 1s)"

    @pytest.mark.asyncio
    async def test_dom_path_under_1s(self, monkeypatch):
        """DOM extraction path should complete in < 1s."""
        from app.jd import orchestrator

        async def mock_fetch(url, **kw):
            return _DOM_HTML

        monkeypatch.setattr(orchestrator, "fetch_url_safely", mock_fetch)

        start = time.perf_counter()
        result = await orchestrator.orchestrate_v2("t", "https://careers.company.com/jobs/pm")
        elapsed = time.perf_counter() - start

        assert result.source == "dom_semantic"
        assert elapsed < 1.0, f"DOM path took {elapsed:.2f}s (SLO: < 1s)"

    @pytest.mark.asyncio
    async def test_playwright_path_under_15s(self, monkeypatch):
        """Playwright path should complete in < 15s (mocked render = fast)."""
        from app.jd import orchestrator

        async def mock_fetch(url, **kw):
            return _SPA_HTML

        async def mock_render(url, **kw):
            await asyncio.sleep(0.1)  # Simulate 100ms render
            return ExtractionResult(
                content="Job content " * 50,
                confidence=ConfidenceResult(level="MEDIUM", score=55, reasons=[]),
                source="headless_dom",
            )

        monkeypatch.setattr(orchestrator, "fetch_url_safely", mock_fetch)
        monkeypatch.setattr(orchestrator, "render_and_extract", mock_render)

        start = time.perf_counter()
        result = await orchestrator.orchestrate_v2("t", "https://spa.myworkdayjobs.com/jobs/123")
        elapsed = time.perf_counter() - start

        assert result.source == "headless_dom"
        assert elapsed < 15.0, f"Playwright path took {elapsed:.2f}s (SLO: < 15s)"

    @pytest.mark.asyncio
    async def test_playwright_only_when_needed(self, monkeypatch):
        """Playwright must NOT launch when levels 1-4 succeed."""
        from app.jd import orchestrator

        render_called = False
        original_render = orchestrator.render_and_extract

        async def spy_render(url, **kw):
            nonlocal render_called
            render_called = True
            return await original_render(url, **kw)

        async def mock_fetch(url, **kw):
            return _JSONLD_HTML  # JSON-LD present → should stop at level 2

        monkeypatch.setattr(orchestrator, "fetch_url_safely", mock_fetch)
        monkeypatch.setattr(orchestrator, "render_and_extract", spy_render)

        result = await orchestrator.orchestrate_v2("t", "https://example.com/jobs/eng")

        assert result.source == "json_ld"
        assert render_called is False, "Playwright was launched unnecessarily!"

    @pytest.mark.asyncio
    async def test_latency_distribution(self, monkeypatch):
        """Run 20 extractions and verify p50 < 3s, p95 < 12s (mocked)."""
        from app.jd import orchestrator

        # Distribution: 60% JSON-LD, 25% DOM, 15% Playwright (realistic)
        fixtures = (
            [_JSONLD_HTML] * 12 +  # 60%
            [_DOM_HTML] * 5 +       # 25%
            [_SPA_HTML] * 3         # 15%
        )

        async def mock_fetch(url, **kw):
            # Return fixture based on URL hash for determinism
            idx = hash(url) % len(fixtures)
            return fixtures[idx]

        async def mock_render(url, **kw):
            await asyncio.sleep(0.05)
            return ExtractionResult(
                content="Rendered job " * 50,
                confidence=ConfidenceResult(level="MEDIUM", score=55, reasons=[]),
                source="headless_dom",
            )

        monkeypatch.setattr(orchestrator, "fetch_url_safely", mock_fetch)
        monkeypatch.setattr(orchestrator, "render_and_extract", mock_render)

        latencies = []
        for i in range(20):
            start = time.perf_counter()
            await orchestrator.orchestrate_v2("t", f"https://jobs-{i}.example.com/role-{i}")
            latencies.append(time.perf_counter() - start)

        p50 = statistics.median(latencies)
        p95 = sorted(latencies)[int(0.95 * len(latencies))]

        assert p50 < 3.0, f"p50 = {p50:.2f}s exceeds 3s SLO"
        assert p95 < 12.0, f"p95 = {p95:.2f}s exceeds 12s SLO"

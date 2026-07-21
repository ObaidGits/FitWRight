"""Phase 4 (Advanced) tests: enterprise adapters, ML scorer, browser-extension
fallback, distributed render gate + edge rendering, adapter health + self-healing,
and employer webhook ingestion.
"""

from urllib.parse import urlparse

import pytest

from app.auth.kvstore.local import LocalKVStore


# ============================================================
# Enterprise detection adapters
# ============================================================

class TestEnterpriseAdapters:
    @pytest.mark.parametrize("url,expected", [
        ("https://company.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX/job/123", "oracle"),
        ("https://career5.successfactors.com/career?company=Acme&jobId=1", "successfactors"),
        ("https://careers.sap.com/job/123", "successfactors"),
        ("https://acme.taleo.net/careersection/jobdetail.ftl?job=1", "taleo"),
        ("https://acme.bamboohr.com/careers/42", "bamboohr"),
        ("https://ats.rippling.com/acme/jobs/abc", "rippling"),
    ])
    def test_detection(self, url, expected):
        from app.jd.adapters.registry import detect_platform
        assert detect_platform(url) == expected

    def test_detection_only_no_api(self):
        from app.jd.adapters.enterprise import OracleAdapter, TaleoAdapter
        assert OracleAdapter().extract_api_url(urlparse("https://x.oraclecloud.com/job/1")) is None
        assert TaleoAdapter().extract_api_url(urlparse("https://x.taleo.net/job/1")) is None

    def test_requires_js_flags(self):
        from app.jd.adapters.enterprise import (
            OracleAdapter, TaleoAdapter, SuccessFactorsAdapter, RipplingAdapter, BambooHrAdapter,
        )
        assert OracleAdapter().REQUIRES_JS is True
        assert TaleoAdapter().REQUIRES_JS is True
        assert SuccessFactorsAdapter().REQUIRES_JS is True
        assert RipplingAdapter().REQUIRES_JS is True
        # BambooHR is server-rendered -> no browser needed.
        assert BambooHrAdapter().REQUIRES_JS is False

    def test_decision_forces_browser_for_js_platforms(self):
        from app.jd.browser.decision import needs_browser
        # Empty shell + JS-only domain -> browser required.
        assert needs_browser("<html></html>", "x.oraclecloud.com", 0) is True
        assert needs_browser("<html></html>", "acme.taleo.net", 0) is True
        assert needs_browser("<html></html>", "ats.rippling.com", 0) is True


# ============================================================
# ML content scorer
# ============================================================

class TestMlScorer:
    def test_separates_jd_from_nav(self):
        from app.jd.ml_scorer import score_content
        jd = ("Senior Backend Engineer\n\nResponsibilities:\n- Build scalable APIs in Python\n"
              "- Mentor engineers\n\nRequirements:\n- 5+ years experience\n- Distributed systems knowledge")
        nav = "Home About Pricing Sign in Create account. We use cookies. © 2026 All rights reserved. Privacy Policy"
        assert score_content(jd) > 0.7
        assert score_content(nav) < 0.3

    def test_feature_vector_shape(self):
        from app.jd.ml_scorer import extract_features, FEATURE_NAMES
        feats = extract_features("Some job description text with responsibilities and requirements.")
        assert len(feats) == len(FEATURE_NAMES)
        assert feats[0] == 1.0  # bias

    def test_training_is_reproducible(self):
        from app.jd.ml_scorer import train
        m1 = train()
        m2 = train()
        assert m1.weights == m2.weights  # deterministic

    def test_empty_text_is_low(self):
        from app.jd.ml_scorer import score_content
        assert score_content("") < 0.5

    @pytest.mark.asyncio
    async def test_ml_downgrades_junk_dom_result(self, monkeypatch):
        """With ML scoring on, a junk DOM result gets downgraded to LOW."""
        from app.jd import orchestrator
        from app.jd.models import ConfidenceResult, ExtractionResult
        from app.config import settings

        monkeypatch.setattr(settings, "jd_ml_scoring_enabled", True)

        junk = ExtractionResult(
            content="Home About Pricing Sign in Create account cookies © 2026 all rights reserved menu navigation " * 3,
            confidence=ConfidenceResult(level="MEDIUM", score=60, reasons=[]),
            source="dom_semantic",
        )
        out = orchestrator._finalize(junk)
        assert out.confidence.level == "LOW"
        assert any("quality check" in w for w in out.explanation.warnings)


# ============================================================
# Browser-extension fallback (rendered DOM)
# ============================================================

class TestRenderedFallback:
    def test_extracts_from_rendered_jsonld(self):
        from app.jd.rendered import extract_from_rendered
        desc = "We are hiring a backend engineer to build scalable systems. " * 20
        html = (
            '<html lang="en"><head><script type="application/ld+json">'
            f'{{"@type":"JobPosting","title":"Rendered Role","description":"{desc}",'
            '"hiringOrganization":{"name":"ExtCo"}}</script></head><body></body></html>'
        )
        r = extract_from_rendered("https://spa.com/jobs/1", html)
        assert r.content
        assert "Rendered Role" in r.content
        assert r.source == "headless_dom"

    def test_extracts_from_rendered_dom(self):
        from app.jd.rendered import extract_from_rendered
        body = "<article><h1>Data Engineer</h1><p>" + ("Build data pipelines and own the warehouse. " * 20) + "</p></article>"
        html = f"<html><body>{body}</body></html>"
        r = extract_from_rendered("https://spa.com/jobs/2", html)
        assert r.content
        assert r.source == "headless_dom"

    def test_login_page_rejected(self):
        from app.jd.rendered import extract_from_rendered
        html = "<html><body><h1>Please log in to view</h1><form>sign in to continue</form></body></html>"
        r = extract_from_rendered("https://spa.com/jobs/3", html)
        assert r.content == ""
        assert r.error_code in ("login_required", "no_content")

    def test_empty_html_rejected(self):
        from app.jd.rendered import extract_from_rendered
        r = extract_from_rendered("https://spa.com/jobs/4", "   ")
        assert r.content == ""
        assert r.error_code == "empty_html"


# ============================================================
# Distributed render gate + edge rendering
# ============================================================

class TestDistributedRenderGate:
    @pytest.mark.asyncio
    async def test_disabled_is_noop(self):
        from app.jd.browser.distributed import DistributedRenderGate
        gate = DistributedRenderGate(LocalKVStore(), 0)
        assert gate.enabled is False
        assert await gate.acquire() is True
        await gate.release()  # no error

    @pytest.mark.asyncio
    async def test_enforces_global_cap(self):
        from app.jd.browser.distributed import DistributedRenderGate
        kv = LocalKVStore()
        g1 = DistributedRenderGate(kv, 2)
        g2 = DistributedRenderGate(kv, 2)
        g3 = DistributedRenderGate(kv, 2)
        assert await g1.acquire() is True
        assert await g2.acquire() is True
        assert await g3.acquire() is False  # over the global cap of 2
        await g1.release()
        g4 = DistributedRenderGate(kv, 2)
        assert await g4.acquire() is True  # slot freed

    @pytest.mark.asyncio
    async def test_edge_render_not_configured(self, monkeypatch):
        from app.jd.browser import distributed
        from app.config import settings
        monkeypatch.setattr(settings, "jd_edge_render_url", "")
        assert distributed.edge_render_configured() is False
        assert await distributed.render_via_edge("https://x.com/j") is None

    @pytest.mark.asyncio
    async def test_edge_render_configured_returns_html(self, monkeypatch):
        from app.jd.browser import distributed
        from app.config import settings
        monkeypatch.setattr(settings, "jd_edge_render_url", "https://edge.internal/render")

        class _Resp:
            status_code = 200
            headers = {"content-type": "application/json"}
            def json(self):
                return {"html": "<html><body>rendered</body></html>"}
            @property
            def text(self):
                return "<html><body>rendered</body></html>"

        class _Client:
            def __init__(self, *a, **k): ...
            async def __aenter__(self):
                return self
            async def __aexit__(self, *a): ...
            async def post(self, url, **k):
                return _Resp()

        import httpx
        monkeypatch.setattr(httpx, "AsyncClient", _Client)
        html = await distributed.render_via_edge("https://x.com/j")
        assert html and "rendered" in html


# ============================================================
# Adapter health + self-healing
# ============================================================

class TestAdapterHealthAndSelfHealing:
    @pytest.mark.asyncio
    async def test_health_snapshot_healthy_initially(self, monkeypatch):
        from app.jd import orchestrator
        from app.jd.drift import DriftMonitor
        monkeypatch.setattr(orchestrator, "_drift", DriftMonitor(LocalKVStore()))
        from app.jd.health import adapter_health_snapshot
        snap = await adapter_health_snapshot()
        assert snap["overall"] == "healthy"
        assert "ashby" in snap["adapters"]
        assert snap["adapters"]["ashby"]["state"] == "closed"

    @pytest.mark.asyncio
    async def test_health_snapshot_degraded_when_tripped(self, monkeypatch):
        from app.jd import orchestrator
        from app.jd.drift import DriftMonitor
        drift = DriftMonitor(LocalKVStore())
        monkeypatch.setattr(orchestrator, "_drift", drift)
        for _ in range(6):
            await drift.record_failure("workday")
        await drift.record_success("workday")
        from app.jd.health import adapter_health_snapshot
        snap = await adapter_health_snapshot()
        assert snap["overall"] == "degraded"
        assert "workday" in snap["degraded"]

    @pytest.mark.asyncio
    async def test_self_heals_after_probe_success(self):
        import time as _time
        from app.jd.drift import DriftMonitor, _RESET_AFTER
        drift = DriftMonitor(LocalKVStore())
        # Trip it.
        for _ in range(6):
            await drift.record_failure("lever")
        await drift.record_success("lever")
        assert await drift.is_healthy("lever") is False
        # Time passes -> half-open probe allowed.
        drift._tripped["lever"] = _time.time() - _RESET_AFTER - 1
        assert await drift.is_healthy("lever") is True  # half-open
        # A probe success fully heals + resets counters.
        await drift.record_success("lever")
        stats = await drift.stats("lever")
        assert stats["state"] == "closed"
        assert stats["failure"] == 0

    @pytest.mark.asyncio
    async def test_reopens_on_probe_failure(self):
        import time as _time
        from app.jd.drift import DriftMonitor, _RESET_AFTER
        drift = DriftMonitor(LocalKVStore())
        for _ in range(6):
            await drift.record_failure("icims")
        await drift.record_success("icims")
        drift._tripped["icims"] = _time.time() - _RESET_AFTER - 1
        assert await drift.is_healthy("icims") is True  # half-open probe
        # Probe fails -> immediately re-open.
        await drift.record_failure("icims")
        assert await drift.is_healthy("icims") is False


# ============================================================
# Employer webhook ingestion
# ============================================================

class TestWebhook:
    def test_signature_valid(self):
        import hashlib
        import hmac
        from app.jd.webhook import verify_signature
        secret = "s3cr3t"
        body = b'{"url":"https://x.com/j"}'
        sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        assert verify_signature(secret, body, sig) is True
        assert verify_signature(secret, body, "sha256=" + sig) is True

    def test_signature_invalid(self):
        from app.jd.webhook import verify_signature
        assert verify_signature("s", b"body", "deadbeef") is False
        assert verify_signature("s", b"body", None) is False
        assert verify_signature("", b"body", "x") is False

    def test_build_result_from_payload(self):
        from app.jd.webhook import build_result_from_payload
        r = build_result_from_payload({
            "url": "https://acme.com/careers/senior-eng?utm_source=x",
            "title": "Senior Engineer",
            "company": "Acme",
            "location": "Remote",
            "description_html": "<p>Build great software with a talented team.</p>",
        })
        assert r.confidence.level == "HIGH"
        assert r.confidence.score == 99
        assert "Senior Engineer" in r.content
        assert "Build great software" in r.content
        assert "utm_source" not in r.canonical_url  # canonicalized
        assert r.fingerprint != ""

    def test_build_requires_url_and_description(self):
        from app.jd.webhook import build_result_from_payload
        with pytest.raises(ValueError):
            build_result_from_payload({"title": "x"})
        with pytest.raises(ValueError):
            build_result_from_payload({"url": "https://x.com/j"})

    @pytest.mark.asyncio
    async def test_ingest_stores_in_cache(self, monkeypatch):
        from app.jd import orchestrator
        from app.jd.cache import JdCache
        from app.jd.canonicalize import canonicalize_url
        cache = JdCache(LocalKVStore())
        monkeypatch.setattr(orchestrator, "_cache", cache)

        from app.jd.webhook import ingest_webhook
        url = "https://acme.com/careers/eng"
        ack = await ingest_webhook({
            "url": url,
            "title": "Engineer",
            "company": "Acme",
            "description": "Build and ship reliable software. " * 10,
        })
        assert ack["status"] == "ok"
        # A subsequent cache lookup returns the authoritative pushed result.
        cached = await cache.get_result(canonicalize_url(url))
        assert cached is not None
        assert "Engineer" in cached.content
        assert cached.confidence.level == "HIGH"

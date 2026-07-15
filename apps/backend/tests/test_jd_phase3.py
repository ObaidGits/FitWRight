"""Phase 3 tests: new adapters, PDF, i18n, fingerprinting, robots, cost, privacy.

Covers §20 (PDF), §21 (i18n), §22 (fingerprinting), §25 (cost), §26 (robots),
§27 (privacy), §29 (schema evolution), plus SmartRecruiters/LinkedIn/Indeed
adapters and their orchestrator integration.
"""

import io

import pytest

from app.auth.kvstore.local import LocalKVStore


# ============================================================
# Helpers
# ============================================================

def _make_text_pdf(n_lines: int = 30) -> bytes:
    """Build a minimal valid single-page PDF with extractable native text."""
    lines = [
        f"(Responsibilities include building scalable APIs and mentoring line {i}) Tj 0 -14 Td"
        for i in range(n_lines)
    ]
    body = "BT /F1 12 Tf 72 750 Td " + " ".join(lines) + " ET"
    stream = body.encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    pdf = b"%PDF-1.4\n"
    offs = []
    for i, o in enumerate(objs, 1):
        offs.append(len(pdf))
        pdf += str(i).encode() + b" 0 obj\n" + o + b"\nendobj\n"
    xref_pos = len(pdf)
    pdf += b"xref\n0 " + str(len(objs) + 1).encode() + b"\n0000000000 65535 f \n"
    for off in offs:
        pdf += ("%010d 00000 n \n" % off).encode()
    pdf += (
        b"trailer\n<< /Size " + str(len(objs) + 1).encode()
        + b" /Root 1 0 R >>\nstartxref\n" + str(xref_pos).encode() + b"\n%%EOF"
    )
    return pdf


def _jsonld_html(title="Senior Engineer", desc=None, company="Acme", lang="en") -> str:
    desc = desc or ("We are hiring a backend engineer. " * 40)
    return (
        f'<html lang="{lang}"><head>'
        '<script type="application/ld+json">'
        f'{{"@type": "JobPosting", "title": "{title}", '
        f'"description": "{desc}", "hiringOrganization": {{"name": "{company}"}}}}'
        "</script></head><body></body></html>"
    )


# ============================================================
# §27 Privacy — canonical token stripping + redact_url
# ============================================================

class TestPrivacyCanonicalization:
    def test_strips_token_params(self):
        from app.jd.canonicalize import canonicalize_url
        out = canonicalize_url("https://x.com/job?token=abc123&id=5")
        assert "token" not in out
        assert "id=5" in out

    def test_strips_aws_signed_params(self):
        from app.jd.canonicalize import canonicalize_url
        out = canonicalize_url("https://s3.x.com/f.pdf?X-Amz-Signature=deadbeef&X-Amz-Credential=k")
        assert "amz" not in out.lower()

    def test_redact_url_drops_query(self):
        from app.jd.canonicalize import redact_url
        red = redact_url("https://x.com/job/123?token=secret")
        assert "secret" not in red
        assert "token" not in red
        assert "x.com/job/123" in red
        assert "#" in red  # includes a short hash

    def test_redact_never_raises(self):
        from app.jd.canonicalize import redact_url
        assert redact_url("::::not a url::::")  # returns something, no exception


# ============================================================
# §21 Internationalization
# ============================================================

class TestI18n:
    def test_lang_from_html_attr(self):
        from app.jd.i18n import detect_language
        assert detect_language(html='<html lang="de-DE"><body>x</body></html>') == "de"

    def test_lang_from_header(self):
        from app.jd.i18n import detect_language
        assert detect_language(content_language_header="fr-FR,fr;q=0.9") == "fr"

    def test_lang_japanese_by_script(self):
        from app.jd.i18n import detect_language
        assert detect_language(text="業務内容 に応募する エンジニア") == "ja"

    def test_lang_heuristic_spanish(self):
        from app.jd.i18n import detect_language
        assert detect_language(text="Buscamos un ingeniero para el equipo con las habilidades y para crecer") == "es"

    def test_supported_language_keywords(self):
        from app.jd.i18n import section_keywords_for
        kw = section_keywords_for("de")
        assert kw is not None
        assert "aufgaben" in kw["responsibilities"]

    def test_unsupported_language_returns_none(self):
        from app.jd.i18n import section_keywords_for
        assert section_keywords_for("ko") is None
        assert section_keywords_for("tr") is None

    def test_salary_en_format(self):
        from app.jd.i18n import parse_salary
        s = parse_salary("$120,000 - $150,000 per year")
        assert s["min"] == 120000.0 and s["max"] == 150000.0
        assert s["currency"] == "USD" and s["period"] == "YEAR"

    def test_salary_de_format(self):
        from app.jd.i18n import parse_salary
        s = parse_salary("120.000 - 150.000 EUR pro Jahr")
        assert s["min"] == 120000.0 and s["max"] == 150000.0
        assert s["currency"] == "EUR"

    def test_salary_none_when_no_numbers(self):
        from app.jd.i18n import parse_salary
        assert parse_salary("competitive salary") is None


# ============================================================
# §22 Content Fingerprinting
# ============================================================

class TestFingerprint:
    def test_deterministic(self):
        from app.jd.fingerprint import content_fingerprint
        a = content_fingerprint("Eng", "Acme", "SF", "x" * 800)
        b = content_fingerprint("Eng", "Acme", "SF", "x" * 800)
        assert a == b

    def test_different_roles_differ(self):
        from app.jd.fingerprint import content_fingerprint
        intro = "About Acme, a leading company. " * 8  # shared boilerplate > 200 chars
        a = content_fingerprint("Eng", "Acme", "SF", intro + "Build the payments API." * 20)
        b = content_fingerprint("Eng", "Acme", "SF", intro + "Design the ML platform." * 20)
        assert a != b

    def test_simhash_near_duplicate(self):
        from app.jd.fingerprint import is_near_duplicate
        a = "the quick brown fox jumps over the lazy dog backend engineer python role"
        b = "the quick brown fox jumps over the lazy dog backend engineer python role extra"
        assert is_near_duplicate(a, b) is True

    def test_simhash_distinct(self):
        from app.jd.fingerprint import is_near_duplicate
        a = "backend python engineer building scalable microservices in the cloud"
        b = "senior graphic designer creating brand identity and marketing visuals"
        assert is_near_duplicate(a, b) is False


# ============================================================
# §26 Robots
# ============================================================

class TestRobotsParsing:
    def test_named_ua_disallow_beats_star_allow(self):
        from app.jd.robots import _parse_robots, _decide
        groups = _parse_robots(
            "User-agent: *\nAllow: /\n\nUser-agent: FitWrightBot\nDisallow: /private/\n"
        )
        assert _decide(groups, "/private/x").allowed is False
        assert _decide(groups, "/jobs/x").allowed is True

    def test_adr13_no_named_rule_proceeds(self):
        """ADR-13: blanket * Disallow with no named rule → we proceed."""
        from app.jd.robots import _parse_robots, _decide
        groups = _parse_robots("User-agent: *\nDisallow: /\n")
        assert _decide(groups, "/anything").allowed is True

    def test_crawl_delay_parsed(self):
        from app.jd.robots import _parse_robots, _decide
        groups = _parse_robots("User-agent: FitWrightBot\nAllow: /\nCrawl-delay: 3\n")
        assert _decide(groups, "/jobs/1").crawl_delay == 3.0

    def test_wildcard_path_matching(self):
        from app.jd.robots import _parse_robots, _decide
        groups = _parse_robots("User-agent: FitWrightBot\nDisallow: /*/secret\n")
        assert _decide(groups, "/a/secret").allowed is False


class TestRobotsChecker:
    @pytest.mark.asyncio
    async def test_check_allows_when_disallowed_for_star_only(self, monkeypatch):
        import app.jd.robots as rmod

        async def fake_fetch(url, *a, **k):
            return "User-agent: *\nDisallow: /\n"

        monkeypatch.setattr(rmod, "fetch_url_safely", fake_fetch)
        checker = rmod.RobotsChecker(LocalKVStore())
        decision = await checker.check("https://site.com/jobs/1")
        assert decision.allowed is True  # ADR-13

    @pytest.mark.asyncio
    async def test_check_blocks_named_disallow(self, monkeypatch):
        import app.jd.robots as rmod

        async def fake_fetch(url, *a, **k):
            return "User-agent: FitWrightBot\nDisallow: /jobs/\n"

        monkeypatch.setattr(rmod, "fetch_url_safely", fake_fetch)
        checker = rmod.RobotsChecker(LocalKVStore())
        decision = await checker.check("https://site.com/jobs/1")
        assert decision.allowed is False

    @pytest.mark.asyncio
    async def test_fail_open_on_fetch_error(self, monkeypatch):
        import app.jd.robots as rmod

        async def boom(url, *a, **k):
            raise rmod.SsrfError("timeout")

        monkeypatch.setattr(rmod, "fetch_url_safely", boom)
        checker = rmod.RobotsChecker(LocalKVStore())
        decision = await checker.check("https://site.com/jobs/1")
        assert decision.allowed is True  # advisory file unreachable → allow


# ============================================================
# §25 Cost Monitor
# ============================================================

class TestCostMonitor:
    @pytest.mark.asyncio
    async def test_within_budget_initially(self):
        from app.jd.monitoring.cost import CostMonitor, MICRO
        cm = CostMonitor(LocalKVStore(), per_user_daily=MICRO, global_hourly_break=100 * MICRO)
        assert await cm.check_budget("u1") is True

    @pytest.mark.asyncio
    async def test_user_cap_enforced(self):
        from app.jd.monitoring.cost import CostMonitor, MICRO
        cm = CostMonitor(LocalKVStore(), per_user_daily=MICRO, global_hourly_break=100 * MICRO)
        await cm.record("u1", MICRO)  # spend the whole daily cap
        assert await cm.check_budget("u1") is False

    @pytest.mark.asyncio
    async def test_other_user_unaffected(self):
        from app.jd.monitoring.cost import CostMonitor, MICRO
        cm = CostMonitor(LocalKVStore(), per_user_daily=MICRO, global_hourly_break=100 * MICRO)
        await cm.record("u1", MICRO)
        assert await cm.check_budget("u2") is True

    @pytest.mark.asyncio
    async def test_global_break(self):
        from app.jd.monitoring.cost import CostMonitor, MICRO
        cm = CostMonitor(LocalKVStore(), per_user_daily=1000 * MICRO, global_hourly_break=MICRO)
        await cm.record("u1", MICRO)
        assert await cm.check_budget("u2") is False  # global cap hit


# ============================================================
# §20 PDF Extraction
# ============================================================

class TestPdfDetection:
    def test_is_pdf_url(self):
        from app.jd.extractors.pdf import is_pdf_url
        assert is_pdf_url("https://x.com/job.pdf") is True
        assert is_pdf_url("https://x.com/job") is False

    def test_looks_like_pdf_magic_bytes(self):
        from app.jd.extractors.pdf import looks_like_pdf
        assert looks_like_pdf(b"%PDF-1.7 ...") is True
        assert looks_like_pdf(b"<html>", "text/html") is False
        assert looks_like_pdf(b"anything", "application/pdf") is True

    def test_google_docs_unsupported(self):
        from app.jd.extractors.pdf import detect_unsupported_source
        assert detect_unsupported_source("https://docs.google.com/document/d/abc") == "UNSUPPORTED_PLATFORM"

    def test_notion_unsupported(self):
        from app.jd.extractors.pdf import detect_unsupported_source
        assert detect_unsupported_source("https://team.notion.site/Job-123") == "UNSUPPORTED_PLATFORM"

    def test_docx_unsupported(self):
        from app.jd.extractors.pdf import detect_unsupported_source
        assert detect_unsupported_source("https://x.com/job.docx") == "UNSUPPORTED_FORMAT"

    def test_regular_url_supported(self):
        from app.jd.extractors.pdf import detect_unsupported_source
        assert detect_unsupported_source("https://boards.greenhouse.io/acme/jobs/1") is None


class TestPdfExtraction:
    def test_native_text_extraction(self):
        from app.jd.extractors.pdf import extract_pdf
        result = extract_pdf(_make_text_pdf(40), "https://x.com/job.pdf")
        assert result.content
        assert "Responsibilities" in result.content
        assert result.confidence.level in ("HIGH", "MEDIUM")
        assert result.source == "pdf_ocr"

    def test_too_large_rejected(self):
        from app.jd.extractors.pdf import extract_pdf
        big = b"%PDF-" + b"0" * (11 * 1024 * 1024)
        result = extract_pdf(big, "https://x.com/big.pdf")
        assert result.error_code == "PDF_TOO_LARGE"
        assert result.content == ""

    def test_non_pdf_bytes_rejected(self):
        from app.jd.extractors.pdf import extract_pdf
        result = extract_pdf(b"<html>not a pdf</html>", "https://x.com/x.pdf")
        assert result.error_code == "UNSUPPORTED_FORMAT"

    def test_scanned_pdf_without_ocr_fails_honestly(self):
        from app.jd.extractors.pdf import extract_pdf
        # A valid PDF header but no text stream → no extractable text.
        empty_pdf = b"%PDF-1.4\n1 0 obj<< /Type /Catalog >>endobj\ntrailer<< /Root 1 0 R >>\n%%EOF"
        result = extract_pdf(empty_pdf, "https://x.com/scan.pdf", ocr_enabled=False)
        assert result.content == ""
        assert result.error_code in ("PDF_NO_TEXT", "UNSUPPORTED_FORMAT")


# ============================================================
# New Adapters
# ============================================================

class TestSmartRecruitersAdapter:
    def test_can_handle(self):
        from app.jd.adapters.smartrecruiters import SmartRecruitersAdapter
        from urllib.parse import urlparse
        a = SmartRecruitersAdapter()
        assert a.can_handle(urlparse("https://jobs.smartrecruiters.com/Acme/744000012345678-eng"))
        assert not a.can_handle(urlparse("https://example.com/x"))

    def test_extract_api_url(self):
        from app.jd.adapters.smartrecruiters import SmartRecruitersAdapter
        from urllib.parse import urlparse
        a = SmartRecruitersAdapter()
        url = a.extract_api_url(urlparse("https://jobs.smartrecruiters.com/Acme/744000012345678-senior-eng"))
        assert url == "https://api.smartrecruiters.com/v1/companies/Acme/postings/744000012345678"

    def test_parse_response(self):
        from app.jd.adapters.smartrecruiters import SmartRecruitersAdapter
        a = SmartRecruitersAdapter()
        data = {
            "name": "Senior Engineer",
            "company": {"name": "Acme"},
            "location": {"city": "Berlin", "country": "DE"},
            "typeOfEmployment": {"label": "Full-time"},
            "jobAd": {"sections": {
                "jobDescription": {"title": "The Role", "text": "<p>Build APIs at scale.</p>"},
                "qualifications": {"title": "Requirements", "text": "<p>5 years Python.</p>"},
            }},
        }
        r = a.parse_response(data, "https://jobs.smartrecruiters.com/Acme/744-eng")
        assert "Senior Engineer" in r.content
        assert "Build APIs at scale." in r.content
        assert r.company.value == "Acme"
        assert r.confidence.level == "HIGH"
        assert r.source == "platform_api"


class TestLinkedInAdapter:
    def test_can_handle(self):
        from app.jd.adapters.linkedin import LinkedInAdapter
        from urllib.parse import urlparse
        a = LinkedInAdapter()
        assert a.can_handle(urlparse("https://www.linkedin.com/jobs/view/123456"))
        assert a.can_handle(urlparse("https://linkedin.com/jobs/view/x-123"))

    def test_no_api_url(self):
        from app.jd.adapters.linkedin import LinkedInAdapter
        from urllib.parse import urlparse
        a = LinkedInAdapter()
        assert a.extract_api_url(urlparse("https://www.linkedin.com/jobs/view/123")) is None

    def test_job_id(self):
        from app.jd.adapters.linkedin import LinkedInAdapter
        from urllib.parse import urlparse
        a = LinkedInAdapter()
        assert a.job_id(urlparse("https://www.linkedin.com/jobs/view/senior-eng-3812345678")) == "3812345678"
        assert a.job_id(urlparse("https://www.linkedin.com/jobs/search?currentJobId=999")) == "999"


class TestIndeedAdapter:
    def test_can_handle_country_subdomains(self):
        from app.jd.adapters.indeed import IndeedAdapter
        from urllib.parse import urlparse
        a = IndeedAdapter()
        assert a.can_handle(urlparse("https://www.indeed.com/viewjob?jk=abc123"))
        assert a.can_handle(urlparse("https://uk.indeed.com/viewjob?jk=def456"))
        assert not a.can_handle(urlparse("https://indeedsomething.com/x"))

    def test_no_api_url(self):
        from app.jd.adapters.indeed import IndeedAdapter
        from urllib.parse import urlparse
        a = IndeedAdapter()
        assert a.extract_api_url(urlparse("https://www.indeed.com/viewjob?jk=abc")) is None

    def test_job_key(self):
        from app.jd.adapters.indeed import IndeedAdapter
        from urllib.parse import urlparse
        a = IndeedAdapter()
        assert a.job_key(urlparse("https://www.indeed.com/viewjob?jk=abcdef123")) == "abcdef123"


class TestRegistryDetection:
    def test_detects_new_platforms(self):
        from app.jd.adapters.registry import detect_platform
        assert detect_platform("https://jobs.smartrecruiters.com/Acme/744-eng") == "smartrecruiters"
        assert detect_platform("https://www.linkedin.com/jobs/view/123") == "linkedin"
        assert detect_platform("https://www.indeed.com/viewjob?jk=abc") == "indeed"


# ============================================================
# Orchestrator Phase 3 Integration
# ============================================================

class TestOrchestratorPhase3:
    def _wire(self, monkeypatch):
        from app.jd import orchestrator
        from app.jd.cache import JdCache
        from app.jd.drift import DriftMonitor
        from app.jd.monitoring.cost import CostMonitor, MICRO
        kv = LocalKVStore()
        monkeypatch.setattr(orchestrator, "_cache", JdCache(kv))
        monkeypatch.setattr(orchestrator, "_drift", DriftMonitor(kv))
        monkeypatch.setattr(orchestrator, "_cost", CostMonitor(kv, per_user_daily=1000 * MICRO, global_hourly_break=1000 * MICRO))
        return orchestrator

    @pytest.mark.asyncio
    async def test_unsupported_source_fast_fail(self, monkeypatch):
        orchestrator = self._wire(monkeypatch)
        r = await orchestrator.orchestrate_v2("u", "https://docs.google.com/document/d/abc123")
        assert r.error_code == "UNSUPPORTED_PLATFORM"
        assert r.content == ""

    @pytest.mark.asyncio
    async def test_robots_disallowed_blocks(self, monkeypatch):
        orchestrator = self._wire(monkeypatch)
        import app.jd.robots as rmod

        async def fake_fetch(url, *a, **k):
            return "User-agent: FitWrightBot\nDisallow: /\n"

        monkeypatch.setattr(rmod, "fetch_url_safely", fake_fetch)
        monkeypatch.setattr(orchestrator, "_robots", rmod.RobotsChecker(LocalKVStore()))

        r = await orchestrator.orchestrate_v2("u", "https://blocked.com/jobs/1")
        assert r.error_code == "ROBOTS_DISALLOWED"

    @pytest.mark.asyncio
    async def test_pdf_routing_extracts(self, monkeypatch):
        orchestrator = self._wire(monkeypatch)

        pdf_bytes = _make_text_pdf(40)

        async def fake_raw(url, *a, **k):
            return pdf_bytes, "application/pdf"

        monkeypatch.setattr(orchestrator, "fetch_raw_safely", fake_raw)

        r = await orchestrator.orchestrate_v2("u", "https://x.com/job-posting.pdf")
        assert r.content
        assert r.source == "pdf_ocr"
        assert "Responsibilities" in r.content

    @pytest.mark.asyncio
    async def test_finalize_adds_fingerprint_and_language(self, monkeypatch):
        orchestrator = self._wire(monkeypatch)

        async def mock_fetch(url, *a, **k):
            return _jsonld_html(lang="en")

        monkeypatch.setattr(orchestrator, "fetch_url_safely", mock_fetch)
        r = await orchestrator.orchestrate_v2("u", "https://example.com/jobs/eng-1", force_refresh=True)
        assert r.source == "json_ld"
        assert r.fingerprint != ""
        assert r.language == "en"

    @pytest.mark.asyncio
    async def test_cost_budget_skips_playwright(self, monkeypatch):
        from app.jd import orchestrator
        from app.jd.cache import JdCache
        from app.jd.drift import DriftMonitor
        from app.jd.monitoring.cost import CostMonitor
        kv = LocalKVStore()
        monkeypatch.setattr(orchestrator, "_cache", JdCache(kv))
        monkeypatch.setattr(orchestrator, "_drift", DriftMonitor(kv))
        # Zero cap → budget always exhausted.
        monkeypatch.setattr(orchestrator, "_cost", CostMonitor(kv, per_user_daily=0, global_hourly_break=0))

        # HTML with no JSON-LD/hydration and thin DOM → cascade would reach Playwright.
        thin_html = "<html><body><div>Apply now</div></body></html>"

        async def mock_fetch(url, *a, **k):
            return thin_html

        render_called = {"v": False}

        async def spy_render(url, *a, **k):
            render_called["v"] = True
            return None

        monkeypatch.setattr(orchestrator, "fetch_url_safely", mock_fetch)
        monkeypatch.setattr(orchestrator, "needs_browser", lambda *a, **k: True)
        monkeypatch.setattr(orchestrator, "render_and_extract", spy_render)

        r = await orchestrator.orchestrate_v2("u", "https://spa.com/jobs/1", force_refresh=True)
        assert render_called["v"] is False  # Playwright skipped due to budget
        assert r.confidence.level == "LOW"
        trace_stages = {s.stage: s for s in r.explanation.pipeline_trace}
        assert "playwright" in trace_stages
        assert trace_stages["playwright"].detail == "cost budget exhausted"

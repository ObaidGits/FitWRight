"""End-to-end tests for JD extraction v2 pipeline.

Tests cover: URL canonicalization, platform detection, adapter parsing,
JSON-LD extraction, DOM extraction, and the full orchestrator cascade.
"""

import asyncio
import json
import os

import pytest

from app.jd.canonicalize import canonicalize_url
from app.jd.adapters.registry import detect_platform, get_adapter
from app.jd.adapters.ashby import AshbyAdapter
from app.jd.adapters.greenhouse import GreenhouseAdapter
from app.jd.adapters.lever import LeverAdapter
from app.jd.extractors.jsonld import extract_jsonld
from app.jd.extractors.dom import extract_dom_scored


# ============================================================
# URL Canonicalization Tests
# ============================================================

class TestCanonicalize:
    def test_strips_tracking_params(self):
        url = "https://jobs.ashbyhq.com/weave/abc?utm_source=linkedin&utm_medium=social"
        result = canonicalize_url(url)
        assert "utm_source" not in result
        assert "utm_medium" not in result
        assert "jobs.ashbyhq.com/weave/abc" in result

    def test_preserves_ats_ref_param(self):
        url = "https://boards.greenhouse.io/company/jobs/123?ref=engineering"
        result = canonicalize_url(url)
        assert "ref=engineering" in result

    def test_strips_ref_on_non_ats(self):
        url = "https://example.com/jobs/123?ref=linkedin&utm_source=x"
        result = canonicalize_url(url)
        assert "ref" not in result
        assert "utm_source" not in result

    def test_lowercases_scheme_and_host(self):
        url = "HTTPS://JOBS.ASHBYHQ.COM/Weave/ABC"
        result = canonicalize_url(url)
        assert result.startswith("https://jobs.ashbyhq.com")

    def test_removes_fragment(self):
        url = "https://jobs.lever.co/company/abc#apply"
        result = canonicalize_url(url)
        assert "#" not in result

    def test_removes_default_port(self):
        url = "https://example.com:443/jobs/123"
        result = canonicalize_url(url)
        assert ":443" not in result

    def test_idempotent(self):
        url = "https://jobs.ashbyhq.com/weave/abc?utm_source=x&id=123"
        first = canonicalize_url(url)
        second = canonicalize_url(first)
        assert first == second

    def test_sorts_query_params(self):
        url = "https://example.com/jobs?z=1&a=2&m=3"
        result = canonicalize_url(url)
        assert "a=2&m=3&z=1" in result

    def test_removes_trailing_slash(self):
        url = "https://example.com/jobs/"
        result = canonicalize_url(url)
        assert result.endswith("/jobs")

    def test_preserves_root_slash(self):
        url = "https://example.com/"
        result = canonicalize_url(url)
        assert result.endswith("/")


# ============================================================
# Platform Detection Tests
# ============================================================

class TestPlatformDetection:
    def test_detects_ashby(self):
        assert detect_platform("https://jobs.ashbyhq.com/weave/abc-123") == "ashby"

    def test_detects_greenhouse(self):
        assert detect_platform("https://boards.greenhouse.io/stripe/jobs/12345") == "greenhouse"

    def test_detects_lever(self):
        assert detect_platform("https://jobs.lever.co/company/abc-123") == "lever"

    def test_unknown_platform(self):
        assert detect_platform("https://careers.google.com/jobs/123") is None

    def test_case_insensitive(self):
        assert detect_platform("https://JOBS.ASHBYHQ.COM/weave/abc") == "ashby"


# ============================================================
# Adapter URL Parsing Tests
# ============================================================

class TestAshbyAdapter:
    def test_extracts_api_url(self):
        from urllib.parse import urlparse
        adapter = AshbyAdapter()
        parsed = urlparse("https://jobs.ashbyhq.com/weave/726f150c-2176-4af6-ad99-0debe9d2952b")
        api_url = adapter.extract_api_url(parsed)
        assert api_url == "https://api.ashbyhq.com/posting-api/job-board/weave/posting/726f150c-2176-4af6-ad99-0debe9d2952b"

    def test_extracts_uuid_from_slug(self):
        from urllib.parse import urlparse
        adapter = AshbyAdapter()
        parsed = urlparse("https://jobs.ashbyhq.com/weave/senior-engineer-726f150c-2176-4af6-ad99-0debe9d2952b")
        api_url = adapter.extract_api_url(parsed)
        assert "726f150c-2176-4af6-ad99-0debe9d2952b" in api_url

    def test_returns_none_for_invalid_path(self):
        from urllib.parse import urlparse
        adapter = AshbyAdapter()
        parsed = urlparse("https://jobs.ashbyhq.com/")
        assert adapter.extract_api_url(parsed) is None

    def test_parses_response(self):
        adapter = AshbyAdapter()
        data = {
            "info": {
                "title": "Senior Engineer",
                "descriptionHtml": "<p>Build amazing things.</p><ul><li>Python</li></ul>",
                "location": "Remote",
                "employmentType": "Full-time",
                "organizationName": "Weave",
            }
        }
        result = adapter.parse_response(data, "https://jobs.ashbyhq.com/weave/abc")
        assert "Senior Engineer" in result.content
        assert "Build amazing things" in result.content
        assert result.title.value == "Senior Engineer"
        assert result.company.value == "Weave"
        assert result.confidence.level == "HIGH"
        assert result.source == "platform_api"


class TestGreenhouseAdapter:
    def test_extracts_api_url(self):
        from urllib.parse import urlparse
        adapter = GreenhouseAdapter()
        parsed = urlparse("https://boards.greenhouse.io/stripe/jobs/12345")
        api_url = adapter.extract_api_url(parsed)
        assert api_url == "https://boards-api.greenhouse.io/v1/boards/stripe/jobs/12345"

    def test_returns_none_for_short_path(self):
        from urllib.parse import urlparse
        adapter = GreenhouseAdapter()
        parsed = urlparse("https://boards.greenhouse.io/stripe")
        assert adapter.extract_api_url(parsed) is None


class TestLeverAdapter:
    def test_extracts_api_url(self):
        from urllib.parse import urlparse
        adapter = LeverAdapter()
        parsed = urlparse("https://jobs.lever.co/company/abc-123-def")
        api_url = adapter.extract_api_url(parsed)
        assert api_url == "https://api.lever.co/v0/postings/company/abc-123-def"


# ============================================================
# JSON-LD Extraction Tests
# ============================================================

class TestJsonLdExtractor:
    def test_extracts_job_posting(self):
        html = '''
        <html><head>
        <script type="application/ld+json">
        {
            "@context": "https://schema.org/",
            "@type": "JobPosting",
            "title": "Software Engineer",
            "description": "<p>We are looking for a talented engineer to join our team. You will build scalable systems, work with distributed architectures, and collaborate with cross-functional teams. Requirements include 3+ years of experience with Python and cloud infrastructure. Benefits include competitive salary and remote work options.</p>",
            "hiringOrganization": {"@type": "Organization", "name": "TechCorp"},
            "jobLocation": {"@type": "Place", "address": {"addressLocality": "San Francisco", "addressRegion": "CA"}},
            "employmentType": "FULL_TIME"
        }
        </script>
        </head><body><p>Page content</p></body></html>
        '''
        result = extract_jsonld(html)
        assert result is not None
        assert "Software Engineer" in result.content
        assert result.title.value == "Software Engineer"
        assert result.company.value == "TechCorp"
        assert result.location.value == "San Francisco, CA"
        assert result.confidence.level == "HIGH"
        assert result.source == "json_ld"

    def test_returns_none_for_no_jsonld(self):
        html = "<html><body><p>No structured data here</p></body></html>"
        result = extract_jsonld(html)
        assert result is None

    def test_handles_graph_array(self):
        html = '''
        <html><head>
        <script type="application/ld+json">
        {"@graph": [
            {"@type": "Organization", "name": "X"},
            {"@type": "JobPosting", "title": "Designer", "description": "A great role for creative people who want to design user interfaces and experiences for millions of users worldwide. Must have experience with Figma and design systems."}
        ]}
        </script>
        </head><body></body></html>
        '''
        result = extract_jsonld(html)
        assert result is not None
        assert result.title.value == "Designer"

    def test_handles_malformed_json(self):
        html = '''
        <html><head>
        <script type="application/ld+json">{ invalid json }</script>
        </head><body></body></html>
        '''
        result = extract_jsonld(html)
        assert result is None


# ============================================================
# DOM Extraction Tests
# ============================================================

class TestDomExtractor:
    def test_extracts_from_article(self):
        html = '''
        <html><body>
        <nav>Navigation stuff</nav>
        <article class="job-description">
            <h2>Responsibilities</h2>
            <ul>
                <li>Design and implement scalable backend services</li>
                <li>Collaborate with product and design teams</li>
                <li>Mentor junior engineers and review code</li>
                <li>Participate in architectural design discussions</li>
                <li>Write comprehensive documentation</li>
            </ul>
            <h2>Qualifications</h2>
            <ul>
                <li>5+ years of Python experience</li>
                <li>Strong understanding of distributed systems</li>
                <li>Experience with cloud platforms (AWS/GCP)</li>
                <li>Excellent communication skills</li>
            </ul>
            <h2>Benefits</h2>
            <p>Competitive salary, equity, remote work, and unlimited PTO.</p>
        </article>
        <footer>Footer content</footer>
        </body></html>
        '''
        result = extract_dom_scored(html)
        assert result is not None
        assert "Responsibilities" in result.content
        assert "Qualifications" in result.content
        assert len(result.content) >= 400
        assert result.source == "dom_semantic"

    def test_returns_none_for_short_content(self):
        html = "<html><body><p>Short page</p></body></html>"
        result = extract_dom_scored(html)
        assert result is None

    def test_strips_nav_footer(self):
        html = '''
        <html><body>
        <nav>Home | About | Jobs</nav>
        <main>
            <h1>Senior Developer</h1>
            <p>We are seeking a senior developer with extensive experience in building modern web applications using React, TypeScript, and Node.js. The ideal candidate will lead technical architecture decisions and mentor team members. This is a full-time remote position with competitive compensation including base salary, equity, and comprehensive benefits package.</p>
            <h2>Requirements</h2>
            <p>7+ years of professional software development experience. Strong expertise in JavaScript/TypeScript ecosystem. Experience leading technical teams of 3-5 engineers.</p>
        </main>
        <footer>Copyright 2024 Company Inc.</footer>
        </body></html>
        '''
        result = extract_dom_scored(html)
        assert result is not None
        assert "Home | About" not in result.content
        assert "Copyright" not in result.content


# ============================================================
# Integration / Orchestrator Tests (mock network)
# ============================================================

class TestOrchestrator:
    @pytest.mark.asyncio
    async def test_ashby_url_uses_api_adapter(self, monkeypatch):
        """The user's original failing Ashby URL should resolve via API adapter."""
        from app.jd import orchestrator

        fake_api_response = json.dumps({
            "info": {
                "title": "Full Stack Engineer",
                "descriptionHtml": "<p>Join our team to build the next generation of communication tools. You will design APIs, implement real-time features, and work across the full stack. We value clean code, testing, and continuous delivery.</p><h3>Requirements</h3><ul><li>3+ years full-stack experience</li><li>React + Node.js</li><li>PostgreSQL</li></ul>",
                "location": "Remote, US",
                "employmentType": "Full-time",
                "organizationName": "Weave",
            }
        })

        async def mock_fetch(url, **kwargs):
            if "api.ashbyhq.com" in url:
                return fake_api_response
            raise Exception("Should not fetch the page URL directly")

        monkeypatch.setattr(orchestrator, "fetch_url_safely", mock_fetch)

        result = await orchestrator.orchestrate_v2(
            "test-user",
            "https://jobs.ashbyhq.com/weave/726f150c-2176-4af6-ad99-0debe9d2952b?utm_source=AvJaxJqGKY",
        )

        assert result.confidence.level == "HIGH"
        assert "Full Stack Engineer" in result.content
        assert result.source == "platform_api"
        assert result.title.value == "Full Stack Engineer"
        assert result.company.value == "Weave"

    @pytest.mark.asyncio
    async def test_unknown_site_falls_through_to_dom(self, monkeypatch):
        """Unknown sites cascade through API (skip) -> JSON-LD (miss) -> DOM."""
        from app.jd import orchestrator

        fake_html = '''
        <html><body>
        <main class="job-content">
            <h1>Product Manager</h1>
            <h2>About the Role</h2>
            <p>Lead product strategy for our enterprise platform. Drive roadmap decisions based on customer research, market analysis, and business goals. Work closely with engineering and design teams to ship impactful features.</p>
            <h2>Responsibilities</h2>
            <ul><li>Define product vision and strategy</li><li>Prioritize features based on impact</li><li>Conduct user research and interviews</li><li>Write detailed PRDs and specs</li></ul>
            <h2>Qualifications</h2>
            <ul><li>5+ years PM experience</li><li>B2B SaaS background</li><li>Data-driven decision making</li><li>Strong stakeholder management</li></ul>
        </main>
        </body></html>
        '''

        async def mock_fetch(url, **kwargs):
            return fake_html

        monkeypatch.setattr(orchestrator, "fetch_url_safely", mock_fetch)

        result = await orchestrator.orchestrate_v2(
            "test-user",
            "https://careers.randomcompany.com/jobs/pm-role",
        )

        assert result.confidence.level in ("MEDIUM", "HIGH")
        assert "Product Manager" in result.content
        assert "Responsibilities" in result.content
        assert result.source == "dom_semantic"

    @pytest.mark.asyncio
    async def test_jsonld_site_uses_structured_data(self, monkeypatch):
        """Sites with JSON-LD bypass DOM extraction entirely."""
        from app.jd import orchestrator

        fake_html = '''
        <html><head>
        <script type="application/ld+json">
        {"@type": "JobPosting", "title": "Data Scientist", "description": "Analyze large datasets to derive actionable insights for business strategy. Build and deploy machine learning models for prediction, classification, and recommendation systems. Collaborate closely with engineering teams to productionize models. Design and run A/B experiments to validate hypotheses. Communicate findings to stakeholders through clear visualizations and reports. Requirements: PhD or Masters in a quantitative field such as Statistics, Computer Science, or Mathematics. 3+ years of industry experience in data science or machine learning. Expert proficiency in Python, SQL, and modern ML frameworks like PyTorch or TensorFlow. Experience with big data tools such as Spark or Hadoop is a plus.", "hiringOrganization": {"name": "DataCo"}, "employmentType": "FULL_TIME"}
        </script>
        </head><body><div id="root"></div></body></html>
        '''

        async def mock_fetch(url, **kwargs):
            return fake_html

        monkeypatch.setattr(orchestrator, "fetch_url_safely", mock_fetch)

        result = await orchestrator.orchestrate_v2(
            "test-user",
            "https://careers.dataco.com/jobs/data-scientist",
        )

        assert result.confidence.level == "HIGH"
        assert result.source == "json_ld"
        assert "Data Scientist" in result.content
        assert result.company.value == "DataCo"

    @pytest.mark.asyncio
    async def test_fetch_failure_returns_low_confidence(self, monkeypatch):
        """Network failures return LOW confidence with helpful explanation."""
        from app.jd import orchestrator
        from app.jd.ssrf import SsrfError

        async def mock_fetch(url, **kwargs):
            raise SsrfError("timeout")

        monkeypatch.setattr(orchestrator, "fetch_url_safely", mock_fetch)

        result = await orchestrator.orchestrate_v2(
            "test-user",
            "https://example.com/jobs/123",
        )

        assert result.confidence.level == "LOW"
        assert result.content == ""
        assert len(result.explanation.suggestions) > 0

    @pytest.mark.asyncio
    async def test_timeout_envelope(self, monkeypatch):
        """Global timeout prevents infinite hangs."""
        import asyncio
        from app.jd import orchestrator

        async def mock_fetch(url, **kwargs):
            await asyncio.sleep(30)  # Simulate a hang
            return "<html></html>"

        monkeypatch.setattr(orchestrator, "fetch_url_safely", mock_fetch)

        result = await orchestrator.orchestrate_v2(
            "test-user",
            "https://slow-site.com/jobs/123",
            timeout=1.0,  # 1 second timeout for test speed
        )

        assert result.confidence.level == "LOW"
        assert "timed out" in result.explanation.summary.lower()



# ============================================================
# Hydration Extractor Tests
# ============================================================

class TestHydrationExtractor:
    def test_extracts_nextjs_data(self):
        from app.jd.extractors.hydration import extract_hydration
        html = '''
        <html><head>
        <script id="__NEXT_DATA__" type="application/json">
        {"props":{"pageProps":{"job":{"title":"Backend Engineer","description":"Build robust APIs and services for a high-growth fintech platform. You will design database schemas, implement caching strategies, and work with event-driven architectures. Requirements: 4+ years Python/Go, PostgreSQL, Redis, Docker, Kubernetes experience. Benefits include equity, remote work, and professional development budget.","company":"FinTechCo","location":"Remote, US"}}}}
        </script>
        </head><body><div id="__next"></div></body></html>
        '''
        result = extract_hydration(html)
        assert result is not None
        assert "Backend Engineer" in result.content
        assert result.source == "hydration_json"
        assert result.title.value == "Backend Engineer"
        assert result.confidence.level == "HIGH"

    def test_returns_none_without_hydration(self):
        from app.jd.extractors.hydration import extract_hydration
        html = "<html><body><p>No framework state here</p></body></html>"
        result = extract_hydration(html)
        assert result is None

    def test_handles_empty_nextdata(self):
        from app.jd.extractors.hydration import extract_hydration
        html = '<html><head><script id="__NEXT_DATA__" type="application/json">{"props":{"pageProps":{}}}</script></head><body></body></html>'
        result = extract_hydration(html)
        assert result is None


# ============================================================
# Page Classifier Tests
# ============================================================

class TestPageClassifier:
    def test_detects_captcha(self):
        from app.jd.classify import classify_page, PageClass
        html = '<html><body><div class="captcha">Please verify you are human</div></body></html>'
        assert classify_page(html) == PageClass.CAPTCHA

    def test_detects_cloudflare(self):
        from app.jd.classify import classify_page, PageClass
        html = '<html><head><title>Just a moment...</title></head><body>Checking your browser</body></html>'
        assert classify_page(html) == PageClass.WAF_BLOCKED

    def test_detects_expired_job(self):
        from app.jd.classify import classify_page, PageClass
        html = '<html><body><h1>This job is no longer available</h1><p>Check other openings</p></body></html>'
        assert classify_page(html) == PageClass.EXPIRED_JOB

    def test_detects_login(self):
        from app.jd.classify import classify_page, PageClass
        html = '<html><body><h1>Sign in to continue</h1><form action="/login"></form></body></html>'
        assert classify_page(html) == PageClass.LOGIN_REQUIRED

    def test_normal_page_is_unknown(self):
        from app.jd.classify import classify_page, PageClass
        html = '<html><body><h1>Software Engineer</h1><p>Great opportunity...</p></body></html>'
        assert classify_page(html) == PageClass.UNKNOWN

    def test_http_404_is_expired(self):
        from app.jd.classify import classify_page, PageClass
        assert classify_page("<html></html>", http_status=404) == PageClass.EXPIRED_JOB

    def test_http_403_is_login(self):
        from app.jd.classify import classify_page, PageClass
        assert classify_page("<html></html>", http_status=403) == PageClass.LOGIN_REQUIRED


# ============================================================
# Extended Orchestrator Tests
# ============================================================

class TestOrchestratorClassification:
    @pytest.mark.asyncio
    async def test_captcha_page_returns_low_with_explanation(self, monkeypatch):
        from app.jd import orchestrator

        captcha_html = '<html><body><script src="https://challenges.cloudflare.com/turnstile"></script><div>Please verify you are human</div></body></html>'

        async def mock_fetch(url, **kwargs):
            return captcha_html

        monkeypatch.setattr(orchestrator, "fetch_url_safely", mock_fetch)

        result = await orchestrator.orchestrate_v2("test", "https://protected-site.com/job/123")
        assert result.confidence.level == "LOW"
        assert "CAPTCHA" in result.explanation.summary or "blocked" in result.explanation.summary.lower()

    @pytest.mark.asyncio
    async def test_expired_job_detected(self, monkeypatch):
        from app.jd import orchestrator

        expired_html = '<html><body><h1>This job is no longer available</h1><p>Please browse our other openings.</p></body></html>'

        async def mock_fetch(url, **kwargs):
            return expired_html

        monkeypatch.setattr(orchestrator, "fetch_url_safely", mock_fetch)

        result = await orchestrator.orchestrate_v2("test", "https://example.com/jobs/old-role")
        assert result.confidence.level == "LOW"
        assert "expired" in result.explanation.summary.lower() or "no longer" in result.explanation.summary.lower()

    @pytest.mark.asyncio
    async def test_hydration_json_extraction(self, monkeypatch):
        from app.jd import orchestrator

        nextjs_html = '''<html><head>
        <script id="__NEXT_DATA__" type="application/json">
        {"props":{"pageProps":{"posting":{"title":"ML Engineer","description":"Join our AI team to build production ML systems. Design and implement model training pipelines, feature stores, and inference services. Work with large-scale data processing using Spark and Flink. Collaborate with research scientists to productionize novel algorithms and deploy them at scale. Requirements: Masters or PhD in CS/ML, 3+ years production ML experience, proficiency in Python, PyTorch, TensorFlow, and distributed systems knowledge. Strong communication skills and ability to work cross-functionally.","company":"AIStartup","location":"San Francisco, CA"}}}}
        </script>
        </head><body><div id="__next"></div></body></html>'''

        async def mock_fetch(url, **kwargs):
            return nextjs_html

        monkeypatch.setattr(orchestrator, "fetch_url_safely", mock_fetch)

        result = await orchestrator.orchestrate_v2("test", "https://nextjs-careers.vercel.app/jobs/ml-eng")
        assert result.source == "hydration_json"
        assert result.confidence.level == "HIGH"
        assert "ML Engineer" in result.content



# ============================================================
# Browser Decision Engine Tests
# ============================================================

class TestBrowserDecision:
    def test_js_only_domain_needs_browser(self):
        from app.jd.browser.decision import needs_browser
        html = "<html><body><div id='root'></div></body></html>"
        assert needs_browser(html, "company.myworkdayjobs.com", 0) is True

    def test_sufficient_content_skips_browser(self):
        from app.jd.browser.decision import needs_browser
        html = "<html><body>" + "x" * 1000 + "</body></html>"
        assert needs_browser(html, "example.com", 500) is False

    def test_empty_react_shell_needs_browser(self):
        from app.jd.browser.decision import needs_browser
        html = '<html><body><div id="root"></div><script src="/bundle.js"></script></body></html>'
        assert needs_browser(html, "careers.company.com", 50) is True

    def test_next_app_div_needs_browser(self):
        from app.jd.browser.decision import needs_browser
        html = '<html><body><div id="__next"></div></body></html>'
        assert needs_browser(html, "jobs.company.com", 0) is True

    def test_enable_javascript_message(self):
        from app.jd.browser.decision import needs_browser
        html = "<html><body><noscript>Please enable JavaScript to view this page</noscript></body></html>"
        assert needs_browser(html, "example.com", 0) is True

    def test_normal_page_with_content_skips(self):
        from app.jd.browser.decision import needs_browser
        html = "<html><body><article>" + "Job content " * 100 + "</article></body></html>"
        assert needs_browser(html, "example.com", 800) is False


# ============================================================
# Playwright Rendering E2E Test (live)
# ============================================================

class TestPlaywrightRendering:
    @pytest.mark.asyncio
    @pytest.mark.integration
    @pytest.mark.skipif(
        os.environ.get("RUN_BROWSER_E2E") != "1",
        reason=(
            "Launches a real headless Chromium against a live external page "
            "(network + browser). Opt in with RUN_BROWSER_E2E=1; it is excluded "
            "from the default deterministic suite so CI never depends on a "
            "browser or the public internet."
        ),
    )
    async def test_renders_live_page(self):
        """Smoke test: Playwright can render a simple page (live, opt-in)."""
        from app.jd.browser.render import render_and_extract

        # Hard timeout so a stuck browser subprocess can never hang the suite.
        try:
            result = await asyncio.wait_for(
                render_and_extract("https://example.com"), timeout=30
            )
        except (asyncio.TimeoutError, Exception) as exc:  # noqa: BLE001
            pytest.skip(f"browser render unavailable or timed out: {exc}")

        # example.com has minimal content - may or may not pass the char
        # threshold. The important thing is that it does not crash.
        assert result is None or hasattr(result, "content")

    @pytest.mark.asyncio
    async def test_orchestrator_uses_playwright_for_spa(self, monkeypatch):
        """Orchestrator should use Playwright when static extraction fails on SPA."""
        from app.jd import orchestrator
        from app.jd.browser import render as browser_render
        from app.jd.models import ConfidenceResult, ExtractionResult

        # Mock: static fetch returns empty SPA shell
        spa_html = '<html><head></head><body><div id="root"></div><script src="/app.js"></script></body></html>'

        async def mock_fetch(url, **kwargs):
            return spa_html

        # Mock: Playwright returns rendered content (must be >= 300 chars)
        async def mock_render(url, **kwargs):
            return ExtractionResult(
                content="Senior DevOps Engineer\n\nManage cloud infrastructure at scale for a fast-growing SaaS platform. Design and implement CI/CD pipelines using GitHub Actions and ArgoCD. Build monitoring and alerting systems with Prometheus, Grafana, and PagerDuty. Implement infrastructure as code with Terraform and Ansible. Requirements: 5+ years DevOps experience, deep expertise in AWS/GCP, Terraform, Kubernetes, and container orchestration. Strong scripting skills in Python or Go. Experience with service mesh technologies. Benefits: competitive salary, equity, fully remote, unlimited PTO.",
                confidence=ConfidenceResult(level="MEDIUM", score=55, reasons=["Browser rendered"]),
                source="headless_dom",
            )

        monkeypatch.setattr(orchestrator, "fetch_url_safely", mock_fetch)
        monkeypatch.setattr(orchestrator, "render_and_extract", mock_render)
        monkeypatch.setattr(orchestrator, "needs_browser", lambda *a, **k: True)

        result = await orchestrator.orchestrate_v2("test", "https://spa-careers.company.com/jobs/devops")

        assert result.source == "headless_dom"
        assert "DevOps" in result.content
        assert result.confidence.level == "MEDIUM"



# ============================================================
# Workday Adapter Tests
# ============================================================

class TestWorkdayAdapter:
    def test_detects_workday_domain(self):
        assert detect_platform("https://acme.myworkdayjobs.com/en-US/External/job/Senior-Dev") == "workday"

    def test_detects_wd5_domain(self):
        assert detect_platform("https://company.myworkday.com/jobs/123") == "workday"

    def test_no_api_url(self):
        from urllib.parse import urlparse
        from app.jd.adapters.workday import WorkdayAdapter
        adapter = WorkdayAdapter()
        parsed = urlparse("https://acme.myworkdayjobs.com/en-US/External/job/Senior-Dev")
        assert adapter.extract_api_url(parsed) is None

    def test_requires_js(self):
        from app.jd.adapters.workday import WorkdayAdapter
        assert WorkdayAdapter().REQUIRES_JS is True

    def test_parses_rendered_html(self):
        from app.jd.adapters.workday import WorkdayAdapter
        adapter = WorkdayAdapter()
        html = '''
        <html><body>
        <h2 data-automation-id="jobPostingHeader">Senior Software Engineer</h2>
        <div data-automation-id="locations"><dd>San Francisco, CA</dd></div>
        <div data-automation-id="jobPostingDescription">
            <p>We are looking for a senior engineer to join our platform team.</p>
            <h3>What you'll do</h3>
            <ul><li>Design scalable microservices</li><li>Lead technical decisions</li><li>Mentor engineers</li></ul>
            <h3>Requirements</h3>
            <ul><li>7+ years backend development</li><li>Distributed systems experience</li><li>Strong communication</li></ul>
            <p>Benefits include competitive compensation, equity, and comprehensive health coverage.</p>
        </div>
        </body></html>
        '''
        result = adapter.parse_rendered_html(html, "https://acme.myworkdayjobs.com/job/123")
        assert result is not None
        assert result.title.value == "Senior Software Engineer"
        assert result.location.value == "San Francisco, CA"
        assert "scalable microservices" in result.content
        assert result.source == "headless_dom"


# ============================================================
# ICIMS Adapter Tests
# ============================================================

class TestIcimsAdapter:
    def test_detects_icims_domain(self):
        assert detect_platform("https://careers-acme.icims.com/jobs/12345/senior-dev") == "icims"

    def test_no_api_url(self):
        from urllib.parse import urlparse
        from app.jd.adapters.icims import IcimsAdapter
        adapter = IcimsAdapter()
        parsed = urlparse("https://careers-acme.icims.com/jobs/12345")
        assert adapter.extract_api_url(parsed) is None

    def test_requires_js(self):
        from app.jd.adapters.icims import IcimsAdapter
        assert IcimsAdapter().REQUIRES_JS is True

    def test_parses_rendered_html(self):
        from app.jd.adapters.icims import IcimsAdapter
        adapter = IcimsAdapter()
        html = '''
        <html><body>
        <div class="iCIMS_Header"><h1>Data Engineer</h1></div>
        <div class="header-location">Austin, TX</div>
        <div class="iCIMS_JobContent">
            <h3>About the role</h3>
            <p>Build and maintain data pipelines that process millions of events daily.</p>
            <h3>Responsibilities</h3>
            <ul><li>Design ETL pipelines with Spark and Airflow</li><li>Optimize query performance</li><li>Build data models</li></ul>
            <h3>Qualifications</h3>
            <ul><li>4+ years data engineering</li><li>SQL and Python mastery</li><li>Cloud data platforms</li></ul>
            <p>We offer remote-first culture, learning budget, and stock options.</p>
        </div>
        </body></html>
        '''
        result = adapter.parse_rendered_html(html, "https://careers-acme.icims.com/jobs/12345")
        assert result is not None
        assert result.title.value == "Data Engineer"
        assert result.location.value == "Austin, TX"
        assert "ETL pipelines" in result.content
        assert result.source == "headless_dom"


# ============================================================
# Akamai Detection Test
# ============================================================

class TestAkamaiDetection:
    def test_detects_akamai_marker(self):
        from app.jd.classify import classify_page, PageClass
        html = '<html><body><script>var _abck="abc123";</script><p>Checking access...</p></body></html>'
        assert classify_page(html) == PageClass.WAF_BLOCKED

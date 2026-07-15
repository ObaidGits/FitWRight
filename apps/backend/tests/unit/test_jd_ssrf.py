"""Unit tests for JD-from-URL SSRF guard + extraction (P3 §D, R9 / Property 4)."""

from __future__ import annotations

import pytest

from app.jd.extract import extract_job_description
from app.jd.ssrf import SsrfError, fetch_url_safely, is_ip_blocked, validate_fetch_url


class TestIpBlocking:
    @pytest.mark.parametrize(
        "ip",
        [
            "127.0.0.1",          # loopback
            "10.0.0.1",           # private A
            "172.16.5.4",         # private B
            "192.168.1.1",        # private C
            "169.254.169.254",    # cloud metadata (link-local)
            "100.64.1.1",         # CGNAT
            "0.0.0.0",            # unspecified
            "::1",                # v6 loopback
            "fc00::1",            # v6 ULA
            "fe80::1",            # v6 link-local
            "::ffff:127.0.0.1",   # v4-mapped loopback
            "::ffff:10.0.0.1",    # v4-mapped private
            "not-an-ip",          # garbage → blocked
        ],
    )
    def test_blocked(self, ip):
        assert is_ip_blocked(ip) is True

    @pytest.mark.parametrize("ip", ["8.8.8.8", "1.1.1.1", "93.184.216.34", "2606:2800:220:1::1"])
    def test_allowed_public(self, ip):
        assert is_ip_blocked(ip) is False


class TestUrlValidation:
    def test_https_ok(self):
        assert validate_fetch_url("https://example.com/jobs/1") == ("https", "example.com", 443)

    def test_http_ok(self):
        assert validate_fetch_url("http://example.com") == ("http", "example.com", 80)

    def test_explicit_allowed_port(self):
        assert validate_fetch_url("https://example.com:443/x")[2] == 443

    @pytest.mark.parametrize(
        "url",
        [
            "ftp://example.com",         # scheme
            "file:///etc/passwd",        # scheme
            "gopher://example.com",      # scheme
            "https://example.com:22/x",  # port (SSH)
            "https://example.com:8080/", # port (non-allowlisted)
            "http://example.com:3306/",  # port (MySQL)
        ],
    )
    def test_rejected(self, url):
        with pytest.raises(SsrfError):
            validate_fetch_url(url)

    def test_no_host(self):
        with pytest.raises(SsrfError):
            validate_fetch_url("https:///nohost")


class TestRealFetchGuard:
    """Exercise the real fetch() path (no mock) — it must block before connecting."""

    @pytest.mark.parametrize(
        "url,reason_prefix",
        [
            ("http://127.0.0.1/", "blocked_ip"),
            ("http://169.254.169.254/latest/meta-data/", "blocked_ip"),
            ("http://10.0.0.1/", "blocked_ip"),
            ("http://localhost:22/", "port_not_allowed"),
            ("ftp://example.com/", "scheme_not_allowed"),
        ],
    )
    async def test_dangerous_targets_blocked_before_connect(self, url, reason_prefix):
        with pytest.raises(SsrfError) as exc:
            await fetch_url_safely(url)
        assert exc.value.reason.startswith(reason_prefix)


class TestExtraction:
    def test_extracts_article_text(self):
        html = """
        <html><head><title>Job</title><style>x{}</style></head>
        <body><nav>menu</nav>
        <article>""" + ("We are hiring a Senior Backend Engineer. " * 40) + """</article>
        <footer>footer junk</footer></body></html>
        """
        content, low = extract_job_description(html)
        assert "Senior Backend Engineer" in content
        assert "menu" not in content and "footer junk" not in content
        assert low is False

    def test_short_content_is_low_confidence(self):
        content, low = extract_job_description("<html><body><p>Hi</p></body></html>")
        assert low is True

    def test_bot_wall_is_low_confidence(self):
        html = "<html><body>" + ("Please enable JavaScript to continue. " * 20) + "</body></html>"
        content, low = extract_job_description(html)
        assert low is True

    def test_empty_html(self):
        content, low = extract_job_description("")
        assert content == "" and low is True

    def test_scripts_stripped(self):
        html = "<html><body><script>alert('x')</script>" + ("Real JD content here. " * 40) + "</body></html>"
        content, _ = extract_job_description(html)
        assert "alert" not in content

"""P3 Productivity — JD-from-URL (design §D, Requirement 9).

Server-side, SSRF-hardened fetch of a job posting URL → readability extraction →
optional bounded/cached LLM cleanup → ``{content, low_confidence, source_url}``
for the tailor flow. Every fetch is scheme/port allow-listed, DNS-pinned,
per-redirect revalidated, byte/decompression/time bounded, rate-limited, and
behind the ``JD_FROM_URL`` kill-switch.
"""

from app.jd.ssrf import SsrfError, fetch_url_safely, is_ip_blocked, validate_fetch_url

__all__ = ["SsrfError", "fetch_url_safely", "is_ip_blocked", "validate_fetch_url"]

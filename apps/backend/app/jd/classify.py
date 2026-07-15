"""Page classifier for JD extraction (§11 of enhancement plan).

Classifies fetched pages to fail fast with specific error codes.
Runs on the raw HTML + HTTP metadata before extraction attempts.
"""

from __future__ import annotations

import re

__all__ = ["classify_page", "PageClass"]


class PageClass:
    SINGLE_JOB = "single_job"
    JOB_LISTING = "job_listing"
    LOGIN_REQUIRED = "login_required"
    CAPTCHA = "captcha"
    EXPIRED_JOB = "expired_job"
    WAF_BLOCKED = "waf_blocked"
    NOT_JOB = "not_job"
    UNKNOWN = "unknown"  # Proceed with extraction


# Expiry signals (soft-404: HTTP 200 but job is gone)
_EXPIRY_SIGNALS = (
    "this job is no longer available",
    "this position has been filled",
    "this job has been removed",
    "this posting has expired",
    "job not found",
    "position is no longer open",
    "this role has been closed",
    "no longer accepting applications",
)

# CAPTCHA / WAF signals
_CAPTCHA_SIGNALS = (
    "challenges.cloudflare.com",
    "recaptcha",
    "hcaptcha.com",
    "captcha",
    "verify you are human",
    "are you a robot",
)

_WAF_SIGNALS = (
    "attention required! | cloudflare",
    "just a moment...",
    "checking your browser",
    "access denied",
    "datadome",
    "perimeterx",
    "_abck",  # Akamai Bot Manager cookie/script marker
    "akamai",
)

# Login signals
_LOGIN_SIGNALS = (
    "sign in to continue",
    "log in to view",
    "please log in",
    "authentication required",
)


def classify_page(html: str, http_status: int = 200) -> str:
    """Classify a page from its HTML content and HTTP status.

    Returns one of PageClass constants.
    Latency: < 1ms (string scanning only).
    """
    if not html:
        return PageClass.UNKNOWN

    # Check HTTP status first (fastest)
    if http_status == 401 or http_status == 403:
        return PageClass.LOGIN_REQUIRED
    if http_status == 404:
        return PageClass.EXPIRED_JOB

    lower = html[:5000].lower()  # Only check first 5KB for speed

    # CAPTCHA detection
    if any(s in lower for s in _CAPTCHA_SIGNALS):
        return PageClass.CAPTCHA

    # WAF detection
    if any(s in lower for s in _WAF_SIGNALS):
        return PageClass.WAF_BLOCKED

    # Login detection
    if any(s in lower for s in _LOGIN_SIGNALS):
        return PageClass.LOGIN_REQUIRED

    # Soft-404: job expired (HTTP 200 but content says gone)
    if any(s in lower for s in _EXPIRY_SIGNALS):
        return PageClass.EXPIRED_JOB

    # If page has apply button or job-like structure, it's likely a job
    # (we don't classify as SINGLE_JOB positively — we just don't reject it)
    return PageClass.UNKNOWN

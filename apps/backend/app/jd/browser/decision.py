"""Browser rendering decision engine (§12 of enhancement plan).

Determines whether Playwright should be launched based on HTML content analysis.
Only triggers when static extraction (JSON-LD, hydration, DOM) all failed.
"""

from __future__ import annotations

import re

__all__ = ["needs_browser"]

# Known JS-only platforms (always need browser rendering)
_JS_ONLY_DOMAINS = frozenset({
    "myworkdayjobs.com",
    "icims.com",
    "ultipro.com",
    "successfactors.com",
    "sapsf.com",
    # Phase 4 enterprise SPAs (see adapters/enterprise.py).
    "oraclecloud.com",
    "taleo.net",
    "rippling.com",
    "rippling-ats.com",
})

# React/SPA shell indicators (only in <script> tags, not body text)
_SCRIPT_SPA_RE = re.compile(
    r"<script[^>]*>.*?(react|vue|angular|svelte|__remix|createRoot|hydrateRoot)", re.S | re.I
)


def needs_browser(html: str, domain: str, extracted_chars: int) -> bool:
    """Decide whether to launch Playwright for this page.

    Called only AFTER static extraction produced < 400 chars.
    Returns True if browser rendering is likely to help.

    Latency: < 1ms (pure string analysis).
    """
    # Rule 1: Known JS-only platforms
    domain_lower = domain.lower()
    if any(d in domain_lower for d in _JS_ONLY_DOMAINS):
        return True

    # Rule 2: Already have enough content — don't waste browser resources
    if extracted_chars >= 400:
        return False

    # Rule 3: Empty React/Vue/Angular shell (small HTML with app root)
    if len(html) < 10000:
        lower_html = html.lower()
        if '<div id="root"' in lower_html or '<div id="app"' in lower_html:
            if extracted_chars < 200:
                return True
        if '<div id="__next"' in lower_html:
            return True

    # Rule 4: SPA framework markers in script tags (not in body text!)
    scripts_only = "".join(
        m.group(0) for m in re.finditer(r"<script[^>]*>.*?</script>", html, re.S | re.I)
    )
    if _SCRIPT_SPA_RE.search(scripts_only):
        if extracted_chars < 200:
            return True

    # Rule 5: Explicit "enable JavaScript" message
    if "please enable javascript" in html.lower() or "javascript is required" in html.lower():
        return True

    # Rule 6: Very short HTML with dynamic loading indicators
    if len(html) < 5000 and extracted_chars < 100:
        return True

    return False

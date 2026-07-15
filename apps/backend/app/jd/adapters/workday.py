"""Workday platform adapter (Phase 1 — browser-required).

Workday renders job postings entirely via JavaScript (React SPA). There is no
public API. Detection is domain-based; extraction uses Playwright + known DOM
selectors for the Workday job posting layout.

Known domains: *.myworkdayjobs.com, *.wd5.myworkday.com, *.myworkday.com
"""

from __future__ import annotations

import re
from urllib.parse import ParseResult

from app.jd.models import ConfidenceResult, ExtractionResult, FieldProvenance

__all__ = ["WorkdayAdapter"]

# Workday job page DOM selectors (stable across most Workday deployments)
SELECTORS = {
    "title": [
        "h2[data-automation-id='jobPostingHeader']",
        "[data-automation-id='jobPostingHeader']",
        "h1.css-1uk7y4m",
        "h2.css-1uk7y4m",
        ".css-1q2dra3 h2",
    ],
    "location": [
        "[data-automation-id='locations'] dd",
        "[data-automation-id='locations']",
        "dd.css-129m7dg",
    ],
    "description": [
        "[data-automation-id='jobPostingDescription']",
        ".css-1dbjc4n [data-automation-id='jobPostingDescription']",
        "#mainContent [role='main']",
        "article",
    ],
    "posted_date": [
        "[data-automation-id='postedOn'] dd",
    ],
}


class WorkdayAdapter:
    """Adapter for Workday ATS (browser-required)."""

    PLATFORM_ID = "workday"
    VERSION = "1.0.0"
    RATE_LIMIT = 30
    REQUIRES_JS = True  # Always needs Playwright

    def can_handle(self, parsed: ParseResult) -> bool:
        host = (parsed.hostname or "").lower()
        return (
            host.endswith(".myworkdayjobs.com")
            or host.endswith(".myworkday.com")
            or "workday" in host
        )

    def extract_api_url(self, parsed: ParseResult) -> str | None:
        # Workday has no public API — always returns None
        return None

    def parse_response(self, data: dict, source_url: str) -> ExtractionResult:
        # Not used for Workday (no API) — this is for interface compliance
        return ExtractionResult(content="", source="platform_api")

    def parse_rendered_html(self, html: str, source_url: str) -> ExtractionResult | None:
        """Parse Workday job from rendered HTML using known selectors.

        Called by the browser renderer after Playwright renders the page.
        """
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")

        # Extract title
        title = ""
        for sel in SELECTORS["title"]:
            elem = soup.select_one(sel)
            if elem and elem.get_text(strip=True):
                title = elem.get_text(strip=True)
                break

        # Extract location
        location = ""
        for sel in SELECTORS["location"]:
            elem = soup.select_one(sel)
            if elem and elem.get_text(strip=True):
                location = elem.get_text(strip=True)
                break

        # Extract description
        description = ""
        for sel in SELECTORS["description"]:
            elem = soup.select_one(sel)
            if elem:
                text = elem.get_text(separator="\n", strip=True)
                if len(text) > len(description):
                    description = text

        if not description or len(description) < 200:
            return None

        # Clean up description
        description = re.sub(r"\n{3,}", "\n\n", description).strip()
        content = f"{title}\n\n{description}" if title else description

        version = self.VERSION
        return ExtractionResult(
            content=content,
            title=FieldProvenance(
                value=title, source="headless_dom", confidence=80,
                extractor_version=version,
                raw_location="workday:data-automation-id=jobPostingHeader",
            ) if title else None,
            location=FieldProvenance(
                value=location, source="headless_dom", confidence=75,
                extractor_version=version,
                raw_location="workday:data-automation-id=locations",
            ) if location else None,
            confidence=ConfidenceResult(
                level="MEDIUM" if len(content) >= 600 else "LOW",
                score=65 if len(content) >= 600 else 40,
                reasons=["Workday browser-rendered extraction", "Platform-specific selectors"],
            ),
            source="headless_dom",
            canonical_url=source_url,
        )

"""ICIMS platform adapter (Phase 1 - browser-required).

ICIMS renders job postings via JavaScript. No public API. Detection by domain
pattern; extraction uses Playwright + known DOM selectors.

Known domains: *.icims.com, careers-*.icims.com
"""

from __future__ import annotations

import re
from urllib.parse import ParseResult

from app.jd.models import ConfidenceResult, ExtractionResult, FieldProvenance

__all__ = ["IcimsAdapter"]

# ICIMS job page DOM selectors
SELECTORS = {
    "title": [
        ".iCIMS_Header h1",
        "h1.iCIMS_Header",
        ".header-job-title h1",
        "h1[class*='title']",
        ".job-title",
    ],
    "location": [
        ".iCIMS_JobHeaderField:has(.iCIMS_InfoMsg_Job_Location)",
        ".header-location",
        "[class*='location']",
    ],
    "description": [
        ".iCIMS_JobContent",
        ".iCIMS_InfoMsg_Job",
        "#job-description",
        ".job-description",
        "[class*='jobContent']",
        "article",
        "#mainContent",
    ],
}


class IcimsAdapter:
    """Adapter for ICIMS ATS (browser-required)."""

    PLATFORM_ID = "icims"
    VERSION = "1.0.0"
    RATE_LIMIT = 30
    REQUIRES_JS = True  # Always needs Playwright

    def can_handle(self, parsed: ParseResult) -> bool:
        host = (parsed.hostname or "").lower()
        return host.endswith(".icims.com") or "icims" in host

    def extract_api_url(self, parsed: ParseResult) -> str | None:
        # ICIMS has no public API - always returns None
        return None

    def parse_response(self, data: dict, source_url: str) -> ExtractionResult:
        # Not used for ICIMS (no API) - interface compliance
        return ExtractionResult(content="", source="platform_api")

    def parse_rendered_html(self, html: str, source_url: str) -> ExtractionResult | None:
        """Parse ICIMS job from rendered HTML using known selectors.

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

        description = re.sub(r"\n{3,}", "\n\n", description).strip()
        content = f"{title}\n\n{description}" if title else description

        version = self.VERSION
        return ExtractionResult(
            content=content,
            title=FieldProvenance(
                value=title, source="headless_dom", confidence=75,
                extractor_version=version,
                raw_location="icims:.iCIMS_Header h1",
            ) if title else None,
            location=FieldProvenance(
                value=location, source="headless_dom", confidence=70,
                extractor_version=version,
                raw_location="icims:.header-location",
            ) if location else None,
            confidence=ConfidenceResult(
                level="MEDIUM" if len(content) >= 600 else "LOW",
                score=60 if len(content) >= 600 else 35,
                reasons=["ICIMS browser-rendered extraction", "Platform-specific selectors"],
            ),
            source="headless_dom",
            canonical_url=source_url,
        )

"""Ashby platform adapter (§6 of enhancement plan).

Ashby exposes a public posting API at:
  https://api.ashbyhq.com/posting-api/job-board/{org}/posting/{posting_id}

This returns structured JSON with title, descriptionHtml, team, location,
employmentType — no auth required, no scraping needed.
"""

from __future__ import annotations

import re
from urllib.parse import ParseResult

from app.jd.models import ConfidenceResult, ExtractionResult, FieldProvenance

__all__ = ["AshbyAdapter"]

# URL pattern: jobs.ashbyhq.com/{org}/{slug-with-uuid}
# The UUID at the end of the slug IS the posting ID
_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


class AshbyAdapter:
    """Adapter for Ashby's public posting API."""

    PLATFORM_ID = "ashby"
    VERSION = "1.0.0"
    RATE_LIMIT = 60
    REQUIRES_JS = False

    def can_handle(self, parsed: ParseResult) -> bool:
        host = (parsed.hostname or "").lower()
        return host == "jobs.ashbyhq.com"

    def extract_api_url(self, parsed: ParseResult) -> str | None:
        """Extract org and posting ID from Ashby URL.

        URL format: https://jobs.ashbyhq.com/{org}/{slug-containing-uuid}
        API format: https://api.ashbyhq.com/posting-api/job-board/{org}/posting/{uuid}
        """
        path_parts = [p for p in parsed.path.strip("/").split("/") if p]
        if len(path_parts) < 2:
            return None

        org = path_parts[0]

        # The posting ID is the UUID embedded in the slug (last path segment)
        slug = path_parts[-1]
        match = _UUID_RE.search(slug)
        if not match:
            # Some Ashby URLs use the full slug as the ID
            posting_id = slug
        else:
            posting_id = match.group(0)

        return f"https://api.ashbyhq.com/posting-api/job-board/{org}/posting/{posting_id}"

    def parse_response(self, data: dict, source_url: str) -> ExtractionResult:
        """Parse Ashby API response into ExtractionResult."""
        info = data.get("info", data)  # Some responses nest under "info"

        title = info.get("title", "")
        description_html = info.get("descriptionHtml", "") or info.get("description", "")
        team = info.get("team", "")
        location = info.get("location", "") or info.get("locationName", "")
        employment_type = info.get("employmentType", "")
        company_name = info.get("organizationName", "") or info.get("companyName", "")

        # Strip HTML tags from description for plain-text content
        import re as _re
        content = _re.sub(r"<[^>]+>", "\n", description_html)
        content = _re.sub(r"\n{3,}", "\n\n", content).strip()

        # Build the full content with title context
        if title and content:
            full_content = f"{title}\n\n{content}"
        else:
            full_content = content or title

        version = self.VERSION
        return ExtractionResult(
            content=full_content,
            title=FieldProvenance(
                value=title, source="platform_api", confidence=95,
                extractor_version=version, raw_location="api:info.title",
            ) if title else None,
            company=FieldProvenance(
                value=company_name, source="platform_api", confidence=90,
                extractor_version=version, raw_location="api:info.organizationName",
            ) if company_name else None,
            location=FieldProvenance(
                value=location, source="platform_api", confidence=90,
                extractor_version=version, raw_location="api:info.location",
            ) if location else None,
            employment_type=FieldProvenance(
                value=employment_type, source="platform_api", confidence=85,
                extractor_version=version, raw_location="api:info.employmentType",
            ) if employment_type else None,
            confidence=ConfidenceResult(
                level="HIGH", score=90,
                reasons=["Authoritative Ashby API", "Structured JSON response"],
            ),
            source="platform_api",
            canonical_url=source_url,
        )

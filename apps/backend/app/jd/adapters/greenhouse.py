"""Greenhouse platform adapter (§6 of enhancement plan).

Greenhouse exposes a public board API at:
  https://boards-api.greenhouse.io/v1/boards/{org}/jobs/{job_id}

Returns structured JSON with title, content (HTML), departments, offices.
"""

from __future__ import annotations

import re
from urllib.parse import ParseResult

from app.jd.models import ConfidenceResult, ExtractionResult, FieldProvenance

__all__ = ["GreenhouseAdapter"]

# boards.greenhouse.io/{org}/jobs/{id} OR job-boards.greenhouse.io/...
_JOB_ID_RE = re.compile(r"/jobs/(\d+)")


class GreenhouseAdapter:
    PLATFORM_ID = "greenhouse"
    VERSION = "1.0.0"
    RATE_LIMIT = 50
    REQUIRES_JS = False

    def can_handle(self, parsed: ParseResult) -> bool:
        host = (parsed.hostname or "").lower()
        return host in ("boards.greenhouse.io", "job-boards.greenhouse.io")

    def extract_api_url(self, parsed: ParseResult) -> str | None:
        path_parts = [p for p in parsed.path.strip("/").split("/") if p]
        if len(path_parts) < 3:
            return None
        org = path_parts[0]
        match = _JOB_ID_RE.search(parsed.path)
        if not match:
            return None
        job_id = match.group(1)
        return f"https://boards-api.greenhouse.io/v1/boards/{org}/jobs/{job_id}"

    def parse_response(self, data: dict, source_url: str) -> ExtractionResult:
        title = data.get("title", "")
        content_html = data.get("content", "")
        departments = [d.get("name", "") for d in data.get("departments", [])]
        offices = [o.get("name", "") for o in data.get("offices", [])]
        location = ", ".join(offices) if offices else ""
        company = data.get("company", {}).get("name", "")

        import re as _re
        content = _re.sub(r"<[^>]+>", "\n", content_html)
        content = _re.sub(r"\n{3,}", "\n\n", content).strip()

        full_content = f"{title}\n\n{content}" if title and content else content or title

        version = self.VERSION
        return ExtractionResult(
            content=full_content,
            title=FieldProvenance(
                value=title, source="platform_api", confidence=95,
                extractor_version=version, raw_location="api:title",
            ) if title else None,
            company=FieldProvenance(
                value=company, source="platform_api", confidence=90,
                extractor_version=version, raw_location="api:company.name",
            ) if company else None,
            location=FieldProvenance(
                value=location, source="platform_api", confidence=85,
                extractor_version=version, raw_location="api:offices[].name",
            ) if location else None,
            confidence=ConfidenceResult(
                level="HIGH", score=88,
                reasons=["Authoritative Greenhouse API", "Structured JSON response"],
            ),
            source="platform_api",
            canonical_url=source_url,
        )

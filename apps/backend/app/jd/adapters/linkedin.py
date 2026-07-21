"""LinkedIn platform adapter (§6 of enhancement plan).

LinkedIn does NOT expose a clean public JSON job API for anonymous callers, but
its public job-view pages (``/jobs/view/{id}``) embed a schema.org ``JobPosting``
in a ``<script type="application/ld+json">`` block. So this adapter is a
*detection + routing* adapter: it identifies LinkedIn URLs (for metrics and the
browser-decision hint) and defers extraction to the JSON-LD stage of the cascade
by returning ``None`` from :meth:`extract_api_url`.

``parse_response`` is provided for completeness/contract but is not exercised by
the orchestrator (no API URL is produced).
"""

from __future__ import annotations

import re
from urllib.parse import ParseResult

from app.jd.models import ConfidenceResult, ExtractionResult, FieldProvenance

__all__ = ["LinkedInAdapter"]

_JOB_ID_RE = re.compile(r"/jobs/view/(?:[^/]*-)?(\d+)")
_CURRENT_JOB_RE = re.compile(r"currentJobId=(\d+)")


class LinkedInAdapter:
    PLATFORM_ID = "linkedin"
    VERSION = "1.0.0"
    RATE_LIMIT = 30
    # Static HTML carries JSON-LD; browser only if JSON-LD/DOM both fail.
    REQUIRES_JS = False

    def can_handle(self, parsed: ParseResult) -> bool:
        host = (parsed.hostname or "").lower()
        return host == "linkedin.com" or host.endswith(".linkedin.com")

    def extract_api_url(self, parsed: ParseResult) -> str | None:
        # No public anonymous JSON API - defer to JSON-LD extractor.
        return None

    def job_id(self, parsed: ParseResult) -> str | None:
        """Best-effort LinkedIn job id (used for logging/metrics)."""
        m = _JOB_ID_RE.search(parsed.path)
        if m:
            return m.group(1)
        m = _CURRENT_JOB_RE.search(parsed.query or "")
        return m.group(1) if m else None

    def parse_response(self, data: dict, source_url: str) -> ExtractionResult:
        # Not used by the orchestrator (extract_api_url returns None), but kept
        # to satisfy the adapter contract for any direct callers.
        title = data.get("title", "")
        description = data.get("description", "")
        return ExtractionResult(
            content=f"{title}\n\n{description}".strip(),
            title=FieldProvenance(
                value=title, source="platform_api", confidence=80,
                extractor_version=self.VERSION, raw_location="api:title",
            ) if title else None,
            confidence=ConfidenceResult(level="MEDIUM", score=60, reasons=["LinkedIn (unofficial)"]),
            source="platform_api",
            canonical_url=source_url,
        )

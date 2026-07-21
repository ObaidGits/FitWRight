"""Indeed platform adapter (§6 of enhancement plan).

Indeed job pages (``/viewjob?jk={id}``) embed a schema.org ``JobPosting`` in
JSON-LD and also expose rich DOM markup (``#jobDescriptionText``). Indeed is
aggressively bot-protected (Cloudflare), so extraction frequently falls through
to classified WAF failures - which is correct behavior (honest failure > garbage).

Like the LinkedIn adapter, this is a *detection + routing* adapter: it recognizes
Indeed URLs and defers to the JSON-LD / DOM stages by returning ``None`` from
:meth:`extract_api_url`.
"""

from __future__ import annotations

import re
from urllib.parse import ParseResult, parse_qs

from app.jd.models import ConfidenceResult, ExtractionResult, FieldProvenance

__all__ = ["IndeedAdapter"]

_JK_RE = re.compile(r"jk=([0-9a-f]+)", re.I)


class IndeedAdapter:
    PLATFORM_ID = "indeed"
    VERSION = "1.0.0"
    RATE_LIMIT = 20
    # JSON-LD/DOM present in static HTML when not WAF-blocked; browser is a
    # last resort (and often futile behind Cloudflare - cascade handles it).
    REQUIRES_JS = False

    def can_handle(self, parsed: ParseResult) -> bool:
        host = (parsed.hostname or "").lower()
        # Matches indeed.com and country subdomains (uk.indeed.com, de.indeed.com, ...).
        return host == "indeed.com" or host.endswith(".indeed.com")

    def extract_api_url(self, parsed: ParseResult) -> str | None:
        # No public anonymous JSON API - defer to JSON-LD / DOM extractors.
        return None

    def job_key(self, parsed: ParseResult) -> str | None:
        """Best-effort Indeed job key (used for logging/metrics)."""
        qs = parse_qs(parsed.query or "")
        if "jk" in qs and qs["jk"]:
            return qs["jk"][0]
        m = _JK_RE.search(parsed.query or "")
        return m.group(1) if m else None

    def parse_response(self, data: dict, source_url: str) -> ExtractionResult:
        title = data.get("title", "")
        description = data.get("description", "")
        return ExtractionResult(
            content=f"{title}\n\n{description}".strip(),
            title=FieldProvenance(
                value=title, source="platform_api", confidence=80,
                extractor_version=self.VERSION, raw_location="api:title",
            ) if title else None,
            confidence=ConfidenceResult(level="MEDIUM", score=60, reasons=["Indeed (unofficial)"]),
            source="platform_api",
            canonical_url=source_url,
        )

"""SmartRecruiters platform adapter (§6 of enhancement plan).

SmartRecruiters exposes a public posting API (no auth):
  https://api.smartrecruiters.com/v1/companies/{company}/postings/{postingId}

Job page URLs look like:
  https://jobs.smartrecruiters.com/{Company}/{postingId}-{slug}
  https://careers.smartrecruiters.com/{Company}/{postingId}-{slug}

The postingId is the leading numeric token of the last path segment.
"""

from __future__ import annotations

import re
from urllib.parse import ParseResult

from app.jd.models import ConfidenceResult, ExtractionResult, FieldProvenance

__all__ = ["SmartRecruitersAdapter"]

# postingId is a long numeric id at the start of the slug (e.g. 744000012345678-...).
_POSTING_ID_RE = re.compile(r"(\d{6,})")


class SmartRecruitersAdapter:
    PLATFORM_ID = "smartrecruiters"
    VERSION = "1.0.0"
    RATE_LIMIT = 60
    REQUIRES_JS = False

    def can_handle(self, parsed: ParseResult) -> bool:
        host = (parsed.hostname or "").lower()
        return host in ("jobs.smartrecruiters.com", "careers.smartrecruiters.com")

    def extract_api_url(self, parsed: ParseResult) -> str | None:
        parts = [p for p in parsed.path.strip("/").split("/") if p]
        if len(parts) < 2:
            return None
        company = parts[0]
        slug = parts[-1]
        m = _POSTING_ID_RE.match(slug) or _POSTING_ID_RE.search(slug)
        if not m:
            return None
        posting_id = m.group(1)
        return f"https://api.smartrecruiters.com/v1/companies/{company}/postings/{posting_id}"

    def parse_response(self, data: dict, source_url: str) -> ExtractionResult:
        title = data.get("name", "") or data.get("title", "")
        company = ""
        company_obj = data.get("company", {})
        if isinstance(company_obj, dict):
            company = company_obj.get("name", "")

        # Location
        location = ""
        loc = data.get("location", {})
        if isinstance(loc, dict):
            location = ", ".join(
                p for p in (loc.get("city", ""), loc.get("region", ""), loc.get("country", "")) if p
            )

        # Job ad sections
        sections: list[str] = []
        job_ad = data.get("jobAd", {})
        if isinstance(job_ad, dict):
            content_sections = job_ad.get("sections", {})
            if isinstance(content_sections, dict):
                for key in ("companyDescription", "jobDescription", "qualifications", "additionalInformation"):
                    sec = content_sections.get(key, {})
                    if isinstance(sec, dict) and sec.get("text"):
                        section_title = sec.get("title", "")
                        text = _strip_html(sec.get("text", ""))
                        if section_title:
                            sections.append(f"{section_title}\n{text}")
                        else:
                            sections.append(text)

        body = "\n\n".join(s for s in sections if s.strip())
        full_content = f"{title}\n\n{body}" if title and body else (body or title)

        emp_type = data.get("typeOfEmployment", {})
        emp_label = emp_type.get("label", "") if isinstance(emp_type, dict) else ""

        version = self.VERSION
        return ExtractionResult(
            content=full_content,
            title=FieldProvenance(
                value=title, source="platform_api", confidence=95,
                extractor_version=version, raw_location="api:name",
            ) if title else None,
            company=FieldProvenance(
                value=company, source="platform_api", confidence=90,
                extractor_version=version, raw_location="api:company.name",
            ) if company else None,
            location=FieldProvenance(
                value=location, source="platform_api", confidence=85,
                extractor_version=version, raw_location="api:location",
            ) if location else None,
            employment_type=FieldProvenance(
                value=emp_label, source="platform_api", confidence=85,
                extractor_version=version, raw_location="api:typeOfEmployment.label",
            ) if emp_label else None,
            confidence=ConfidenceResult(
                level="HIGH", score=88,
                reasons=["Authoritative SmartRecruiters API", "Structured JSON response"],
            ),
            source="platform_api",
            canonical_url=source_url,
        )


def _strip_html(html_str: str) -> str:
    if not html_str:
        return ""
    text = re.sub(r"<[^>]+>", "\n", html_str)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

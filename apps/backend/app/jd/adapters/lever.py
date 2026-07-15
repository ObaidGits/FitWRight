"""Lever platform adapter (§6 of enhancement plan).

Lever exposes postings at:
  https://api.lever.co/v0/postings/{org}/{posting_id}

Returns structured JSON with text, categories, lists, additional.
"""

from __future__ import annotations

from urllib.parse import ParseResult

from app.jd.models import ConfidenceResult, ExtractionResult, FieldProvenance

__all__ = ["LeverAdapter"]


class LeverAdapter:
    PLATFORM_ID = "lever"
    VERSION = "1.0.0"
    RATE_LIMIT = 60
    REQUIRES_JS = False

    def can_handle(self, parsed: ParseResult) -> bool:
        host = (parsed.hostname or "").lower()
        return host == "jobs.lever.co"

    def extract_api_url(self, parsed: ParseResult) -> str | None:
        path_parts = [p for p in parsed.path.strip("/").split("/") if p]
        if len(path_parts) < 2:
            return None
        org = path_parts[0]
        posting_id = path_parts[1]
        return f"https://api.lever.co/v0/postings/{org}/{posting_id}"

    def parse_response(self, data: dict, source_url: str) -> ExtractionResult:
        text = data.get("text", "")
        categories = data.get("categories", {})
        location = categories.get("location", "")
        team = categories.get("team", "")
        commitment = categories.get("commitment", "")  # Full-time, Part-time

        # Build description from lists
        lists = data.get("lists", [])
        sections = []
        for lst in lists:
            header = lst.get("text", "")
            items = lst.get("content", "")
            if header:
                sections.append(f"{header}\n{items}" if items else header)

        additional = data.get("additional", "")
        description = "\n\n".join(sections)
        if additional:
            description += f"\n\n{additional}"

        import re as _re
        description = _re.sub(r"<[^>]+>", "\n", description)
        description = _re.sub(r"\n{3,}", "\n\n", description).strip()

        full_content = f"{text}\n\n{description}" if text and description else description or text

        version = self.VERSION
        return ExtractionResult(
            content=full_content,
            title=FieldProvenance(
                value=text, source="platform_api", confidence=95,
                extractor_version=version, raw_location="api:text",
            ) if text else None,
            location=FieldProvenance(
                value=location, source="platform_api", confidence=85,
                extractor_version=version, raw_location="api:categories.location",
            ) if location else None,
            employment_type=FieldProvenance(
                value=commitment, source="platform_api", confidence=80,
                extractor_version=version, raw_location="api:categories.commitment",
            ) if commitment else None,
            confidence=ConfidenceResult(
                level="HIGH", score=85,
                reasons=["Authoritative Lever API", "Structured JSON response"],
            ),
            source="platform_api",
            canonical_url=source_url,
        )

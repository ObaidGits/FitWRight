"""Pydantic schemas for JD-from-URL (P3 §D, Requirement 9)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class FetchUrlRequest(BaseModel):
    """Fetch a job posting by URL for the tailor flow."""

    url: str = Field(min_length=1, max_length=2048)
    # Opt-in AI cleanup (R15 — never auto-fires; cost-aware).
    use_ai: bool = False


class ExtractRenderedRequest(BaseModel):
    """Browser-extension fallback: the user's already-rendered DOM (Phase 4)."""

    url: str = Field(min_length=1, max_length=2048)
    html: str = Field(min_length=1, max_length=5_242_880)  # 5 MiB cap
    use_ai: bool = False


class WebhookJobPayload(BaseModel):
    """Employer-pushed authoritative job posting (Phase 4, zero-scrape)."""

    url: str = Field(min_length=1, max_length=2048)
    title: str | None = Field(default=None, max_length=512)
    company: str | None = Field(default=None, max_length=512)
    location: str | None = Field(default=None, max_length=512)
    employment_type: str | None = Field(default=None, max_length=128)
    salary: str | None = Field(default=None, max_length=256)
    description: str | None = Field(default=None, max_length=131_072)
    description_html: str | None = Field(default=None, max_length=262_144)


class FetchUrlResponse(BaseModel):
    """Extracted JD; ``low_confidence`` prompts user verification before tailoring.

    v2 fields are optional and default to ``None`` so v1 responses are byte-for-byte
    unchanged. The frontend detects v2 by the presence of ``schema_version``.
    """

    content: str
    low_confidence: bool
    source_url: str

    # --- v2 metadata (optional, backward compatible) ---
    schema_version: str | None = None
    confidence_level: str | None = None      # HIGH / MEDIUM / LOW
    confidence_score: int | None = None
    source: str | None = None                # platform_api / json_ld / dom_semantic / ...
    partial: bool | None = None
    error_code: str | None = None
    language: str | None = None
    suggestions: list[str] | None = None
    warnings: list[str] | None = None

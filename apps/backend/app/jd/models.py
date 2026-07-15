"""Data models for JD extraction v2 (§9, §16, §31 of enhancement plan).

Defines the structured result types with per-field provenance, confidence scoring,
and explainability metadata.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

__all__ = [
    "FieldProvenance",
    "ExtractionResult",
    "ConfidenceResult",
    "StageTrace",
    "ExtractionExplanation",
]

SourceType = Literal[
    "platform_api", "json_ld", "hydration_json",
    "dom_semantic", "headless_dom", "pdf_ocr", "manual_upload",
]


@dataclass
class FieldProvenance:
    """Metadata for a single extracted field."""
    value: str | int | float | None
    source: SourceType
    confidence: int  # 0-100
    extractor_version: str
    extracted_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    raw_location: str = ""  # JSON path, CSS selector, or "api:field_name"


@dataclass
class StageTrace:
    """Execution record for a single pipeline stage."""
    stage: str
    duration_ms: float
    status: Literal["success", "skipped", "failed", "timeout"]
    detail: str = ""


@dataclass
class ConfidenceResult:
    """Confidence assessment for an extraction."""
    level: Literal["HIGH", "MEDIUM", "LOW"]
    score: int  # 0-100
    reasons: list[str] = field(default_factory=list)


@dataclass
class ExtractionExplanation:
    """Human/developer-readable explanation of the extraction process."""
    summary: str
    pipeline_trace: list[StageTrace] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    missing_fields: list[dict[str, str]] = field(default_factory=list)


@dataclass
class ExtractionResult:
    """Complete v2 extraction result with provenance and confidence."""
    content: str
    title: FieldProvenance | None = None
    company: FieldProvenance | None = None
    location: FieldProvenance | None = None
    salary: FieldProvenance | None = None
    employment_type: FieldProvenance | None = None
    remote_status: FieldProvenance | None = None

    # --- Schema evolution v2.2 (§29): additive, nullable fields ---
    # Never remove or type-change existing fields; missing = null in old caches.
    visa_sponsorship: FieldProvenance | None = None
    security_clearance: FieldProvenance | None = None
    equity_compensation: FieldProvenance | None = None
    travel_requirement: FieldProvenance | None = None
    hybrid_schedule: FieldProvenance | None = None
    required_skills: FieldProvenance | None = None
    experience_years: FieldProvenance | None = None

    # Pipeline metadata
    confidence: ConfidenceResult = field(
        default_factory=lambda: ConfidenceResult(level="LOW", score=0)
    )
    explanation: ExtractionExplanation = field(
        default_factory=lambda: ExtractionExplanation(summary="")
    )
    source: SourceType = "dom_semantic"
    canonical_url: str = ""
    submitted_url: str = ""
    schema_version: str = "2.2"
    partial: bool = False

    # --- i18n + fingerprinting metadata (§21, §22) ---
    language: str = ""             # BCP-47-ish code (e.g. "en", "de"); "" if unknown
    fingerprint: str = ""          # SHA-256 content fingerprint (§22)
    near_duplicate_of: str | None = None  # canonical_url of a near-duplicate, if any
    error_code: str | None = None  # classified error code when content is empty

    # Legacy compat fields (derived)
    @property
    def low_confidence(self) -> bool:
        return self.confidence.level != "HIGH"

    def to_legacy_dict(self) -> dict:
        """Convert to v1 response shape for backward compatibility."""
        return {
            "content": self.content,
            "low_confidence": self.low_confidence,
            "source_url": self.canonical_url or self.submitted_url,
        }

    def to_v2_dict(self) -> dict:
        """Full v2 response with provenance and explanation."""
        return {
            "schema_version": self.schema_version,
            "content": self.content,
            "low_confidence": self.low_confidence,
            "source_url": self.canonical_url or self.submitted_url,
            "confidence": {
                "level": self.confidence.level,
                "score": self.confidence.score,
                "reasons": self.confidence.reasons,
            },
            "source": self.source,
            "partial": self.partial,
            "explanation": {
                "summary": self.explanation.summary,
                "warnings": self.explanation.warnings,
                "suggestions": self.explanation.suggestions,
                "pipeline_trace": [
                    {"stage": s.stage, "duration_ms": s.duration_ms,
                     "status": s.status, "detail": s.detail}
                    for s in self.explanation.pipeline_trace
                ],
            },
            "title": self.title.value if self.title else None,
            "company": self.company.value if self.company else None,
            "location": self.location.value if self.location else None,
            "salary": self.salary.value if self.salary else None,
            "employment_type": self.employment_type.value if self.employment_type else None,
            "remote_status": self.remote_status.value if self.remote_status else None,
            "visa_sponsorship": self.visa_sponsorship.value if self.visa_sponsorship else None,
            "security_clearance": self.security_clearance.value if self.security_clearance else None,
            "equity_compensation": self.equity_compensation.value if self.equity_compensation else None,
            "travel_requirement": self.travel_requirement.value if self.travel_requirement else None,
            "hybrid_schedule": self.hybrid_schedule.value if self.hybrid_schedule else None,
            "required_skills": self.required_skills.value if self.required_skills else None,
            "experience_years": self.experience_years.value if self.experience_years else None,
            "language": self.language or None,
            "fingerprint": self.fingerprint or None,
            "near_duplicate_of": self.near_duplicate_of,
            "error_code": self.error_code,
        }

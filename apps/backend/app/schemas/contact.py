"""Public "Contact" form schemas.

Server-authoritative validation for the unauthenticated contact endpoint. The
frontend validates too (UX), but the backend never trusts it: every field is
length-bounded, control characters / header-injection sequences are rejected,
and a honeypot + submit-timing field back the spam defenses in the router.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

__all__ = ["ContactRequest", "ContactResponse", "CONTACT_PURPOSES"]

# Allowed purpose codes (mirrored in the frontend select). Kept small + stable;
# unknown values are coerced to "general" rather than rejected, so a future
# frontend addition never hard-fails an otherwise valid submission.
CONTACT_PURPOSES: frozenset[str] = frozenset(
    {
        "general",
        "hiring",
        "collaboration",
        "support",
        "feedback",
        "press",
        # Feedback-center categories (Connect page).
        "bug",
        "feature",
        "improvement",
        "business",
        "job",
        "other",
    }
)

# Control chars (incl. CR/LF used for SMTP header injection) are never allowed in
# single-line fields. Newlines ARE allowed in the message body (stripped of the
# other control chars).
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_LINE_BREAK_RE = re.compile(r"[\r\n]")


def _clean_line(value: str) -> str:
    """Trim and reject a single-line field containing control/CRLF characters."""
    candidate = value.strip()
    if _CONTROL_RE.search(candidate) or _LINE_BREAK_RE.search(candidate):
        raise ValueError("contains invalid characters")
    return candidate


def _validate_email_shape(value: str) -> str:
    """Minimal, dependency-free email sanity check (matches the auth surface)."""
    candidate = value.strip()
    if not candidate or candidate.count("@") != 1:
        raise ValueError("must be a valid email address")
    local, _, domain = candidate.partition("@")
    if not local or not domain or "." not in domain or any(c.isspace() for c in candidate):
        raise ValueError("must be a valid email address")
    return candidate


class ContactRequest(BaseModel):
    """A public contact-form submission."""

    model_config = ConfigDict(str_strip_whitespace=False, extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    email: str = Field(min_length=3, max_length=320)
    subject: str = Field(min_length=1, max_length=150)
    message: str = Field(min_length=10, max_length=4000)

    company: str | None = Field(default=None, max_length=120)
    linkedin: str | None = Field(default=None, max_length=200)
    purpose: str = Field(default="general", max_length=40)
    project_type: str | None = Field(default=None, max_length=80)
    budget: str | None = Field(default=None, max_length=60)

    # --- anti-spam (never surfaced in the UI as real fields) ---------------
    # Honeypot: a field hidden from humans; a bot that fills it is dropped.
    company_website: str = Field(default="", max_length=200)
    # Milliseconds the user spent on the form before submitting; near-instant
    # submissions are almost always bots.
    elapsed_ms: int | None = Field(default=None, ge=0)

    @field_validator("name", "subject")
    @classmethod
    def _clean_required_lines(cls, v: str) -> str:
        cleaned = _clean_line(v)
        if not cleaned:
            raise ValueError("must not be empty")
        return cleaned

    @field_validator("company", "linkedin", "project_type", "budget")
    @classmethod
    def _clean_optional_lines(cls, v: str | None) -> str | None:
        if v is None:
            return None
        cleaned = _clean_line(v)
        return cleaned or None

    @field_validator("email")
    @classmethod
    def _check_email(cls, v: str) -> str:
        return _validate_email_shape(v)

    @field_validator("message")
    @classmethod
    def _clean_message(cls, v: str) -> str:
        # Preserve newlines; strip other control chars; collapse to a bounded,
        # non-empty body.
        cleaned = _CONTROL_RE.sub("", v).strip()
        if len(cleaned) < 10:
            raise ValueError("message is too short")
        return cleaned

    @field_validator("purpose")
    @classmethod
    def _normalize_purpose(cls, v: str) -> str:
        candidate = v.strip().lower()
        return candidate if candidate in CONTACT_PURPOSES else "general"


class ContactResponse(BaseModel):
    """Acknowledgement returned on a successful (or silently-dropped) submission.

    A ``reference`` id lets the sender quote the message if they follow up; the
    ``estimated_response`` is a human-friendly SLA string for the success UI.
    """

    message: str
    reference: str
    estimated_response: str

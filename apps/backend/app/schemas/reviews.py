"""Public product review schemas (Connect page).

An unauthenticated review submission: a 1–5 star rating with a short title and
body, plus an optional name (anonymous when omitted) and optional contact email.
Server-authoritative validation mirrors the contact schema (length bounds,
control-char rejection, dependency-free email shape) and carries the shared
honeypot + submit-timing anti-spam fields.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

__all__ = ["ReviewRequest", "ReviewResponse"]

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_LINE_BREAK_RE = re.compile(r"[\r\n]")


def _clean_line(value: str) -> str:
    candidate = value.strip()
    if _CONTROL_RE.search(candidate) or _LINE_BREAK_RE.search(candidate):
        raise ValueError("contains invalid characters")
    return candidate


def _validate_email_shape(value: str) -> str:
    candidate = value.strip()
    if not candidate or candidate.count("@") != 1:
        raise ValueError("must be a valid email address")
    local, _, domain = candidate.partition("@")
    if not local or not domain or "." not in domain or any(c.isspace() for c in candidate):
        raise ValueError("must be a valid email address")
    return candidate


class ReviewRequest(BaseModel):
    """A public product review submission."""

    model_config = ConfigDict(extra="forbid")

    rating: int = Field(ge=1, le=5)
    title: str = Field(min_length=1, max_length=120)
    body: str = Field(min_length=10, max_length=2000)
    # Optional attribution; when omitted the review is treated as anonymous.
    name: str | None = Field(default=None, max_length=100)
    email: str | None = Field(default=None, max_length=320)

    # --- anti-spam (never surfaced as real UI fields) ---
    company_website: str = Field(default="", max_length=200)
    elapsed_ms: int | None = Field(default=None, ge=0)

    @field_validator("title")
    @classmethod
    def _clean_title(cls, v: str) -> str:
        cleaned = _clean_line(v)
        if not cleaned:
            raise ValueError("must not be empty")
        return cleaned

    @field_validator("name")
    @classmethod
    def _clean_name(cls, v: str | None) -> str | None:
        if v is None:
            return None
        cleaned = _clean_line(v)
        return cleaned or None

    @field_validator("email")
    @classmethod
    def _check_email(cls, v: str | None) -> str | None:
        return _validate_email_shape(v) if v and v.strip() else None

    @field_validator("body")
    @classmethod
    def _clean_body(cls, v: str) -> str:
        cleaned = _CONTROL_RE.sub("", v).strip()
        if len(cleaned) < 10:
            raise ValueError("review is too short")
        return cleaned


class ReviewResponse(BaseModel):
    """Acknowledgement for a submitted review.

    Reviews are moderated before appearing publicly, so the response confirms
    receipt (with a reference) rather than immediate publication.
    """

    message: str
    reference: str

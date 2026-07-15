"""Pydantic schemas for the extended profile + avatar (P3 §H, R13/R14)."""

from __future__ import annotations

from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator

_MAX_LINKS = 10


class ProfileLink(BaseModel):
    """A labeled external link; URL scheme/host validated, length-bounded."""

    label: str = Field(min_length=1, max_length=60)
    url: str = Field(min_length=1, max_length=500)

    @field_validator("url")
    @classmethod
    def _validate_url(cls, v: str) -> str:
        parsed = urlparse(v.strip())
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("Links must be absolute http(s) URLs.")
        return v.strip()


class ProfileUpdateRequest(BaseModel):
    """Update the reusable profile fields used to prefill resumes."""

    headline: str | None = Field(default=None, max_length=200)
    location: str | None = Field(default=None, max_length=120)
    links: list[ProfileLink] | None = None

    @field_validator("links")
    @classmethod
    def _cap_links(cls, v: list[ProfileLink] | None) -> list[ProfileLink] | None:
        if v is not None and len(v) > _MAX_LINKS:
            raise ValueError(f"At most {_MAX_LINKS} links are allowed.")
        return v


class ProfileResponse(BaseModel):
    headline: str | None = None
    location: str | None = None
    links: list[ProfileLink] = Field(default_factory=list)
    avatar_url: str | None = None


class AvatarResponse(BaseModel):
    """The canonical profile-image master + its metadata (Photo System).

    Metadata lets the client render CLS-free (known aspect ratio), show a
    dominant-colour skeleton, and build responsive ``srcset`` from the one
    master without any extra upload. ``deduplicated`` is true when the same file
    was already stored (no new CDN write happened).
    """

    avatar_url: str | None = None
    width: int | None = None
    height: int | None = None
    aspect_ratio: float | None = None
    dominant_color: str | None = None
    format: str | None = None
    byte_size: int | None = None
    checksum: str | None = None
    deduplicated: bool = False

"""Resume Photo Configuration — the render+provenance contract (Photo System).

A resume's photo is described by a structured :class:`PhotoConfig`, **not** a
boolean and **not** a copy of the image. It lives in the resume's
``processed_data.personalInfo.photo`` (co-located with the header identity it
decorates) and captures every render decision the templates honour: visibility,
shape, size, alignment, position slot, crop mode, in-frame reposition/zoom,
border/shadow/background, opacity, and margin.

Two orthogonal concerns are separated on purpose:

1. **Presentation** (shape/size/position/…): pure render hints. Templates map
   the *position slot* + *size token* to their own layout; they never hardcode a
   pixel layout, and unknown slots fall back to the template default.
2. **Provenance** (``ref`` + ``snapshot``): where the pixels come from.
   - ``ref="canonical"`` → the resume tracks the user's *live* profile photo.
     Replacing the profile photo later updates this resume too (opt-in freshness).
   - ``ref="snapshot"`` → the resume is pinned to the exact master captured at
     generation time (``snapshot.url``/checksum/dims). A later profile-photo
     change never mutates this already-generated resume.

This module is **pure** (no I/O), so it is shared by the Projection Engine,
sync, public projection, and tests, and it mirrors the frontend
``lib/types/photo.ts`` type 1:1.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

__all__ = ["PhotoConfig", "PhotoSnapshot", "resolve_photo_url", "DEFAULT_PHOTO_CONFIG"]

PhotoShape = Literal["circle", "rounded", "square", "custom"]
PhotoSize = Literal["xs", "sm", "md", "lg", "xl", "custom"]
PhotoAlign = Literal["left", "center", "right"]
PhotoPosition = Literal[
    "template-default",
    "header-left",
    "header-right",
    "header-center",
    "sidebar",
    "floating",
]
PhotoCrop = Literal["cover", "contain", "fill"]
PhotoRef = Literal["canonical", "snapshot"]

# Size token → rendered edge length in px (the render layer maps these; kept
# here so preview and PDF agree and future surfaces reuse one scale).
SIZE_PX: dict[str, int] = {"xs": 48, "sm": 64, "md": 96, "lg": 128, "xl": 160}


class PhotoSnapshot(BaseModel):
    """Frozen master reference for a pinned (``ref="snapshot"``) resume photo."""

    url: str | None = None
    checksum: str | None = None
    width: int | None = None
    height: int | None = None


class PhotoConfig(BaseModel):
    """Per-resume photo configuration (presentation + provenance)."""

    show: bool = False
    ref: PhotoRef = "canonical"
    snapshot: PhotoSnapshot = Field(default_factory=PhotoSnapshot)

    # Presentation ---------------------------------------------------------
    shape: PhotoShape = "circle"
    radius: int = 12  # px, only used when shape == "custom"
    size: PhotoSize = "md"
    customSize: int | None = None  # px, only used when size == "custom"
    align: PhotoAlign = "left"
    position: PhotoPosition = "template-default"
    crop: PhotoCrop = "cover"
    # In-frame reposition (object-position %) + zoom (>=1 scales the master up
    # inside the fixed frame). Together these give crop/recenter/resize with zero
    # mutation of the master.
    offsetX: float = 50.0
    offsetY: float = 50.0
    zoom: float = 1.0
    # Framing.
    border: bool = False
    borderWidth: int = 2
    borderColor: str = "#e5e7eb"
    shadow: bool = False
    background: str | None = None
    opacity: float = 1.0
    margin: int = 0  # px around the frame

    @field_validator("opacity")
    @classmethod
    def _clamp_opacity(cls, v: float) -> float:
        return max(0.0, min(1.0, float(v)))

    @field_validator("offsetX", "offsetY")
    @classmethod
    def _clamp_offset(cls, v: float) -> float:
        return max(0.0, min(100.0, float(v)))

    @field_validator("zoom")
    @classmethod
    def _clamp_zoom(cls, v: float) -> float:
        # Guard against absurd zoom (perf + layout). 1.0 = fit, 3.0 = tight crop.
        return max(1.0, min(3.0, float(v)))

    def resolved_size_px(self) -> int:
        """Rendered edge length in px for the current size token."""
        if self.size == "custom" and self.customSize:
            return max(24, min(512, int(self.customSize)))
        return SIZE_PX.get(self.size, SIZE_PX["md"])


# Safe default: photo hidden, tracking the canonical profile photo, circular,
# medium, header-left. Turning a photo on is an explicit, per-resume opt-in.
DEFAULT_PHOTO_CONFIG = PhotoConfig()


def resolve_photo_url(config: PhotoConfig | None, profile_avatar_url: str | None) -> str | None:
    """Resolve the URL a resume should render for its photo (provenance-aware).

    - Hidden / no config → ``None`` (template renders its no-photo fallback).
    - ``ref="snapshot"`` → the pinned ``snapshot.url`` (frozen; immune to a later
      profile-photo change).
    - ``ref="canonical"`` → the *live* ``profile_avatar_url`` (tracks the profile).

    This single function is the authority reused by projection, read-time
    re-resolution, and public projection so the rule is enforced in one place.
    """
    if config is None or not config.show:
        return None
    if config.ref == "snapshot":
        return config.snapshot.url or profile_avatar_url
    return profile_avatar_url

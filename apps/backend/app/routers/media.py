"""Local media serving (dev/local ``STORAGE_PROVIDER`` only).

Serves re-encoded avatar objects written by :class:`LocalStorageProvider`. In
hosted deployments avatars are served from the CDN (Cloudinary/S3) and this
route is simply unused. Path traversal is prevented by resolving under the
avatars root; only files inside it are served (never arbitrary paths).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/media", tags=["media"])

_ALLOWED_SUFFIXES = {".webp", ".png", ".jpg", ".jpeg"}


@router.get("/{user_id}/{filename}")
async def get_avatar(user_id: str, filename: str) -> FileResponse:
    """Serve a stored avatar object (local provider). Bounded, traversal-safe.

    The provider root is ``data/avatars`` and the object key is
    ``{user_id}/{file}``, so on disk the object lives at
    ``data/avatars/{user_id}/{file}``.
    """
    root = (settings.data_dir / "avatars").resolve()
    target = (root / user_id / filename).resolve()
    if not str(target).startswith(str(root)) or target.suffix.lower() not in _ALLOWED_SUFFIXES:
        raise HTTPException(status_code=404, detail="Not found")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(
        target,
        media_type="image/webp",
        # Object keys are content-unique (per-upload UUID + checksum dedup), so a
        # given URL never changes - cache it immutably for a year (a new photo
        # gets a new URL). Mirrors Cloudinary's immutable CDN delivery.
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )

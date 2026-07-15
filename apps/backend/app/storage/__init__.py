"""Object storage + the canonical profile-image pipeline (Photo System).

A pluggable :class:`~app.storage.provider.StorageProvider`
(``STORAGE_PROVIDER``: local dev / Cloudinary free / S3 premium — ADR-10) plus
the hardened canonical-image pipeline (:mod:`app.storage.image`): magic-byte
sniff (no SVG/polyglot), byte + pixel caps (image-bomb guard), EXIF-orientation
normalize, EXIF/GPS strip, aspect-ratio-preserving downscale, canonical WebP
re-encode, content-addressed dedup, and CDN derivation — with a
server-generated key and orphan garbage collection.
"""

from app.storage.image import ImageError, process_profile_image, sniff_image_type
from app.storage.provider import get_storage_provider

__all__ = ["ImageError", "process_profile_image", "sniff_image_type", "get_storage_provider"]

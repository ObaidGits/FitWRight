"""Canonical profile-image pipeline + metadata + CDN derivation (Photo System).

This is the storage/processing core of the Profile Photo System - the single
canonical-master pipeline (it replaced an earlier square-cropping avatar path):
a single, aspect-ratio-preserving, EXIF-stripped, orientation-normalized,
downscaled WebP that every downstream surface (resume header, PDF, public
profile, portfolio, future website) derives from **without re-uploading**.

Design invariants (map 1:1 to the feature's ABSOLUTE RULES):

- **One canonical master, aspect ratio preserved.** We never center-crop the
  master. Shaping (circle/square), cropping, and repositioning are *render-time*
  decisions expressed by :class:`app.profile.photo.PhotoConfig` and realized via
  CSS ``object-fit``/``object-position`` (preview + PDF) or on-the-fly Cloudinary
  transforms (public/OG) - so the original is never mutated and quality never
  degrades.
- **Never store multiple copies.** Responsive variants are *URL transforms* of
  the master (Cloudinary), not new uploads. On the local dev provider the master
  URL is returned unchanged (no CDN to transform against).
- **Content-addressed dedup.** ``checksum`` is the SHA-256 of the *original*
  upload bytes; a re-upload of the same file can be short-circuited by the
  caller (no wasted Cloudinary write).

Accepted inputs: JPEG, PNG, WebP, AVIF (native in Pillow ≥ 11), and HEIC/HEIF
when the optional ``pillow-heif`` plugin is installed. Everything is re-encoded
to one canonical output format (WebP): broad browser + PDF-engine support, alpha,
and materially smaller than PNG/JPEG at equal quality. SVG/polyglots are rejected
(never decoded), matching the existing avatar hardening.
"""

from __future__ import annotations

import hashlib
import io
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

__all__ = [
    "ImageError",
    "ProcessedImage",
    "process_profile_image",
    "sniff_image_type",
    "derive_cdn_url",
    "responsive_srcset",
    "is_cloudinary_url",
    "CANONICAL_CONTENT_TYPE",
    "CANONICAL_EXT",
]

# One canonical output format for every stored master (justification in module
# docstring). Alpha-capable, well-supported, small.
CANONICAL_CONTENT_TYPE = "image/webp"
CANONICAL_EXT = "webp"

# ISO-BMFF (HEIF family) brands we recognise. AVIF is decoded natively by Pillow
# ≥ 11; HEIC/HEIF needs the optional pillow-heif opener (registered below).
_HEIF_BRANDS = frozenset(
    {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"hevm", b"hevs", b"mif1", b"msf1"}
)
_AVIF_BRANDS = frozenset({b"avif", b"avis"})

# Register the HEIC/HEIF opener once if the plugin is present. This is a no-op
# when pillow-heif isn't installed - those inputs are then rejected with a clean
# "unsupported_type" instead of a decode crash.
_HEIF_SUPPORTED = False
try:  # pragma: no cover - depends on optional dependency being installed
    import pillow_heif  # type: ignore

    pillow_heif.register_heif_opener()
    _HEIF_SUPPORTED = True
except Exception:  # pragma: no cover - plugin absent is the common case
    _HEIF_SUPPORTED = False


class ImageError(Exception):
    """Invalid/oversized/unsafe image -> router returns 422 ``invalid_file``.

    The ``reason`` is intentionally coarse and stays server-side (never leaks the
    precise decode failure to the client).
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class ProcessedImage:
    """The canonical master plus the metadata we persist (no binary in the DB)."""

    data: bytes
    content_type: str
    ext: str
    width: int
    height: int
    aspect_ratio: float  # width / height, rounded to 4 dp
    checksum: str  # sha256 hex of the *original* upload bytes (dedup key)
    source_format: str  # jpeg | png | webp | avif | heic
    byte_size: int  # len(data) of the encoded master
    dominant_color: str  # "#rrggbb" - for skeletons / theme accents


def sniff_image_type(data: bytes) -> str | None:
    """Return ``jpeg``/``png``/``webp``/``avif``/``heic`` from magic bytes, else ``None``.

    Never trusts the client extension/MIME. SVG and any non-raster/polyglot
    payload returns ``None`` (rejected before decode).
    """
    if len(data) < 12:
        return None
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    # ISO-BMFF container (HEIF/AVIF): `....ftyp<brand>`.
    if data[4:8] == b"ftyp":
        brand = data[8:12]
        if brand in _AVIF_BRANDS:
            return "avif"
        if brand in _HEIF_BRANDS:
            return "heic"
    return None


def _dominant_color(img) -> str:
    """Cheap dominant colour: average via a 1×1 downscale. Returns ``#rrggbb``."""
    try:
        from PIL import Image

        px = img.convert("RGB").resize((1, 1), Image.LANCZOS).getpixel((0, 0))
        return "#{:02x}{:02x}{:02x}".format(int(px[0]), int(px[1]), int(px[2]))
    except Exception:  # pragma: no cover - defensive; colour is non-critical
        return "#e5e7eb"


def process_profile_image(data: bytes) -> ProcessedImage:
    """Validate + normalize + re-encode an upload into the canonical master.

    Steps (all bounded, all deterministic):
    byte-cap -> magic sniff -> decompression-bomb guard -> decode ->
    EXIF-orientation transpose -> RGB -> downscale (aspect preserved, never upscale)
    -> strip metadata -> WebP re-encode -> metadata (dims/aspect/checksum/colour).

    Raises :class:`ImageError` on any violation. Pure (no I/O) -> trivially tested.
    """
    from app.config import settings
    from PIL import Image, ImageOps, UnidentifiedImageError
    from PIL.Image import DecompressionBombError

    if not data:
        raise ImageError("empty")
    if len(data) > settings.avatar_max_bytes:
        raise ImageError("too_large")

    kind = sniff_image_type(data)
    if kind is None:
        raise ImageError("unsupported_type")  # includes SVG / polyglots
    if kind == "heic" and not _HEIF_SUPPORTED:
        # Clear, honest rejection when the optional decoder isn't installed.
        raise ImageError("unsupported_type")

    checksum = hashlib.sha256(data).hexdigest()

    max_dim = settings.avatar_max_dimension
    # Bomb guard: cap total pixels Pillow will decode (slightly above our cap²).
    prev_limit = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = max_dim * max_dim + 1
    try:
        with Image.open(io.BytesIO(data)) as img:
            fmt = (img.format or "").lower()
            # Anti-polyglot: the decoder must agree with the sniffed family.
            _assert_format_matches(kind, fmt)
            width, height = img.size
            if width < 1 or height < 1 or width > max_dim or height > max_dim:
                raise ImageError("bad_dimensions")

            # Normalize orientation from EXIF, then drop the metadata entirely.
            img = ImageOps.exif_transpose(img)
            img = img.convert("RGB")

            # Downscale (never upscale) so the master's longest edge ≤ target,
            # preserving aspect ratio exactly. This is the canonical master; all
            # crops/shapes are applied later at render time.
            target = int(settings.image_master_max_dimension)
            w, h = img.size
            longest = max(w, h)
            if longest > target:
                scale = target / float(longest)
                img = img.resize(
                    (max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS
                )

            out = io.BytesIO()
            # No exif/metadata passed -> the master is metadata-free (GPS stripped).
            img.save(
                out,
                format="WEBP",
                quality=int(settings.image_master_quality),
                method=6,
            )
            encoded = out.getvalue()
            fw, fh = img.size
            return ProcessedImage(
                data=encoded,
                content_type=CANONICAL_CONTENT_TYPE,
                ext=CANONICAL_EXT,
                width=fw,
                height=fh,
                aspect_ratio=round(fw / fh, 4) if fh else 1.0,
                checksum=checksum,
                source_format=kind,
                byte_size=len(encoded),
                dominant_color=_dominant_color(img),
            )
    except ImageError:
        raise
    except DecompressionBombError as exc:
        raise ImageError("bad_dimensions") from exc
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ImageError("decode_failed") from exc
    finally:
        Image.MAX_IMAGE_PIXELS = prev_limit


def _assert_format_matches(kind: str, decoded_fmt: str) -> None:
    """Reject a payload whose real decoded format disagrees with its magic bytes."""
    allowed = {
        "jpeg": {"jpeg"},
        "png": {"png"},
        "webp": {"webp"},
        "avif": {"avif"},
        "heic": {"heif", "heic"},
    }.get(kind, set())
    if decoded_fmt not in allowed:
        raise ImageError("unsupported_type")


# ---------------------------------------------------------------------------
# CDN derivation - responsive/optimized variants WITHOUT re-uploading.
# ---------------------------------------------------------------------------


def is_cloudinary_url(url: str | None) -> bool:
    """Whether ``url`` is a Cloudinary delivery URL we can transform in place."""
    return bool(url) and "res.cloudinary.com" in url and "/image/upload/" in url


def derive_cdn_url(
    master_url: str | None,
    *,
    width: int | None = None,
    height: int | None = None,
    crop: str = "fill",
    gravity: str = "auto",
    radius: str | int | None = None,
    fmt: str = "auto",
    quality: str | int = "auto",
    dpr: str | int | None = None,
) -> str | None:
    """Return a transformed delivery URL derived from the canonical master.

    For a Cloudinary master this injects a transformation segment right after
    ``/image/upload/`` - a **pure URL rewrite**, so no extra bytes are stored and
    the original is untouched. For any non-Cloudinary URL (local dev provider,
    external avatar) the master URL is returned unchanged (graceful degradation).

    ``crop='fill'`` + ``gravity='auto'`` is the safe default for a fixed frame
    (content-aware face-preserving crop). Use ``crop='fit'`` to letterbox
    (``object-fit: contain`` semantics).
    """
    if not master_url:
        return master_url
    if not is_cloudinary_url(master_url):
        return master_url

    parts: list[str] = [f"f_{fmt}", f"q_{quality}"]
    if crop:
        parts.append(f"c_{crop}")
    if gravity and crop in ("fill", "thumb", "crop"):
        parts.append(f"g_{gravity}")
    if width:
        parts.append(f"w_{int(width)}")
    if height:
        parts.append(f"h_{int(height)}")
    if dpr:
        parts.append(f"dpr_{dpr}")
    if radius is not None:
        parts.append(f"r_{radius}")
    transform = ",".join(parts)
    return master_url.replace("/image/upload/", f"/image/upload/{transform}/", 1)


def responsive_srcset(
    master_url: str | None,
    widths: tuple[int, ...] = (96, 192, 384, 768),
    *,
    square: bool = True,
) -> list[dict[str, object]]:
    """Build a responsive descriptor list ``[{url, width}]`` from the master.

    Consumed by the frontend to emit a ``srcset`` (public profile / portfolio /
    OG). When the master isn't a Cloudinary URL every entry points at the master
    (still valid; the browser just can't pick a smaller byte size).
    """
    out: list[dict[str, object]] = []
    for w in widths:
        url = derive_cdn_url(
            master_url,
            width=w,
            height=w if square else None,
            crop="fill" if square else "fit",
        )
        out.append({"url": url, "width": w})
    return out

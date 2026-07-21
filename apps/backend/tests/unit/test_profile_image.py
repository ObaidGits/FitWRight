"""Unit tests for the canonical profile-image pipeline + Photo System contract.

Covers ``app.storage.image`` (master processing, metadata, dedup checksum,
orientation, CDN derivation) and ``app.profile.photo`` (config + provenance
resolution) and the Projection Engine photo stamping.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image

from app.storage.image import (
    ImageError,
    derive_cdn_url,
    is_cloudinary_url,
    process_profile_image,
    responsive_srcset,
    sniff_image_type,
)


def _img_bytes(fmt: str, size=(800, 600), color=(120, 30, 200), *, exif=False) -> bytes:
    img = Image.new("RGB", size, color)
    out = io.BytesIO()
    if exif and fmt == "JPEG":
        ex = Image.Exif()
        ex[0x0110] = "SecretCameraModel"
        img.save(out, format=fmt, exif=ex)
    else:
        img.save(out, format=fmt)
    return out.getvalue()


class TestSniff:
    def test_jpeg_png_webp(self):
        assert sniff_image_type(_img_bytes("JPEG")) == "jpeg"
        assert sniff_image_type(_img_bytes("PNG")) == "png"
        assert sniff_image_type(_img_bytes("WEBP")) == "webp"

    def test_svg_and_garbage_rejected(self):
        assert sniff_image_type(b'<?xml version="1.0"?><svg></svg>') is None
        assert sniff_image_type(b"nope") is None
        assert sniff_image_type(b"") is None

    def test_avif_brand_detected(self):
        # Synthetic ISO-BMFF header with an AVIF major brand.
        data = b"\x00\x00\x00\x18ftypavif" + b"\x00" * 16
        assert sniff_image_type(data) == "avif"

    def test_heic_brand_detected(self):
        data = b"\x00\x00\x00\x18ftypheic" + b"\x00" * 16
        assert sniff_image_type(data) == "heic"


class TestProcessMaster:
    def test_preserves_aspect_ratio(self):
        # Master must NOT be square-cropped - aspect ratio is preserved.
        out = process_profile_image(_img_bytes("JPEG", size=(1000, 400)))
        assert out.width / out.height == pytest.approx(1000 / 400, rel=0.02)
        assert out.aspect_ratio == pytest.approx(2.5, rel=0.02)

    def test_downscales_to_master_max(self, monkeypatch):
        from app.config import settings as s

        monkeypatch.setattr(s, "image_master_max_dimension", 256)
        out = process_profile_image(_img_bytes("PNG", size=(1024, 512)))
        assert max(out.width, out.height) == 256
        assert out.width / out.height == pytest.approx(2.0, rel=0.02)

    def test_never_upscales(self, monkeypatch):
        from app.config import settings as s

        monkeypatch.setattr(s, "image_master_max_dimension", 4096)
        out = process_profile_image(_img_bytes("PNG", size=(200, 100)))
        assert (out.width, out.height) == (200, 100)

    def test_reencoded_to_webp(self):
        out = process_profile_image(_img_bytes("PNG"))
        assert out.content_type == "image/webp" and out.ext == "webp"
        assert sniff_image_type(out.data) == "webp"

    def test_exif_stripped(self):
        out = process_profile_image(_img_bytes("JPEG", exif=True))
        with Image.open(io.BytesIO(out.data)) as img:
            assert len(dict(img.getexif())) == 0

    def test_checksum_is_deterministic_for_same_bytes(self):
        raw = _img_bytes("PNG")
        assert process_profile_image(raw).checksum == process_profile_image(raw).checksum

    def test_checksum_differs_for_different_bytes(self):
        a = process_profile_image(_img_bytes("PNG", color=(1, 2, 3)))
        b = process_profile_image(_img_bytes("PNG", color=(9, 9, 9)))
        assert a.checksum != b.checksum

    def test_dominant_color_hex(self):
        out = process_profile_image(_img_bytes("PNG", color=(255, 0, 0)))
        assert out.dominant_color.startswith("#") and len(out.dominant_color) == 7

    def test_metadata_shape(self):
        out = process_profile_image(_img_bytes("JPEG", size=(640, 480)))
        assert out.source_format == "jpeg"
        assert out.byte_size == len(out.data) > 0

    def test_svg_rejected(self):
        with pytest.raises(ImageError) as e:
            process_profile_image(b'<?xml version="1.0"?><svg></svg>')
        assert e.value.reason == "unsupported_type"

    def test_empty_rejected(self):
        with pytest.raises(ImageError) as e:
            process_profile_image(b"")
        assert e.value.reason == "empty"

    def test_oversized_bytes_rejected(self, monkeypatch):
        from app.config import settings as s

        monkeypatch.setattr(s, "avatar_max_bytes", 50)
        with pytest.raises(ImageError) as e:
            process_profile_image(_img_bytes("PNG"))
        assert e.value.reason == "too_large"

    def test_oversized_dimensions_rejected(self, monkeypatch):
        from app.config import settings as s

        monkeypatch.setattr(s, "avatar_max_dimension", 100)
        with pytest.raises(ImageError) as e:
            process_profile_image(_img_bytes("PNG", size=(800, 800)))
        assert e.value.reason == "bad_dimensions"

    def test_polyglot_rejected(self):
        with pytest.raises(ImageError):
            process_profile_image(b"\xff\xd8\xff" + b"junk not really a jpeg body")


_CLOUD = "https://res.cloudinary.com/demo/image/upload/v1/u/abc.webp"
_LOCAL = "http://localhost:8000/api/v1/media/u/abc.webp"


class TestCdnDerivation:
    def test_detects_cloudinary(self):
        assert is_cloudinary_url(_CLOUD)
        assert not is_cloudinary_url(_LOCAL)
        assert not is_cloudinary_url(None)

    def test_derive_injects_transform_after_upload(self):
        url = derive_cdn_url(_CLOUD, width=192, height=192)
        assert "/image/upload/" in url
        seg = url.split("/image/upload/")[1].split("/")[0]
        assert "w_192" in seg and "h_192" in seg and "c_fill" in seg and "f_auto" in seg

    def test_derive_is_noop_for_local(self):
        assert derive_cdn_url(_LOCAL, width=192) == _LOCAL

    def test_derive_noop_for_none(self):
        assert derive_cdn_url(None, width=10) is None

    def test_srcset_widths(self):
        rows = responsive_srcset(_CLOUD, (96, 192))
        assert [r["width"] for r in rows] == [96, 192]
        assert all("res.cloudinary.com" in r["url"] for r in rows)

    def test_srcset_local_points_at_master(self):
        rows = responsive_srcset(_LOCAL, (96, 192))
        assert all(r["url"] == _LOCAL for r in rows)


class TestPhotoConfig:
    def test_defaults_hidden_canonical(self):
        from app.profile.photo import DEFAULT_PHOTO_CONFIG

        assert DEFAULT_PHOTO_CONFIG.show is False
        assert DEFAULT_PHOTO_CONFIG.ref == "canonical"

    def test_clamps(self):
        from app.profile.photo import PhotoConfig

        c = PhotoConfig(opacity=5, offsetX=-10, offsetY=200, zoom=99)
        assert c.opacity == 1.0 and c.offsetX == 0.0 and c.offsetY == 100.0 and c.zoom == 3.0

    def test_size_px(self):
        from app.profile.photo import PhotoConfig

        assert PhotoConfig(size="xl").resolved_size_px() == 160
        assert PhotoConfig(size="custom", customSize=200).resolved_size_px() == 200

    def test_resolve_hidden_returns_none(self):
        from app.profile.photo import PhotoConfig, resolve_photo_url

        assert resolve_photo_url(PhotoConfig(show=False), "http://x/a.webp") is None

    def test_resolve_canonical_tracks_live(self):
        from app.profile.photo import PhotoConfig, resolve_photo_url

        c = PhotoConfig(show=True, ref="canonical")
        assert resolve_photo_url(c, "http://live/new.webp") == "http://live/new.webp"

    def test_resolve_snapshot_is_frozen(self):
        from app.profile.photo import PhotoConfig, PhotoSnapshot, resolve_photo_url

        c = PhotoConfig(show=True, ref="snapshot", snapshot=PhotoSnapshot(url="http://frozen/old.webp"))
        # Even if the live profile photo changed, the snapshot URL is returned.
        assert resolve_photo_url(c, "http://live/new.webp") == "http://frozen/old.webp"


class TestCanonicalReresolution:
    """The resume read path re-points canonical photos at the live profile."""

    def _resume(self, ref: str, avatar="http://old/a.webp"):
        return {
            "processed_data": {
                "personalInfo": {
                    "name": "Ada",
                    "avatarUrl": avatar,
                    "photo": {"show": True, "ref": ref, "snapshot": {"url": "http://frozen/s.webp"}},
                }
            }
        }

    async def test_canonical_tracks_live_avatar(self, monkeypatch):
        from app.routers import resumes
        from types import SimpleNamespace

        async def fake_get_by_id(uid):
            return SimpleNamespace(avatar_url="http://live/new.webp")

        monkeypatch.setattr("app.auth.accounts.get_by_id", fake_get_by_id)
        resume = self._resume("canonical")
        await resumes._reresolve_canonical_photo(resume, "u1")
        assert resume["processed_data"]["personalInfo"]["avatarUrl"] == "http://live/new.webp"

    async def test_snapshot_is_not_reresolved(self, monkeypatch):
        from app.routers import resumes
        from types import SimpleNamespace

        async def fake_get_by_id(uid):  # pragma: no cover - must not be called
            return SimpleNamespace(avatar_url="http://live/new.webp")

        monkeypatch.setattr("app.auth.accounts.get_by_id", fake_get_by_id)
        resume = self._resume("snapshot", avatar="http://old/a.webp")
        await resumes._reresolve_canonical_photo(resume, "u1")
        # Untouched: the snapshot render URL is resolved elsewhere and stays frozen.
        assert resume["processed_data"]["personalInfo"]["avatarUrl"] == "http://old/a.webp"

    async def test_no_photo_block_is_noop(self):
        from app.routers import resumes

        resume = {"processed_data": {"personalInfo": {"name": "Ada"}}}
        await resumes._reresolve_canonical_photo(resume, "u1")
        assert "avatarUrl" not in resume["processed_data"]["personalInfo"]


class TestProjectionPhoto:
    def _profile(self, avatar="http://cdn/a.webp"):
        from app.profile.schemas import ProfileData

        return ProfileData.model_validate({"identity": {"name": "Ada", "avatarUrl": avatar}})

    def test_no_photo_by_default(self):
        from app.profile.projection import ProjectionEngine

        r = ProjectionEngine.project_resume(self._profile(), options={})
        assert "photo" not in r["personalInfo"]

    def test_include_photo_shorthand_canonical(self):
        from app.profile.projection import ProjectionEngine

        r = ProjectionEngine.project_resume(self._profile(), options={"include_photo": True})
        assert r["personalInfo"]["photo"]["show"] is True
        assert r["personalInfo"]["photo"]["ref"] == "canonical"
        assert r["personalInfo"]["avatarUrl"] == "http://cdn/a.webp"

    def test_explicit_snapshot_freezes_current_avatar(self):
        from app.profile.projection import ProjectionEngine

        r = ProjectionEngine.project_resume(
            self._profile("http://cdn/current.webp"),
            options={"photo": {"show": True, "ref": "snapshot", "shape": "rounded"}},
        )
        photo = r["personalInfo"]["photo"]
        assert photo["ref"] == "snapshot"
        assert photo["shape"] == "rounded"
        # Snapshot URL was frozen from the current avatar at generation time.
        assert photo["snapshot"]["url"] == "http://cdn/current.webp"
        assert r["personalInfo"]["avatarUrl"] == "http://cdn/current.webp"

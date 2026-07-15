"""Integration tests for extended profile + avatar upload (P3 §H, R13/R14).

Uses the real auth stack (``auth_env`` + login) to exercise the authenticated
``/users/me/profile`` and ``/users/me/avatar`` endpoints, the storage provider
(local), and orphan GC.
"""

from __future__ import annotations

import io

import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image

from app.main import app
from app.storage.provider import reset_storage_provider

from tests.integration.test_users_api import _login_new_user

pytestmark = pytest.mark.integration


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="https://test")


@pytest.fixture(autouse=True)
def _reset_storage(tmp_path, monkeypatch):
    from app.config import settings as s

    monkeypatch.setattr(s, "data_dir", tmp_path)
    monkeypatch.setattr(s, "storage_provider", "local")
    reset_storage_provider()
    yield
    reset_storage_provider()


def _png(size=(600, 400)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, (10, 120, 200)).save(out, format="PNG")
    return out.getvalue()


class TestProfile:
    async def test_update_and_get_profile(self, auth_env):
        async with _client() as c:
            await _login_new_user(c, auth_env, "p1@example.com")
            resp = await c.patch(
                "/api/v1/users/me/profile",
                json={
                    "headline": "Staff Engineer",
                    "location": "Berlin",
                    "links": [{"label": "GitHub", "url": "https://github.com/me"}],
                },
            )
            assert resp.status_code == 200
            got = (await c.get("/api/v1/users/me/profile")).json()
        assert got["headline"] == "Staff Engineer"
        assert got["location"] == "Berlin"
        assert got["links"][0]["url"] == "https://github.com/me"

    async def test_invalid_link_url_rejected(self, auth_env):
        async with _client() as c:
            await _login_new_user(c, auth_env, "p2@example.com")
            resp = await c.patch(
                "/api/v1/users/me/profile",
                json={"links": [{"label": "bad", "url": "javascript:alert(1)"}]},
            )
        assert resp.status_code == 422

    async def test_anonymous_unauthorized(self, auth_env):
        async with _client() as c:
            resp = await c.get("/api/v1/users/me/profile")
        assert resp.status_code == 401


class TestAvatar:
    async def test_upload_reencodes_and_sets_url(self, auth_env):
        async with _client() as c:
            await _login_new_user(c, auth_env, "av1@example.com")
            resp = await c.post(
                "/api/v1/users/me/avatar",
                files={"file": ("photo.png", _png(), "image/png")},
            )
            assert resp.status_code == 200
            url = resp.json()["avatar_url"]
            assert "/api/v1/media/" in url
            # The URL is now on the user record (via the profile endpoint).
            profile = (await c.get("/api/v1/users/me/profile")).json()
        assert profile["avatar_url"] == url

    async def test_svg_rejected(self, auth_env):
        svg = b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"><script/></svg>'
        async with _client() as c:
            await _login_new_user(c, auth_env, "av2@example.com")
            resp = await c.post(
                "/api/v1/users/me/avatar",
                files={"file": ("evil.svg", svg, "image/svg+xml")},
            )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "invalid_file"

    async def test_lying_mime_still_sniffed(self, auth_env):
        # Claims to be a PNG but is not an image → rejected by magic-byte sniff.
        async with _client() as c:
            await _login_new_user(c, auth_env, "av3@example.com")
            resp = await c.post(
                "/api/v1/users/me/avatar",
                files={"file": ("x.png", b"totally not an image", "image/png")},
            )
        assert resp.status_code == 422

    async def test_old_avatar_gc_on_replace(self, auth_env):
        from app.config import settings as s

        async with _client() as c:
            await _login_new_user(c, auth_env, "av4@example.com")
            first = (await c.post("/api/v1/users/me/avatar", files={"file": ("a.png", _png(), "image/png")})).json()
            await c.post("/api/v1/users/me/avatar", files={"file": ("b.png", _png((500, 500)), "image/png")})
        # The first object file should have been deleted on replace.
        first_key = first["avatar_url"].split("/api/v1/media/")[1]
        old_path = (s.data_dir / "avatars" / first_key)
        assert not old_path.exists()

    async def test_served_via_media_route(self, auth_env):
        async with _client() as c:
            await _login_new_user(c, auth_env, "av5@example.com")
            url = (await c.post("/api/v1/users/me/avatar", files={"file": ("a.png", _png(), "image/png")})).json()["avatar_url"]
            path = url.split("https://test")[-1] if "https://test" in url else url[url.index("/api/v1"):]
            resp = await c.get(path)
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/webp"

    async def test_response_carries_canonical_metadata(self, auth_env):
        # Photo System: the response exposes master metadata (aspect preserved).
        async with _client() as c:
            await _login_new_user(c, auth_env, "av6@example.com")
            body = (
                await c.post(
                    "/api/v1/users/me/avatar",
                    files={"file": ("a.png", _png((600, 400)), "image/png")},
                )
            ).json()
        assert body["width"] and body["height"]
        # Master is NOT square-cropped — 3:2 aspect is preserved.
        assert body["aspect_ratio"] == pytest.approx(1.5, rel=0.02)
        assert body["format"] == "png"
        assert body["dominant_color"].startswith("#")
        assert body["deduplicated"] is False

    async def test_identical_upload_is_deduplicated(self, auth_env):
        # Content-addressed dedup: re-uploading the same bytes reuses the master.
        raw = _png((512, 512))
        async with _client() as c:
            await _login_new_user(c, auth_env, "av7@example.com")
            first = (
                await c.post("/api/v1/users/me/avatar", files={"file": ("a.png", raw, "image/png")})
            ).json()
            second = (
                await c.post("/api/v1/users/me/avatar", files={"file": ("a.png", raw, "image/png")})
            ).json()
        assert second["deduplicated"] is True
        assert second["avatar_url"] == first["avatar_url"]
        assert second["checksum"] == first["checksum"]

    async def test_delete_removes_avatar(self, auth_env):
        async with _client() as c:
            await _login_new_user(c, auth_env, "av8@example.com")
            await c.post("/api/v1/users/me/avatar", files={"file": ("a.png", _png(), "image/png")})
            resp = await c.delete("/api/v1/users/me/avatar")
            assert resp.status_code == 200
            assert resp.json()["avatar_url"] is None
            profile = (await c.get("/api/v1/users/me/profile")).json()
        assert profile["avatar_url"] is None

    async def test_profile_identity_live_resolves_avatar(self, auth_env):
        # The professional profile's identity.avatarUrl is resolved LIVE from the
        # account master on every read — so changing the account photo is
        # reflected with no stored-profile write and no drift.
        async with _client() as c:
            await _login_new_user(c, auth_env, "av-live@example.com")
            # First read lazily creates the profile with no avatar yet.
            p0 = (await c.get("/api/v1/profile")).json()
            assert p0["data"]["identity"]["avatarUrl"] in (None, "")
            # Upload the account avatar, then re-read the profile.
            url = (
                await c.post(
                    "/api/v1/users/me/avatar", files={"file": ("a.png", _png(), "image/png")}
                )
            ).json()["avatar_url"]
            p1 = (await c.get("/api/v1/profile")).json()
        # Live-resolved to the new account master (no profile PATCH happened).
        assert p1["data"]["identity"]["avatarUrl"] == url

    async def test_session_reflects_avatar(self, auth_env):
        # The top-bar avatar reads /auth/session — it must carry the live URL so
        # the badge shows the photo (and clears when removed).
        async with _client() as c:
            await _login_new_user(c, auth_env, "av9@example.com")
            before = (await c.get("/api/v1/auth/session")).json()
            assert before.get("avatarUrl") in (None, "")

            url = (
                await c.post(
                    "/api/v1/users/me/avatar", files={"file": ("a.png", _png(), "image/png")}
                )
            ).json()["avatar_url"]
            after = (await c.get("/api/v1/auth/session")).json()
            assert after["avatarUrl"] == url

            await c.delete("/api/v1/users/me/avatar")
            cleared = (await c.get("/api/v1/auth/session")).json()
        assert cleared.get("avatarUrl") in (None, "")

"""Photo System - cross-flow provenance/preservation tests.

Covers the flows the audit flagged as photo-sensitive: profile->resume sync must
preserve the resume's photo config; JD tailoring must preserve the header photo;
the public projection must expose avatar metadata for CLS-free rendering.
"""

from __future__ import annotations

import pytest


class TestSyncPreservesPhoto:
    """Re-projecting a resume from the profile must not clobber its photo config."""

    def _profile(self, avatar="http://cdn/live.webp"):
        from app.profile.schemas import ProfileData

        return ProfileData.model_validate({"identity": {"name": "Ada", "avatarUrl": avatar}})

    def test_existing_photo_config_is_carried_through(self):
        from app.profile.sync import _existing_photo, _project

        resume = {
            "processed_data": {
                "personalInfo": {
                    "name": "Ada",
                    "photo": {
                        "show": True,
                        "ref": "snapshot",
                        "shape": "square",
                        "size": "xl",
                        "position": "sidebar",
                        "snapshot": {"url": "http://frozen/s.webp"},
                    },
                }
            }
        }
        existing = _existing_photo(resume)
        projected = _project(self._profile(), version=2, include_photo=False, existing_photo=existing)
        photo = projected["personalInfo"]["photo"]
        # Shape/size/position/provenance all preserved (not reset to defaults).
        assert photo["show"] is True
        assert photo["shape"] == "square"
        assert photo["size"] == "xl"
        assert photo["position"] == "sidebar"
        assert photo["ref"] == "snapshot"
        assert photo["snapshot"]["url"] == "http://frozen/s.webp"

    def test_no_existing_photo_stays_absent_without_include(self):
        from app.profile.sync import _existing_photo, _project

        resume = {"processed_data": {"personalInfo": {"name": "Ada"}}}
        projected = _project(
            self._profile(), version=1, include_photo=False, existing_photo=_existing_photo(resume)
        )
        assert "photo" not in projected["personalInfo"]


class TestTailorPreservesPersonalInfo:
    """JD tailoring must never rewrite/drop identity - incl. the photo config."""

    async def test_improve_restores_personal_info_with_photo(self, monkeypatch):
        from app.services import improver

        original = {
            "personalInfo": {
                "name": "Ada Lovelace",
                "email": "ada@x.io",
                "avatarUrl": "http://cdn/a.webp",
                "photo": {"show": True, "ref": "canonical", "shape": "rounded"},
            },
            "summary": "Original summary",
            "workExperience": [],
        }

        # LLM returns content WITHOUT personalInfo (as the prompts instruct).
        async def fake_complete_json(*args, **kwargs):
            return {"summary": "Tailored summary", "workExperience": []}

        monkeypatch.setattr(improver, "complete_json", fake_complete_json)
        out = await improver.improve_resume(
            original_resume="(md)",
            job_description="JD",
            job_keywords={},
            original_resume_data=original,
        )
        # Identity (and the Photo System config) is restored verbatim.
        assert out["personalInfo"]["name"] == "Ada Lovelace"
        assert out["personalInfo"]["avatarUrl"] == "http://cdn/a.webp"
        assert out["personalInfo"]["photo"]["show"] is True
        assert out["personalInfo"]["photo"]["shape"] == "rounded"
        # Content was still tailored.
        assert out["summary"] == "Tailored summary"


class TestProfileAvatarOverlay:
    """Profile reads live-resolve identity.avatarUrl from the account master."""

    async def test_overlay_sets_live_url(self, monkeypatch):
        from types import SimpleNamespace
        from app.profile.service import ProfileService

        async def fake_get_by_id(uid):
            return SimpleNamespace(avatar_url="http://cdn/live-new.webp")

        monkeypatch.setattr("app.auth.accounts.get_by_id", fake_get_by_id)
        # Stored profile has a STALE avatar URL from an earlier state.
        row = {"user_id": "u1", "data": {"identity": {"name": "Ada", "avatarUrl": "http://cdn/old.webp"}}}
        await ProfileService._overlay_live_avatar(row)
        assert row["data"]["identity"]["avatarUrl"] == "http://cdn/live-new.webp"

    async def test_overlay_clears_when_account_has_no_avatar(self, monkeypatch):
        from types import SimpleNamespace
        from app.profile.service import ProfileService

        async def fake_get_by_id(uid):
            return SimpleNamespace(avatar_url=None)

        monkeypatch.setattr("app.auth.accounts.get_by_id", fake_get_by_id)
        row = {"user_id": "u1", "data": {"identity": {"name": "Ada", "avatarUrl": "http://cdn/old.webp"}}}
        await ProfileService._overlay_live_avatar(row)
        assert row["data"]["identity"]["avatarUrl"] is None


class TestPublicAvatarEnrichment:
    """The public projection resolves the avatar from the LIVE account master."""

    async def test_enrich_uses_live_url_srcset_and_metadata(self, monkeypatch):
        from types import SimpleNamespace
        from app.profile.service import ProfileService

        async def fake_get_by_id(uid):
            return SimpleNamespace(
                avatar_url="https://res.cloudinary.com/demo/image/upload/v1/u/live.webp",
                avatar_width=800,
                avatar_height=600,
                avatar_dominant_color="#abcdef",
            )

        monkeypatch.setattr("app.auth.accounts.get_by_id", fake_get_by_id)
        # Stale stored URL must be overridden by the live account master.
        payload = {"identity": {"name": "Ada", "avatarUrl": "http://cdn/stale.webp", "avatarSrcset": []}}
        await ProfileService._enrich_public_avatar(payload, "u1")
        ident = payload["identity"]
        assert ident["avatarUrl"] == "https://res.cloudinary.com/demo/image/upload/v1/u/live.webp"
        assert ident["avatarWidth"] == 800 and ident["avatarHeight"] == 600
        assert ident["avatarDominantColor"] == "#abcdef"
        assert [r["width"] for r in ident["avatarSrcset"]] == [96, 192, 384]
        assert all("live.webp" in r["url"] for r in ident["avatarSrcset"])

    async def test_enrich_clears_when_account_photo_removed(self, monkeypatch):
        from types import SimpleNamespace
        from app.profile.service import ProfileService

        async def fake_get_by_id(uid):
            return SimpleNamespace(avatar_url=None)

        monkeypatch.setattr("app.auth.accounts.get_by_id", fake_get_by_id)
        # Public projection had a stale URL; the account photo is now gone.
        payload = {
            "identity": {
                "name": "Ada",
                "avatarUrl": "http://cdn/stale.webp",
                "avatarSrcset": [{"url": "http://cdn/stale.webp?96", "width": 96}],
            }
        }
        await ProfileService._enrich_public_avatar(payload, "u1")
        # Cleared -> the public view renders initials, not a dead image.
        assert payload["identity"]["avatarUrl"] is None
        assert payload["identity"]["avatarSrcset"] == []


class TestPublicSrcsetProjection:
    """project_public_profile derives responsive variants from the master."""

    def test_srcset_present_for_cloudinary_master(self):
        from app.profile.public import project_public_profile
        from app.profile.schemas import ProfileData

        p = ProfileData.model_validate(
            {
                "identity": {
                    "name": "Ada",
                    "avatarUrl": "https://res.cloudinary.com/demo/image/upload/v1/u/a.webp",
                }
            }
        )
        pub = project_public_profile(p)
        srcset = pub["identity"]["avatarSrcset"]
        assert [row["width"] for row in srcset] == [96, 192, 384]
        assert all("res.cloudinary.com" in row["url"] for row in srcset)

    def test_srcset_empty_without_avatar(self):
        from app.profile.public import project_public_profile
        from app.profile.schemas import ProfileData

        pub = project_public_profile(ProfileData.model_validate({"identity": {"name": "Ada"}}))
        assert pub["identity"]["avatarSrcset"] == []

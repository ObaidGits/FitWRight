"""Unit tests for the Professional Profile domain logic (pure engines).

Covers the schema (uid minting, coercion), the Canonical Skill Engine, the
Completion Engine (weights + suggestions), the Projection Engine
(ProfileData -> ResumeData), and the non-destructive resume->profile backfill.
No I/O - these are the deterministic building blocks the service composes.
"""

from __future__ import annotations

from app.profile.backfill import build_profile_from_resume
from app.profile.completion import build_suggestions, compute_completeness
from app.profile.projection import ProjectionEngine
from app.profile.schemas import ProfileData, Skill
from app.profile.skills import canonicalize, make_skill_dict


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


class TestSchema:
    def test_empty_profile_is_valid(self):
        p = ProfileData()
        assert p.identity.name == ""
        assert p.workExperience == []
        assert p.meta.schemaVersion == 1

    def test_list_items_get_stable_uids(self):
        p = ProfileData.model_validate(
            {"workExperience": [{"title": "Eng"}, {"title": "SWE"}]}
        )
        uids = [e.uid for e in p.workExperience]
        assert all(uids)
        assert uids[0] != uids[1]

    def test_provided_uid_is_preserved(self):
        p = ProfileData.model_validate(
            {"workExperience": [{"uid": "fixed-uid", "title": "Eng"}]}
        )
        assert p.workExperience[0].uid == "fixed-uid"

    def test_string_list_coercion(self):
        # A single string for a list field is coerced to a one-item list.
        p = ProfileData.model_validate(
            {"workExperience": [{"title": "Eng", "description": "did things"}]}
        )
        assert p.workExperience[0].description == ["did things"]

    def test_unknown_top_level_keys_dropped(self):
        p = ProfileData.model_validate({"summary": "hi", "evil": "x"})
        assert not hasattr(p, "evil")
        assert p.summary == "hi"


# ---------------------------------------------------------------------------
# Canonical Skill Engine
# ---------------------------------------------------------------------------


class TestSkills:
    def test_alias_normalization(self):
        canonical, display = canonicalize("JS")
        assert display == "JavaScript"
        assert canonical == "javascript"

    def test_unknown_skill_kept_verbatim(self):
        canonical, display = canonicalize("Rust")
        assert display == "Rust"
        assert canonical == "rust"

    def test_blank_skill(self):
        assert canonicalize("   ") == ("", "")

    def test_make_skill_dict_records_alias(self):
        d = make_skill_dict("js", category="technical")
        assert d["displayName"] == "JavaScript"
        assert "js" in d["aliases"]
        assert d["category"] == "technical"


# ---------------------------------------------------------------------------
# Completion Engine
# ---------------------------------------------------------------------------


class TestCompletion:
    def test_empty_profile_scores_zero(self):
        assert compute_completeness(ProfileData()) == 0

    def test_full_profile_scores_100(self):
        p = ProfileData.model_validate(
            {
                "identity": {
                    "name": "Ada",
                    "headline": "Engineer",
                    "email": "a@b.co",
                    "location": "NYC",
                    "linkedin": "https://linkedin.com/in/ada",
                },
                "summary": "A summary.",
                "workExperience": [
                    {"title": "Eng", "description": ["Built things"]}
                ],
                "education": [{"institution": "MIT", "degree": "BS"}],
                "personalProjects": [{"name": "Proj"}],
                "skills": {"technical": [{"canonical": "python", "displayName": "Python"}]},
            }
        )
        assert compute_completeness(p) == 100

    def test_score_is_clamped_and_monotonic(self):
        empty = compute_completeness(ProfileData())
        with_name = compute_completeness(
            ProfileData.model_validate({"identity": {"name": "Ada"}})
        )
        assert 0 <= empty <= with_name <= 100

    def test_suggestions_prioritize_unmet_high_weight(self):
        suggestions = build_suggestions(ProfileData())
        assert suggestions[0].done is False
        # Highest-weight unmet item (experience=18) should be near the top.
        assert suggestions[0].weight >= suggestions[-1].weight

    def test_suggestions_mark_done(self):
        p = ProfileData.model_validate({"identity": {"name": "Ada"}})
        by_key = {s.key: s for s in build_suggestions(p)}
        assert by_key["name"].done is True
        assert by_key["summary"].done is False


# ---------------------------------------------------------------------------
# Projection Engine
# ---------------------------------------------------------------------------


class TestProjection:
    def _profile(self) -> ProfileData:
        return ProfileData.model_validate(
            {
                "identity": {
                    "name": "Ada Lovelace",
                    "headline": "Staff Engineer",
                    "email": "ada@example.com",
                    "linkedin": "https://linkedin.com/in/ada",
                    "avatarUrl": "https://cdn/x.webp",
                },
                "summary": "Builds reliable systems.",
                "workExperience": [
                    {"uid": "w1", "title": "Eng", "company": "Acme", "description": ["Shipped"]}
                ],
                "education": [{"uid": "e1", "institution": "MIT", "degree": "BS"}],
                "personalProjects": [{"uid": "p1", "name": "Proj", "description": ["Did"]}],
                "skills": {
                    "technical": [{"canonical": "python", "displayName": "Python"}],
                    "tools": [{"canonical": "docker", "displayName": "Docker"}],
                    "languages": [{"canonical": "english", "displayName": "English"}],
                },
                "certifications": [{"name": "AWS SA", "issuer": "Amazon"}],
                "achievements": [{"kind": "award", "title": "Best Eng"}],
            }
        )

    def test_projects_resume_shape(self):
        resume = ProjectionEngine.project_resume(self._profile())
        assert resume["personalInfo"]["name"] == "Ada Lovelace"
        assert resume["personalInfo"]["title"] == "Staff Engineer"
        assert resume["summary"] == "Builds reliable systems."
        assert resume["workExperience"][0]["company"] == "Acme"
        assert resume["education"][0]["institution"] == "MIT"
        assert resume["personalProjects"][0]["name"] == "Proj"

    def test_skills_fold_into_additional(self):
        resume = ProjectionEngine.project_resume(self._profile())
        additional = resume["additional"]
        assert "Python" in additional["technicalSkills"]
        assert "Docker" in additional["technicalSkills"]  # tools folded in
        assert additional["languages"] == ["English"]
        assert additional["awards"] == ["Best Eng"]
        assert additional["certificationsTraining"] == ["AWS SA - Amazon"]

    def test_provenance_stamped(self):
        resume = ProjectionEngine.project_resume(self._profile(), profile_version=7)
        assert resume["meta"]["derivedFromProfile"] is True
        assert resume["meta"]["derivedFromProfileVersion"] == 7
        # Each item carries its originating profile uid for future sync.
        assert resume["workExperience"][0]["profileUid"] == "w1"

    def test_photo_excluded_by_default(self):
        resume = ProjectionEngine.project_resume(self._profile())
        assert "avatarUrl" not in resume["personalInfo"]
        resume2 = ProjectionEngine.project_resume(
            self._profile(), options={"include_photo": True}
        )
        assert resume2["personalInfo"]["avatarUrl"] == "https://cdn/x.webp"

    def test_default_section_meta_present(self):
        resume = ProjectionEngine.project_resume(self._profile())
        assert isinstance(resume["sectionMeta"], list)
        assert len(resume["sectionMeta"]) > 0


# ---------------------------------------------------------------------------
# Backfill (resume -> profile, non-destructive)
# ---------------------------------------------------------------------------


class TestBackfill:
    def test_derives_from_processed_data(self):
        processed = {
            "personalInfo": {"name": "Ada", "title": "Eng", "email": "a@b.co"},
            "summary": "Sum",
            "workExperience": [{"title": "SWE", "company": "X", "description": ["a"]}],
            "education": [{"institution": "MIT", "degree": "BS"}],
            "additional": {"technicalSkills": ["Python", "js"], "awards": ["Prize"]},
        }
        profile = build_profile_from_resume(processed)
        assert profile.identity.name == "Ada"
        assert profile.summary == "Sum"
        assert profile.workExperience[0].company == "X"
        skill_names = [s.displayName for s in profile.skills.technical]
        assert "Python" in skill_names
        assert "JavaScript" in skill_names  # canonicalized from "js"
        assert profile.meta.source == "migration"

    def test_user_fallbacks_used(self):
        profile = build_profile_from_resume(
            {"personalInfo": {}},
            user_fallback={
                "name": "Grace",
                "headline": "Rear Admiral",
                "location": "DC",
                "links": [{"label": "site", "url": "https://g.co"}],
                "avatar_url": "https://cdn/a.webp",
            },
        )
        assert profile.identity.name == "Grace"
        assert profile.identity.headline == "Rear Admiral"
        assert profile.identity.location == "DC"
        assert profile.identity.avatarUrl == "https://cdn/a.webp"
        assert profile.links[0].url == "https://g.co"

    def test_empty_input_yields_valid_profile(self):
        profile = build_profile_from_resume(None)
        assert profile.identity.name == ""
        assert compute_completeness(profile) == 0

    def test_backfill_roundtrips_through_projection(self):
        processed = {
            "personalInfo": {"name": "Ada", "title": "Eng"},
            "workExperience": [{"title": "SWE", "company": "X", "description": ["a"]}],
        }
        profile = build_profile_from_resume(processed)
        resume = ProjectionEngine.project_resume(profile)
        assert resume["personalInfo"]["name"] == "Ada"
        assert resume["workExperience"][0]["title"] == "SWE"

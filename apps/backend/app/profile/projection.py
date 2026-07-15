"""Projection Engine — the sole ``ProfileData -> ResumeData`` boundary (ADR-6).

Every resume produced from a profile funnels through
:meth:`ProjectionEngine.project_resume`, so the profile→resume contract is
enforced in exactly one place (templates/section ordering evolve here only).
The projection is **pure** (no I/O) and deterministic, so it is trivially
testable and safe to reuse for preview, persistence, and future portfolio/public
exports.

Provenance stamping (ADR-9/10): each generated resume item carries the
originating ``profileUid`` and the resume ``meta`` records
``derivedFromProfileVersion`` so a later profile↔resume sync (P4) can refresh
only non-overridden items without breaking the immutable-snapshot invariant.
"""

from __future__ import annotations

import copy
from typing import Any

from app.profile.schemas import ProfileData, Skill
from app.schemas.models import DEFAULT_SECTION_META


class ProjectionEngine:
    """Turns a canonical :class:`ProfileData` into resume-shaped data."""

    @staticmethod
    def _resolve_photo_config(options: dict[str, Any], avatar_url: str | None):
        """Build the resume's :class:`PhotoConfig` from options (Photo System).

        Precedence: an explicit ``photo`` config wins; otherwise ``include_photo``
        turns on a default canonical-tracking photo; otherwise ``None`` (no photo
        block on the resume). When the config pins a snapshot without a frozen URL
        yet, we freeze the current profile avatar so the snapshot is immutable
        from creation.
        """
        from app.profile.photo import PhotoConfig

        raw = options.get("photo")
        if raw is not None:
            config = raw if isinstance(raw, PhotoConfig) else PhotoConfig.model_validate(raw)
        elif options.get("include_photo"):
            config = PhotoConfig(show=True, ref="canonical")
        else:
            return None
        if config.ref == "snapshot" and not config.snapshot.url and avatar_url:
            config = config.model_copy(deep=True)
            config.snapshot.url = avatar_url
        return config

    @staticmethod
    def _skill_names(skills: list[Skill]) -> list[str]:
        """Display names for a skill list, de-duplicated, order-preserving."""
        seen: set[str] = set()
        out: list[str] = []
        for s in skills:
            name = (s.displayName or s.canonical or "").strip()
            if name and name.lower() not in seen:
                seen.add(name.lower())
                out.append(name)
        return out

    @classmethod
    def project_resume(
        cls,
        profile: ProfileData,
        options: dict[str, Any] | None = None,
        *,
        profile_version: int | None = None,
    ) -> dict[str, Any]:
        """Project ``profile`` into a ``ResumeData``-shaped dict.

        ``options`` (all optional):
        - ``include_photo`` (bool): shorthand to show the header photo tracking
          the canonical profile photo (``ref="canonical"``).
        - ``photo`` (PhotoConfig | dict): explicit per-resume photo configuration
          (presentation + provenance). Takes precedence over ``include_photo``.
        - ``section_meta`` (list): explicit ordering/visibility override.
        - ``template`` (str): template id recorded in ``meta.template`` for the
          render engine to pick up.
        - ``sections`` (dict[str, bool]): per-section visibility override keyed by
          the section ``key`` (e.g. ``{"education": False}``) — applied on top of
          the profile's ``sectionMeta`` so a generated resume can hide sections
          without mutating the profile.
        - ``overrides`` (dict): resume-specific top-level overrides shallow-merged
          onto the projection last (e.g. a tailored ``summary``).
        """
        options = options or {}
        identity = profile.identity

        personal_info: dict[str, Any] = {
            "name": identity.name,
            "title": identity.headline or identity.currentRole,
            "email": identity.email,
            "phone": identity.phone,
            "location": identity.location,
            "website": identity.website,
            "linkedin": identity.linkedin,
            "github": identity.github,
        }
        photo_config = cls._resolve_photo_config(options, identity.avatarUrl)
        if photo_config is not None:
            from app.profile.photo import resolve_photo_url

            personal_info["photo"] = photo_config.model_dump(mode="json")
            personal_info["avatarUrl"] = resolve_photo_url(photo_config, identity.avatarUrl)

        work_experience = [
            {
                "id": i + 1,
                "profileUid": exp.uid,
                "title": exp.title,
                "company": exp.company,
                "location": exp.location,
                "years": exp.years,
                "description": list(exp.description),
            }
            for i, exp in enumerate(profile.workExperience)
        ]

        education = [
            {
                "id": i + 1,
                "profileUid": edu.uid,
                "institution": edu.institution,
                "degree": edu.degree,
                "years": edu.years,
                "description": edu.description,
            }
            for i, edu in enumerate(profile.education)
        ]

        projects = [
            {
                "id": i + 1,
                "profileUid": proj.uid,
                "name": proj.name,
                "role": proj.role,
                "years": proj.years,
                "github": proj.github,
                "website": proj.website,
                "description": list(proj.description),
            }
            for i, proj in enumerate(profile.personalProjects)
        ]

        # Skills → additional{}. Technical + tools fold into technicalSkills.
        technical = cls._skill_names(profile.skills.technical) + cls._skill_names(
            profile.skills.tools
        )
        # De-dup across the merged technical+tools list (order-preserving).
        seen: set[str] = set()
        technical_skills: list[str] = []
        for name in technical:
            if name.lower() not in seen:
                seen.add(name.lower())
                technical_skills.append(name)

        awards = [
            a.title for a in profile.achievements if a.kind in ("award", "achievement") and a.title
        ]
        certifications_training = [
            (f"{c.name} — {c.issuer}" if c.issuer else c.name)
            for c in profile.certifications
            if c.name
        ]

        additional = {
            "technicalSkills": technical_skills,
            "languages": cls._skill_names(profile.skills.languages),
            "certificationsTraining": certifications_training,
            "awards": awards,
        }

        section_meta = options.get("section_meta")
        if not section_meta:
            section_meta = (
                [m.model_dump() for m in profile.sectionMeta]
                if profile.sectionMeta
                else copy.deepcopy(DEFAULT_SECTION_META)
            )
        # Apply per-section visibility overrides without mutating the profile.
        visibility = options.get("sections")
        if isinstance(visibility, dict) and section_meta:
            section_meta = copy.deepcopy(section_meta)
            for meta in section_meta:
                key = meta.get("key")
                if key in visibility:
                    meta["isVisible"] = bool(visibility[key])

        custom_sections = {
            key: cs.model_dump() for key, cs in profile.customSections.items()
        }

        resume: dict[str, Any] = {
            "personalInfo": personal_info,
            "summary": profile.summary or identity.careerObjective,
            "workExperience": work_experience,
            "education": education,
            "personalProjects": projects,
            "additional": additional,
            "sectionMeta": section_meta,
            "customSections": custom_sections,
            # Provenance for safe sync (ADR-10). Kept in processed_data.meta so
            # no resume column change is needed.
            "meta": {
                "derivedFromProfile": True,
                "derivedFromProfileVersion": profile_version,
                "template": options.get("template"),
            },
        }

        # Resume-specific top-level overrides applied last (tailoring, etc.).
        overrides = options.get("overrides")
        if isinstance(overrides, dict):
            for key, value in overrides.items():
                if key in resume and value is not None:
                    resume[key] = value
        return resume

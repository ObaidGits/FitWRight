"""Non-destructive backfill: master resume ``processed_data`` -> ``ProfileData``.

Pure derivation used by :class:`app.profile.service.ProfileService` when a user
has no profile yet (lazy migration - ADR-14). The resume is **never modified**;
we only *read* its structured data and the identity-level fallbacks on the user
record (headline/location/links/avatar_url). Missing input yields an empty but
valid profile so onboarding can proceed.
"""

from __future__ import annotations

from typing import Any

from app.profile.schemas import ProfileData
from app.profile.skills import make_skill_dict


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def build_profile_from_resume(
    processed_data: dict[str, Any] | None,
    *,
    user_fallback: dict[str, Any] | None = None,
) -> ProfileData:
    """Derive a ``ProfileData`` from a resume's processed_data (+ user fallbacks).

    ``user_fallback`` may carry ``name``/``headline``/``location``/``links``/
    ``avatar_url`` from the ``users`` row, used only where the resume lacks them.
    """
    data = processed_data or {}
    fb = user_fallback or {}

    pi = data.get("personalInfo") or {}
    identity: dict[str, Any] = {
        "name": pi.get("name") or fb.get("name") or "",
        "headline": pi.get("title") or fb.get("headline") or "",
        "email": pi.get("email") or "",
        "phone": pi.get("phone") or "",
        "location": pi.get("location") or fb.get("location") or "",
        "website": pi.get("website") or None,
        "linkedin": pi.get("linkedin") or None,
        "github": pi.get("github") or None,
        "avatarUrl": fb.get("avatar_url") or None,
    }

    work_experience = [
        {
            "title": e.get("title", ""),
            "company": e.get("company", ""),
            "location": e.get("location"),
            "years": e.get("years", ""),
            "description": _as_list(e.get("description")),
        }
        for e in _as_list(data.get("workExperience"))
    ]

    education = [
        {
            "institution": e.get("institution", ""),
            "degree": e.get("degree", ""),
            "years": e.get("years", ""),
            "description": e.get("description"),
        }
        for e in _as_list(data.get("education"))
    ]

    projects = [
        {
            "name": p.get("name", ""),
            "role": p.get("role", ""),
            "years": p.get("years", ""),
            "github": p.get("github"),
            "website": p.get("website"),
            "description": _as_list(p.get("description")),
        }
        for p in _as_list(data.get("personalProjects"))
    ]

    additional = data.get("additional") or {}
    skills = {
        "technical": [
            make_skill_dict(s, category="technical")
            for s in _as_list(additional.get("technicalSkills"))
            if isinstance(s, str) and s.strip()
        ],
        "languages": [
            make_skill_dict(s, category="language")
            for s in _as_list(additional.get("languages"))
            if isinstance(s, str) and s.strip()
        ],
        "soft": [],
        "tools": [],
    }

    certifications = [
        {"name": c} for c in _as_list(additional.get("certificationsTraining")) if isinstance(c, str) and c.strip()
    ]
    achievements = [
        {"kind": "award", "title": a}
        for a in _as_list(additional.get("awards"))
        if isinstance(a, str) and a.strip()
    ]

    links = [
        {"label": link.get("label", ""), "url": link.get("url", "")}
        for link in _as_list(fb.get("links"))
        if isinstance(link, dict) and link.get("url")
    ]

    profile_dict: dict[str, Any] = {
        "identity": identity,
        "summary": data.get("summary") or "",
        "workExperience": work_experience,
        "education": education,
        "personalProjects": projects,
        "skills": skills,
        "certifications": certifications,
        "achievements": achievements,
        "links": links,
        "customSections": data.get("customSections") or {},
        "sectionMeta": data.get("sectionMeta") or [],
        "meta": {"schemaVersion": 1, "source": "migration"},
    }
    return ProfileData.model_validate(profile_dict)

"""Public projection platform — reusable outputs derived from the profile (P6).

The profile is the single source; every *external* representation is a pure
projection here, mirroring how resumes are produced by the Projection Engine.
This module adds three sibling projectors — **public profile**, **portfolio**,
and **JSON Resume export** — sharing the invariant that private fields
(salary expectation, visa/work-authorization, phone unless public) are never
leaked. Adding a future output (personal website, LinkedIn export) is a new pure
function here; no storage or API redesign.
"""

from __future__ import annotations

import re
from typing import Any

from app.profile.schemas import ProfileData

__all__ = [
    "slugify",
    "project_public_profile",
    "project_portfolio",
    "export_json_resume",
    "build_vcard",
    "public_json_ld",
]

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(value: str) -> str:
    """A URL-safe slug from a name/headline (stable, lowercased)."""
    return _SLUG_RE.sub("-", (value or "").lower()).strip("-") or "profile"


def _avatar_srcset(avatar_url: str | None) -> list[dict[str, object]]:
    """Responsive avatar descriptors for the public page (derived from master)."""
    if not avatar_url:
        return []
    from app.storage.image import responsive_srcset

    return responsive_srcset(avatar_url, (96, 192, 384))


def _skill_names(profile: ProfileData) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for group in (
        profile.skills.technical,
        profile.skills.tools,
        profile.skills.languages,
        profile.skills.soft,
    ):
        for s in group:
            name = (s.displayName or s.canonical or "").strip()
            if name and name.lower() not in seen:
                seen.add(name.lower())
                out.append(name)
    return out


def project_public_profile(profile: ProfileData, *, slug: str | None = None) -> dict[str, Any]:
    """A safe, public-facing view of the profile (no private/contact-sensitive data).

    Honors ``identity.careerVisibility``. Contact details are intentionally
    omitted except opt-in public links (website/linkedin/github) so a shared
    profile never leaks phone/email/salary.
    """
    ident = profile.identity
    return {
        "slug": slug or slugify(ident.name or ident.headline),
        "visibility": ident.careerVisibility,
        "identity": {
            "name": ident.name,
            "headline": ident.headline or ident.currentRole,
            "location": ident.location,
            "website": ident.website,
            "linkedin": ident.linkedin,
            "github": ident.github,
            "avatarUrl": ident.avatarUrl,
            # Responsive, optimized variants derived from the one canonical
            # master (CDN URL transforms — no extra storage). Empty-safe when no
            # avatar or a non-CDN (local) master.
            "avatarSrcset": _avatar_srcset(ident.avatarUrl),
        },
        "summary": profile.summary or ident.careerObjective,
        "experience": [
            {
                "title": e.title,
                "company": e.company,
                "years": e.years,
                "description": list(e.description),
            }
            for e in profile.workExperience
        ],
        "projects": [
            {
                "name": p.name,
                "role": p.role,
                "github": p.github,
                "website": p.website,
                "description": list(p.description),
                "tech": list(p.tech),
            }
            for p in profile.personalProjects
        ],
        "skills": _skill_names(profile),
        "education": [
            {"institution": ed.institution, "degree": ed.degree, "years": ed.years}
            for ed in profile.education
        ],
    }


def project_portfolio(profile: ProfileData) -> dict[str, Any]:
    """A portfolio-oriented projection (projects-first, with proof of skills)."""
    public = project_public_profile(profile)
    return {
        "identity": public["identity"],
        "summary": public["summary"],
        "projects": public["projects"],
        "experience": public["experience"],
        "skills": public["skills"],
        "certifications": [
            {"name": c.name, "issuer": c.issuer, "date": c.date, "url": c.url}
            for c in profile.certifications
        ],
    }


def _vcard_escape(value: str) -> str:
    """Escape a value per RFC 6350 (vCard 3.0) text rules."""
    return (
        (value or "")
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def build_vcard(profile: ProfileData) -> str:
    """Build an RFC-6350 vCard (3.0) from the profile identity (public-safe).

    Only contact fields the user chose to expose publicly are included; salary,
    visa, and private notes are never emitted. Returns CRLF-delimited text ready
    to serve as ``text/vcard``.
    """
    ident = profile.identity
    lines = ["BEGIN:VCARD", "VERSION:3.0"]
    if ident.name:
        lines.append(f"FN:{_vcard_escape(ident.name)}")
        parts = ident.name.split(" ", 1)
        last = parts[1] if len(parts) > 1 else ""
        lines.append(f"N:{_vcard_escape(last)};{_vcard_escape(parts[0])};;;")
    if ident.headline or ident.currentRole:
        lines.append(f"TITLE:{_vcard_escape(ident.headline or ident.currentRole)}")
    if ident.currentCompany:
        lines.append(f"ORG:{_vcard_escape(ident.currentCompany)}")
    if ident.email:
        lines.append(f"EMAIL;TYPE=INTERNET:{_vcard_escape(ident.email)}")
    if ident.location:
        lines.append(f"ADR;TYPE=WORK:;;{_vcard_escape(ident.location)};;;;")
    for url in (ident.website, ident.linkedin, ident.github):
        if url:
            lines.append(f"URL:{_vcard_escape(url)}")
    lines.append("END:VCARD")
    return "\r\n".join(lines) + "\r\n"


def public_json_ld(public: dict[str, Any]) -> dict[str, Any]:
    """Schema.org ``Person`` JSON-LD for a public profile (SEO structured data)."""
    identity = public.get("identity", {})
    same_as = [u for u in (identity.get("linkedin"), identity.get("github")) if u]
    node: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "Person",
        "name": identity.get("name") or "",
        "jobTitle": identity.get("headline") or "",
        "description": public.get("summary") or "",
        "knowsAbout": public.get("skills", []),
    }
    if identity.get("website"):
        node["url"] = identity["website"]
    if identity.get("avatarUrl"):
        # schema.org ImageObject (richer than a bare URL) for image rich results.
        image: dict[str, Any] = {
            "@type": "ImageObject",
            "url": identity["avatarUrl"],
            "caption": identity.get("name") or "",
        }
        if identity.get("avatarWidth"):
            image["width"] = identity["avatarWidth"]
        if identity.get("avatarHeight"):
            image["height"] = identity["avatarHeight"]
        node["image"] = image
    if identity.get("location"):
        node["address"] = {"@type": "PostalAddress", "addressLocality": identity["location"]}
    if same_as:
        node["sameAs"] = same_as
    return node


def export_json_resume(profile: ProfileData) -> dict[str, Any]:
    """Export the profile in the `JSON Resume <https://jsonresume.org>`_ schema.

    Round-trips with :func:`app.profile.import_adapters._from_json_resume` for
    third-party integrations and portability.
    """
    ident = profile.identity
    profiles = []
    if ident.linkedin:
        profiles.append({"network": "LinkedIn", "url": ident.linkedin})
    if ident.github:
        profiles.append({"network": "GitHub", "url": ident.github})

    return {
        "$schema": "https://raw.githubusercontent.com/jsonresume/resume-schema/v1.0.0/schema.json",
        "basics": {
            "name": ident.name,
            "label": ident.headline or ident.currentRole,
            "email": ident.email,
            "phone": ident.phone,
            "url": ident.website,
            "summary": profile.summary or ident.careerObjective,
            "location": {"city": ident.location},
            "profiles": profiles,
        },
        "work": [
            {
                "name": e.company,
                "position": e.title,
                "startDate": "",
                "endDate": e.years,
                "highlights": list(e.description),
            }
            for e in profile.workExperience
        ],
        "education": [
            {"institution": ed.institution, "area": ed.degree, "studyType": "", "endDate": ed.years}
            for ed in profile.education
        ],
        "projects": [
            {"name": p.name, "url": p.website, "highlights": list(p.description)}
            for p in profile.personalProjects
        ],
        "skills": [
            {"name": s.displayName or s.canonical, "keywords": s.aliases}
            for s in profile.skills.technical
        ],
        "certificates": [
            {"name": c.name, "issuer": c.issuer, "date": c.date, "url": c.url}
            for c in profile.certifications
        ],
    }

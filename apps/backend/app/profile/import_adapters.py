"""Import adapters — pluggable sources that derive a ``ProfileData`` candidate.

Every import source (a parsed resume today; LinkedIn / GitHub / Europass /
Portfolio later) implements one contract: turn its native payload into a
``ProfileData`` the Merge Engine can plan against. Adding a source is a new
adapter + registry entry — the merge/preview/apply pipeline is untouched
(open/closed — design §P3 "Import Sources").

Only the **resume** and **json_resume** adapters are implemented now; the others
are declared as ``NotImplementedError`` stubs so the surface is discoverable and
wiring them later needs no pipeline change.
"""

from __future__ import annotations

from typing import Any, Callable

from app.profile.backfill import build_profile_from_resume
from app.profile.schemas import ProfileData
from app.profile.skills import make_skill_dict

__all__ = ["derive_candidate", "IMPORT_SOURCES", "ImportError_"]


class ImportError_(Exception):
    """Raised when a payload cannot be turned into a profile candidate."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _from_resume(payload: dict[str, Any]) -> ProfileData:
    """Derive from a resume's ``processed_data`` (already parsed) + user fallbacks."""
    processed = payload.get("processed_data")
    if not isinstance(processed, dict):
        raise ImportError_("invalid", "Resume has no structured data to import.")
    return build_profile_from_resume(processed, user_fallback=payload.get("user_fallback"))


def _str_list(value: Any) -> list[str]:
    return [x for x in value if isinstance(x, str)] if isinstance(value, list) else []


def _from_json_resume(payload: dict[str, Any]) -> ProfileData:
    """Map a `JSON Resume <https://jsonresume.org>`_ document to ``ProfileData``.

    Supports the common ``basics``/``work``/``education``/``projects``/``skills``
    blocks; unknown fields are ignored. Enables round-trip with the JSON Resume
    export (``app/profile/public.py``) and third-party integrations.
    """
    doc = payload.get("data")
    if not isinstance(doc, dict):
        raise ImportError_("invalid", "Expected a JSON Resume 'data' object.")

    basics = doc.get("basics") or {}
    profiles = {p.get("network", "").lower(): p.get("url") for p in basics.get("profiles") or [] if isinstance(p, dict)}
    identity = {
        "name": basics.get("name") or "",
        "headline": basics.get("label") or "",
        "email": basics.get("email") or "",
        "phone": basics.get("phone") or "",
        "location": (basics.get("location") or {}).get("city") or "",
        "website": basics.get("url") or None,
        "linkedin": profiles.get("linkedin"),
        "github": profiles.get("github"),
    }

    work = [
        {
            "title": w.get("position", ""),
            "company": w.get("name") or w.get("company") or "",
            "years": " – ".join(x for x in [w.get("startDate"), w.get("endDate")] if x),
            "description": _str_list(w.get("highlights")),
        }
        for w in doc.get("work") or []
        if isinstance(w, dict)
    ]
    education = [
        {
            "institution": e.get("institution", ""),
            "degree": " ".join(x for x in [e.get("studyType"), e.get("area")] if x),
            "years": " – ".join(x for x in [e.get("startDate"), e.get("endDate")] if x),
        }
        for e in doc.get("education") or []
        if isinstance(e, dict)
    ]
    projects = [
        {
            "name": p.get("name", ""),
            "description": _str_list(p.get("highlights")),
            "website": p.get("url") or None,
        }
        for p in doc.get("projects") or []
        if isinstance(p, dict)
    ]
    technical: list[dict] = []
    for s in doc.get("skills") or []:
        if isinstance(s, dict):
            for kw in _str_list(s.get("keywords")) or ([s.get("name")] if s.get("name") else []):
                if isinstance(kw, str) and kw.strip():
                    technical.append(make_skill_dict(kw, category="technical"))

    return ProfileData.model_validate(
        {
            "identity": identity,
            "summary": basics.get("summary") or "",
            "workExperience": work,
            "education": education,
            "personalProjects": projects,
            "skills": {"technical": technical, "soft": [], "languages": [], "tools": []},
            "meta": {"schemaVersion": 1, "source": "import"},
        }
    )


def _not_implemented(name: str) -> Callable[[dict[str, Any]], ProfileData]:
    def _adapter(_payload: dict[str, Any]) -> ProfileData:
        raise ImportError_("unsupported", f"The {name} import source is not available yet.")

    return _adapter


# Registry: source key → adapter. Extend by adding an entry (open/closed).
IMPORT_SOURCES: dict[str, Callable[[dict[str, Any]], ProfileData]] = {
    "resume": _from_resume,
    "json_resume": _from_json_resume,
    "linkedin": _not_implemented("LinkedIn"),
    "github": _not_implemented("GitHub"),
    "europass": _not_implemented("Europass"),
    "portfolio": _not_implemented("Portfolio"),
}


def derive_candidate(source: str, payload: dict[str, Any]) -> ProfileData:
    """Derive a ``ProfileData`` candidate from ``source`` + its native payload."""
    adapter = IMPORT_SOURCES.get(source)
    if adapter is None:
        raise ImportError_("unsupported", f"Unknown import source: {source!r}")
    return adapter(payload)

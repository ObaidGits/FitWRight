"""Synchronization Engine — refresh a resume from the profile (P4).

A resume is only ever a **projection** of the profile at a point in time; sync
re-projects the current profile and lets the user preview the field-level diff
before applying it back to a *draft* resume (optimistic-concurrency guarded).

Hard invariant (design §P4): **submitted resumes are immutable forever.** A
resume referenced by any application in a non-``saved`` status is locked — sync
previews still work (read-only) but apply is refused; the user regenerates a new
resume instead. This keeps the historical record of what was actually sent
truthful.

All persistence goes through the ``app.database`` facade (scoped by ``user_id``);
the pure diff is reused from the resume version service so profile-sync and
version-compare speak the same diff language.
"""

from __future__ import annotations

from typing import Any

from app.profile.projection import ProjectionEngine
from app.profile.schemas import ProfileData
from app.versions.service import diff_processed_data

__all__ = [
    "SUBMITTED_STATUSES",
    "is_resume_locked",
    "preview_sync",
    "apply_sync",
]

# An application in any of these statuses means its resume was sent → immutable.
SUBMITTED_STATUSES = frozenset(
    {"applied", "no_response", "response", "interview", "accepted", "rejected"}
)


def _db():
    from app import database

    return database.db


async def is_resume_locked(user_id: str, resume_id: str) -> bool:
    """Whether ``resume_id`` is referenced by a submitted application (immutable)."""
    apps = await _db().list_applications(user_id)
    for app in apps:
        if app.get("resume_id") == resume_id and app.get("status") in SUBMITTED_STATUSES:
            return True
    return False


def _existing_photo(resume: dict[str, Any]) -> dict[str, Any] | None:
    """The resume's current photo config, if any (preserved across sync)."""
    processed = resume.get("processed_data")
    if isinstance(processed, dict):
        personal = processed.get("personalInfo")
        if isinstance(personal, dict) and isinstance(personal.get("photo"), dict):
            return personal["photo"]
    return None


def _project(
    profile: ProfileData,
    version: int,
    include_photo: bool,
    existing_photo: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Re-project the profile, PRESERVING the resume's existing photo config.

    Sync refreshes content from the profile but must never clobber the user's
    per-resume photo presentation/provenance (Photo System rule). An existing
    photo config is carried through verbatim (only its resolved URL is refreshed
    by the engine); absent one, ``include_photo`` adds the default canonical photo.
    """
    options: dict[str, Any] = {"include_photo": include_photo}
    if existing_photo is not None:
        options["photo"] = existing_photo
    return ProjectionEngine.project_resume(
        profile, options=options, profile_version=version
    )


async def preview_sync(
    user_id: str,
    resume_id: str,
    profile_row: dict[str, Any],
    *,
    include_photo: bool = False,
) -> dict[str, Any] | None:
    """Diff a resume's current data against a fresh projection of the profile.

    Returns ``None`` if the resume does not exist for this user; otherwise a dict
    with ``changes`` (field-level), the ``projected`` data, and an ``immutable``
    flag (with ``reason``) when the resume is a locked/submitted one.
    """
    resume = await _db().get_resume(user_id, resume_id)
    if resume is None:
        return None

    profile = ProfileData.model_validate(profile_row.get("data") or {})
    version = int(profile_row.get("version") or 1)
    projected = _project(profile, version, include_photo, _existing_photo(resume))

    current = resume.get("processed_data") or {}
    # Compare the meaningful content only (ignore volatile meta so the diff is
    # about real resume changes, not the provenance stamp).
    current_cmp = {k: v for k, v in current.items() if k != "meta"}
    projected_cmp = {k: v for k, v in projected.items() if k != "meta"}
    changes = diff_processed_data(current_cmp, projected_cmp)

    locked = await is_resume_locked(user_id, resume_id)
    return {
        "resume_id": resume_id,
        "resume_version": int(resume.get("version") or 1),
        "changes": changes,
        "projected": projected,
        "immutable": locked,
        "reason": "This resume was submitted with an application and is locked."
        if locked
        else None,
    }


async def apply_sync(
    user_id: str,
    resume_id: str,
    profile_row: dict[str, Any],
    *,
    base_version: int,
    include_photo: bool = False,
) -> tuple[str, dict[str, Any] | None]:
    """Apply the profile projection to a draft resume (resume version CAS).

    Returns ``(status, resume_dict)``:
    - ``("updated", dict)`` — applied.
    - ``("immutable", dict)`` — the resume is submitted/locked (no change).
    - ``("conflict", dict)`` — stale ``base_version``.
    - ``("not_found", None)`` — no such resume for this user.

    A ``manual`` snapshot ("Synced from profile") is captured after applying so
    the pre-sync state is always recoverable (non-destructive).
    """
    resume = await _db().get_resume(user_id, resume_id)
    if resume is None:
        return "not_found", None
    if await is_resume_locked(user_id, resume_id):
        return "immutable", resume

    profile = ProfileData.model_validate(profile_row.get("data") or {})
    version = int(profile_row.get("version") or 1)
    projected = _project(profile, version, include_photo, _existing_photo(resume))

    import json

    status, updated = await _db().update_resume_cas(
        user_id,
        resume_id,
        {
            "processed_data": projected,
            "content": json.dumps(projected, indent=2),
            "content_type": "json",
            "processing_status": "ready",
        },
        base_version=base_version,
    )
    if status == "updated" and updated is not None:
        try:
            from app.versions import service as version_service

            await version_service.capture_snapshot(
                user_id, resume_id, projected, "manual", label="Synced from profile"
            )
        except Exception:  # pragma: no cover - snapshot is best-effort
            pass
    return status, updated

"""ProfileService — orchestrates the canonical professional profile.

The service is the single entry point the router calls. It coordinates the pure
domain engines (backfill, completion, projection) with the ``app.database``
facade (the owned-table scoping boundary) and the version-snapshot subsystem.
Invariants it upholds:

- **One profile per user**, created lazily on first read by deriving it from the
  user's master resume (``source=migration``) — the resume is never mutated
  (ADR-14).
- **Optimistic concurrency** on every write via ``version`` CAS (mirrors the
  resume editor). A stale ``base_version`` yields a conflict the router maps to
  409, never a lost update.
- **Immutable history**: every applied write captures a content-hash-deduped,
  debounced gzip snapshot (``app/profile/versions.py``); the ``migration``
  baseline is always retained by prune.
- **Provenance-preserving projection**: generating a resume funnels through the
  Projection Engine only (ADR-6), stamping ``derivedFromProfileVersion``.

Domain events (``profile.created`` / ``profile.updated`` / ``profile.completed``
/ ``profile.resume_generated``) are emitted to the transactional outbox so
analytics/notification consumers stay decoupled from the write path.
"""

from __future__ import annotations

import logging
from typing import Any

from app.profile.backfill import build_profile_from_resume
from app.profile.completion import (
    build_suggestions,
    compute_ai_readiness,
    compute_ats_readiness,
    compute_completeness,
)
from app.profile.projection import ProjectionEngine
from app.profile.schemas import ProfileData
from app.profile.versions import capture_profile_snapshot

logger = logging.getLogger(__name__)


class ProfileServiceError(Exception):
    """Domain error with a machine code the router maps to an HTTP status."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _db():
    """Resolve the live DB facade lazily (tests swap ``database.db``)."""
    from app import database

    return database.db


async def _emit(event_type, user_id: str, payload: dict[str, Any]) -> None:
    """Best-effort domain event emit (never blocks/breaks the write path)."""
    try:
        from app.events import emit

        await emit(event_type, payload, user_id=user_id)
    except Exception:  # pragma: no cover - analytics must never break writes
        logger.exception("Profile event emit failed: %s", event_type)


def _import_statistics(incoming: ProfileData, plan) -> dict[str, Any]:
    """Quality + shape signals for an import preview (see ``ImportStatistics``)."""
    counts = plan.counts or {}
    return {
        "quality_score": compute_completeness(incoming),
        "sections": {
            "workExperience": len(incoming.workExperience),
            "education": len(incoming.education),
            "personalProjects": len(incoming.personalProjects),
            "certifications": len(incoming.certifications),
            "achievements": len(incoming.achievements),
            "skills": (
                len(incoming.skills.technical)
                + len(incoming.skills.tools)
                + len(incoming.skills.languages)
                + len(incoming.skills.soft)
            ),
        },
        "total_operations": len(plan.operations),
        "new_items": counts.get("add", 0),
        "updates": counts.get("update", 0),
        "conflicts": counts.get("conflict", 0),
        "duplicates": counts.get("duplicate", 0),
    }


async def _load_user_fallback(user_id: str) -> dict[str, Any]:
    """Identity-level fallbacks from the ``users`` row (headline/location/links)."""
    try:
        from app.auth import accounts

        rec = await accounts.get_by_id(user_id)
    except Exception:  # pragma: no cover - defensive; profile still derivable
        logger.exception("User fallback load failed for %s", user_id)
        return {}
    if rec is None:
        return {}
    return {
        "name": rec.name or "",
        "headline": rec.headline or "",
        "location": rec.location or "",
        "links": rec.links or [],
        "avatar_url": rec.avatar_url or None,
    }


def _compose_profile_resume_title(profile: "ProfileData") -> str:
    """Build a concise, relatable title for a profile-generated resume.

    Formats as ``"<Name> — <Role> [@ <Company>]"`` from the profile identity,
    degrading gracefully as fields are missing (never a sentence/paragraph).
    """
    ident = profile.identity
    name = (ident.name or "").strip()
    role = (ident.headline or ident.currentRole or "").strip()
    company = (ident.currentCompany or "").strip()

    descriptor = role
    if role and company:
        descriptor = f"{role} @ {company}"

    if name and descriptor:
        return f"{name} — {descriptor}"[:80]
    if descriptor:
        return descriptor[:80]
    if name:
        return f"{name} — Resume"[:80]
    return "Generated resume"


class ProfileService:
    """Orchestrates profile read/write/generate/versioning."""

    # -- Read / lazy create -------------------------------------------------

    async def get_or_create(self, user_id: str) -> dict[str, Any]:
        """Return the user's profile, deriving it from the master resume if absent.

        The derivation is a pure read of the master resume's ``processed_data``
        plus the user's identity fallbacks; the resume is never modified. The
        newly created profile is snapshotted with ``source=migration`` so history
        always has a baseline.
        """
        existing = await _db().get_profile(user_id)
        if existing is not None:
            await self._overlay_live_avatar(existing)
            return existing

        master = await _db().get_master_resume(user_id)
        processed = master.get("processed_data") if master else None
        fallback = await _load_user_fallback(user_id)
        profile = build_profile_from_resume(processed, user_fallback=fallback)
        completeness = compute_completeness(profile)
        data = profile.model_dump(mode="json")

        created = await _db().create_profile(
            user_id, data=data, completeness=completeness
        )
        # Baseline snapshot (best-effort; never blocks first load).
        try:
            await capture_profile_snapshot(
                user_id, created["id"], data, "migration", label="Initial profile"
            )
        except Exception:  # pragma: no cover - snapshot is best-effort
            logger.exception("Baseline profile snapshot failed for %s", user_id)
        await _emit(
            "profile.created", user_id, {"profile_id": created["id"], "source": "migration"}
        )
        await self._overlay_live_avatar(created)
        return created

    @staticmethod
    async def _overlay_live_avatar(row: dict[str, Any]) -> None:
        """Overlay the live account avatar onto ``identity.avatarUrl`` (read-time).

        The profile picture's single source of truth is the hardened account
        master (``users.avatar_url``); the profile document's ``identity.avatarUrl``
        is a derived mirror. We resolve it live on every read so uploading /
        replacing / removing the photo in the account is reflected everywhere the
        profile is consumed (workspace display, generated resumes, public page)
        with no drift. Best-effort: a lookup failure leaves the stored value.
        """
        data = row.get("data")
        if not isinstance(data, dict):
            return
        identity = data.get("identity")
        if not isinstance(identity, dict):
            return
        try:
            from app.auth import accounts

            rec = await accounts.get_by_id(row.get("user_id"))
        except Exception:  # pragma: no cover - defensive; display still works
            return
        identity["avatarUrl"] = rec.avatar_url if rec else None

    # -- Write (CAS) --------------------------------------------------------

    async def update(
        self,
        user_id: str,
        *,
        data: ProfileData,
        base_version: int,
        source: str = "manual",
        label: str | None = None,
    ) -> tuple[str, dict[str, Any] | None]:
        """Apply a validated profile document with optimistic-concurrency CAS.

        Returns ``(status, profile_dict)`` where status is ``updated`` /
        ``conflict`` / ``not_found`` (router maps to 200/409/404). On success a
        snapshot is captured and ``profile.updated`` (and ``profile.completed``
        when it crosses 100%) is emitted.
        """
        # Ensure the profile exists so a first-write-after-load path is coherent.
        current = await _db().get_profile(user_id)
        if current is None:
            # Lazily create, then require the client to retry against the fresh
            # version (avoids silently clobbering the just-derived baseline).
            await self.get_or_create(user_id)
            current = await _db().get_profile(user_id)
            if current is None:  # pragma: no cover - create is idempotent
                return "not_found", None

        payload = data.model_dump(mode="json")
        completeness = compute_completeness(data)

        status, row = await _db().update_profile_cas(
            user_id,
            data=payload,
            completeness=completeness,
            base_version=base_version,
        )
        if status != "updated" or row is None:
            return status, row

        try:
            await capture_profile_snapshot(
                user_id, row["id"], payload, source, label=label
            )
        except Exception:  # pragma: no cover - snapshot is best-effort
            logger.exception("Profile snapshot failed for %s", user_id)

        await _emit(
            "profile.updated",
            user_id,
            {"profile_id": row["id"], "completeness": completeness, "source": source},
        )
        was_complete = (current.get("completeness") or 0) >= 100
        if completeness >= 100 and not was_complete:
            await _emit("profile.completed", user_id, {"profile_id": row["id"]})
        return status, row

    # -- Completeness -------------------------------------------------------

    async def completeness(self, user_id: str) -> dict[str, Any]:
        """Return the weighted score + prioritized suggestions + readiness bands."""
        row = await self.get_or_create(user_id)
        profile = ProfileData.model_validate(row.get("data") or {})
        return {
            "score": compute_completeness(profile),
            "suggestions": build_suggestions(profile),
            "ats_readiness": compute_ats_readiness(profile),
            "ai_readiness": compute_ai_readiness(profile),
        }

    # -- Resume generation (Projection Engine) ------------------------------

    async def generate_resume(
        self,
        user_id: str,
        *,
        title: str | None = None,
        persist: bool = False,
        as_master: bool = False,
        include_photo: bool = False,
        photo: dict[str, Any] | None = None,
        template: str | None = None,
        sections: dict[str, bool] | None = None,
        template_settings: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Project the profile into resume data; optionally persist a new resume.

        Returns ``{"resume_data": {...}, "resume_id": str | None}``. Persistence
        creates a brand-new resume (never mutates an existing one) whose
        ``processed_data`` is the projection and whose ``meta`` records the
        source profile version for a future safe sync (ADR-10). ``template`` and
        ``sections`` tailor the projection without touching the profile.
        """
        import json

        row = await self.get_or_create(user_id)
        profile = ProfileData.model_validate(row.get("data") or {})
        version = int(row.get("version") or 1)
        options: dict[str, Any] = {"include_photo": include_photo}
        if photo is not None:
            options["photo"] = photo
        if template:
            options["template"] = template
        if sections:
            options["sections"] = sections
        resume_data = ProjectionEngine.project_resume(
            profile,
            options=options,
            profile_version=version,
        )

        resume_id: str | None = None
        if persist:
            content = json.dumps(resume_data, indent=2)
            resume_title = title or _compose_profile_resume_title(profile)
            if as_master:
                created = await _db().create_resume_atomic_master(
                    user_id,
                    content=content,
                    content_type="json",
                    processed_data=resume_data,
                    processing_status="ready",
                    title=resume_title,
                    template_settings=template_settings,
                )
            else:
                created = await _db().create_resume(
                    user_id,
                    content=content,
                    content_type="json",
                    processed_data=resume_data,
                    processing_status="ready",
                    title=resume_title,
                    template_settings=template_settings,
                )
            resume_id = created.get("resume_id")
            await _emit(
                "profile.resume_generated",
                user_id,
                {"profile_id": row["id"], "resume_id": resume_id},
            )

        return {"resume_data": resume_data, "resume_id": resume_id}

    # -- Import / Merge (P3) ------------------------------------------------

    async def preview_import(
        self, user_id: str, source: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Derive a candidate from ``source`` and plan its merge into the profile."""
        from app.profile.import_adapters import ImportError_, derive_candidate
        from app.profile.merge import build_merge_plan

        # Resume imports resolve the resume + identity fallbacks server-side.
        if source == "resume":
            resume_id = payload.get("resume_id")
            if not resume_id:
                raise ProfileServiceError("invalid", "resume_id is required for a resume import.")
            resume = await _db().get_resume(user_id, resume_id)
            if resume is None:
                raise ProfileServiceError("not_found", "Resume not found.")
            payload = {
                "processed_data": resume.get("processed_data"),
                "user_fallback": await _load_user_fallback(user_id),
            }

        try:
            incoming = derive_candidate(source, payload)
        except ImportError_ as exc:
            raise ProfileServiceError(exc.code, exc.message)

        row = await self.get_or_create(user_id)
        existing = ProfileData.model_validate(row.get("data") or {})
        plan = build_merge_plan(existing, incoming)
        statistics = _import_statistics(incoming, plan)
        warnings: list[str] = []
        if not plan.operations:
            warnings.append("This source adds nothing new to your profile.")
        if statistics["quality_score"] < 30:
            warnings.append("The imported document looks sparse — parsing may have missed content.")
        return {
            "source": source,
            "incoming": incoming,
            "plan": plan,
            "statistics": statistics,
            "warnings": warnings,
        }

    async def apply_import(
        self,
        user_id: str,
        *,
        incoming: ProfileData,
        resolutions: dict[str, str],
        base_version: int,
        source: str = "import",
    ) -> tuple[str, dict[str, Any] | None, int, int]:
        """Apply a reviewed merge plan under version CAS.

        Returns ``(status, profile_dict, applied, skipped)``.
        """
        from app.profile.merge import apply_merge_plan

        row = await self.get_or_create(user_id)
        existing = ProfileData.model_validate(row.get("data") or {})
        merged, applied, skipped = apply_merge_plan(
            existing, incoming, resolutions, source=source
        )
        status, updated = await self.update(
            user_id,
            data=merged,
            base_version=base_version,
            source=source,
            label="Imported resume" if source == "import" else "Merged profile",
        )
        if status == "updated":
            await _emit(
                "profile.imported" if source == "import" else "merge.completed",
                user_id,
                {"applied": applied, "skipped": skipped, "source": source},
            )
        return status, updated, applied, skipped

    # -- Synchronization (P4) -----------------------------------------------

    async def preview_sync(
        self, user_id: str, resume_id: str, *, include_photo: bool = False
    ) -> dict[str, Any] | None:
        """Preview refreshing a resume from the current profile (field-level diff)."""
        from app.profile.sync import preview_sync

        row = await self.get_or_create(user_id)
        return await preview_sync(user_id, resume_id, row, include_photo=include_photo)

    async def apply_sync(
        self,
        user_id: str,
        resume_id: str,
        *,
        base_version: int,
        include_photo: bool = False,
    ) -> tuple[str, dict[str, Any] | None]:
        """Apply the profile projection to a draft resume (submitted → refused)."""
        from app.profile.sync import apply_sync

        row = await self.get_or_create(user_id)
        status, updated = await apply_sync(
            user_id, resume_id, row, base_version=base_version, include_photo=include_photo
        )
        if status == "updated" and updated is not None:
            await _emit(
                "resume.synced",
                user_id,
                {"resume_id": resume_id, "profile_id": row["id"]},
            )
        return status, updated

    # -- AI layer (P5) ------------------------------------------------------

    async def update_ai_memory(
        self, user_id: str, ai_memory, base_version: int
    ) -> tuple[str, dict[str, Any] | None]:
        """Update only the AI-memory namespace (kept separate from resume data)."""
        row = await self.get_or_create(user_id)
        data = ProfileData.model_validate(row.get("data") or {})
        data.aiMemory = ai_memory
        return await self.update(
            user_id, data=data, base_version=base_version, source="ai", label="AI memory"
        )

    async def suggest(
        self, user_id: str, kind: str, *, experience_uid: str | None = None
    ) -> dict[str, Any]:
        """Produce an AI suggestion for a field (never auto-applied, never invents)."""
        from app.profile import ai

        row = await self.get_or_create(user_id)
        profile = ProfileData.model_validate(row.get("data") or {})
        if kind == "summary":
            result = await ai.suggest_summary(profile, user_id=user_id)
        elif kind == "experience_bullets":
            if not experience_uid:
                raise ProfileServiceError("invalid", "experience_uid is required.")
            result = await ai.suggest_experience_bullets(
                profile, experience_uid, user_id=user_id
            )
        elif kind == "skills_normalize":
            r = ai.normalize_skills(profile)
            result = {
                "kind": "skills_normalize",
                "suggestion": r["skills"],
                "note": None if r["changed"] else "Skills are already normalized.",
            }
        elif kind == "skills_gap":
            result = ai.skills_gap(profile)
        elif kind == "keywords":
            result = ai.suggest_keywords(profile)
        else:
            raise ProfileServiceError("invalid", f"Unknown suggestion kind: {kind!r}")
        await _emit("profile.ai_used", user_id, {"kind": kind})
        return result

    def skill_suggestions(self, query: str) -> list[dict[str, str]]:
        """Autocomplete canonical skills for the editor (pure, no LLM)."""
        from app.profile.skills import suggest_skills

        return suggest_skills(query)

    async def search(self, user_id: str, query: str, *, limit: int = 20) -> dict[str, Any]:
        """Ranked, highlighted search across the user's own profile document."""
        from app.profile.search import search_profile

        row = await self.get_or_create(user_id)
        profile = ProfileData.model_validate(row.get("data") or {})
        results = search_profile(profile, query, limit=limit)
        if query.strip():
            await _emit("profile.searched", user_id, {"q_len": len(query)})
        return {"query": query, "results": results}

    async def skills_gap(self, user_id: str) -> dict[str, Any]:
        """Deterministic gap analysis: skills implied by target roles vs. held."""
        from app.profile import ai

        row = await self.get_or_create(user_id)
        profile = ProfileData.model_validate(row.get("data") or {})
        return ai.skills_gap(profile)

    async def analytics(self, user_id: str) -> dict[str, Any]:
        """Return the per-user analytics snapshot (+ refresh completeness gauge)."""
        from app.profile.analytics import get_analytics_service

        row = await self.get_or_create(user_id)
        svc = get_analytics_service()
        await svc.set_gauge(user_id, "completeness", int(row.get("completeness") or 0))
        return await svc.snapshot(user_id)

    # -- Public projection platform (P6) ------------------------------------

    async def public_profile(self, user_id: str) -> dict[str, Any]:
        """Safe, public-facing projection (honors visibility; no private fields)."""
        from app.profile.public import project_public_profile

        row = await self.get_or_create(user_id)
        profile = ProfileData.model_validate(row.get("data") or {})
        await _emit("profile.exported", user_id, {"format": "public"})
        return project_public_profile(profile)

    async def portfolio(self, user_id: str) -> dict[str, Any]:
        """Portfolio-oriented projection (projects-first)."""
        from app.profile.public import project_portfolio

        row = await self.get_or_create(user_id)
        profile = ProfileData.model_validate(row.get("data") or {})
        await _emit("profile.exported", user_id, {"format": "portfolio"})
        return project_portfolio(profile)

    async def export_json_resume(self, user_id: str) -> dict[str, Any]:
        """Export the profile in the JSON Resume schema (round-trips with import)."""
        from app.profile.public import export_json_resume

        row = await self.get_or_create(user_id)
        profile = ProfileData.model_validate(row.get("data") or {})
        await _emit("profile.exported", user_id, {"format": "json_resume"})
        return export_json_resume(profile)

    # -- Public sharing (P7) ------------------------------------------------

    _VISIBILITIES = ("private", "unlisted", "public")

    async def _unique_slug(self, user_id: str, desired: str) -> str:
        """Return a globally-unique slug from ``desired`` (suffix on collision)."""
        from app.profile.public import slugify

        base = slugify(desired)
        candidate = base
        suffix = 2
        # Bounded loop; the DB unique index is the final backstop.
        while await _db().slug_exists(candidate, exclude_user_id=user_id):
            candidate = f"{base}-{suffix}"
            suffix += 1
            if suffix > 1000:  # pragma: no cover - pathological
                from uuid import uuid4

                candidate = f"{base}-{uuid4().hex[:6]}"
                break
        return candidate

    _THEMES = ("minimal", "modern", "developer")

    async def publish(
        self,
        user_id: str,
        *,
        visibility: str = "public",
        slug: str | None = None,
        theme: str | None = None,
    ) -> dict[str, Any]:
        """Publish the profile at a unique slug with the given visibility + theme.

        ``visibility`` ∈ {public, unlisted}. Reuses the existing slug when the
        caller doesn't supply one (stable share URLs across re-publishes).
        """
        if visibility not in ("public", "unlisted"):
            raise ProfileServiceError("invalid", "visibility must be public or unlisted.")
        if theme is not None and theme not in self._THEMES:
            raise ProfileServiceError("invalid", f"theme must be one of {self._THEMES}.")
        row = await self.get_or_create(user_id)
        profile = ProfileData.model_validate(row.get("data") or {})

        desired = slug or row.get("public_slug") or (
            profile.identity.name or profile.identity.headline or "profile"
        )
        unique = await self._unique_slug(user_id, desired)

        try:
            updated = await _db().set_profile_publication(
                user_id, public_slug=unique, visibility=visibility, public_theme=theme
            )
        except Exception:
            # Lost the uniqueness race — retry once with a fresh suffix.
            from uuid import uuid4

            updated = await _db().set_profile_publication(
                user_id,
                public_slug=f"{unique}-{uuid4().hex[:6]}",
                visibility=visibility,
                public_theme=theme,
            )
        if updated is None:
            raise ProfileServiceError("not_found", "Profile not found.")
        await _emit("public.shared", user_id, {"slug": updated["public_slug"], "visibility": visibility})
        if theme:
            await _emit("profile.theme_changed", user_id, {"theme": theme})
        return updated

    async def unpublish(self, user_id: str) -> dict[str, Any]:
        """Make the profile private again (slug reserved for a stable re-publish)."""
        row = await self.get_or_create(user_id)
        updated = await _db().set_profile_publication(
            user_id, public_slug=row.get("public_slug"), visibility="private"
        )
        if updated is None:
            raise ProfileServiceError("not_found", "Profile not found.")
        return updated

    async def publication_state(self, user_id: str) -> dict[str, Any]:
        """Return the current publish state (slug + visibility + theme) for the owner."""
        row = await self.get_or_create(user_id)
        return {
            "public_slug": row.get("public_slug"),
            "visibility": row.get("visibility") or "private",
            "public_theme": row.get("public_theme") or "minimal",
        }

    async def get_public_by_slug(self, slug: str) -> dict[str, Any] | None:
        """Anonymous public projection by slug, gated by visibility.

        Returns ``None`` when the slug is unclaimed OR the profile is ``private``
        (indistinguishable to avoid slug-enumeration disclosure). ``unlisted`` and
        ``public`` both resolve (unlisted is link-only / noindex — enforced by the
        page's robots metadata, not here).
        """
        from app.profile.public import project_public_profile, public_json_ld

        row = await _db().get_profile_by_slug(slug)
        if row is None or (row.get("visibility") or "private") == "private":
            return None
        profile = ProfileData.model_validate(row.get("data") or {})
        public = project_public_profile(profile, slug=slug)
        public["visibility"] = row.get("visibility")
        await self._enrich_public_avatar(public, row.get("user_id"))
        return {
            "profile": public,
            "json_ld": public_json_ld(public),
            "indexable": row.get("visibility") == "public",
            "theme": row.get("public_theme") or "minimal",
        }

    async def get_public_portfolio_by_slug(self, slug: str) -> dict[str, Any] | None:
        """Anonymous portfolio projection by slug (same visibility gate)."""
        from app.profile.public import project_portfolio

        row = await _db().get_profile_by_slug(slug)
        if row is None or (row.get("visibility") or "private") == "private":
            return None
        profile = ProfileData.model_validate(row.get("data") or {})
        portfolio = project_portfolio(profile)
        portfolio["slug"] = slug
        portfolio["visibility"] = row.get("visibility")
        await self._enrich_public_avatar(portfolio, row.get("user_id"))
        return portfolio

    @staticmethod
    async def _enrich_public_avatar(payload: dict[str, Any], user_id: str | None) -> None:
        """Resolve the public avatar from the LIVE account master + attach metadata.

        The public URL, responsive ``avatarSrcset`` and metadata (dims + dominant
        colour) are ALL taken from the live ``users`` row so they always describe
        the same, current image (no stale URL, no URL/metadata mismatch, no
        broken image after the photo is changed/removed). If the account has no
        avatar, the avatar fields are cleared so the page renders initials.
        """
        identity = payload.get("identity")
        if not isinstance(identity, dict) or not user_id:
            return
        try:
            from app.auth import accounts

            rec = await accounts.get_by_id(user_id)
        except Exception:  # pragma: no cover - non-critical
            return

        live_url = rec.avatar_url if rec else None
        identity["avatarUrl"] = live_url
        if not live_url:
            # Photo removed → clear derived fields so the view falls back to initials.
            identity["avatarSrcset"] = []
            identity["avatarWidth"] = None
            identity["avatarHeight"] = None
            identity["avatarDominantColor"] = None
            return
        from app.profile.public import _avatar_srcset

        identity["avatarSrcset"] = _avatar_srcset(live_url)
        identity["avatarWidth"] = rec.avatar_width
        identity["avatarHeight"] = rec.avatar_height
        identity["avatarDominantColor"] = rec.avatar_dominant_color

    async def get_public_vcard_by_slug(self, slug: str) -> str | None:
        """Anonymous vCard by slug (same visibility gate as the public page)."""
        from app.profile.public import build_vcard

        row = await _db().get_profile_by_slug(slug)
        if row is None or (row.get("visibility") or "private") == "private":
            return None
        profile = ProfileData.model_validate(row.get("data") or {})
        return build_vcard(profile)


# Module-level singleton (mirrors other service singletons).
profile_service = ProfileService()

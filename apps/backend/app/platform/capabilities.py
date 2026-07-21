"""Capability model (ARCHITECTURE §5).

A **capability** is a validated statement that "this deployment provides X".
Capabilities are the contract between a *profile* (declared intent) and the
*composition root* (what actually gets wired).

This module:
- enumerates the capabilities the system reasons about,
- **detects** which are actually present from settings,
- declares which each **profile requires**, and
- **validates** the two, returning precise errors (fail-fast, ARCHITECTURE §5).

Design note (no duplication - IMPLEMENTATION_PLAN §8): the existing
``Settings._validate_auth_surface`` remains the authoritative *boot* gate for
hosted secrets/Postgres. This module adds the **explicit profile->capability
contract** and a structured, testable report used by diagnostics and the
composition root. Where they overlap (e.g. hosted needs Postgres) the messages
are complementary, not contradictory.

Pure module: no I/O, no framework.
"""

from __future__ import annotations

import enum

from app.platform.profiles import (
    DeploymentProfile,
    explicit_profile_error,
    is_consistent_with_mode,
    resolve_profile,
)

# Redis-style KVStore URLs indicate a shared cache (cluster-wide state).
_SHARED_KV_SCHEMES = ("redis://", "rediss://")


class Capability(str, enum.Enum):
    """What a deployment can provide (ARCHITECTURE §5 categories)."""

    # Deployment
    MULTI_USER = "multi_user"
    EXTERNAL_SCHEDULER = "external_scheduler"
    # Infrastructure
    PERSISTENT_POSTGRES = "persistent_postgres"
    SHARED_CACHE = "shared_cache"
    OBJECT_STORAGE = "object_storage"
    OUTBOUND_EMAIL = "outbound_email"
    # Policy
    OAUTH_LOGIN = "oauth_login"
    EMAIL_VERIFICATION = "email_verification"
    # Security
    RATE_LIMITING_SHARED = "rate_limiting_shared"


def _has_shared_cache(settings) -> bool:
    kv = (getattr(settings, "kvstore_url", "") or "").strip().lower()
    return kv.startswith(_SHARED_KV_SCHEMES)


def _has_object_storage(settings) -> bool:
    provider = (getattr(settings, "storage_provider", "") or "").strip().lower()
    if provider == "cloudinary":
        return bool(getattr(settings, "cloudinary_configured", False))
    # ``s3`` is reserved/not implemented; ``local`` is not durable object storage.
    return False


def _has_outbound_email(settings) -> bool:
    provider = (getattr(settings, "email_provider", "") or "").strip().lower()
    if provider == "smtp":
        return bool(
            (getattr(settings, "email_smtp_host", "") or "").strip()
            and (getattr(settings, "email_from", "") or "").strip()
        )
    if provider in ("resend", "sendgrid", "ses", "mailgun"):
        return bool((getattr(settings, "email_api_key", "") or "").strip())
    return False


def detect_capabilities(settings) -> set[Capability]:
    """Return the set of capabilities the current settings actually provide."""
    caps: set[Capability] = set()

    if not settings.single_user_mode:
        caps.add(Capability.MULTI_USER)

    url = (settings.effective_database_url or "").strip().lower()
    if url.startswith("postgresql") or url.startswith("postgres://"):
        caps.add(Capability.PERSISTENT_POSTGRES)

    if _has_shared_cache(settings):
        caps.add(Capability.SHARED_CACHE)
        caps.add(Capability.RATE_LIMITING_SHARED)

    if _has_object_storage(settings):
        caps.add(Capability.OBJECT_STORAGE)

    if _has_outbound_email(settings):
        caps.add(Capability.OUTBOUND_EMAIL)

    if getattr(settings, "google_oauth_configured", False):
        caps.add(Capability.OAUTH_LOGIN)

    if getattr(settings, "email_verification_enabled", False):
        caps.add(Capability.EMAIL_VERIFICATION)

    scheduler = (getattr(settings, "scheduler_mode", "") or "").strip().lower()
    if scheduler == "external_cron" and (getattr(settings, "internal_job_token", "") or "").strip():
        caps.add(Capability.EXTERNAL_SCHEDULER)

    return caps


def required_capabilities(profile: DeploymentProfile) -> set[Capability]:
    """The capabilities a profile *must* have to boot (ARCHITECTURE §4).

    Kept intentionally minimal - only what would make the profile
    non-functional if absent. Optional features (email, OAuth) are NOT required;
    they degrade gracefully. This mirrors, and does not contradict, the existing
    ``Settings`` validator (hosted requires Postgres).
    """
    if profile.is_multi_user:
        # Multi-user profiles need a durable, concurrent datastore and a real
        # user boundary. Shared cache is only required at horizontal scale,
        # which is not expressible from settings alone, so it is validated by
        # the existing ``Settings`` scheduler/KV rules, not duplicated here.
        return {Capability.MULTI_USER, Capability.PERSISTENT_POSTGRES}
    # Local/single-user profiles require nothing external (zero-config).
    return set()


def profile_consistency_error(settings) -> str | None:
    """Return an error if an explicit profile contradicts ``single_user_mode``.

    This is the one gap the existing ``Settings`` validator does NOT cover, so
    it is the check the startup gate enforces (see :func:`startup_validation`).
    """
    profile = resolve_profile(settings)
    if not is_consistent_with_mode(profile, settings.single_user_mode):
        return (
            f"DEPLOYMENT_PROFILE={profile.value!r} contradicts "
            f"SINGLE_USER_MODE={settings.single_user_mode!r}: a "
            f"{'multi-user' if profile.is_multi_user else 'single-user'} profile "
            f"requires SINGLE_USER_MODE={'false' if profile.is_multi_user else 'true'}."
        )
    return None


def validate_deployment(settings) -> list[str]:
    """Full profile validation: contradiction + missing required capabilities.

    Returns a list of human-readable error strings (empty => valid). Used by the
    capability **report** and unit tests. Complements ``Settings`` validation; it
    does not replace it. NOTE: the missing-capability branch is, for correctly
    *constructed* settings, already guaranteed by ``Settings._validate_auth_surface``
    (hosted requires Postgres + is multi-user), so at real boot it never fires -
    which is why the startup gate uses the narrower :func:`startup_validation`.
    """
    errors: list[str] = []
    invalid = explicit_profile_error(settings)
    if invalid:
        errors.append(invalid)
    consistency = profile_consistency_error(settings)
    if consistency:
        errors.append(consistency)

    profile = resolve_profile(settings)
    present = detect_capabilities(settings)
    for capability in sorted(required_capabilities(profile), key=lambda c: c.value):
        if capability not in present:
            errors.append(
                f"Profile {profile.value!r} requires capability "
                f"{capability.value!r}, which is not provided by the current "
                f"configuration."
            )
    return errors


def startup_validation(settings) -> list[str]:
    """The fail-fast checks the startup gate enforces (main.py lifespan).

    Deliberately narrow: it raises only on a profile<->mode **contradiction**,
    because required-capability gating for hosted (Postgres, multi-user, secrets)
    is already owned by ``Settings._validate_auth_surface`` at construction.
    Re-enforcing it here would duplicate that gate and would misfire in tests
    that patch settings post-construction. Optional capabilities degrade
    gracefully and are surfaced via :func:`capability_report`, not raised.
    """
    errors: list[str] = []
    invalid = explicit_profile_error(settings)
    if invalid:
        errors.append(invalid)
    contradiction = profile_consistency_error(settings)
    if contradiction:
        errors.append(contradiction)
    return errors


def capability_report(settings) -> dict:
    """Secret-free structured capability report for diagnostics (ARCHITECTURE §I.2)."""
    profile = resolve_profile(settings)
    present = detect_capabilities(settings)
    required = required_capabilities(profile)
    return {
        "profile": profile.value,
        "multi_user": profile.is_multi_user,
        "present": sorted(c.value for c in present),
        "required": sorted(c.value for c in required),
        "missing": sorted(c.value for c in (required - present)),
        "valid": not validate_deployment(settings),
    }

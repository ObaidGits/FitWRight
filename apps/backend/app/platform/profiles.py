"""Deployment profiles (ARCHITECTURE §3, §4).

A **profile is declared intent**, not inferred topology. It maps to a
required-capability contract (see ``capabilities``) that boot validation
enforces fail-fast. This replaces ``single_user_mode`` as the *behavioral
deployment axis*; ``single_user_mode`` survives only as a backward-compatible
derived view during the migration (IMPLEMENTATION_PLAN Phase 1).

Resolution precedence (explicit beats implicit):
1. An explicit ``DEPLOYMENT_PROFILE`` setting, if present and valid.
2. Otherwise derived from the legacy ``single_user_mode`` boolean
   (``True`` -> ``desktop``; ``False`` -> ``saas``) so existing ``.env`` files
   keep working unchanged.

Pure module: no I/O, no framework. Safe to import anywhere in ``platform``/
``config``; forbidden in the domain (``app.services``).
"""

from __future__ import annotations

import enum


class DeploymentProfile(str, enum.Enum):
    """The declared shape of a deployment (ARCHITECTURE §4).

    ``development``/``test``/``ci`` are thin presets of ``desktop``/``saas`` -
    they exist for clarity, not divergent architecture (ARCHITECTURE §22).
    """

    DESKTOP = "desktop"
    SAAS = "saas"
    ENTERPRISE = "enterprise"
    SELF_HOSTED = "self_hosted"
    DEVELOPMENT = "development"
    TEST = "test"
    CI = "ci"

    @property
    def is_multi_user(self) -> bool:
        """Whether this profile serves more than the bootstrap owner."""
        return self in {
            DeploymentProfile.SAAS,
            DeploymentProfile.ENTERPRISE,
            DeploymentProfile.SELF_HOSTED,
        }

    @property
    def is_local(self) -> bool:
        """Whether this profile is a local/single-user shape (owner auto-login)."""
        return self in {
            DeploymentProfile.DESKTOP,
            DeploymentProfile.DEVELOPMENT,
            DeploymentProfile.TEST,
            DeploymentProfile.CI,
        }


def _coerce(value: str | None) -> DeploymentProfile | None:
    """Parse a raw profile string to the enum, or ``None`` if absent/invalid."""
    if not value:
        return None
    normalized = value.strip().lower().replace("-", "_")
    for profile in DeploymentProfile:
        if profile.value == normalized:
            return profile
    return None


def resolve_profile(settings) -> DeploymentProfile:
    """Resolve the active :class:`DeploymentProfile` from settings.

    ``settings`` is the app ``Settings`` instance. An explicit
    ``deployment_profile`` wins; otherwise the legacy ``single_user_mode``
    boolean is mapped (``True`` -> desktop, ``False`` -> saas) so this is a
    zero-behavior-change addition for existing deployments.
    """
    explicit = _coerce(getattr(settings, "deployment_profile", "") or None)
    if explicit is not None:
        return explicit
    return DeploymentProfile.DESKTOP if settings.single_user_mode else DeploymentProfile.SAAS


def explicit_profile_error(settings) -> str | None:
    """Return an error if ``deployment_profile`` is set to an unknown value.

    A non-blank ``DEPLOYMENT_PROFILE`` that does not name a real profile must
    fail fast (Golden Rule 6: never silently degrade) rather than quietly
    falling back to the derived profile.
    """
    raw = (getattr(settings, "deployment_profile", "") or "").strip()
    if raw and _coerce(raw) is None:
        valid = ", ".join(p.value for p in DeploymentProfile)
        return f"DEPLOYMENT_PROFILE={raw!r} is not a valid profile (expected one of: {valid})."
    return None


def is_consistent_with_mode(profile: DeploymentProfile, single_user_mode: bool) -> bool:
    """Guard against an explicit profile contradicting the legacy boolean.

    A ``desktop``/``development``/``test``/``ci`` profile must correspond to
    single-user mode; a ``saas``/``enterprise``/``self_hosted`` profile must
    correspond to multi-user. Used by validation to catch a misconfigured
    ``DEPLOYMENT_PROFILE=saas`` alongside ``SINGLE_USER_MODE=true``.
    """
    return profile.is_multi_user == (not single_user_mode)

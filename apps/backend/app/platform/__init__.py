"""Platform module (ARCHITECTURE §9) — composition, deployment profiles, and
capability validation.

This package owns the *wiring* concerns of the system, not business logic:

- ``profiles`` — the explicit ``DeploymentProfile`` (ARCHITECTURE §3/§4) that
  replaces ``single_user_mode`` as the *deployment axis*. ``single_user_mode``
  remains a backward-compatible derived view during the migration
  (IMPLEMENTATION_PLAN Phase 1).
- ``capabilities`` — the capability model (ARCHITECTURE §5): what a deployment
  *provides*, what a profile *requires*, and fail-fast validation of the two.

Nothing in ``app.services`` (the domain) may import this package; only the
composition root, config, diagnostics, and startup wiring may.
"""

from app.platform.capabilities import (
    Capability,
    capability_report,
    detect_capabilities,
    profile_consistency_error,
    required_capabilities,
    startup_validation,
    validate_deployment,
)
from app.platform.composition import Container, get_container, reset_container
from app.platform.profiles import DeploymentProfile, resolve_profile

__all__ = [
    "DeploymentProfile",
    "resolve_profile",
    "Container",
    "get_container",
    "reset_container",
    "Capability",
    "detect_capabilities",
    "required_capabilities",
    "validate_deployment",
    "startup_validation",
    "profile_consistency_error",
    "capability_report",
]

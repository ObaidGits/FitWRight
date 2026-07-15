"""Architecture fitness function: deployment-mode containment
(ARCHITECTURE §18 rule 5; IMPLEMENTATION_PLAN Phase 2).

The deployment axis (``single_user_mode`` / ``deployment_profile`` /
``resolved_profile``) must only be *read* in the composition/config/validation
seam — never scattered as a behavioral branch across the codebase.

This test encodes the **baseline allow-list**: the exact set of modules that
reference the deployment axis today. It blocks *new* references (the ratchet):
adding a mode read to any other module fails CI. As later phases move identity
behind a port (Phase 5) and centralize wiring (Phase 3), entries are *removed*
from this allow-list — it only ever shrinks.
"""

from __future__ import annotations

from pathlib import Path

import app as app_pkg

APP_DIR = Path(app_pkg.__file__).parent

# Tokens that indicate a read of the deployment axis.
MODE_TOKENS = ("single_user_mode", "deployment_profile", "resolved_profile")

# Baseline allow-list (paths relative to app/). Each entry is legitimate today;
# the migration shrinks this set. Do NOT add entries to "make CI pass" — that is
# the anti-pattern this guard exists to prevent (IMPLEMENTATION_PLAN §4.1).
ALLOW_LIST = {
    # config + validation (owns the setting)
    "config.py",
    # platform seam (owns profile/capability resolution + composition)
    "platform/__init__.py",
    "platform/profiles.py",
    "platform/capabilities.py",
    "platform/composition.py",
    # startup wiring
    "main.py",
    # diagnostics report (secret-free provider report)
    "diagnostics.py",
    # TLS default depends on hosted vs local (db_engine)
    "db_engine.py",
    # auth middleware — still reads the mode for the per-session CSRF gate
    # (the identity owner-fallback fork was moved to the IdentityProvider in
    # Phase 5; the CSRF-gate read is the remaining legitimate use here).
    "auth/principal.py",
    # NOTE (Phase 5): routers/health.py was removed from this list — its
    # owner-resolve now goes through the composition root's IdentityProvider.
}


def _iter_app_files():
    for path in APP_DIR.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        yield path


def test_deployment_mode_reads_are_contained():
    offenders: list[str] = []
    for path in _iter_app_files():
        rel = path.relative_to(APP_DIR).as_posix()
        if rel in ALLOW_LIST:
            continue
        text = path.read_text(encoding="utf-8")
        if any(token in text for token in MODE_TOKENS):
            hits = sorted({t for t in MODE_TOKENS if t in text})
            offenders.append(f"{rel}: references {hits} (not in allow-list)")
    assert not offenders, (
        "New deployment-mode reads outside the allowed seam (ARCHITECTURE §18.5).\n"
        "Route the decision through the composition root / identity port instead "
        "of reading the mode directly:\n" + "\n".join(sorted(offenders))
    )


def test_allow_list_has_no_stale_entries():
    """Every allow-list entry must still exist and still reference the axis.

    Keeps the ratchet honest: once a phase removes the last mode read from a
    file, its allow-list entry must be deleted (this test flags the leftover).
    """
    stale: list[str] = []
    for rel in ALLOW_LIST:
        path = APP_DIR / rel
        if not path.exists():
            stale.append(f"{rel}: allow-listed file no longer exists")
            continue
        text = path.read_text(encoding="utf-8")
        if not any(token in text for token in MODE_TOKENS):
            stale.append(f"{rel}: allow-listed but no longer references the deployment axis")
    assert not stale, "Stale allow-list entries (remove them):\n" + "\n".join(sorted(stale))

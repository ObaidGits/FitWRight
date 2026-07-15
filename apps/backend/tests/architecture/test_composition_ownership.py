"""Architecture fitness function: single construction site (Phase 3 exit).

Infrastructure adapters must be *constructed* only by the composition root
(``app/platform``), never scattered across call sites. The pure ``build_*``
functions may be *defined* in their owning modules, but they must only be
*called* from ``platform`` (today the container passes them by reference to a
single build-and-cache helper, so there are zero direct call sites at all).

This guards against a future regression where a router/service calls
``build_kvstore(...)`` or ``get_kvstore()``-style construction directly instead
of receiving the adapter from the composition root.
"""

from __future__ import annotations

import re
from pathlib import Path

import app as app_pkg

APP_DIR = Path(app_pkg.__file__).parent

BUILDERS = (
    "build_kvstore",
    "build_email_sender",
    "build_captcha_verifier",
    "build_breached_password_check",
    "build_storage_provider",
)

# A call site looks like ``build_x(`` but is not the ``def build_x(`` definition.
_CALL = re.compile(r"\b(" + "|".join(BUILDERS) + r")\s*\(")


def _iter_app_files():
    for path in APP_DIR.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        yield path


def test_adapters_are_only_constructed_in_platform():
    offenders: list[str] = []
    for path in _iter_app_files():
        rel = path.relative_to(APP_DIR).as_posix()
        if rel.startswith("platform/"):
            continue  # the composition root is the allowed construction site
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("def "):
                continue  # the builder *definition* is allowed in its owner module
            if _CALL.search(line):
                offenders.append(f"{rel}:{i}: constructs an adapter outside platform/ → {stripped}")
    assert not offenders, (
        "Adapter construction escaped the composition root (Phase 3 exit criterion).\n"
        "Receive the adapter from the composition Container instead:\n" + "\n".join(offenders)
    )

"""Architecture fitness function: module ownership + mutation rights
(ARCHITECTURE §9, Amendment E; IMPLEMENTATION_PLAN Phase 7).

A module owns its tables, and its ``repo`` (the sole writer of those tables) is
private to the module. No *other* module may import a foreign ``repo`` — cross
-module access must go through the owning module's service/use-case layer. This
makes "the owning module is the only writer" structurally enforced.

A module's own HTTP surface (``app/routers/<module>.py``) is permitted to use
its module's repo, since the router is that module's presentation layer.
"""

from __future__ import annotations

import re
from pathlib import Path

import app as app_pkg

APP_DIR = Path(app_pkg.__file__).parent

# Matches ``from app.<pkg>.repo import ...`` and ``import app.<pkg>.repo``.
_REPO_IMPORT = re.compile(r"\bapp\.(?P<pkg>\w+)\.repo\b")


def _iter_app_files():
    for path in APP_DIR.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        yield path


def _is_allowed(importer_rel: str, pkg: str) -> bool:
    # Allowed: files inside the owning module, or the module's own router.
    return importer_rel.startswith(f"{pkg}/") or importer_rel == f"routers/{pkg}.py"


def test_no_cross_module_repo_access():
    offenders: list[str] = []
    for path in _iter_app_files():
        rel = path.relative_to(APP_DIR).as_posix()
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not (stripped.startswith("from ") or stripped.startswith("import ")):
                continue
            for match in _REPO_IMPORT.finditer(stripped):
                pkg = match.group("pkg")
                if not _is_allowed(rel, pkg):
                    offenders.append(
                        f"{rel}: imports foreign module repo 'app.{pkg}.repo' "
                        f"(go through app.{pkg}'s service/use-case instead)"
                    )
    assert not offenders, (
        "Cross-module repo access violates mutation rights (ARCHITECTURE Amendment E):\n"
        + "\n".join(sorted(offenders))
    )

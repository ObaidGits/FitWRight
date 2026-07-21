"""Architecture fitness function: domain purity (ARCHITECTURE §10, §14, §18).

The domain (``app/services`` - the AI pipeline + business logic) must not import
infrastructure or framework. This test scans real import statements and fails if
any forbidden dependency appears, making the dependency rule mechanical rather
than a matter of discipline (IMPLEMENTATION_PLAN Phase 2/6).

Enforcement is intentionally started at the ``services`` package (verified pure
today) and widened in later phases.
"""

from __future__ import annotations

import ast
from pathlib import Path

import app as app_pkg

APP_DIR = Path(app_pkg.__file__).parent
DOMAIN_DIRS = ["services"]

# Modules the domain must never depend on (ARCHITECTURE §14 "MUST NEVER know").
FORBIDDEN_ROOTS = {
    "sqlalchemy",
    "fastapi",
    "redis",
    "cloudinary",
    "httpx",
    "starlette",
}
# App infrastructure the domain must not reach into directly.
FORBIDDEN_APP_MODULES = {
    "app.database",
    "app.db_engine",
    "app.platform",  # deployment profile / composition - domain must not know it
    "app.repository",
}


def _iter_domain_files():
    for d in DOMAIN_DIRS:
        yield from (APP_DIR / d).rglob("*.py")


def _imported_modules(path: Path) -> set[str]:
    """Return the set of fully-qualified module names imported by a file."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:  # ignore relative imports
                modules.add(node.module)
    return modules


def test_domain_has_no_forbidden_imports():
    violations: list[str] = []
    for path in _iter_domain_files():
        rel = path.relative_to(APP_DIR)
        for module in _imported_modules(path):
            root = module.split(".")[0]
            if root in FORBIDDEN_ROOTS:
                violations.append(f"{rel}: imports forbidden framework/infra '{module}'")
            if any(module == m or module.startswith(m + ".") for m in FORBIDDEN_APP_MODULES):
                violations.append(f"{rel}: imports forbidden app infra '{module}'")
    assert not violations, "Domain purity violations:\n" + "\n".join(sorted(violations))

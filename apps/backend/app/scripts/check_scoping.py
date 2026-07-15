"""CI guard: forbid unscoped owned-table queries (ADR-4, R10.2/10.6/10.8).

This is the enforcement counterpart to :class:`app.repository.Repo`. It statically
analyzes the backend source with the ``ast`` module and fails if either rule is
violated:

**Rule 1 — owned queries live only in the repository layer.** Any ORM query
against an owned table (``select(Resume)``, ``session.get(Job, …)``,
``delete(ApiKey)``, ``update(Application)``, ``select(func.count()).select_from(
Improvement)``) is allowed only inside ``app/database.py`` (the ``Repo`` layer)
and a small set of documented system files. A router or service that builds an
owned query directly is a scoping-bypass risk and is rejected.

**Rule 2 — every repository method that queries an owned table is scoped.**
Inside ``app/database.py``, any function that builds an owned-table query must
reference ``user_id`` (it composes the scope through ``Repo.scoped`` /
``Repo.owns`` / a ``user_id`` filter). A function touching an owned table without
``user_id`` in scope is an unscoped query and is rejected.

System files that legitimately run unscoped (schema definitions, the ``Repo``
composer itself, the bootstrap owner backfill, one-time importers, and Alembic
migrations) are allow-listed — they operate before/around multi-user and are not
user-facing request paths.

Run standalone: ``uv run python -m app.scripts.check_scoping`` (exit code 1 on
any violation). The unit test ``tests/unit/test_scoping_guard.py`` runs the same
check so a regression fails the suite.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

# Owned tables carry an ownership scope (must match app.repository.Repo.OWNED_TABLES).
OWNED_MODELS: frozenset[str] = frozenset(
    {
        "Resume",
        "Job",
        "Improvement",
        "Application",
        "ApiKey",
        "ResumeVersion",
        "Notification",
        "NotificationPref",
        "UserUnreadCount",
        "SearchDocument",
        "Reminder",
        "Interview",
    }
)

# The single repository layer where scoped owned queries are allowed to live.
REPO_FILE = "database.py"

# Files/dirs allowed to contain owned-table access without the per-request scope:
# schema, the scope composer, owner bootstrap/backfill, importers, migrations.
_ALLOWLIST_SUFFIXES: tuple[str, ...] = (
    "app/models.py",
    "app/repository.py",
    "app/auth/owner.py",
    "app/scripts/check_scoping.py",
    # P3 notification data access is centralized + user-scoped in this repo
    # module (same trust model as app/admin/repo.py); the outbox consumer and
    # the NotificationService route every owned query through it.
    "app/notifications/repo.py",
    # P3 search-document access is centralized + user-scoped-in-SQL here (FTS/
    # tsvector raw queries + scoped upserts); same trust model as admin/repo.py.
    "app/search/repo.py",
    # P3 reminder/interview data access is centralized + user-scoped here
    # (CRUD + claim-based scheduler scans); same trust model as admin/repo.py.
    "app/scheduling/repo.py",
    # The isolated, heavily-reviewed cross-user READ path for admin (ADR admin
    # §Architecture). This is the *only* module allowed to query owned tables
    # without a user_id scope; it holds no write methods (the purge delete goes
    # through the user-scoped Database.purge_user_owned_data facade instead).
    "app/admin/repo.py",
)
_ALLOWLIST_DIR_PARTS: tuple[str, ...] = ("scripts", "alembic")


@dataclass(frozen=True)
class Violation:
    path: str
    line: int
    message: str


def _is_owned_query(node: ast.AST) -> bool:
    """True if ``node`` is an ORM query construct against an owned table."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func

    # select(<Owned>), delete(<Owned>), update(<Owned>)
    if isinstance(func, ast.Name) and func.id in ("select", "delete", "update"):
        for arg in node.args:
            if isinstance(arg, ast.Name) and arg.id in OWNED_MODELS:
                return True

    # X.select_from(<Owned>)  and  session.get(<Owned>, ...)
    if isinstance(func, ast.Attribute) and func.attr in ("select_from", "get"):
        if node.args and isinstance(node.args[0], ast.Name) and node.args[0].id in OWNED_MODELS:
            return True

    return False


class _OwnedQueryFinder(ast.NodeVisitor):
    """Collect line numbers of owned-table query constructs in a module."""

    def __init__(self) -> None:
        self.hits: list[int] = []

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        if _is_owned_query(node):
            self.hits.append(node.lineno)
        self.generic_visit(node)


def _function_references_user_id(func: ast.AST) -> bool:
    """Whether a function references the name ``user_id`` anywhere (param or body)."""
    for node in ast.walk(func):
        if isinstance(node, ast.Name) and node.id == "user_id":
            return True
        if isinstance(node, ast.arg) and node.arg == "user_id":
            return True
    return False


def _is_allowlisted(path: Path) -> bool:
    posix = path.as_posix()
    if any(posix.endswith(sfx) for sfx in _ALLOWLIST_SUFFIXES):
        return True
    return any(part in path.parts for part in _ALLOWLIST_DIR_PARTS)


def check_source(app_dir: Path) -> list[Violation]:
    """Return all scoping violations under ``app_dir`` (empty ⇒ clean)."""
    violations: list[Violation] = []
    for path in sorted(app_dir.rglob("*.py")):
        rel = path.relative_to(app_dir.parent)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

        if path.name == REPO_FILE and path.parent.name == "app":
            # Rule 2: every function that queries an owned table must be scoped.
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    finder = _OwnedQueryFinder()
                    for stmt in node.body:
                        finder.visit(stmt)
                    if finder.hits and not _function_references_user_id(node):
                        violations.append(
                            Violation(
                                str(rel),
                                node.lineno,
                                f"repository method '{node.name}' issues an owned-table "
                                "query without a user_id scope",
                            )
                        )
            continue

        if _is_allowlisted(path):
            continue

        # Rule 1: owned queries are not allowed outside the repository layer.
        finder = _OwnedQueryFinder()
        finder.visit(tree)
        for line in finder.hits:
            violations.append(
                Violation(
                    str(rel),
                    line,
                    "owned-table query issued outside the repository layer "
                    "(app/database.py) — route it through the db facade / Repo.scoped",
                )
            )
    return violations


def main() -> int:
    app_dir = Path(__file__).resolve().parents[1]  # .../app
    violations = check_source(app_dir)
    if not violations:
        print("scoping guard: OK — no unscoped owned-table queries found")
        return 0
    print("scoping guard: FAILED — unscoped owned-table query violations:")
    for v in violations:
        print(f"  {v.path}:{v.line}: {v.message}")
    return 1


if __name__ == "__main__":
    sys.exit(main())

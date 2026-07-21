"""The CI scoping guard must pass, and must actually catch unscoped queries.

This is the unit-test half of the owned-query guard (Task 3.1). It runs the same
static analysis the CI script does (``app.scripts.check_scoping``) against the
live source tree and fails if any owned-table query is issued outside the
repository layer or without a ``user_id`` scope. It also proves the guard is not
vacuous by feeding it a synthetic module that violates each rule.
"""

import textwrap
from pathlib import Path

from app.scripts.check_scoping import check_source


def _app_dir() -> Path:
    # tests/unit/ -> tests/ -> apps/backend/ ; app package lives beside tests.
    return Path(__file__).resolve().parents[2] / "app"


def test_live_source_has_no_unscoped_owned_queries():
    """The real backend passes the guard (no unscoped owned-table queries)."""
    violations = check_source(_app_dir())
    assert violations == [], "unscoped owned-table query violations:\n" + "\n".join(
        f"  {v.path}:{v.line}: {v.message}" for v in violations
    )


def test_guard_flags_owned_query_outside_repo(tmp_path):
    """Rule 1: an owned query in a non-repo module is rejected."""
    pkg = tmp_path / "app"
    pkg.mkdir()
    (pkg / "models.py").write_text("class Resume: ...\n")
    (pkg / "leaky_router.py").write_text(
        textwrap.dedent(
            """
            from sqlalchemy import select
            from app.models import Resume

            async def handler(session):
                return await session.execute(select(Resume))
            """
        )
    )
    violations = check_source(pkg)
    assert any("leaky_router.py" in v.path for v in violations)


def test_guard_flags_unscoped_repo_method(tmp_path):
    """Rule 2: a database.py method querying an owned table without user_id fails."""
    pkg = tmp_path / "app"
    pkg.mkdir()
    (pkg / "models.py").write_text("class Job: ...\n")
    (pkg / "database.py").write_text(
        textwrap.dedent(
            """
            from sqlalchemy import select
            from app.models import Job

            async def list_all(session):
                # No user_id in scope - this is exactly what the guard forbids.
                return await session.execute(select(Job))
            """
        )
    )
    violations = check_source(pkg)
    assert any("database.py" in v.path and "list_all" in v.message for v in violations)


def test_guard_accepts_scoped_repo_method(tmp_path):
    """A database.py method that references user_id is accepted."""
    pkg = tmp_path / "app"
    pkg.mkdir()
    (pkg / "models.py").write_text("class Job: ...\n")
    (pkg / "database.py").write_text(
        textwrap.dedent(
            """
            from sqlalchemy import select
            from app.models import Job
            from app.repository import Repo

            async def list_for(session, user_id):
                return await session.execute(Repo.scoped(select(Job), Job, user_id))
            """
        )
    )
    violations = check_source(pkg)
    assert violations == []

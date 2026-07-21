"""End-to-end Postgres/pooler verification harness (Goal A: portable Postgres hosting).

This is the runtime half of the Supabase-readiness proof. It complements the
Alembic chain (which owns the schema on Postgres) by exercising the *application*
data layer against a **real Postgres** reached through a **transaction-mode
connection pooler** - i.e. exactly the topology Supabase uses (PgBouncer on the
6543 pooled endpoint).

Why a pooler is the interesting case: under transaction pooling a client's
server connection can change between statements, so server-side prepared
statements are unsafe. ``app/db_engine.py`` already disables them when
``DB_USE_POOLER=true`` (``statement_cache_size=0`` +
``prepared_statement_cache_size=0`` for asyncpg; ``prepare_threshold=None`` for
psycopg, both behind ``NullPool``). If that config is wrong, the repeated-query
stress loop below fails with ``prepared statement "__asyncpg_stmt_..." already
exists``. A clean run proves the pooler-safe path works.

Prerequisites (the harness does NOT migrate - keep migration on the DIRECT
connection, mirroring the runbook):

    # 1) migrate schema on the DIRECT (non-pooled) connection
    ALEMBIC_DATABASE_URL="postgresql+asyncpg://user:pass@host:5432/db" \
        python -m alembic upgrade head

    # 2) run this harness against the POOLED endpoint
    DATABASE_URL="postgresql+asyncpg://user:pass@host:6543/db" \
    DB_USE_POOLER=true SINGLE_USER_MODE=true \
        python scripts/verify_postgres.py

Exit code 0 = all checks passed; non-zero = a check failed (message on stderr).
No rows are left behind on success (best-effort cleanup of the seeded owner's
resume/job/search rows); it is safe to re-run.
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid

# The app resolves the database from settings at import time, so DATABASE_URL /
# DB_USE_POOLER / SINGLE_USER_MODE must already be set in the environment before
# importing anything under ``app`` (that is the caller's responsibility above).


def _require_env() -> None:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        sys.exit("DATABASE_URL is required (point it at the POOLED Postgres endpoint).")
    if url.startswith("sqlite"):
        sys.exit("DATABASE_URL must be a Postgres URL - this harness verifies Postgres, not SQLite.")


class _Check:
    """Tiny pass/fail tracker with readable output."""

    def __init__(self) -> None:
        self.failures: list[str] = []
        self.count = 0

    def ok(self, label: str, condition: bool, detail: str = "") -> None:
        self.count += 1
        mark = "PASS" if condition else "FAIL"
        suffix = f" - {detail}" if detail else ""
        print(f"  [{mark}] {label}{suffix}", flush=True)
        if not condition:
            self.failures.append(label)


async def _run() -> int:
    from app import database
    from app.auth.owner import ensure_owner
    from app.config import settings
    from app.search.repo import SearchRepo

    db = database.db
    check = _Check()

    # Confirm we really are on Postgres via the pooled path (dialect is resolved
    # from the live engine, not the URL string).
    dialect = db.async_engine.dialect.name
    check.ok("engine dialect is postgresql", dialect == "postgresql", dialect)
    check.ok("DB_USE_POOLER enabled", settings.db_use_pooler is True, str(settings.db_use_pooler))

    # -- owner bootstrap (writes to users; exercises auth-owned tables) --------
    owner_id = await ensure_owner(db)
    check.ok("ensure_owner returns an id", bool(owner_id), owner_id)

    # -- resume CRUD through the facade (owned-table scoping + JSON column) -----
    marker = uuid.uuid4().hex[:8]
    title = f"Senior Backend Engineer {marker}"
    created = await db.create_resume_atomic_master(
        owner_id,
        content=f"# {title}\nPython, FastAPI, PostgreSQL, Docker. Marker {marker}.",
        title=title,
        processed_data={"skills": ["python", "fastapi", "postgres"], "marker": marker},
        processing_status="ready",
    )
    resume_id = created["resume_id"]
    check.ok("create_resume_atomic_master persisted", bool(resume_id), resume_id)

    fetched = await db.get_resume(owner_id, resume_id)
    check.ok("get_resume round-trips JSON column", bool(fetched) and fetched.get("processed_data", {}).get("marker") == marker)

    master = await db.get_master_resume(owner_id)
    check.ok("first resume becomes master", bool(master) and master["resume_id"] == resume_id)

    listed = await db.list_resumes(owner_id)
    check.ok("list_resumes returns the new resume", any(r["resume_id"] == resume_id for r in listed), f"{len(listed)} row(s)")

    # -- job row (a second owned table) ----------------------------------------
    job = await db.create_job(owner_id, content=f"JD looking for a backend engineer {marker}", resume_id=resume_id)
    job_id = job["job_id"]
    check.ok("create_job persisted", bool(job_id), job_id)

    # -- Postgres GIN full-text search path (0011: to_tsvector @@ plainto_tsquery)
    repo = SearchRepo()
    await repo.upsert(
        user_id=owner_id,
        node_type="resume",
        node_id=resume_id,
        title=title,
        body="Backend engineer specializing in Python and PostgreSQL",
        status=None,
    )
    hits = await repo.search(owner_id, "engineer", limit=10)
    check.ok("GIN FTS search returns the indexed doc", any(h["node_id"] == resume_id for h in hits), f"{len(hits)} hit(s)")

    # Cross-user isolation: a different user must not see the row (Repo.scoped).
    other = await repo.search("does-not-exist-user", "engineer", limit=10)
    check.ok("search is user-scoped (no cross-user leak)", all(h["node_id"] != resume_id for h in other), f"{len(other)} hit(s)")

    # -- prepared-statement stress under transaction pooling -------------------
    # Repeated identical queries on distinct pooled connections are the exact
    # scenario that breaks server-side prepared statements. A clean loop proves
    # the pooler-safe engine options are effective.
    try:
        for _ in range(25):
            await db.list_resumes(owner_id)
            await db.get_master_resume(owner_id)
        check.ok("25x repeated queries under pooler (no prepared-stmt error)", True)
    except Exception as exc:  # noqa: BLE001
        check.ok("25x repeated queries under pooler (no prepared-stmt error)", False, repr(exc))

    # -- best-effort cleanup so the harness is idempotent ----------------------
    try:
        await repo.remove("resume", resume_id)
        await db.delete_resume(owner_id, resume_id)
    except Exception as exc:  # noqa: BLE001
        print(f"  [warn] cleanup skipped: {exc!r}", flush=True)

    await db.close()

    print(flush=True)
    if check.failures:
        print(f"RESULT: FAIL ({len(check.failures)}/{check.count} checks failed): {', '.join(check.failures)}")
        return 1
    print(f"RESULT: PASS ({check.count}/{check.count} checks passed)")
    return 0


def main() -> None:
    _require_env()
    raise SystemExit(asyncio.run(_run()))


if __name__ == "__main__":
    main()

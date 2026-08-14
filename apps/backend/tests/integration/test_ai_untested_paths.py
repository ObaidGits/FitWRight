"""Execute the paths that were written but never run.

Both bugs these cover were found by a LINTER, not by 3,532 passing tests, because
neither path was ever executed:

* ``accounts_sharing_ip_hash`` referenced a model that was not imported, so the abuse
  review endpoint would have raised NameError on first use.
* ``run_admin_jobs`` referenced the credit maintenance job without importing it, so
  every scheduler tick would have failed after the change that added it.

A test that imports a module proves only that it parses. These call the functions.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest


async def _seed_login(db, *, user_id: str, ip_hash: str) -> None:
    """Insert an audit row directly.

    Deliberately NOT via ``get_audit_service()``: that service is a process-wide
    singleton that binds to whichever database existed when it was first built, so in a
    suite of per-test isolated databases its writes can land in a previous test's
    database. A test that depends on it passes or fails according to execution order,
    which is how one of these tests behaved before this helper existed.
    """
    from uuid import uuid4

    from app.models import AuditLog

    async with db.session_factory() as session:
        session.add(
            AuditLog(
                id=str(uuid4()),
                event="login",
                actor_user_id=user_id,
                ip_hash=ip_hash,
                ts=datetime.now(timezone.utc).isoformat(),
            )
        )
        await session.commit()


@pytest.mark.asyncio
class TestAbuseSignalQueries:
    async def test_shared_ip_query_executes(self, isolated_db):
        """The NameError case: written, reviewed, never called."""
        from app.database import db

        result = await db.accounts_sharing_ip_hash(days=7, min_accounts=3)
        assert isinstance(result, list)

    async def test_groups_accounts_seen_from_one_network(self, isolated_db):
        from app.database import db

        for uid in ("u-a", "u-b", "u-c"):
            await _seed_login(db, user_id=uid, ip_hash="same-hash")

        groups = await db.accounts_sharing_ip_hash(days=7, min_accounts=3)
        assert any(len(g["user_ids"]) >= 3 for g in groups)

    async def test_a_lone_account_is_not_grouped(self, isolated_db):
        """min_accounts exists so an ordinary user is never on this list."""
        from app.database import db

        await _seed_login(db, user_id="u-solo", ip_hash="unique-hash")
        groups = await db.accounts_sharing_ip_hash(days=7, min_accounts=3)
        assert all("u-solo" not in g["user_ids"] for g in groups)

    async def test_rapid_spend_query_executes(self, isolated_db):
        from app.database import db

        assert isinstance(
            await db.accounts_spending_allowance_immediately(days=7), list
        )

    async def test_the_review_list_always_states_the_innocent_explanation(
        self, isolated_db
    ):
        """An operator about to restrict someone should be reminded that the boring
        explanation is usually the true one."""
        from app.ai_abuse_signals import abuse_review_candidates
        from app.database import db

        for uid in ("u-1", "u-2", "u-3"):
            await _seed_login(db, user_id=uid, ip_hash="office")

        candidates = await abuse_review_candidates(days=7)
        assert candidates
        for c in candidates:
            assert c["innocent_explanation"]
            assert c["strength"] in ("weak", "moderate", "strong")


@pytest.mark.asyncio
class TestJobRunnerExecutes:
    async def test_run_admin_jobs_actually_runs(self, isolated_db):
        """The second NameError case. Importing the module was not enough - the
        undefined name only bites when the function is called."""
        from app.admin.jobs import run_admin_jobs

        result = await run_admin_jobs()
        assert "credit_maintenance" in result

    async def test_the_maintenance_job_sweeps_and_reports(self, isolated_db, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "ai_credits_enabled", True)
        from app.ai_allowance import ensure_allowance, run_credit_maintenance_job
        from app.database import db

        await ensure_allowance("u-sweep")
        await db.reserve_credits(
            "u-sweep",
            feature="resume_tailor",
            credits=3,
            idempotency_key="expired-key",
            ttl_seconds=0,
        )

        result = await run_credit_maintenance_job()
        assert result["swept"] >= 1
        assert "reconciliation" in result

        account = await db.get_or_create_credit_account("u-sweep")
        assert account["reserved_credits"] == 0

    async def test_the_job_survives_a_reconciliation_problem(
        self, isolated_db, monkeypatch
    ):
        """A maintenance run must report problems, not die on them - otherwise the
        sweep stops working precisely when something is already wrong."""
        from app.ai_allowance import run_credit_maintenance_job
        from app.config import settings
        from app.database import db
        from app.models import CreditAccount

        monkeypatch.setattr(settings, "ai_credits_enabled", True)
        await db.get_or_create_credit_account("u-neg")
        async with db.session_factory() as session:
            row = await session.get(CreditAccount, "u-neg")
            row.wallet_credits = -10
            row.allowance_period_start = datetime.now(timezone.utc).isoformat()
            await session.commit()

        result = await run_credit_maintenance_job()
        assert result["reconciliation"]["status"] == "attention"

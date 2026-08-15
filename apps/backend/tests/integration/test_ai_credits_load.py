"""The reserve/settle path under concurrency (task 6.4).

Not a benchmark - a CORRECTNESS test at load. The interesting failures in this design
only appear when requests overlap, and they are all silent:

* Two requests both passing a balance check and both spending it (overdraft).
* A hold that is neither settled nor released, permanently freezing part of a balance.
* Lifetime totals drifting away from the balance they are supposed to explain.

Sized to run in CI rather than to find a throughput number. The concurrency is high
enough to interleave transactions on SQLite and Postgres alike; going higher would make
the suite slow without testing a different property.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.models import CreditAccount


@pytest.fixture
def credits_on(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "ai_credits_enabled", True)
    return settings


async def _fund(db, user_id: str, credits: int):
    await db.get_or_create_credit_account(user_id)
    async with db.session_factory() as session:
        row = await session.get(CreditAccount, user_id)
        row.allowance_credits = credits
        row.allowance_period_start = datetime.now(timezone.utc).isoformat()
        await session.commit()


@pytest.mark.asyncio
class TestReserveSettleUnderLoad:
    async def test_concurrent_spends_never_exceed_the_balance(
        self, isolated_db, credits_on
    ):
        """The invariant the whole design exists to protect.

        Forty simultaneous attempts against a balance that affords eight. Exactly eight
        may win. An earlier read-then-write version of the reserve let five times the
        balance through, which is why this is a load test and not a unit test.
        """
        from app.database import db

        uid = f"u-{uuid4().hex[:8]}"
        await _fund(db, uid, 40)  # 8 reservations of 5

        async def attempt(n: int):
            status, _ = await db.reserve_credits(
                uid,
                feature="resume_tailor",
                credits=5,
                idempotency_key=f"load-{n}",
                ttl_seconds=300,
            )
            return status

        results = await asyncio.gather(*(attempt(i) for i in range(40)))

        created = [r for r in results if r == "created"]
        assert len(created) == 8, f"expected 8 winners, got {len(created)}"

        account = await db.get_or_create_credit_account(uid)
        assert account["reserved_credits"] == 40
        assert account["available_credits"] == 0
        assert account["available_credits"] >= 0, "balance went negative under load"

    async def test_every_hold_is_resolved_under_load(self, isolated_db, credits_on):
        """A hold that is neither settled nor released freezes credits forever, and the
        user sees a balance they cannot spend with no error to explain it."""
        from app.database import db

        uid = f"u-{uuid4().hex[:8]}"
        await _fund(db, uid, 200)

        async def cycle(n: int):
            status, reservation = await db.reserve_credits(
                uid,
                feature="cover_letter",
                credits=2,
                idempotency_key=f"cycle-{n}",
                ttl_seconds=300,
            )
            if status != "created" or not reservation:
                return
            # Half settle, half fail and release - the real mix.
            if n % 2 == 0:
                await db.settle_reservation(
                    reservation["id"],
                    actual_credits=1,
                    ledger={"outcome": "ok", "total_tokens": 100},
                )
            else:
                await db.release_reservation(reservation["id"])

        await asyncio.gather(*(cycle(i) for i in range(30)))

        account = await db.get_or_create_credit_account(uid)
        assert account["reserved_credits"] == 0, "holds leaked under load"

    async def test_lifetime_spent_matches_what_was_settled(self, isolated_db, credits_on):
        """Lifetime totals are what an operator reconciles against. If they drift from
        the balance under load, every report built on them is wrong."""
        from app.database import db

        uid = f"u-{uuid4().hex[:8]}"
        await _fund(db, uid, 100)

        async def spend(n: int):
            status, reservation = await db.reserve_credits(
                uid,
                feature="outreach",
                credits=2,
                idempotency_key=f"spend-{n}",
                ttl_seconds=300,
            )
            if status == "created" and reservation:
                await db.settle_reservation(
                    reservation["id"],
                    actual_credits=2,
                    ledger={"outcome": "ok", "total_tokens": 50},
                )
                return 2
            return 0

        charged = sum(await asyncio.gather(*(spend(i) for i in range(20))))

        account = await db.get_or_create_credit_account(uid)
        assert account["lifetime_spent"] == charged
        assert account["available_credits"] == 100 - charged

    async def test_the_same_idempotency_key_wins_once_under_load(
        self, isolated_db, credits_on
    ):
        """A retried request must not take a second hold. Clients retry; that is normal,
        and a duplicate hold would quietly halve the user's balance."""
        from app.database import db

        uid = f"u-{uuid4().hex[:8]}"
        await _fund(db, uid, 100)

        results = await asyncio.gather(
            *(
                db.reserve_credits(
                    uid,
                    feature="resume_tailor",
                    credits=5,
                    idempotency_key="one-key-only",
                    ttl_seconds=300,
                )
                for _ in range(10)
            )
        )

        statuses = [r[0] for r in results]
        assert statuses.count("created") == 1
        account = await db.get_or_create_credit_account(uid)
        assert account["reserved_credits"] == 5, "a retry took a second hold"

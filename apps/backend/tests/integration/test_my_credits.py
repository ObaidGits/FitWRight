"""The user-facing balance surface.

These assert the PRODUCT contract, not just the plumbing: a user is told what they
can still do, is never shown a limit that does not apply to them, and is never left
in a dead end when they run out.
"""

from __future__ import annotations

from datetime import datetime, timezone

from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.models import CreditAccount


@pytest.fixture
async def me() -> str:
    """A user id unique to this test.

    Deliberately NOT the bootstrap owner: that id is cached on the Database
    instance, which is a process-level singleton, so leaning on it couples tests to
    each other's ordering. This file hit exactly that and two tests failed only when
    run alongside the others.
    """
    return f"u-{uuid4().hex[:12]}"


@pytest.fixture
async def owner_client(isolated_db, me):
    """A client authenticated as `me`.

    Overrides the auth dependency rather than driving a real login, because these
    tests are about the credits contract, not the session machinery. The override is
    always removed so it cannot leak into another test on the shared app.
    """
    from app.auth.principal import get_effective_user_id
    from app.main import app

    app.dependency_overrides[get_effective_user_id] = lambda: me
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="https://test"
        ) as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_effective_user_id, None)


async def _fund(db, user_id: str, *, allowance: int = 0, wallet: int = 0):
    await db.get_or_create_credit_account(user_id)
    async with db.session_factory() as session:
        row = await session.get(CreditAccount, user_id)
        row.allowance_credits = allowance
        row.wallet_credits = wallet
        # Stamp the CURRENT period so the lazy allowance grant treats this account as
        # already topped up for the month. Without it, `allowance=0` would be read as
        # "never granted" and refilled - and a test that means "this user is out of
        # credits" would silently become "this user has 50".
        row.allowance_period_start = datetime.now(timezone.utc).isoformat()
        await session.commit()


@pytest.fixture
def credits_on(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "ai_credits_enabled", True)
    monkeypatch.setattr("app.routers.credits.user_has_own_key", lambda _uid: False)
    return settings


@pytest.mark.asyncio
class TestMyCredits:
    async def test_speaks_in_actions_not_just_credits(
        self, isolated_db, owner_client, me, credits_on
    ):
        """A raw credit count is not information a user can act on."""
        from app.database import db

        await _fund(db, me, allowance=200)

        res = await owner_client.get("/api/v1/credits")
        assert res.status_code == 200
        body = res.json()

        assert body["mode"] == "credits"
        assert body["summary"], "no human-readable summary"
        labels = {a["feature"] for a in body["actions"]}
        assert "resume_tailor" in labels
        assert any(a["remaining"] > 0 for a in body["actions"])

    async def test_never_promises_more_than_the_spend_guard_allows(
        self, isolated_db, owner_client, me, credits_on
    ):
        """The count shown must come from the same price the charge uses.

        If the screen promised 5 and the guard allowed 3, the product would be lying
        to the user twice: once optimistically, then once as a refusal.
        """
        from app.database import db

        await db.upsert_feature_price(
            "resume_tailor",
            label="Tailored resume",
            credits=20,
            is_charged=True,
            active=True,
        )
        from app.ai_feature_prices import invalidate_price_cache

        invalidate_price_cache()

        await _fund(db, me, allowance=200)
        res = await owner_client.get("/api/v1/credits")
        body = res.json()

        action = next(a for a in body["actions"] if a["feature"] == "resume_tailor")
        # The response now carries the price alongside the count, so the two cannot be
        # derived from different numbers.
        assert action["credits_each"] == 20
        assert action["remaining"] == body["available_credits"] // 20

    async def test_running_out_still_names_the_free_alternative(
        self, isolated_db, owner_client, me, credits_on
    ):
        """Out of credits is a fork in the road, not a wall. Their own provider key
        works forever and costs the operator nothing."""
        from app.database import db

        await _fund(db, me, allowance=0)

        body = (await owner_client.get("/api/v1/credits")).json()
        assert body["low"] is True
        assert body["own_key_is_free"] is True

    async def test_a_user_on_their_own_key_is_not_shown_a_limit(
        self, isolated_db, owner_client, me, monkeypatch
    ):
        """They are not spending the operator's money, so "0 credits" would be both
        alarming and false."""
        from app.config import settings

        monkeypatch.setattr(settings, "ai_credits_enabled", True)
        monkeypatch.setattr("app.routers.credits.user_has_own_key", lambda _uid: True)

        body = (await owner_client.get("/api/v1/credits")).json()
        assert body["mode"] == "own_key"
        assert body["unlimited"] is True

    async def test_shows_no_limit_while_the_feature_ships_dark(
        self, isolated_db, owner_client, me, monkeypatch
    ):
        """Inventing a balance before charging starts would train users to worry
        about a limit that does not exist."""
        from app.config import settings

        monkeypatch.setattr(settings, "ai_credits_enabled", False)
        monkeypatch.setattr("app.routers.credits.user_has_own_key", lambda _uid: False)

        body = (await owner_client.get("/api/v1/credits")).json()
        assert body["unlimited"] is True
        assert body["mode"] == "unlimited"

    async def test_reading_the_balance_never_consumes_it(
        self, isolated_db, owner_client, me, credits_on
    ):
        """It is called on page load. A read that reserved would drain an idle user."""
        from app.database import db

        await _fund(db, me, allowance=200)
        for _ in range(3):
            assert (await owner_client.get("/api/v1/credits")).status_code == 200

        account = await db.get_or_create_credit_account(me)
        assert account["reserved_credits"] == 0
        assert account["available_credits"] == 200

    async def test_a_disabled_account_is_told_plainly(
        self, isolated_db, owner_client, me, credits_on
    ):
        """Distinct from running out: no amount of waiting or topping up fixes it, so
        it must not render as an empty balance."""
        from app.database import db

        await _fund(db, me, allowance=100)
        async with db.session_factory() as session:
            row = await session.get(CreditAccount, me)
            row.ai_disabled = True
            await session.commit()

        body = (await owner_client.get("/api/v1/credits")).json()
        assert body["mode"] == "disabled"


@pytest.mark.asyncio
class TestMyUsage:
    async def test_a_user_can_see_where_it_went(
        self, isolated_db, owner_client, me, credits_on
    ):
        """A balance that drops with no visible history is indistinguishable from a
        bug, and support tickets are the expensive way to find that out."""
        from app.database import db

        await db.get_or_create_credit_account(me)
        await db.record_usage_only(
            me, feature="resume_tailor", credits_charged=4, total_tokens=12000, outcome="ok"
        )

        body = (await owner_client.get("/api/v1/credits/usage")).json()
        assert body["items"]
        assert body["items"][0]["feature"] == "resume_tailor"

    async def test_only_their_own_history(
        self, isolated_db, owner_client, me, credits_on
    ):
        """Usage is per-user data; another account's activity must never appear."""
        from app.database import db

        other = f"u-{uuid4().hex[:8]}"
        await db.get_or_create_credit_account(other)
        await db.record_usage_only(
            other, feature="cover_letter", credits_charged=2, total_tokens=4000, outcome="ok"
        )

        body = (await owner_client.get("/api/v1/credits/usage")).json()
        assert all(i["feature"] != "cover_letter" for i in body["items"])

"""The metering dependency as endpoints actually experience it.

Built against a throwaway app rather than the real routers, so these assert the
CONTRACT (when is a request refused, when is it charged, what happens on failure)
without dragging in resume parsing or a provider. The architecture ratchet separately
proves the real endpoints carry this dependency.
"""

from __future__ import annotations

from datetime import datetime, timezone

from uuid import uuid4

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from app.ai_metered import ai_metered
from app.ai_usage_meter import note_call
from app.auth.principal import get_effective_user_id
from app.errors import ApiError, install_error_handlers
from app.models import CreditAccount


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
    # Nobody in these tests is on their own key; that path has its own tests.
    monkeypatch.setattr("app.ai_metered.user_has_own_key", lambda _uid: False)
    return settings


def _build_app(user_id: str, *, tokens: int = 5000, blow_up: bool = False, blocking: bool = True):
    """A minimal app with one metered endpoint that reports `tokens` of usage."""
    app = FastAPI()
    install_error_handlers(app)
    app.dependency_overrides[get_effective_user_id] = lambda: user_id

    state = {"handler_ran": False}

    @app.post("/gen", dependencies=[Depends(ai_metered("cover_letter", blocking=blocking))])
    async def gen():
        state["handler_ran"] = True
        note_call(total_tokens=tokens)
        if blow_up:
            raise ApiError(502, "provider_down", "The provider failed.")
        return {"ok": True}

    return app, state


async def _post(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post("/gen")


@pytest.mark.asyncio
class TestMeteredEndpoint:
    async def test_a_funded_user_is_charged_for_what_the_call_used(
        self, isolated_db, credits_on
    ):
        from app.database import db

        uid = f"u-{uuid4().hex[:8]}"
        await _fund(db, uid, allowance=500)

        app, state = _build_app(uid, tokens=5000)
        res = await _post(app)

        assert res.status_code == 200
        assert state["handler_ran"] is True
        account = await db.get_or_create_credit_account(uid)
        assert account["lifetime_spent"] > 0
        # The hold must be given back either way - a leaked hold is a balance the
        # user can see but never spend.
        assert account["reserved_credits"] == 0

    async def test_an_unaffordable_request_is_refused_before_the_handler_runs(
        self, isolated_db, credits_on
    ):
        """The ordering IS the feature. Refusing after the work is done means the
        operator has already paid the provider for a request it will not bill."""
        from app.database import db

        uid = f"u-{uuid4().hex[:8]}"
        await _fund(db, uid, allowance=0)

        app, state = _build_app(uid)
        res = await _post(app)

        assert res.status_code == 402
        assert state["handler_ran"] is False, "work happened despite an empty balance"

    async def test_the_refusal_offers_the_free_alternative(self, isolated_db, credits_on):
        """Out of credits is not a dead end: bringing your own key is free forever,
        and costs the operator nothing. A wall here would just lose the user."""
        from app.database import db

        uid = f"u-{uuid4().hex[:8]}"
        await _fund(db, uid, allowance=0)

        res = await _post(_build_app(uid)[0])
        body = res.json()
        assert res.status_code == 402
        assert "own provider key" in str(body).lower() or "own" in str(body).lower()

    async def test_a_provider_failure_does_not_charge_the_user(
        self, isolated_db, credits_on
    ):
        """Billing for our own outage is the fastest way to lose trust."""
        from app.database import db

        uid = f"u-{uuid4().hex[:8]}"
        await _fund(db, uid, allowance=500)
        before = (await db.get_or_create_credit_account(uid))["available_credits"]

        app, _ = _build_app(uid, blow_up=True)
        res = await _post(app)

        assert res.status_code == 502
        account = await db.get_or_create_credit_account(uid)
        assert account["available_credits"] == before, "charged for a provider failure"
        assert account["reserved_credits"] == 0, "hold leaked on the failure path"

    async def test_a_non_blocking_endpoint_completes_despite_an_empty_balance(
        self, isolated_db, credits_on
    ):
        """`/improve/confirm` is this case: the tailoring was already paid for at
        preview, and confirm is what SAVES it. Refusing there would delete work the
        user already bought - worse than an uncharged call."""
        from app.database import db

        uid = f"u-{uuid4().hex[:8]}"
        await _fund(db, uid, allowance=0)

        app, state = _build_app(uid, blocking=False)
        res = await _post(app)

        assert res.status_code == 200
        assert state["handler_ran"] is True

    async def test_a_non_blocking_endpoint_still_respects_an_operator_block(
        self, isolated_db, credits_on
    ):
        """Leniency about MONEY must not become leniency about ACCESS. A user the
        operator switched off stays off."""
        from app.database import db

        uid = f"u-{uuid4().hex[:8]}"
        await _fund(db, uid, allowance=500)
        async with db.session_factory() as session:
            row = await session.get(CreditAccount, uid)
            row.ai_disabled = True
            await session.commit()

        app, state = _build_app(uid, blocking=False)
        res = await _post(app)

        assert res.status_code == 403
        assert state["handler_ran"] is False

    async def test_usage_is_recorded_even_when_the_flag_is_off(self, isolated_db, monkeypatch):
        """Shipping dark still has to produce data - that is how the operator prices
        the thing before charging anyone."""
        from app.config import settings
        from app.database import db

        monkeypatch.setattr(settings, "ai_credits_enabled", False)
        monkeypatch.setattr("app.ai_metered.user_has_own_key", lambda _uid: False)

        uid = f"u-{uuid4().hex[:8]}"
        app, _ = _build_app(uid, tokens=3000)
        assert (await _post(app)).status_code == 200

        rows = await db.list_usage(uid, limit=10)
        assert rows, "no usage row written while metering with the flag off"
        assert rows[0]["credits_charged"] == 0

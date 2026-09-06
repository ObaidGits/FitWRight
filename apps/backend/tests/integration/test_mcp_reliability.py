"""MCP reliability suite: every real-deployment failure mode fails GRACEFULLY.

The data-integrity suite (``test_mcp_data_integrity.py``) attacks the write
paths under concurrency and partial failure. This suite attacks the
INFRASTRUCTURE around them: the database dying mid-session and at auth time,
the LLM provider dying / hanging / being abandoned by a disconnecting client,
the background search worker dying, settings being flipped at runtime, and the
timeout discipline of every slow path.

The spine of every test is the same as the integrity suite's: assert the
RESPONSE SHAPE (tool error, never a 500, never a traceback, never partial
data) AND the post-recovery behavior - a failure mode that poisons state so
the NEXT call also fails is a reliability bug even if the failure itself was
handled cleanly. Only externals are mocked (the LLM provider and the job-board
scrape, at the same seams the other suites use); FitWright logic always runs
for real, and failures are injected at the infrastructure boundary (the db
layer, the token service, the provider call).
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import re
import time
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app.models import DailyUsageCounter, Job, Reminder
from tests.integration.conftest import (
    MCP_ENDPOINT,
    mcp_ok as _ok,
    mcp_post as _mcp_post,
)

pytestmark = pytest.mark.integration

PASSWORD = "correct-horse-battery-staple-9"

JD_ACME = """Site Reliability Engineer - Acme Corp (Remote)
Keep Acme's platform boring: 12 clusters, 2M req/day, humane on-call.
Requirements: Kubernetes, Python, calm under pressure.
"""

MAX_LOG_TEXT = 200_000  # a multi-MB traceback in any one failure is a bug
MAX_RECORD_MESSAGE = 100_000


# ---------------------------------------------------------------------------
# Fixtures + harness
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_search_state():
    from app.job_discovery import search_jobs
    from app.routers import discovery

    search_jobs.reset_for_tests()
    discovery._search_timestamps.clear()
    yield
    search_jobs.reset_for_tests()
    discovery._search_timestamps.clear()


@pytest.fixture
def credits_on(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "ai_credits_enabled", True)
    monkeypatch.setattr("app.ai_metered.user_has_own_key", lambda _uid: False)
    return settings


@pytest.fixture
async def reliability(auth_env, mcp_app, mcp_token, isolated_db, owner_id, monkeypatch):
    """The whole app with the MCP mount on, over the isolated DB.

    Same shape as the integrity suite's ``integrity`` fixture: tests drive MCP
    through their own ``httpx.AsyncClient`` (ASGI transport, same event loop,
    real bearer auth) - the sync TestClient stays open just to own the lifespan.
    """
    from app.applications import submissions
    from app.config import settings as app_settings

    monkeypatch.setattr(submissions, "db", isolated_db)
    monkeypatch.setattr(app_settings, "JOB_DISCOVERY", True)
    app = mcp_app(True)
    with TestClient(app) as _lifespan_holder:
        yield SimpleNamespace(
            app=app,
            db=isolated_db,
            owner_id=owner_id,
            owner_token=mcp_token["raw"],
            factory=mcp_app,
        )


def _http(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="https://test")


@asynccontextmanager
async def _lifespan_on_this_loop(app):
    """Drive the app's ASGI lifespan on the CALLING loop - the production
    shape, where uvicorn runs the lifespan and every request on ONE loop.

    ``TestClient`` instead runs the lifespan on a portal thread, which changes
    how a cancelled request's cleanup interleaves with the (loop-bound) MCP
    session-manager task group. The disconnect test needs the real shape.
    """
    rx: asyncio.Queue = asyncio.Queue()  # messages TO the app
    tx: asyncio.Queue = asyncio.Queue()  # messages FROM the app
    task = asyncio.create_task(
        app(
            {
                "type": "lifespan",
                "asgi": {"version": "3.0", "spec_version": "2.0"},
                "http_version": "1.1",
                "scheme": "https",
                "state": {},  # uvicorn always provides shared lifespan state
            },
            rx.get,
            tx.put,
        )
    )
    try:
        await rx.put({"type": "lifespan.startup"})
        msg = await asyncio.wait_for(tx.get(), 10)
        assert msg["type"] == "lifespan.startup.complete", msg
        yield
    finally:
        await rx.put({"type": "lifespan.shutdown"})
        msg = await asyncio.wait_for(tx.get(), 15)
        assert msg["type"] == "lifespan.shutdown.complete", msg
        await asyncio.wait_for(task, 15)


@pytest.fixture
async def reliability_one_loop(
    auth_env, mcp_app, mcp_token, isolated_db, owner_id, monkeypatch
):
    """The MCP-mounted app with the lifespan driven on the TEST loop."""
    from app.applications import submissions
    from app.config import settings as app_settings

    monkeypatch.setattr(submissions, "db", isolated_db)
    monkeypatch.setattr(app_settings, "JOB_DISCOVERY", True)
    app = mcp_app(True)
    async with _lifespan_on_this_loop(app):
        yield SimpleNamespace(
            app=app,
            db=isolated_db,
            owner_id=owner_id,
            owner_token=mcp_token["raw"],
            factory=mcp_app,
        )


async def _acall(http: AsyncClient, token: str, name: str, arguments: dict, _id: int = 1) -> dict:
    resp = await http.post(
        MCP_ENDPOINT,
        json={
            "jsonrpc": "2.0",
            "id": _id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, f"transport failed: {resp.status_code} {resp.text[:500]}"
    return resp.json()


def _classify(result: dict) -> tuple[str, object]:
    """``("ok", payload)`` or ``("error", text)`` for one tools/call body."""
    assert result.get("error") is None, f"protocol-level error: {result}"  # nosec
    res = result["result"]
    if res.get("isError") is not True:
        payload = res.get("structuredContent")
        if payload is None:
            import json as _json

            payload = _json.loads(res["content"][0]["text"])
        return ("ok", payload)
    text = res["content"][0]["text"]
    assert "Traceback" not in text, text  # never leak internals to the AI client
    return ("error", text)


# ---------------------------------------------------------------------------
# Failure-injection machinery
# ---------------------------------------------------------------------------


def _operational_error() -> OperationalError:
    """A realistic SQLAlchemy 'the database went away' error."""
    return OperationalError(
        "SELECT resumes.id FROM resumes",
        {},
        Exception("server closed the connection unexpectedly"),
    )


class BlackoutDb:
    """Delegate proxy over the isolated DB that raises while ``active``.

    Coroutine-function attributes are wrapped so every awaited db call raises
    ``OperationalError`` for the duration of the outage; ``session_factory``
    fails too, so repo paths that open their own session (the scheduling repo)
    see the outage instead of silently bypassing it. Delegation is transparent
    once ``active`` is False - which is exactly how a healed connection should
    look to a call-time ``from app.database import db`` resolution.
    """

    def __init__(self, real):
        self._real = real
        self.active = False
        self.failed_calls = 0

    def session_factory(self):
        if self.active:
            self.failed_calls += 1
            raise _operational_error()
        return self._real.session_factory

    def __getattr__(self, name):
        attr = getattr(self._real, name)
        if inspect.iscoroutinefunction(attr):
            async def _outage(*args, **kwargs):
                if self.active:
                    self.failed_calls += 1
                    raise _operational_error()
                return await attr(*args, **kwargs)

            return _outage
        return attr


class GatedDb:
    """Delegate proxy that parks one method on an asyncio.Event.

    For the settings-flip test: an in-flight read that is still being served
    when the deployment turns the MCP surface off must not crash or corrupt
    anything - it just finishes on the app instance that accepted it.
    """

    def __init__(self, real, method: str):
        self._real = real
        self._method = method
        self.gate = asyncio.Event()

    def __getattr__(self, name):
        return getattr(self._real, name)

    async def list_applications(self, *a, **k):
        await self.gate.wait()
        return await self._real.list_applications(*a, **k)


# ---------------------------------------------------------------------------
# Seeding + direct-DB assertion helpers
# ---------------------------------------------------------------------------


async def _new_user(db, email: str) -> str:
    from app.auth.accounts import create_user
    from app.auth.passwords import get_password_service

    record = await create_user(
        email=email,
        name="Reliability User",
        password_hash=get_password_service().hash_password(PASSWORD),
        role="user",
        status="active",
        email_verified_at="2024-01-01T00:00:00+00:00",
        db=db,
    )
    return record.id


async def _mcp_token_for(user_id: str, label: str) -> str:
    from app.auth.mcp_tokens import get_mcp_token_service

    _, raw = await get_mcp_token_service().issue(user_id, label)
    return raw


async def _seed_resume(db, user_id: str) -> dict:
    return await db.create_resume(
        user_id,
        content="# Jane Doe\nSenior SRE with Kubernetes depth.",
        filename="resume.md",
        is_master=False,
        processing_status="ready",
    )


async def _seed_tailored_resume(db, user_id: str, **kwargs) -> dict:
    """A tailored resume with its job context, ready for AI generation."""
    master = await db.get_master_resume(user_id)
    if master is None:
        master = await db.create_resume(
            user_id, content="# Master", filename="master.md",
            is_master=True, processing_status="ready",
        )
    job = await db.create_job(user_id, content="Senior SRE: Kubernetes, Python, AWS.")
    defaults = dict(
        content="# Tailored",
        filename="tailored.md",
        parent_id=master["resume_id"],
        processed_data={"skills": ["kubernetes"], "summary": "SRE"},
        processing_status="ready",
    )
    defaults.update(kwargs)
    tailored = await db.create_resume(user_id, **defaults)
    await db.create_improvement(
        user_id,
        original_resume_id=master["resume_id"],
        tailored_resume_id=tailored["resume_id"],
        job_id=job["job_id"],
        improvements=[],
    )
    return tailored


def _future_iso(hours: int = 48) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


async def _job_rows(db, user_id: str) -> list:
    async with db.session_factory() as session:
        rows = (
            await session.execute(select(Job).where(Job.user_id == user_id))
        ).scalars().all()
        return [SimpleNamespace(job_id=r.job_id) for r in rows]


async def _reminder_rows(db, user_id: str) -> list[Reminder]:
    async with db.session_factory() as session:
        return list(
            (
                await session.execute(select(Reminder).where(Reminder.user_id == user_id))
            )
            .scalars()
            .all()
        )


async def _wallet(db, user_id: str) -> int:
    account = await db.get_or_create_credit_account(user_id)
    return account["wallet_credits"]


async def _reserved(db, user_id: str) -> int:
    account = await db.get_or_create_credit_account(user_id)
    return account["reserved_credits"]


async def _ledger(db, user_id: str) -> list[dict]:
    return await db.list_usage(user_id, limit=100)


async def _fund(db, user_id: str, wallet: int) -> None:
    await db.get_or_create_credit_account(user_id)
    async with db.session_factory() as session:
        from app.models import CreditAccount

        row = await session.get(CreditAccount, user_id)
        row.wallet_credits = wallet
        row.allowance_period_start = datetime.now(timezone.utc).isoformat()
        await session.commit()


async def _await_status(
    http: AsyncClient, token: str, search_id: str, *statuses: str, timeout: float = 5.0
) -> dict:
    """Poll get_job_search_status until it reports one of ``statuses``."""
    deadline = time.time() + timeout
    state = None
    while time.time() < deadline:
        result = await _acall(
            http, token, "get_job_search_status", {"search_id": search_id}
        )
        state = _ok(result)
        if state["status"] in statuses:
            return state
        await asyncio.sleep(0.05)
    pytest.fail(f"search never reached {statuses}: {state}")


def _assert_log_hygiene(caplog, *secrets: str) -> None:
    """No token material, no other-user data, no mega-tracebacks, no spam."""
    assert len(caplog.text) < MAX_LOG_TEXT, "log output exploded under failure"
    for secret in secrets:
        if secret:
            assert secret not in caplog.text, f"secret material leaked to logs: {secret!r}"
    for record in caplog.records:
        assert len(record.getMessage()) < MAX_RECORD_MESSAGE, (
            f"{record.name}: single log record is huge "
            f"({len(record.getMessage())} chars)"
        )


# ===========================================================================
# Injection 1 - DB outage mid-session
# ===========================================================================


class TestDbOutageMidSession:
    async def test_read_tools_fail_closed_no_partial_data(
        self, reliability, monkeypatch, caplog
    ):
        """The database dies mid-session. Every READ tool must return a tool
        error over HTTP 200 (fail-closed, never a 500, never a partial board),
        the outage must actually be exercised, and the logs must stay clean."""
        import app.database as database_module
        from app.applications import submissions

        db = reliability.db
        user = await _new_user(db, "outage-read@example.com")
        token = await _mcp_token_for(user, "claude-desktop")
        resume = await _seed_resume(db, user)
        job = await db.create_job(user, content=JD_ACME)
        card = await db.create_application(
            user, job_id=job["job_id"], resume_id=resume["resume_id"]
        )

        proxy = BlackoutDb(db)
        proxy.active = True
        monkeypatch.setattr(database_module, "db", proxy)
        # The queue/duplicate tools resolve `submissions.db` (bound by the
        # fixture straight to the isolated DB) - the outage must cover it too.
        monkeypatch.setattr(submissions, "db", proxy)

        reads = [
            ("list_applications", {}),
            ("get_application", {"application_id": card["application_id"]}),
            ("list_resumes", {}),
            ("get_resume", {"resume_id": resume["resume_id"]}),
            ("get_apply_queue", {}),
            ("check_duplicate", {"company": "Acme Corp", "role": "SRE"}),
            ("list_reminders", {"application_id": card["application_id"]}),
        ]
        async with _http(reliability.app) as http:
            for name, args in reads:
                result = await _acall(http, token, name, args)
                kind, text = _classify(result)
                assert kind == "error", f"{name} succeeded during a DB outage: {text}"
                assert "storage_unavailable" in text, (
                    f"{name}: outage must surface as the actionable "
                    f"storage_unavailable code, got: {text}"
                )
                assert "SQL:" not in text, f"{name}: internals leaked: {text}"

        assert proxy.failed_calls >= len(reads), "outage was never exercised"
        monkeypatch.setattr(database_module, "db", db)
        monkeypatch.setattr(submissions, "db", db)

        # State untouched by every failed read.
        applications = await db.list_applications(user)
        assert len(applications) == 1
        assert applications[0]["application_id"] == card["application_id"]
        _assert_log_hygiene(caplog, token)

    async def test_write_tools_leave_state_untouched_and_never_charge(
        self, reliability, monkeypatch, caplog
    ):
        """The database dies mid-session. Every WRITE tool (and the billed AI
        tool) must fail without leaving a row behind - and without charging
        the user a single credit for the operator's outage."""
        import app.database as database_module
        from app.applications import submissions
        from app.routers import resumes as resumes_router

        db = reliability.db
        user = await _new_user(db, "outage-write@example.com")
        token = await _mcp_token_for(user, "cursor")
        resume = await _seed_resume(db, user)
        job = await db.create_job(user, content=JD_ACME)
        card = await db.create_application(
            user, job_id=job["job_id"], resume_id=resume["resume_id"]
        )
        jobs_before = await _job_rows(db, user)
        reminders_before = await _reminder_rows(db, user)

        proxy = BlackoutDb(db)
        proxy.active = True
        monkeypatch.setattr(database_module, "db", proxy)
        monkeypatch.setattr(submissions, "db", proxy)
        # The billed AI tools call the REST endpoint in app/routers/resumes,
        # which binds ``db`` at import time - cover it too.
        monkeypatch.setattr(resumes_router, "db", proxy)

        writes = [
            (
                "add_application",
                {
                    "job_description": JD_ACME,
                    "resume_id": resume["resume_id"],
                    "company": "Acme Corp",
                    "role": "SRE",
                },
                "storage_unavailable",
            ),
            (
                "update_application_status",
                {"application_id": card["application_id"], "status": "interview"},
                "application_update_failed",
            ),
            (
                "create_reminder",
                {
                    "application_id": card["application_id"],
                    "remind_at": _future_iso(24),
                    "note": "must never exist",
                },
                "storage_unavailable",
            ),
            (
                "generate_cover_letter",
                {"resume_id": resume["resume_id"]},
                "storage_unavailable",
            ),
        ]
        async with _http(reliability.app) as http:
            for name, args, marker in writes:
                result = await _acall(http, token, name, args)
                kind, text = _classify(result)
                assert kind == "error", f"{name} succeeded during a DB outage: {text}"
                assert marker in text, f"{name}: incoherent error for the client: {text}"
                assert "Traceback" not in text
                assert "SQL:" not in text, f"{name}: internals leaked: {text}"

        monkeypatch.setattr(database_module, "db", db)
        monkeypatch.setattr(submissions, "db", db)
        monkeypatch.setattr(resumes_router, "db", db)

        # State untouched: no new cards, no orphan jobs, no phantom reminders,
        # the ledger never moved, and the refused status move did not mutate.
        assert len(await db.list_applications(user)) == 1
        assert await _job_rows(db, user) == jobs_before
        assert await _reminder_rows(db, user) == reminders_before
        detail = await db.get_application_detail(user, card["application_id"])
        assert detail["status"] == "applied", "a refused move mutated the row"
        assert await _ledger(db, user) == [], "billing moved during a DB outage"
        assert await _reserved(db, user) == 0, "hold leaked during a DB outage"
        _assert_log_hygiene(caplog, token)

    async def test_recovery_no_poisoned_state_no_stuck_binding(
        self, reliability, monkeypatch
    ):
        """After the outage heals, the very next call succeeds - proving the
        db-in-body resolution pattern re-resolves on every call. Healing the
        SAME proxy object (the module binding never changes) is the strongest
        form: the tool layer cannot have memoized the failure or the broken
        binding."""
        import app.database as database_module
        from app.applications import submissions

        db = reliability.db
        user = await _new_user(db, "outage-recover@example.com")
        token = await _mcp_token_for(user, "claude-desktop")
        resume = await _seed_resume(db, user)

        proxy = BlackoutDb(db)
        monkeypatch.setattr(database_module, "db", proxy)
        monkeypatch.setattr(submissions, "db", proxy)

        async with _http(reliability.app) as http:
            # Outage: every read fails closed.
            proxy.active = True
            result = await _acall(http, token, "list_applications", {})
            kind, _ = _classify(result)
            assert kind == "error"

            # Heal (same binding, same proxy object): the next call succeeds.
            proxy.active = False
            board = _ok(await _acall(http, token, "list_applications", {}))
            assert board["total"] == 0

            # And a write lands normally.
            added = _ok(
                await _acall(
                    http,
                    token,
                    "add_application",
                    {
                        "job_description": JD_ACME,
                        "resume_id": resume["resume_id"],
                        "company": "Acme Corp",
                        "role": "SRE",
                    },
                )
            )
            assert added["status"] == "applied"

        # Restore the pristine binding; still coherent.
        monkeypatch.setattr(database_module, "db", db)
        monkeypatch.setattr(submissions, "db", db)
        async with _http(reliability.app) as http:
            board = _ok(await _acall(http, token, "list_applications", {}))
            assert board["total"] == 1


# ===========================================================================
# Injection 2 - DB outage at auth time, mid-session
# ===========================================================================


class TestAuthOutageMidSession:
    async def test_verify_outage_after_successes_fails_closed_then_heals(
        self, auth_env, mcp_app, mcp_token, monkeypatch, caplog
    ):
        """test_mcp_auth pins a STATIC verification outage; this extends it to
        an outage that starts DURING a valid token's session, after successful
        verifications: 401 while the DB is down (fail closed, never a bypass,
        never a 500), and the healed service authenticates the SAME token
        again - no stuck state in the verifier."""
        from app.auth.mcp_tokens import get_mcp_token_service

        service = get_mcp_token_service()
        real_verify = service.verify
        state = {"outage": False, "calls": 0}

        async def flaky_verify(raw: str):
            state["calls"] += 1
            if state["outage"]:
                raise _operational_error()
            return await real_verify(raw)

        monkeypatch.setattr(service, "verify", flaky_verify)

        app = mcp_app(True)
        raw = mcp_token["raw"]
        with TestClient(app) as client:
            # A working session first...
            for _ in range(2):
                res = _mcp_post(
                    client, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, raw
                )
                assert res.status_code == 200
            assert state["calls"] >= 2

            # ...then the database dies mid-session.
            state["outage"] = True
            res = _mcp_post(
                client, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, raw
            )
            assert res.status_code == 401, "outage at verify time must fail CLOSED"
            assert state["calls"] >= 3

            # ...then it heals: the same token works again, immediately.
            state["outage"] = False
            res = _mcp_post(
                client, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, raw
            )
            assert res.status_code == 200

        assert "MCP token verification failed" in caplog.text  # exception fired
        _assert_log_hygiene(caplog, raw)


# ===========================================================================
# Injection 3 - LLM provider outage
# ===========================================================================


class TestLlmOutage:
    async def test_cover_letter_timeout_charges_nothing_and_recovers(
        self, reliability, credits_on, monkeypatch, caplog
    ):
        """The provider hangs and the call times out mid-generation. The tool
        error must name the failure (no traceback), the wallet must be intact
        with a fully released hold, exactly one zero-charge 'failed' ledger
        row must exist (the contract test_mcp_data_integrity #5 pinned), the
        rate-limit state must not poison the next call - and after the
        provider heals, generation bills exactly once."""
        from app.ai_feature_prices import resolve_feature_cost
        from app.routers import resumes as resumes_router

        db = reliability.db
        user = await _new_user(db, "llm-timeout@example.com")
        token = await _mcp_token_for(user, "claude-desktop")
        tailored = await _seed_tailored_resume(db, user)
        price = (await resolve_feature_cost(db, "cover_letter")).effective_credits
        wallet0 = price + 100
        await _fund(db, user, wallet0)

        state = {"fail": True}

        async def flaky_provider(*a, **k):
            if state["fail"]:
                raise TimeoutError("provider hung mid-generation")
            return "Dear Acme team, ... sincerely Jane."

        monkeypatch.setattr(resumes_router, "generate_cover_letter", flaky_provider)

        async with _http(reliability.app) as http:
            result = await _acall(
                http, token, "generate_cover_letter", {"resume_id": tailored["resume_id"]}
            )
        kind, text = _classify(result)
        assert kind == "error", text
        assert "llm_timeout" in text, text
        assert "did not respond in time" in text, text
        assert "rate_limited" not in text, "an outage is not a rate refusal"

        # No charge: wallet intact, hold fully released, no half-written letter,
        # exactly one provable zero-charge failed row (the pinned contract).
        assert await _wallet(db, user) == wallet0
        assert await _reserved(db, user) == 0, "hold leaked on provider timeout"
        stored = await db.get_resume(user, tailored["resume_id"])
        assert stored.get("cover_letter") is None, "half-written deliverable"
        assert [
            (r["feature"], r["credits_charged"], r["outcome"]) for r in await _ledger(db, user)
        ] == [("cover_letter", 0, "failed")]

        # Recovery: the provider heals; the very next call works and is billed
        # exactly once (rate-limit state was not poisoned by the failure).
        state["fail"] = False
        async with _http(reliability.app) as http:
            payload = _ok(
                await _acall(
                    http, token, "generate_cover_letter", {"resume_id": tailored["resume_id"]}
                )
            )
        assert payload["content"] == "Dear Acme team, ... sincerely Jane."
        assert await _wallet(db, user) == wallet0 - price
        charged = [r for r in await _ledger(db, user) if r["credits_charged"] > 0]
        assert [(r["feature"], r["credits_charged"], r["outcome"]) for r in charged] == [
            ("cover_letter", price, "ok")
        ]
        assert await _reserved(db, user) == 0
        _assert_log_hygiene(caplog, token)

    async def test_interview_prep_connection_error_charges_nothing_and_recovers(
        self, reliability, credits_on, monkeypatch, caplog
    ):
        """The provider refuses connections (litellm APIConnectionError) on
        interview prep: actionable 'llm_provider_unavailable' tool error, zero
        charge with a failed ledger row, no half-written deliverable, and a
        working call after recovery."""
        import litellm
        from app.ai_feature_prices import resolve_feature_cost
        from app.routers import resumes as resumes_router
        from app.schemas.models import InterviewPrepData

        db = reliability.db
        user = await _new_user(db, "llm-conn@example.com")
        token = await _mcp_token_for(user, "cursor")
        tailored = await _seed_tailored_resume(db, user)
        price = (await resolve_feature_cost(db, "interview_prep")).effective_credits
        wallet0 = price + 100
        await _fund(db, user, wallet0)

        state = {"fail": True}

        async def flaky_provider(*a, **k):
            if state["fail"]:
                raise litellm.APIConnectionError(
                    message="connection refused by provider",
                    llm_provider="openai",
                    model="gpt-4o",
                )
            return InterviewPrepData.model_validate(
                {
                    "role_fit_analysis": ["Strong SRE background"],
                    "resume_questions": [],
                    "project_follow_ups": [],
                    "skill_gaps": [],
                    "talking_points": ["Kubernetes at scale"],
                }
            )

        monkeypatch.setattr(resumes_router, "generate_interview_prep", flaky_provider)

        async with _http(reliability.app) as http:
            result = await _acall(
                http, token, "generate_interview_prep", {"resume_id": tailored["resume_id"]}
            )
        kind, text = _classify(result)
        assert kind == "error", text
        assert "llm_provider_unavailable" in text, text
        assert "temporarily unavailable" in text, text

        assert await _wallet(db, user) == wallet0
        assert await _reserved(db, user) == 0
        stored = await db.get_resume(user, tailored["resume_id"])
        assert stored.get("interview_prep") is None
        assert [
            (r["feature"], r["credits_charged"], r["outcome"]) for r in await _ledger(db, user)
        ] == [("interview_prep", 0, "failed")]

        # Recovery: billed exactly once, hold fully released.
        state["fail"] = False
        async with _http(reliability.app) as http:
            payload = _ok(
                await _acall(
                    http, token, "generate_interview_prep", {"resume_id": tailored["resume_id"]}
                )
            )
        assert payload["interview_prep"]["talking_points"] == ["Kubernetes at scale"]
        assert await _wallet(db, user) == wallet0 - price
        assert await _reserved(db, user) == 0
        charged = [r for r in await _ledger(db, user) if r["credits_charged"] > 0]
        assert len(charged) == 1 and charged[0]["outcome"] == "ok"
        _assert_log_hygiene(caplog, token)


# ===========================================================================
# Injection 4 - slow LLM + client disconnect mid-call
# ===========================================================================


class TestSlowLlmDisconnect:
    async def test_slow_provider_blocks_then_settles_exactly_once(
        self, reliability, credits_on, monkeypatch
    ):
        """A provider slower than any realistic client timeout is DOCUMENTED
        behavior: the tool call blocks until the provider answers (there is no
        internal deadline that would silently abort a paid generation), then
        settles exactly once."""
        from app.ai_feature_prices import resolve_feature_cost
        from app.routers import resumes as resumes_router

        db = reliability.db
        user = await _new_user(db, "llm-slow@example.com")
        token = await _mcp_token_for(user, "claude-desktop")
        tailored = await _seed_tailored_resume(db, user)
        price = (await resolve_feature_cost(db, "cover_letter")).effective_credits
        await _fund(db, user, price + 100)

        async def slow_provider(*a, **k):
            await asyncio.sleep(0.5)  # scaled stand-in for a 30s provider
            return "Slow but complete."

        monkeypatch.setattr(resumes_router, "generate_cover_letter", slow_provider)

        async with _http(reliability.app) as http:
            started = time.monotonic()
            payload = _ok(
                await _acall(
                    http, token, "generate_cover_letter", {"resume_id": tailored["resume_id"]}
                )
            )
            elapsed = time.monotonic() - started
        assert payload["content"] == "Slow but complete."
        assert elapsed >= 0.4, "the call must have actually waited on the provider"

        # Settled exactly once: one charge, hold released, no failed row.
        assert await _wallet(db, user) == 100  # wallet0 - price
        assert await _reserved(db, user) == 0
        rows = await _ledger(db, user)
        assert [(r["credits_charged"], r["outcome"]) for r in rows] == [(price, "ok")], rows

    async def test_client_disconnect_settles_exactly_once_no_leak(
        self, reliability_one_loop, credits_on, monkeypatch
    ):
        """The client hangs up mid-generation (uvicorn cancels the request
        task; here that is the request task being cancelled on the same loop
        the lifespan runs on).

        Pinned behavior: the disconnect does NOT abort the in-flight
        generation - the MCP stateless transport runs the tool on the session
        manager's task group, so it completes detached - and the billing
        context settles EXACTLY ONCE at the published price with the hold
        fully released. Never a leaked hold, never two ledger rows, never a
        half-written deliverable; and the next call works normally.
        (The detached-completion contract is REST-equivalent: a client that
        leaves mid-request still pays for provider work that ran. The
        per-disconnect task leak this exposes is an upstream SDK gap - see
        the reliability report - but it never corrupts billing state.)"""
        from app.ai_feature_prices import resolve_feature_cost
        from app.routers import resumes as resumes_router

        reliability = reliability_one_loop
        db = reliability.db
        user = await _new_user(db, "llm-disconnect@example.com")
        token = await _mcp_token_for(user, "claude-desktop")
        tailored = await _seed_tailored_resume(db, user)
        price = (await resolve_feature_cost(db, "cover_letter")).effective_credits
        wallet0 = price + 100
        await _fund(db, user, wallet0)

        gate = asyncio.Event()
        provider_task: dict = {}

        async def gated_provider(*a, **k):
            provider_task["task"] = asyncio.current_task()
            await gate.wait()
            return "Generated for a client that already left."

        monkeypatch.setattr(resumes_router, "generate_cover_letter", gated_provider)

        async with _http(reliability.app) as http:
            request = asyncio.create_task(
                _acall(http, token, "generate_cover_letter", {"resume_id": tailored["resume_id"]})
            )
            # Wait until the hold is actually taken, so the disconnect lands
            # mid-generation rather than before the reservation.
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if await _reserved(db, user) >= price:
                    break
                await asyncio.sleep(0.02)
            assert await _reserved(db, user) >= price, "hold was never taken"
            assert not request.done(), "the call must still be blocked on the provider"

            # The client disconnects.
            request.cancel()
            with suppress(asyncio.CancelledError):
                await request

            # The generation completes detached (documented above) - let it
            # finish and give the billing teardown a moment to settle.
            gate.set()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                await asyncio.sleep(0.05)
                if provider_task["task"].done() and await _reserved(db, user) == 0:
                    break

        assert provider_task["task"].done(), "provider work never finished"
        assert await _reserved(db, user) == 0, "hold leaked after client disconnect"
        rows = await _ledger(db, user)
        assert [(r["feature"], r["credits_charged"], r["outcome"]) for r in rows] == [
            ("cover_letter", price, "ok")
        ], f"must settle exactly once at the published price, got: {rows}"
        assert await _wallet(db, user) == wallet0 - price, "settled more than once"
        stored = await db.get_resume(user, tailored["resume_id"])
        assert stored.get("cover_letter") == "Generated for a client that already left."

        # No poisoned state: the next call works and bills exactly once more.
        async def fast_provider(*a, **k):
            return "Fresh generation."

        monkeypatch.setattr(resumes_router, "generate_cover_letter", fast_provider)
        # Drop the stored copy so the follow-up actually regenerates (the
        # endpoint serves an existing cover letter for free by design).
        await db.update_resume(user, tailored["resume_id"], {"cover_letter": None})
        async with _http(reliability.app) as http:
            payload = _ok(
                await _acall(
                    http, token, "generate_cover_letter", {"resume_id": tailored["resume_id"]}
                )
            )
        assert payload["content"] == "Fresh generation."
        assert await _wallet(db, user) == wallet0 - 2 * price
        assert await _reserved(db, user) == 0
        charged = [r for r in await _ledger(db, user) if r["credits_charged"] > 0]
        assert [(r["credits_charged"], r["outcome"]) for r in charged] == [
            (price, "ok"),
            (price, "ok"),
        ]


# ===========================================================================
# Injection 5 - the background search worker dies mid-scrape
# ===========================================================================


class TestSearchWorkerDeath:
    async def test_worker_death_reported_cleanly_and_user_can_retry(
        self, reliability, monkeypatch
    ):
        """The background scrape dies mid-run. The status must report
        ``failed`` WITHOUT the exception text (test_background_search pins
        'reported without leaking exception text' - this proves the same
        contract through the MCP tool), the user can start a NEW search
        immediately (the dead one no longer pins the single-flight slot), and
        the daily counter counts each STARTED search exactly once - the same
        burn-at-start semantics REST has, because both channels share
        ``start_manual_search``."""
        from app.routers import discovery

        db = reliability.db
        user = await _new_user(db, "search-death@example.com")
        token = await _mcp_token_for(user, "claude-desktop")

        # The 10s cooldown is a real REST+MCP rule; 0 keeps the retry immediate
        # without faking any state (the cooldown is in-process by design).
        monkeypatch.setattr(discovery, "_SEARCH_COOLDOWN_SECONDS", 0)

        fragment = "SUPER-SECRET-SCRAPE-FRAGMENT <img> user-b@example.com"

        async def dying_worker(payload, user_id, db, config, job=None):
            raise RuntimeError(fragment)

        monkeypatch.setattr(discovery, "_execute_manual_search", dying_worker)

        async with _http(reliability.app) as http:
            started = _ok(
                await _acall(http, token, "start_job_search", {"query": "SRE London"})
            )
            assert started["status"] == "running"
            assert started["already_running"] is False

            failed = await _await_status(
                http, token, started["search_id"], "failed", "done", "expired"
            )
            assert failed["status"] == "failed"
            assert "RuntimeError" in failed["error"], failed["error"]
            assert fragment not in failed["error"], "scrape fragment leaked to the client"
            assert "Traceback" not in (failed["error"] or "")

            # The dead search does not pin the single-flight slot: a new search
            # starts immediately (and does not report already_running).
            async def healthy_worker(payload, user_id, db, config, job=None):
                job.site_finished("indeed", found=3)
                job.saved = 2

            monkeypatch.setattr(discovery, "_execute_manual_search", healthy_worker)
            retry = _ok(
                await _acall(http, token, "start_job_search", {"query": "SRE Berlin"})
            )
            assert retry["status"] == "running"
            assert retry["already_running"] is False
            assert retry["search_id"] != started["search_id"]

            done = await _await_status(http, token, retry["search_id"], "done")
            assert done["status"] == "done"
            assert done["saved"] == 2

        # Daily cap: each STARTED search counts exactly once (the failed one
        # burned its slot at start - burn-at-start is REST's behavior too,
        # because MCP calls the same handler; a failed search is NOT free).
        async with db.session_factory() as session:
            counters = (
                await session.execute(
                    select(DailyUsageCounter).where(DailyUsageCounter.user_id == user)
                )
            ).scalars().all()
        search_count = sum(c.count for c in counters if c.kind == "job_search")
        assert search_count == 2, (
            f"expected 2 started searches counted, got {search_count} - "
            "MCP and REST must burn the daily cap identically (both at start)"
        )


# ===========================================================================
# Injection 6 - settings flipped at runtime
# ===========================================================================


class TestRuntimeSettingsFlip:
    async def test_mcp_enabled_flip_mount_gone_tokens_404_inflight_survives(
        self, reliability, monkeypatch
    ):
        """mcp_enabled goes true -> false BETWEEN two calls. The mount must
        vanish (404, no protocol trace), the REST token-management surface
        must 404 with it, and - best-effort - an in-flight request accepted
        before the flip completes coherently on the app instance that took
        it (the reload builds a NEW app; the old one is not torn out from
        under the running request)."""
        import app.database as database_module

        db = reliability.db
        user = await _new_user(db, "flip-mcp@example.com")
        token = await _mcp_token_for(user, "claude-desktop")
        resume = await _seed_resume(db, user)
        job = await db.create_job(user, content=JD_ACME)
        await db.create_application(
            user, job_id=job["job_id"], resume_id=resume["resume_id"]
        )

        gated = GatedDb(db, "list_applications")
        monkeypatch.setattr(database_module, "db", gated)

        app_enabled = reliability.app
        async with _http(app_enabled) as http:
            # An in-flight read, parked inside the db call.
            in_flight = asyncio.create_task(
                _acall(http, token, "list_applications", {})
            )
            await asyncio.sleep(0.2)
            assert not in_flight.done(), "the read should still be in flight"

            # The deployment flips the kill-switch: app.main is rebuilt with
            # the mount absent (the same reload the mcp_app fixture models).
            app_disabled = reliability.factory(False)

            # On the NEW app the mount is gone and the REST token surface 404s.
            async with _http(app_disabled) as http2:
                res = await http2.post(
                    MCP_ENDPOINT, json={"jsonrpc": "2.0", "id": 1, "method": "ping"}
                )
                assert res.status_code == 404, "the mount must be absent when disabled"
                res = await http2.get("/api/v1/mcp/tokens")
                assert res.status_code == 404, (
                    f"token management must 404 with the mount, got {res.status_code}"
                )

            # The request the OLD app already accepted completes coherently.
            gated.gate.set()
            board = _ok(await in_flight)
            assert board["total"] == 1

        monkeypatch.setattr(database_module, "db", db)

    async def test_job_discovery_flip_running_search_completes_new_refused(
        self, reliability, monkeypatch
    ):
        """JOB_DISCOVERY goes true -> false while a search runs. The running
        search must complete (its work was accepted before the flip), new
        starts must be refused with ``job_discovery_disabled``, and - pinned
        as current, coherent behavior - status polls are refused the same
        way while the flag is off (the kill-switch gates the whole tool
        surface, so a flipped deployment leaks no progress data)."""
        from app.config import settings as app_settings
        from app.job_discovery import search_jobs
        from app.routers import discovery

        db = reliability.db
        user = await _new_user(db, "flip-discovery@example.com")
        token = await _mcp_token_for(user, "claude-desktop")

        gate = asyncio.Event()
        seen: dict = {}

        async def gated_worker(payload, user_id, db, config, job=None):
            seen["job"] = job
            await gate.wait()
            job.site_finished("indeed", found=5)
            job.saved = 4

        monkeypatch.setattr(discovery, "_execute_manual_search", gated_worker)

        async with _http(reliability.app) as http:
            started = _ok(
                await _acall(http, token, "start_job_search", {"query": "SRE London"})
            )
            assert started["status"] == "running"

            # The flip happens mid-scrape.
            monkeypatch.setattr(app_settings, "JOB_DISCOVERY", False)

            # New starts are refused with the kill-switch code...
            result = await _acall(http, token, "start_job_search", {"query": "SRE Berlin"})
            kind, text = _classify(result)
            assert kind == "error", text
            assert "job_discovery_disabled" in text, text

            # ...and polls too (the whole tool surface is gated).
            result = await _acall(
                http, token, "get_job_search_status", {"search_id": started["search_id"]}
            )
            kind, text = _classify(result)
            assert kind == "error", text
            assert "job_discovery_disabled" in text, text

            # The already-running search completes anyway.
            gate.set()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if search_jobs.get(user, started["search_id"])["status"] == "done":
                    break
                await asyncio.sleep(0.05)
            assert search_jobs.get(user, started["search_id"])["status"] == "done"
            assert search_jobs.get(user, started["search_id"])["saved"] == 4

            # Re-enabled, the finished search reads normally through the tool.
            monkeypatch.setattr(app_settings, "JOB_DISCOVERY", True)
            done = _ok(
                await _acall(
                    http, token, "get_job_search_status", {"search_id": started["search_id"]}
                )
            )
            assert done["status"] == "done"
            assert done["saved"] == 4


# ===========================================================================
# Injection 7 - timeout discipline sweep
# ===========================================================================


class TestTimeoutDiscipline:
    """No MCP-reachable slow path may wait forever without a bound.

    Source-level sweep of every slow path the tools can reach: provider
    calls (bounded by an explicit litellm timeout), the streaming relay and
    improve flow (bounded by ``asyncio.wait_for``), a wedged search (bounded
    by ``_MAX_RUNTIME_SECONDS``), and the tool layer itself (must never
    introduce an unbounded internal wait of its own).
    """

    BACKEND_ROOT = Path(__file__).resolve().parents[2] / "app"

    def test_every_llm_provider_call_carries_a_timeout(self):
        """Every ``acompletion(...)`` call site in app/llm.py builds its kwargs
        with an explicit ``timeout`` - a hung provider can never pin a worker
        past the adaptive bound."""
        source = (self.BACKEND_ROOT / "llm.py").read_text()
        lines = source.splitlines()
        sites = [
            i for i, line in enumerate(lines) if re.search(r"\bacompletion\(", line)
        ]
        assert len(sites) >= 4, f"expected the known provider call sites, found {len(sites)}"
        for i in sites:
            window = lines[max(0, i - 35) : i + 1]
            assert any('"timeout"' in line or "timeout=" in line for line in window), (
                f"app/llm.py line {i + 1}: acompletion call without a timeout bound"
            )

    def test_streaming_and_improve_waits_are_bounded(self):
        """Every ``asyncio.wait_for`` in the resumes router names an explicit
        timeout (the SSE relay and the tailoring flow), so no generation can
        wait forever inside the request."""
        source = (self.BACKEND_ROOT / "routers" / "resumes.py").read_text()
        lines = source.splitlines()
        sites = [i for i, line in enumerate(lines) if "asyncio.wait_for(" in line]
        assert sites, "the bounded waits this sweep pins have moved"
        for i in sites:
            window = lines[i : i + 15]
            assert any("timeout=" in line for line in window), (
                f"resumes.py line {i + 1}: asyncio.wait_for without a timeout"
            )

    def test_wedged_search_is_abandoned_not_eternal(self):
        """A wedged connector cannot pin a search as running forever: the
        registry hard-caps runtime at a few minutes."""
        from app.job_discovery import search_jobs

        assert 0 < search_jobs._MAX_RUNTIME_SECONDS <= 300

    def test_mcp_tools_add_no_unbounded_waits(self):
        """The tool layer itself must not introduce an unbounded internal
        wait: no ``asyncio.sleep`` and no bare ``wait_for`` in the tools -
        slowness may only come from the layers below (each of which is
        bounded by the tests above)."""
        tools_dir = self.BACKEND_ROOT / "mcp" / "tools"
        for path in tools_dir.glob("*.py"):
            source = path.read_text()
            assert "asyncio.sleep(" not in source, f"{path.name}: unbounded internal sleep"
            assert "asyncio.wait_for(" not in source, f"{path.name}: internal wait_for"

    def test_sqlite_busy_contention_is_bounded(self):
        """The SQLite engine rides out lock contention with a bounded
        busy_timeout instead of erroring forever on a locked file."""
        source = (self.BACKEND_ROOT / "db_engine.py").read_text()
        assert "busy_timeout" in source, "the busy_timeout PRAGMA is gone"

"""MCP data-integrity attack suite: mutations under concurrency and partial failure.

The e2e workflow suite (``test_mcp_e2e_workflows.py``) proves the happy paths
produce the same truth as REST. This suite attacks the write paths the way real
production load does: N parallel tool calls for the same row, DB failures
injected after the first write, a provider dying mid-generation, the in-memory
search registry vanishing mid-flight (restart proxy), two tokens racing on one
user, and a client retrying after a timeout-style error.

The spine of every test: assert the DATABASE state directly (via the app's db
layer), not just response codes - a coherent response over corrupted rows is
exactly the bug class this suite exists to catch. Only externals are mocked
(the LLM provider and the job-board scrape, at the same seams the other suites
use); FitWright logic always runs for real.

REST parity is asserted where the mission demands it (status races, status
validation, billing failure) by racing the browser PATCH/POST endpoints the web
app uses against the same data, through real session + CSRF - one database,
zero divergence.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.models import DailyUsageCounter, Job, Reminder
from tests.integration.conftest import (
    MCP_ENDPOINT,
    mcp_ok as _ok,
)

pytestmark = pytest.mark.integration

PASSWORD = "correct-horse-battery-staple-9"

JD_ACME = """Site Reliability Engineer - Acme Corp (Remote)
Keep Acme's platform boring: 12 clusters, 2M req/day, humane on-call.
Requirements: Kubernetes, Python, calm under pressure.
"""

JD_GLOBEX = """Platform Reliability Engineer - Globex (Remote, EU)
Requirements: 3+ years Kubernetes in production, Python or Go.
"""


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
async def integrity(auth_env, mcp_app, mcp_token, isolated_db, owner_id, monkeypatch):
    """The whole app with the MCP mount on, over the isolated DB.

    Yields ``(app, db, owner_id, owner_token)``; tests drive MCP concurrently
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
        )


def _http(app) -> AsyncClient:
    # https base_url so the httpx cookie jar stores/returns Secure cookies
    # (needed for the REST parity legs that log a browser session in).
    return AsyncClient(transport=ASGITransport(app=app), base_url="https://test")


async def _acall(http: AsyncClient, token: str, name: str, arguments: dict, _id: int = 1) -> dict:
    """One async ``tools/call`` JSON-RPC round-trip; returns the parsed body."""
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


async def _arace(http: AsyncClient, token: str, calls: list[tuple[str, dict]]) -> list[dict]:
    """N tool calls dispatched concurrently (one task each)."""
    return await asyncio.gather(
        *(_acall(http, token, name, args, _id=i) for i, (name, args) in enumerate(calls))
    )


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
# Seeding + direct-DB assertion helpers
# ---------------------------------------------------------------------------


async def _new_user(db, email: str) -> str:
    from app.auth.accounts import create_user
    from app.auth.passwords import get_password_service

    record = await create_user(
        email=email,
        name="Integrity User",
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


def _future_iso(hours: int = 48) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


async def _job_rows(db, user_id: str) -> list[Job]:
    async with db.session_factory() as session:
        rows = (
            await session.execute(select(Job).where(Job.user_id == user_id))
        ).scalars().all()
        # Detach-friendly: return plain data.
        return [
            SimpleNamespace(job_id=r.job_id, content=r.content, resume_id=r.resume_id)
            for r in rows
        ]


async def _reminder_rows(db, user_id: str) -> list[Reminder]:
    async with db.session_factory() as session:
        return list(
            (
                await session.execute(select(Reminder).where(Reminder.user_id == user_id))
            )
            .scalars()
            .all()
        )


def _assert_columns_contiguous(applications: list[dict], context: str) -> None:
    """Every status column must be a contiguous 0..n-1 position sequence."""
    by_status: dict[str, list[int]] = {}
    for card in applications:
        by_status.setdefault(card["status"], []).append(card["position"])
    for status, positions in by_status.items():
        assert sorted(positions) == list(range(len(positions))), (
            f"{context}: column {status!r} positions {sorted(positions)} "
            f"are not a contiguous 0..n-1 sequence"
        )


async def _assert_no_orphans(db, user_id: str, *, applications: list[dict]) -> None:
    """Invariant from app/applications/manual.py: every tracker card's job
    exists (the manual-add seam deletes the job when the application write
    fails, so a card without its JD text is corruption)."""
    jobs = {job.job_id for job in await _job_rows(db, user_id)}
    for card in applications:
        assert card["job_id"] in jobs, f"orphan application {card['application_id']}"
        detail = await db.get_application_detail(user_id, card["application_id"])
        assert detail is not None and detail.get("job_content"), (
            f"application {card['application_id']} lost its job description"
        )


async def _wallet(db, user_id: str) -> int:
    account = await db.get_or_create_credit_account(user_id)
    return account["wallet_credits"]


async def _ledger(db, user_id: str) -> list[dict]:
    return await db.list_usage(user_id, limit=100)


async def _login_browser(app, email: str) -> AsyncClient:
    """A browser client with a REAL session (login endpoint + cookies)."""
    client = _http(app)
    csrf = (await client.get("/api/v1/auth/csrf")).json()["csrfToken"]
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": PASSWORD},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 200, resp.text
    return client


def _browser_headers(client: AsyncClient) -> dict:
    return {"X-CSRF-Token": client.cookies.get("csrf")}


# ===========================================================================
# Attack 1 - concurrent duplicate suppression
# ===========================================================================


class TestConcurrentDuplicateSuppression:
    async def test_parallel_add_application_same_company_role(
        self, integrity
    ):
        """N parallel ``add_application`` for the SAME (user, company, role).

        Duplicate suppression on the manual-add path is ADVISORY (check_duplicate
        before queueing) - there is no (user, company, role) constraint, and each
        call creates its own job row, so N successful calls mean N cards. What
        must hold regardless of interleaving: no constraint violation, no orphan
        rows, and the DB count exactly matches the successful responses.
        """
        db = integrity.db
        user = await _new_user(db, "race-add@example.com")
        token = await _mcp_token_for(user, "claude-desktop")
        resume = await _seed_resume(db, user)
        args = {
            "job_description": JD_ACME,
            "resume_id": resume["resume_id"],
            "company": "Acme Corp",
            "role": "Site Reliability Engineer",
        }
        n = 6

        async with _http(integrity.app) as http:
            results = await _arace(
                http, token, [("add_application", args)] * n
            )

        ok_ids, errors = [], []
        for result in results:
            kind, payload = _classify(result)
            if kind == "ok":
                ok_ids.append(payload["application_id"])
            else:
                errors.append(payload)
        # The manual-add seam serializes a user's creates (per-user storage
        # lock), so no parallel call may fail and no error text may leak.
        assert errors == [], errors

        applications = await db.list_applications(user)
        assert len(applications) == len(ok_ids), (
            f"{len(ok_ids)} successful responses but {len(applications)} rows: "
            "a failed call wrote a row, or a successful one wrote none"
        )
        assert len(set(ok_ids)) == len(ok_ids), "two responses claim one card"
        assert {c["application_id"] for c in applications} == set(ok_ids)

        # No orphan jobs: one job per successful card, every card keeps its JD.
        assert len(await _job_rows(db, user)) == len(ok_ids)
        await _assert_no_orphans(db, user, applications=applications)

        for card in applications:
            assert card["status"] == "applied"
            assert card["company"] == "Acme Corp"
            assert card["role"] == "Site Reliability Engineer"

        # The applied column is a contiguous 0..n-1 sequence even though all N
        # inserts raced (this is the invariant the per-user storage lock
        # protects; it failed before the lock was added - two cards at position 0).
        _assert_columns_contiguous(applications, "parallel add_application")

        # Board-level sanity through the tool itself.
        async with _http(integrity.app) as http:
            board = _ok(await _acall(http, token, "list_applications", {}))
        assert board["total"] == len(ok_ids)
        assert len(board["columns"]["applied"]) == len(ok_ids)

    async def test_parallel_create_reminder_one_application(
        self, integrity
    ):
        """N parallel ``create_reminder`` on one application.

        The tool exposes no idempotency key, so N calls legitimately mean N
        reminders (REST parity: REST only collapses identical creates when the
        client sends an Idempotency-Key). What must hold: every row is attached
        to the right application, ids are unique, and the count matches the
        successful responses - no phantom, no lost row, no cross-attachment.
        """
        db = integrity.db
        user = await _new_user(db, "race-remind@example.com")
        token = await _mcp_token_for(user, "cursor")
        resume = await _seed_resume(db, user)
        job = await db.create_job(user, content=JD_ACME)
        card = await db.create_application(
            user, job_id=job["job_id"], resume_id=resume["resume_id"]
        )
        n = 6
        args = {
            "application_id": card["application_id"],
            "remind_at": _future_iso(24),
            "note": "parallel follow-up",
        }

        async with _http(integrity.app) as http:
            results = await _arace(http, token, [("create_reminder", args)] * n)

        ok_ids, errors = [], []
        for result in results:
            kind, payload = _classify(result)
            if kind == "ok":
                ok_ids.append(payload["id"])
            else:
                errors.append(payload)
        assert errors == [], errors

        rows = await _reminder_rows(db, user)
        assert len(rows) == len(ok_ids), (
            f"{len(ok_ids)} successful responses but {len(rows)} rows"
        )
        assert len({r.id for r in rows}) == len(rows)
        assert {r.id for r in rows} == set(ok_ids)
        for row in rows:
            assert row.application_id == card["application_id"]
            assert row.status == "pending"
            assert row.user_id == user

        # The parent card is untouched by the reminder race.
        detail = await db.get_application_detail(user, card["application_id"])
        assert detail["status"] == "applied"

    async def test_parallel_reminders_respect_the_cap(self, integrity, monkeypatch):
        """The per-user reminder cap is check-then-insert; the service wraps it
        in a per-user lock, so racing creates must not blow past the cap: with
        two slots left, four parallel creates land exactly two and refuse the
        rest with ``reminder_limit_reached``."""
        from app.config import settings

        db = integrity.db
        user = await _new_user(db, "race-cap@example.com")
        token = await _mcp_token_for(user, "claude-desktop")
        resume = await _seed_resume(db, user)
        job = await db.create_job(user, content=JD_ACME)
        card = await db.create_application(
            user, job_id=job["job_id"], resume_id=resume["resume_id"]
        )

        cap = 6
        monkeypatch.setattr(settings, "max_reminders_per_user", cap)

        from app.scheduling.service import get_scheduling_service

        svc = get_scheduling_service()
        for i in range(cap - 2):  # two slots left
            await svc.create_reminder(
                user, card["application_id"], due_at=_future_iso(24), note=f"seed-{i}"
            )
        assert len(await _reminder_rows(db, user)) == cap - 2

        args = {
            "application_id": card["application_id"],
            "remind_at": _future_iso(48),
            "note": "racing create",
        }
        async with _http(integrity.app) as http:
            results = await _arace(http, token, [("create_reminder", args)] * 4)

        refused = 0
        for result in results:
            kind, text = _classify(result)
            if kind == "ok":
                continue
            assert "reminder_limit_reached" in text, text  # the only legal refusal
            refused += 1
        assert refused == 2, f"expected exactly 2 cap refusals, got {refused}: {results}"

        rows = await _reminder_rows(db, user)
        assert len(rows) == cap, f"cap {cap} must hold under racing creates: {len(rows)} rows"
        # Whatever landed is well-formed.
        assert all(r.application_id == card["application_id"] for r in rows)
        assert len({r.id for r in rows}) == len(rows)


# ===========================================================================
# Attack 2 - concurrent status transitions
# ===========================================================================


class TestConcurrentStatusTransitions:
    async def test_parallel_status_moves_stay_consistent(self, integrity):
        """Six parallel ``update_application_status`` racing on one card
        (alternating interview/rejected).

        Last-write-wins is the contract (there is no legal-transition table and
        REST PATCH behaves identically - proven below against the real REST
        route). What must hold: the final DB status is one of the raced values,
        it matches what every later read sees, and every column's positions are
        a contiguous 0..n-1 sequence (the renumber-on-move invariant).
        """
        db = integrity.db
        user = await _new_user(db, "race-status@example.com")
        token = await _mcp_token_for(user, "cursor")
        resume = await _seed_resume(db, user)
        # The card under attack plus two siblings in the same column, so the
        # renumber-on-move path actually has rows to renumber.
        cards = []
        for i in range(3):
            job = await db.create_job(user, content=f"JD {i}")
            cards.append(
                await db.create_application(
                    user,
                    job_id=job["job_id"],
                    resume_id=resume["resume_id"],
                    status="applied",
                )
            )
        target = cards[0]["application_id"]

        calls = [
            ("update_application_status", {"application_id": target, "status": s})
            for s in ("interview", "rejected") * 3
        ]
        async with _http(integrity.app) as http:
            results = await _arace(http, token, calls)

        ok, errors = 0, []
        for result in results:
            kind, payload = _classify(result)
            if kind == "ok":
                ok += 1
                assert payload["status"] in ("interview", "rejected")
            else:
                errors.append(payload)
        # Moves on one user's board serialize (per-user storage lock), so a
        # racing move is never lost to a lock error - it just orders after the
        # previous one. Last write wins, exactly like the REST race below.
        assert errors == [], errors
        assert ok == 6

        applications = await db.list_applications(user)
        final = next(c for c in applications if c["application_id"] == target)
        assert final["status"] in ("interview", "rejected")
        _assert_columns_contiguous(applications, "parallel MCP status race")
        await _assert_no_orphans(db, user, applications=applications)

        # Every read path agrees on the winner.
        detail = await db.get_application_detail(user, target)
        assert detail["status"] == final["status"]
        async with _http(integrity.app) as http:
            board = _ok(await _acall(http, token, "list_applications", {}))
        raced = [
            c
            for column in board["columns"].values()
            for c in column
            if c["application_id"] == target
        ]
        assert len(raced) == 1
        assert raced[0]["status"] == final["status"]

    async def test_rest_patch_race_is_last_write_wins_too(self, integrity):
        """REST parity: the same race through the browser PATCH route.

        ``update_application_status`` and REST PATCH both call
        ``db.update_application`` with an enum-validated value; this proves the
        REST path is the same last-write-wins contract, so an AI client and a
        browser racing on one card cannot produce channel-specific outcomes.
        """
        db = integrity.db
        email = "race-rest@example.com"
        user = await _new_user(db, email)
        token = await _mcp_token_for(user, "claude-desktop")
        resume = await _seed_resume(db, user)
        job = await db.create_job(user, content=JD_ACME)
        card = await db.create_application(
            user, job_id=job["job_id"], resume_id=resume["resume_id"]
        )
        browser = await _login_browser(integrity.app, email)
        try:
            async def _patch(status: str, _id: int):
                return await browser.patch(
                    f"/api/v1/applications/{card['application_id']}",
                    json={"status": status},
                    headers=_browser_headers(browser),
                )

            responses = await asyncio.gather(
                _patch("interview", 1), _patch("rejected", 2)
            )
            for resp in responses:
                assert resp.status_code == 200, resp.text

            detail = await db.get_application_detail(user, card["application_id"])
            assert detail["status"] in ("interview", "rejected")
            _assert_columns_contiguous(
                await db.list_applications(user), "parallel REST status race"
            )

            # The MCP board sees the same winner - one truth.
            async with _http(integrity.app) as http:
                board = _ok(await _acall(http, token, "list_applications", {}))
            raced = [
                c
                for column in board["columns"].values()
                for c in column
                if c["application_id"] == card["application_id"]
            ]
            assert raced[0]["status"] == detail["status"]
        finally:
            await browser.aclose()


# ===========================================================================
# Attack 3 - status validation
# ===========================================================================


class TestStatusValidation:
    @pytest.mark.parametrize(
        "bad",
        [
            "Interview",  # wrong case
            "INTERVIEW",  # shouting
            "interview ",  # trailing whitespace
            " interview",  # leading whitespace
            "interview\n",  # newline smuggle
            "acceptedx",  # unknown
            "unknown_status",  # unknown
            "",  # empty
            " ",  # whitespace-only
            "іnterview",  # Cyrillic i lookalike
            "accepted​",  # zero-width space
            "x" * 300,  # very long
            "accepted,rejected",  # list smuggle
        ],
        ids=lambda v: repr(v)[:40],
    )
    async def test_invalid_status_is_refused_and_changes_nothing(
        self, integrity, bad
    ):
        db = integrity.db
        user = await _new_user(db, "status-matrix@example.com")
        token = await _mcp_token_for(user, "cursor")
        resume = await _seed_resume(db, user)
        job = await db.create_job(user, content=JD_ACME)
        card = await db.create_application(
            user, job_id=job["job_id"], resume_id=resume["resume_id"], status="applied"
        )
        before = await db.get_application_detail(user, card["application_id"])

        async with _http(integrity.app) as http:
            result = await _acall(
                http,
                token,
                "update_application_status",
                {"application_id": card["application_id"], "status": bad},
            )
        kind, text = _classify(result)
        assert kind == "error", f"{bad!r} must be refused, got {text!r}"
        assert "invalid_status" in text, text
        assert "interview" in text  # the valid values are listed

        after = await db.get_application_detail(user, card["application_id"])
        assert after["status"] == "applied"
        assert after["updated_at"] == before["updated_at"], "a refused move mutated the row"

    async def test_non_string_status_is_refused(self, integrity):
        db = integrity.db
        user = await _new_user(db, "status-type@example.com")
        token = await _mcp_token_for(user, "claude-desktop")
        resume = await _seed_resume(db, user)
        job = await db.create_job(user, content=JD_ACME)
        card = await db.create_application(
            user, job_id=job["job_id"], resume_id=resume["resume_id"]
        )
        async with _http(integrity.app) as http:
            result = await _acall(
                http,
                token,
                "update_application_status",
                {"application_id": card["application_id"], "status": 7},
            )
        kind, _ = _classify(result)
        assert kind == "error"
        assert (await db.get_application_detail(user, card["application_id"]))["status"] == "applied"

    @pytest.mark.parametrize(
        "status",
        ["saved", "applied", "no_response", "response", "interview", "accepted", "rejected"],
    )
    async def test_every_status_is_reachable_and_persisted(self, integrity, status):
        db = integrity.db
        user = await _new_user(db, f"status-ok-{status}@example.com")
        token = await _mcp_token_for(user, "cursor")
        resume = await _seed_resume(db, user)
        job = await db.create_job(user, content=JD_ACME)
        card = await db.create_application(
            user, job_id=job["job_id"], resume_id=resume["resume_id"]
        )
        async with _http(integrity.app) as http:
            payload = _ok(
                await _acall(
                    http,
                    token,
                    "update_application_status",
                    {"application_id": card["application_id"], "status": status},
                )
            )
        assert payload["status"] == status
        assert (
            await db.get_application_detail(user, card["application_id"])
        )["status"] == status
        _assert_columns_contiguous(await db.list_applications(user), f"move to {status}")

    async def test_backward_transition_allowed_on_both_channels(self, integrity):
        """There is NO legal-transition table - any move between the seven
        columns is legal (REST PATCH accepts accepted->saved; the MCP tool must
        accept it identically, or an AI client would fight the browser)."""
        db = integrity.db
        email = "status-back@example.com"
        user = await _new_user(db, email)
        token = await _mcp_token_for(user, "claude-desktop")
        resume = await _seed_resume(db, user)
        job = await db.create_job(user, content=JD_ACME)
        card = await db.create_application(
            user, job_id=job["job_id"], resume_id=resume["resume_id"], status="accepted"
        )

        async with _http(integrity.app) as http:
            moved = _ok(
                await _acall(
                    http,
                    token,
                    "update_application_status",
                    {"application_id": card["application_id"], "status": "saved"},
                )
            )
        assert moved["status"] == "saved"
        assert (
            await db.get_application_detail(user, card["application_id"])
        )["status"] == "saved"

        # The browser PATCH accepts the same backwards move.
        browser = await _login_browser(integrity.app, email)
        try:
            resp = await browser.patch(
                f"/api/v1/applications/{card['application_id']}",
                json={"status": "accepted"},
                headers=_browser_headers(browser),
            )
            assert resp.status_code == 200, resp.text
            assert (
                await db.get_application_detail(user, card["application_id"])
            )["status"] == "accepted"

            # And REST refuses a bad status with 422 where MCP says invalid_status.
            resp = await browser.patch(
                f"/api/v1/applications/{card['application_id']}",
                json={"status": "Interview"},
                headers=_browser_headers(browser),
            )
            assert resp.status_code == 422, resp.text
        finally:
            await browser.aclose()
        assert (
            await db.get_application_detail(user, card["application_id"])
        )["status"] == "accepted"


# ===========================================================================
# Attack 4 - partial-failure atomicity
# ===========================================================================


class _ProxyDb:
    """Delegate-everything proxy over the isolated DB with injected failures."""

    def __init__(self, real, *, fail_methods: set[str], fail_exc: Exception):
        self._real = real
        self._fail_methods = fail_methods
        self._fail_exc = fail_exc

    def __getattr__(self, name):
        return getattr(self._real, name)

    async def create_application(self, *a, **k):
        if "create_application" in self._fail_methods:
            raise self._fail_exc
        return await self._real.create_application(*a, **k)

    async def delete_job(self, *a, **k):
        if "delete_job" in self._fail_methods:
            raise self._fail_exc
        return await self._real.delete_job(*a, **k)


class TestPartialFailureAtomicity:
    async def test_add_application_failure_after_job_write_leaves_no_orphan(
        self, integrity, monkeypatch
    ):
        """DB dies on the application write (AFTER the job was created).

        The manual-add seam must roll the whole operation back: no application
        row, no orphan job, an error response (never a silent success), and the
        billing ledger untouched.
        """
        import app.database as database_module

        db = integrity.db
        user = await _new_user(db, "partial-add@example.com")
        token = await _mcp_token_for(user, "claude-desktop")
        resume = await _seed_resume(db, user)

        jobs_before = await _job_rows(db, user)
        proxy = _ProxyDb(
            db,
            fail_methods={"create_application"},
            fail_exc=RuntimeError("simulated DB failure mid-operation"),
        )
        monkeypatch.setattr(database_module, "db", proxy)

        async with _http(integrity.app) as http:
            result = await _acall(
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
        monkeypatch.setattr(database_module, "db", db)

        kind, text = _classify(result)
        assert kind == "error", text
        assert "application_create_failed" in text, text

        # The just-created job was cleaned up: same job set as before.
        assert await _job_rows(db, user) == jobs_before, "orphan job survived the failure"
        assert await db.list_applications(user) == [], "phantom application row"
        assert await _ledger(db, user) == [], "ledger mutated by a failed free operation"

    async def test_cleanup_failure_is_best_effort_and_honest(
        self, integrity, monkeypatch, caplog
    ):
        """Both the application write AND the orphan cleanup fail.

        Documented seam behavior: the client gets the error (never a silent
        success) and the orphan job cleanup is best-effort - it is logged for
        the operator, and no application row is ever created.
        """
        import app.database as database_module

        db = integrity.db
        user = await _new_user(db, "partial-cleanup@example.com")
        token = await _mcp_token_for(user, "cursor")
        resume = await _seed_resume(db, user)

        proxy = _ProxyDb(
            db,
            fail_methods={"create_application", "delete_job"},
            fail_exc=RuntimeError("simulated cascading DB failure"),
        )
        monkeypatch.setattr(database_module, "db", proxy)
        async with _http(integrity.app) as http:
            result = await _acall(
                http,
                token,
                "add_application",
                {"job_description": JD_ACME, "resume_id": resume["resume_id"]},
            )
        monkeypatch.setattr(database_module, "db", db)

        kind, text = _classify(result)
        assert kind == "error", text
        assert "application_create_failed" in text, text

        # No application ever existed; the orphan job is the known best-effort
        # gap, and it MUST be logged (silent orphans are unfixable).
        assert await db.list_applications(user) == []
        orphan_jobs = await _job_rows(db, user)
        assert len(orphan_jobs) == 1  # documented, logged limitation
        warnings = [
            r
            for r in caplog.records
            if r.name == "app.applications.manual" and "Failed to clean up orphan job" in r.getMessage()
        ]
        assert warnings, "orphan-job cleanup failure must be logged, not silent"

    async def test_reminder_create_failure_leaves_no_phantom(
        self, integrity, monkeypatch
    ):
        """The scheduling repo's persist step dies: no reminder row, an honest
        error, the parent card and the ledger untouched."""
        from app.scheduling.repo import SchedulingRepo

        db = integrity.db
        user = await _new_user(db, "partial-remind@example.com")
        token = await _mcp_token_for(user, "claude-desktop")
        resume = await _seed_resume(db, user)
        job = await db.create_job(user, content=JD_ACME)
        card = await db.create_application(
            user, job_id=job["job_id"], resume_id=resume["resume_id"]
        )

        async def boom(*a, **k):
            raise RuntimeError("simulated reminder persist failure")

        monkeypatch.setattr(SchedulingRepo, "create_reminder", boom)
        async with _http(integrity.app) as http:
            result = await _acall(
                http,
                token,
                "create_reminder",
                {
                    "application_id": card["application_id"],
                    "remind_at": _future_iso(24),
                    "note": "should never exist",
                },
            )
        kind, text = _classify(result)
        assert kind == "error", text
        assert "Traceback" not in text

        assert await _reminder_rows(db, user) == [], "phantom reminder row"
        detail = await db.get_application_detail(user, card["application_id"])
        assert detail["status"] == "applied"
        assert await _ledger(db, user) == []


# ===========================================================================
# Attack 5 - billing atomicity under provider failure
# ===========================================================================


class TestBillingAtomicityUnderFailure:
    async def test_provider_dies_mid_generation_charges_nothing_and_recovers(
        self, integrity, credits_on, monkeypatch
    ):
        """The LLM call raises mid-generation (mocked external, same seam as the
        other suites). REST behavior on this path: hold released, zero-charge
        'failed' ledger row, no half-written deliverable. MCP bills through the
        literally-same context manager, so the outcome must be identical - and
        after recovery the saved-copy reuse still works for free."""
        from app.routers import resumes as resumes_router

        db = integrity.db
        email = "billing-fail@example.com"
        user = await _new_user(db, email)
        token = await _mcp_token_for(user, "claude-desktop")
        browser = await _login_browser(integrity.app, email)

        master = await db.create_resume(
            user,
            content="# Jane\nSenior SRE",
            filename="master.md",
            is_master=True,
            processing_status="ready",
        )
        jd = await db.create_job(user, content=JD_ACME)
        tailored = await db.create_resume(
            user,
            content="# Jane\nTailored for Acme",
            filename="tailored.md",
            parent_id=master["resume_id"],
            processed_data={"skills": ["kubernetes"]},
            processing_status="ready",
        )
        await db.create_improvement(
            user,
            original_resume_id=master["resume_id"],
            tailored_resume_id=tailored["resume_id"],
            job_id=jd["job_id"],
            improvements=[],
        )
        from app.ai_feature_prices import resolve_feature_cost

        price = (await resolve_feature_cost(db, "cover_letter")).effective_credits
        wallet0 = price + 100
        await db.get_or_create_credit_account(user)
        async with db.session_factory() as session:
            from app.models import CreditAccount

            row = await session.get(CreditAccount, user)
            row.wallet_credits = wallet0
            row.allowance_period_start = datetime.now(timezone.utc).isoformat()
            await session.commit()

        # The provider call is the ONLY thing mocked; everything else is real.
        state = {"fail": True}

        async def flaky_provider(*a, **k):
            if state["fail"]:
                raise RuntimeError("provider 500 mid-generation")
            return "Dear Acme team, ... sincerely Jane."

        monkeypatch.setattr(resumes_router, "generate_cover_letter", flaky_provider)

        async with _http(integrity.app) as http:
            result = await _acall(
                http, token, "generate_cover_letter", {"resume_id": tailored["resume_id"]}
            )
        kind, text = _classify(result)
        assert kind == "error", text
        assert "Traceback" not in text

        # No charge: wallet intact, hold fully released, no half-written letter.
        assert await _wallet(db, user) == wallet0
        account = await db.get_or_create_credit_account(user)
        assert account["reserved_credits"] == 0, "hold leaked on failure"
        stored = await db.get_resume(user, tailored["resume_id"])
        assert stored.get("cover_letter") is None, "half-written deliverable"
        rows = await _ledger(db, user)
        assert [(r["feature"], r["credits_charged"], r["outcome"]) for r in rows] == [
            ("cover_letter", 0, "failed")
        ], "exactly one provable zero-charge failed row"

        # REST parity: the same failure through the browser's generate endpoint
        # lands the same way (same handler, same billing context).
        resp = await browser.post(
            f"/api/v1/resumes/{tailored['resume_id']}/generate-cover-letter",
            json={},
            headers=_browser_headers(browser),
        )
        assert resp.status_code in (500, 502, 503), resp.text
        assert await _wallet(db, user) == wallet0
        rows = await _ledger(db, user)
        assert [r["outcome"] for r in rows] == ["failed", "failed"]
        assert all(r["credits_charged"] == 0 for r in rows)

        # Recovery: the provider heals, the retry is billed exactly once...
        state["fail"] = False
        async with _http(integrity.app) as http:
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

        # ...and saved-copy reuse afterwards is still free.
        async with _http(integrity.app) as http:
            reused = _ok(
                await _acall(
                    http, token, "generate_cover_letter", {"resume_id": tailored["resume_id"]}
                )
            )
        assert reused["content"] == payload["content"]
        assert await _wallet(db, user) == wallet0 - price
        account = await db.get_or_create_credit_account(user)
        assert account["reserved_credits"] == 0
        await browser.aclose()


# ===========================================================================
# Attack 6 - restart mid-flight (in-memory search registry reset)
# ===========================================================================


class TestRestartMidFlight:
    async def test_registry_reset_is_expired_not_corrupt(self, integrity, monkeypatch):
        """A background search is running; the in-memory registry is wiped
        (restart proxy). Every poll must report ``expired`` - never a crash,
        never another user's job - and the user can start a new search
        immediately because the 10s cooldown is in-process state too (a real
        restart forgets it; only the DB-backed daily cap survives)."""
        from app.routers import discovery

        db = integrity.db
        user_a = await _new_user(db, "restart-a@example.com")
        token_a = await _mcp_token_for(user_a, "claude-desktop")
        user_b = await _new_user(db, "restart-b@example.com")
        token_b = await _mcp_token_for(user_b, "cursor")

        state: dict = {"events": [], "loop": None}

        async def gated_work(payload, user_id, db, config, job=None):
            state["loop"] = asyncio.get_running_loop()
            event = asyncio.Event()
            state["events"].append(event)
            await event.wait()
            assert job is not None
            job.saved = 3

        monkeypatch.setattr(discovery, "_execute_manual_search", gated_work)

        async with _http(integrity.app) as http:
            search_a = _ok(
                await _acall(http, token_a, "start_job_search", {"query": "SRE London"})
            )
            assert search_a["status"] == "running"
            search_b = _ok(
                await _acall(http, token_b, "start_job_search", {"query": "DevOps Berlin"})
            )
            assert search_b["status"] == "running"

            # --- the process "restarts" -------------------------------------
            from app.job_discovery import search_jobs

            search_jobs.reset_for_tests()
            discovery._search_timestamps.clear()

            # Old searches read as expired - for their owner...
            expired_a = _ok(
                await _acall(
                    http, token_a, "get_job_search_status", {"search_id": search_a["search_id"]}
                )
            )
            assert expired_a["status"] == "expired"
            expired_b = _ok(
                await _acall(
                    http, token_b, "get_job_search_status", {"search_id": search_b["search_id"]}
                )
            )
            assert expired_b["status"] == "expired"
            # ...and a foreign id leaks nothing (empty expired shape, not B's data).
            foreign = _ok(
                await _acall(
                    http, token_a, "get_job_search_status", {"search_id": search_b["search_id"]}
                )
            )
            assert foreign["status"] == "expired"
            assert foreign.get("query") != "DevOps Berlin"  # no foreign data leak
            assert foreign["saved"] == 0 and foreign["found"] == 0

            # A new search starts IMMEDIATELY (the cooldown was in-process).
            new_search = _ok(
                await _acall(http, token_a, "start_job_search", {"query": "SRE Kubernetes"})
            )
            assert new_search["status"] == "running"
            assert new_search["already_running"] is False
            assert new_search["search_id"] != search_a["search_id"]

            # Release every gated scrape (old, orphaned ones included) and let
            # the new search finish.
            for event in state["events"]:
                state["loop"].call_soon_threadsafe(event.set)
            deadline = time.time() + 5
            done = None
            while time.time() < deadline:
                done = _ok(
                    await _acall(
                        http,
                        token_a,
                        "get_job_search_status",
                        {"search_id": new_search["search_id"]},
                    )
                )
                if done["status"] in ("done", "failed", "expired"):
                    break
                await asyncio.sleep(0.05)
            assert done and done["status"] == "done", done

        # Rate-limit state coherent: only the DB-backed daily cap survives the
        # restart, and each STARTED search (including the interrupted one) is
        # counted exactly once.
        async with db.session_factory() as session:
            counters = (
                await session.execute(
                    select(DailyUsageCounter).where(DailyUsageCounter.user_id == user_a)
                )
            ).scalars().all()
        search_count = sum(
            c.count for c in counters if c.kind == "job_search"
        )
        assert search_count == 2, (
            f"daily search counter after restart should be 2 (interrupted + new), "
            f"got {search_count}"
        )


# ===========================================================================
# Attack 7 - cross-token, same user
# ===========================================================================


class TestCrossTokenSameUser:
    async def test_two_tokens_one_user_full_interference_is_correct(
        self, integrity
    ):
        """Two MCP tokens for the SAME user mutating concurrently.

        Same user = same data: both tokens must see each other's writes
        (that is correct behavior, not interference), and nothing may be lost
        or duplicated by the interleaving.
        """
        db = integrity.db
        user = await _new_user(db, "twin-tokens@example.com")
        token_1 = await _mcp_token_for(user, "claude-desktop")
        token_2 = await _mcp_token_for(user, "cursor")
        resume = await _seed_resume(db, user)

        add_acme = (
            "add_application",
            {
                "job_description": JD_ACME,
                "resume_id": resume["resume_id"],
                "company": "Acme Corp",
                "role": "SRE",
            },
        )
        add_globex = (
            "add_application",
            {
                "job_description": JD_GLOBEX,
                "resume_id": resume["resume_id"],
                "company": "Globex",
                "role": "Platform Engineer",
            },
        )
        async with _http(integrity.app) as http:
            results = await asyncio.gather(
                _acall(http, token_1, *add_acme, _id=1),
                _acall(http, token_2, *add_globex, _id=2),
            )
        ids = []
        for result in results:
            kind, payload = _classify(result)
            assert kind == "ok", payload
            ids.append(payload["application_id"])
        acme_id, globex_id = ids

        applications = await db.list_applications(user)
        assert {c["application_id"] for c in applications} == set(ids)
        await _assert_no_orphans(db, user, applications=applications)
        _assert_columns_contiguous(applications, "cross-token same-user adds")

        # Both tokens see BOTH cards (same user = same board).
        async with _http(integrity.app) as http:
            board_1 = _ok(await _acall(http, token_1, "list_applications", {}))
            board_2 = _ok(await _acall(http, token_2, "list_applications", {}))
        assert board_1["total"] == board_2["total"] == 2
        assert {
            c["application_id"] for c in board_1["columns"]["applied"]
        } == set(ids)
        assert {
            c["application_id"] for c in board_2["columns"]["applied"]
        } == set(ids)

        # Token 2 moves token 1's card; token 1 sees it immediately.
        async with _http(integrity.app) as http:
            moved = _ok(
                await _acall(
                    http,
                    token_2,
                    "update_application_status",
                    {"application_id": acme_id, "status": "interview"},
                )
            )
        assert moved["status"] == "interview"
        async with _http(integrity.app) as http:
            board_1 = _ok(await _acall(http, token_1, "list_applications", {}))
        assert [
            c["application_id"] for c in board_1["columns"]["interview"]
        ] == [acme_id]

        # Token 1 schedules a reminder on token 2's card; token 2 sees it.
        async with _http(integrity.app) as http:
            reminder = _ok(
                await _acall(
                    http,
                    token_1,
                    "create_reminder",
                    {"application_id": globex_id, "remind_at": _future_iso(24)},
                )
            )
        async with _http(integrity.app) as http:
            listed = _ok(
                await _acall(http, token_2, "list_reminders", {"application_id": globex_id})
            )
        assert listed["total"] == 1
        assert listed["reminders"][0]["id"] == reminder["id"]

        rows = await _reminder_rows(db, user)
        assert len(rows) == 1 and rows[0].application_id == globex_id


# ===========================================================================
# Attack 8 - idempotency / retry after a timeout-style error
# ===========================================================================


class TestIdempotencyRetry:
    async def test_retry_after_timeout_error_lands_exactly_one_card(
        self, integrity, monkeypatch
    ):
        """The first add_application dies after the job write with a
        timeout-style error; the AI client retries the same call. Result: the
        failed attempt leaves nothing behind, the retry lands exactly one card,
        and check_duplicate flags it on the next pass."""
        import app.database as database_module

        db = integrity.db
        user = await _new_user(db, "retry@example.com")
        token = await _mcp_token_for(user, "claude-desktop")
        resume = await _seed_resume(db, user)
        args = {
            "job_description": JD_ACME,
            "resume_id": resume["resume_id"],
            "company": "Acme Corp",
            "role": "SRE",
        }

        calls = {"n": 0}
        real = db

        class _FlakyDb(_ProxyDb):
            async def create_application(self, *a, **k):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise TimeoutError("simulated gateway timeout")
                return await real.create_application(*a, **k)

        monkeypatch.setattr(database_module, "db", _FlakyDb(real, fail_methods=set(), fail_exc=None))
        async with _http(integrity.app) as http:
            first = await _acall(http, token, "add_application", args)
        kind, text = _classify(first)
        assert kind == "error", text
        assert "application_create_failed" in text, text

        # The failed attempt left NOTHING: no card, no orphan job.
        assert await db.list_applications(user) == []
        assert await _job_rows(db, user) == []

        # Retry with a healthy DB.
        monkeypatch.setattr(database_module, "db", db)
        async with _http(integrity.app) as http:
            retried = _ok(await _acall(http, token, "add_application", args))
        assert retried["status"] == "applied"

        applications = await db.list_applications(user)
        assert len(applications) == 1, "retry created more than one card"
        assert applications[0]["application_id"] == retried["application_id"]
        assert len(await _job_rows(db, user)) == 1
        await _assert_no_orphans(db, user, applications=applications)

        async with _http(integrity.app) as http:
            dupe = _ok(
                await _acall(
                    http, token, "check_duplicate", {"company": "Acme Corp", "role": "SRE"}
                )
            )
        assert dupe["is_duplicate"] is True
        assert dupe["application"]["application_id"] == retried["application_id"]

    async def test_double_submit_after_success_is_advisory_not_silent(
        self, integrity
    ):
        """A client that re-sends add_application AFTER a successful call gets
        a second card (advisory duplicate semantics - identical to the REST
        manual-add endpoint, which has no (company, role) uniqueness either).
        Coherence requirements: exactly two well-formed cards, two jobs, no
        orphans, contiguous positions, and check_duplicate flags the repeat."""
        db = integrity.db
        user = await _new_user(db, "double-submit@example.com")
        token = await _mcp_token_for(user, "claude-desktop")
        resume = await _seed_resume(db, user)
        args = {
            "job_description": JD_ACME,
            "resume_id": resume["resume_id"],
            "company": "Acme Corp",
            "role": "SRE",
        }

        async with _http(integrity.app) as http:
            first = _ok(await _acall(http, token, "add_application", args))
            second = _ok(await _acall(http, token, "add_application", args))
        assert first["application_id"] != second["application_id"]

        applications = await db.list_applications(user)
        assert len(applications) == 2
        assert len(await _job_rows(db, user)) == 2
        await _assert_no_orphans(db, user, applications=applications)
        _assert_columns_contiguous(applications, "double submit")

        async with _http(integrity.app) as http:
            dupe = _ok(
                await _acall(
                    http, token, "check_duplicate", {"company": "acme corp", "role": "sre"}
                )
            )
        assert dupe["is_duplicate"] is True  # the advisory signal an AI client needs

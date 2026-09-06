"""MCP job-search tools (Task 8): start a search, poll its progress.

Same harness as the other MCP tool suites: one ``tools/call`` JSON-RPC POST
against the real mounted FastMCP app (bearer ``fw_`` token), over the isolated
temp DB. The scrape itself (15-35s of real board traffic) is NEVER run here:
``_execute_manual_search`` is monkeypatched with gated fakes, the same seam
``tests/test_background_search.py`` uses, so the tests exercise the start/
status contract without touching the network.

REST parity is the point of this suite: the tool must reuse
``start_manual_search`` / ``manual_search_progress`` verbatim (order of the
rate check, the one-at-a-time check, the daily cap - already_running must NOT
burn a cap charge), not reimplement them.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.models import User
from tests.integration.conftest import (
    mcp_call as _call,
    mcp_error_text as _error_text,
    mcp_ok as _ok,
    mcp_tools_list as _tools_list,
)

pytestmark = pytest.mark.integration

SEARCH_TOOLS = {"start_job_search", "get_job_search_status"}


@pytest.fixture(autouse=True)
def _clean_search_state():
    """Blank slate per test: no in-flight jobs, no rate-limit timestamps.

    ``search_jobs`` state is process-global and the 10s cooldown timestamps
    live in a module dict - without this, one test's search would 429 the
    next test's.
    """
    from app.job_discovery import search_jobs
    from app.routers import discovery

    search_jobs.reset_for_tests()
    discovery._search_timestamps.clear()
    yield
    search_jobs.reset_for_tests()
    discovery._search_timestamps.clear()


@pytest.fixture
async def mcp_client(auth_env, mcp_app, mcp_token, isolated_db, monkeypatch):
    """A live MCP-mounted TestClient plus ``(client, owner_token, db)``.

    JOB_DISCOVERY is flipped ON here (it ships OFF) because these tests
    exercise the happy path of the search tools; the kill-switch regression
    lives in ``TestKillSwitch`` and flips it back off.
    """
    from app.config import settings as app_settings

    monkeypatch.setattr(app_settings, "JOB_DISCOVERY", True)
    app = mcp_app(True)
    with TestClient(app) as client:
        yield client, mcp_token["raw"]


async def _seed_user(db, email: str) -> str:
    uid = str(uuid4())
    async with db.session_factory() as session:
        session.add(User(id=uid, email=email, name="U", role="user", status="active"))
        await session.commit()
    return uid


async def _other_users_token(db, owner_id: str) -> str:
    """A second, unrelated user's MCP bearer token (cross-user boundary)."""
    from app.auth.mcp_tokens import get_mcp_token_service

    user_b = await _seed_user(db, "b@example.com")
    _, raw_b = await get_mcp_token_service().issue(user_b, "b-client")
    return raw_b


def _gate_the_scrape(monkeypatch) -> dict:
    """Replace the scrape with work that blocks until the test releases it.

    The fake runs on the app's event loop (the TestClient portal thread), so
    the Event is created there and released from the test thread via
    ``call_soon_threadsafe`` - the same blocking-work shape
    ``test_background_search.py`` uses with ``asyncio.Event``.
    """
    from app.routers import discovery

    state: dict = {}

    async def fake_work(payload, user_id, db, config, job=None):
        state["loop"] = asyncio.get_running_loop()
        state["event"] = asyncio.Event()
        await state["event"].wait()
        assert job is not None
        job.saved = 3

    monkeypatch.setattr(discovery, "_execute_manual_search", fake_work)
    return state


def _release(state: dict) -> None:
    state["loop"].call_soon_threadsafe(state["event"].set)


def _finish_the_scrape(monkeypatch, saved: int = 3) -> None:
    """Replace the scrape with work that completes after one loop tick."""
    from app.routers import discovery

    async def fake_work(payload, user_id, db, config, job=None):
        await asyncio.sleep(0)
        assert job is not None
        job.saved = saved

    monkeypatch.setattr(discovery, "_execute_manual_search", fake_work)


def _poll_until_done(client: TestClient, token: str, search_id: str) -> dict:
    """Poll the status tool until the (fake) work reports done, 5s ceiling."""
    state: dict = {}
    deadline = time.time() + 5
    while time.time() < deadline:
        state = _ok(_call(client, token, "get_job_search_status", {"search_id": search_id}))
        if state["status"] == "done":
            return state
        time.sleep(0.05)
    pytest.fail(f"search never finished; last state: {state}")


async def _searches_used_today(db, user_id: str) -> int:
    kind = "job_search"
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return await db.get_daily_usage(user_id, kind=kind, day=day)


# ---------------------------------------------------------------------------
# 1. start: immediate return, running -> done
# ---------------------------------------------------------------------------


class TestStartJobSearch:
    async def test_start_returns_immediately_with_search_id(
        self, mcp_client, isolated_db, owner_id, monkeypatch
    ):
        client, token = mcp_client
        state = _gate_the_scrape(monkeypatch)

        began = time.monotonic()
        result = _ok(_call(client, token, "start_job_search", {"query": "Backend Engineer Python"}))
        elapsed = time.monotonic() - began

        # The whole point: milliseconds, never the 15-35s scrape.
        assert elapsed < 5.0
        assert result["search_id"]
        assert result["status"] == "running"
        assert result["already_running"] is False

        # The work really is in flight, and the tool never blocked on it.
        progress = _ok(
            _call(client, token, "get_job_search_status", {"search_id": result["search_id"]})
        )
        assert progress["status"] == "running"

        _release(state)
        done = _poll_until_done(client, token, result["search_id"])
        assert done["saved"] == 3

    async def test_status_reports_running_then_done(
        self, mcp_client, isolated_db, owner_id, monkeypatch
    ):
        client, token = mcp_client
        _finish_the_scrape(monkeypatch)

        result = _ok(_call(client, token, "start_job_search", {"query": "SRE", "sites": ["indeed"]}))
        assert result["sites"] == ["indeed"]

        done = _poll_until_done(client, token, result["search_id"])
        assert done["status"] == "done"
        assert done["saved"] == 3

    async def test_blank_query_is_rejected_before_any_work(
        self, mcp_client, isolated_db, owner_id
    ):
        client, token = mcp_client
        message = _error_text(_call(client, token, "start_job_search", {"query": "   "}))
        # FastMCP prefixes the message; the machine code is what must travel.
        assert "invalid_argument: query" in message

    async def test_oversized_query_is_rejected_before_any_work(
        self, mcp_client, isolated_db, owner_id
    ):
        """Hardening F9 (T10 M1): the shared 256-char bound (ManualSearchRequest)
        must refuse a hostile 1MB query BEFORE any work - a boundless query
        used to echo back through the error path at ~2x."""
        from app.job_discovery import search_jobs
        from app.routers import discovery

        client, token = mcp_client
        message = _error_text(
            _call(client, token, "start_job_search", {"query": "x" * 1_048_576})
        )
        assert "invalid_argument: query" in message
        assert "256" in message

        # Refused before any guard ran or any work was registered.
        assert search_jobs.running_for(owner_id) is None
        assert discovery._search_timestamps == {}

    async def test_query_at_exactly_256_chars_is_accepted(
        self, mcp_client, isolated_db, owner_id, monkeypatch
    ):
        """The bound is inclusive: a real (if long) search term still starts."""
        client, token = mcp_client
        _finish_the_scrape(monkeypatch)

        result = _ok(
            _call(client, token, "start_job_search", {"query": "e" * 256})
        )
        assert result["status"] == "running"

    async def test_sites_default_to_the_configured_boards_when_omitted(
        self, mcp_client, isolated_db, owner_id, monkeypatch
    ):
        client, token = mcp_client
        _finish_the_scrape(monkeypatch)

        result = _ok(_call(client, token, "start_job_search", {"query": "SRE"}))
        assert result["sites"]  # config.job_discovery_jobspy_sites, non-empty


# ---------------------------------------------------------------------------
# 2. single-flight + the two refusals
# ---------------------------------------------------------------------------


class TestSingleFlightAndLimits:
    async def test_second_concurrent_start_returns_already_running(
        self, mcp_client, isolated_db, owner_id, monkeypatch
    ):
        """One search at a time - and the duplicate must NOT burn a cap charge."""
        from app.routers import discovery

        client, token = mcp_client
        _gate_the_scrape(monkeypatch)

        first = _ok(_call(client, token, "start_job_search", {"query": "python"}))
        assert first["already_running"] is False
        used_after_first = await _searches_used_today(isolated_db, owner_id)
        # The first start DID charge the cap (guards ran, the search started) -
        # pinned so a skip-the-cap-entirely regression can't pass this test.
        assert used_after_first == 1

        # The 10s cooldown is a separate, EARLIER guard than single-flight;
        # simulating "10s later" isolates the one-at-a-time rule under test.
        discovery._search_timestamps.clear()

        second = _ok(_call(client, token, "start_job_search", {"query": "python"}))
        assert second["already_running"] is True
        assert second["search_id"] == first["search_id"]

        # REST parity: the cap is charged only when a search actually starts.
        used_after_second = await _searches_used_today(isolated_db, owner_id)
        assert used_after_second == used_after_first

    async def test_search_rate_limit_is_respected(
        self, mcp_client, isolated_db, owner_id, monkeypatch
    ):
        """The 10s cooldown fires BEFORE the one-at-a-time check, like REST."""
        from app.job_discovery import search_jobs

        client, token = mcp_client
        _finish_the_scrape(monkeypatch)

        first = _ok(_call(client, token, "start_job_search", {"query": "python"}))
        message = _error_text(_call(client, token, "start_job_search", {"query": "react"}))
        assert "http_429" in message
        assert "wait" in message.lower()

        # Refused before any work: still exactly one job for this user.
        assert search_jobs.running_for(owner_id) is None

    async def test_daily_search_cap_is_respected(
        self, mcp_client, isolated_db, owner_id, monkeypatch
    ):
        """Today's searches used up -> refusal names the plan ceiling, not credits."""
        from app.ai_plans import SearchAllowance, consume_search, resolve_account_plan

        client, token = mcp_client
        _finish_the_scrape(monkeypatch)

        account = await isolated_db.get_or_create_credit_account(owner_id)
        plan = await resolve_account_plan(isolated_db, account)
        assert plan.search_daily_limit is not None  # uncapped plans can't be tested this way
        allowance = SearchAllowance(
            allowed=True, used=0, limit=plan.search_daily_limit, plan_label=plan.label
        )
        for _ in range(plan.search_daily_limit + 1):
            allowance = await consume_search(isolated_db, owner_id, plan)
            if not allowance.allowed:
                break
        assert allowance.allowed is False

        message = _error_text(_call(client, token, "start_job_search", {"query": "python"}))
        assert "search_limit_reached" in message

        # The refusal explains the reset, and does not sell credits: searching
        # costs no credits on purpose (the error text is REST's, verbatim).
        assert "midnight UTC" in message


# ---------------------------------------------------------------------------
# 3. ownership
# ---------------------------------------------------------------------------


class TestKillSwitch:
    async def test_job_discovery_disabled_refuses_both_tools_without_side_effects(
        self, mcp_client, isolated_db, owner_id, monkeypatch
    ):
        """The JOB_DISCOVERY kill-switch gates the tool surface too.

        On REST the gate is a router-level dependency, so calling the handlers
        directly (as the tools do) bypasses it - without this check, a
        deployment with the feature off would still run real board scrapes
        through MCP. The refusal must come before ANY work: no search job, no
        rate-limit timestamp, no cap charge.
        """
        from app.config import settings as app_settings
        from app.job_discovery import search_jobs
        from app.routers import discovery

        client, token = mcp_client
        monkeypatch.setattr(app_settings, "JOB_DISCOVERY", False)  # ships off

        message = _error_text(
            _call(client, token, "start_job_search", {"query": "python"})
        )
        assert "job_discovery_disabled" in message

        # Refused before any guard ran or any work was registered.
        assert search_jobs.running_for(owner_id) is None
        assert discovery._search_timestamps == {}
        assert await _searches_used_today(isolated_db, owner_id) == 0

        # The status tool is behind the same gate.
        status_message = _error_text(
            _call(client, token, "get_job_search_status", {"search_id": "any"})
        )
        assert "job_discovery_disabled" in status_message


class TestSearchOwnership:
    async def test_cross_user_status_check_yields_nothing(
        self, mcp_client, isolated_db, owner_id, monkeypatch
    ):
        """User B polling user A's search_id learns nothing: expired, not A's data."""
        client, token_a = mcp_client
        _finish_the_scrape(monkeypatch)

        started = _ok(_call(client, token_a, "start_job_search", {"query": "python"}))
        _poll_until_done(client, token_a, started["search_id"])

        token_b = await _other_users_token(isolated_db, owner_id)
        leaked = _ok(
            _call(client, token_b, "get_job_search_status", {"search_id": started["search_id"]})
        )
        assert leaked["status"] == "expired"
        assert leaked.get("saved", 0) == 0
        assert leaked.get("query") in (None, "")

    async def test_unknown_search_id_is_expired_not_an_error(
        self, mcp_client, isolated_db, owner_id
    ):
        client, token = mcp_client
        state = _ok(
            _call(client, token, "get_job_search_status", {"search_id": "never-existed"})
        )
        assert state["status"] == "expired"


# ---------------------------------------------------------------------------
# 4. registration
# ---------------------------------------------------------------------------


class TestToolRegistration:
    async def test_both_search_tools_are_listed(self, mcp_client):
        client, token = mcp_client
        tools = {
            tool["name"]
            for tool in _tools_list(client, token)["result"]["tools"]
        }
        assert SEARCH_TOOLS <= set(tools), f"missing: {SEARCH_TOOLS - set(tools)}"

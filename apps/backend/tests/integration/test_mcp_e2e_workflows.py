"""MCP end-to-end workflow tests: the integration as a REAL AI client uses it.

Not tool-by-tool unit coverage (that lives in ``test_mcp_tools_*``) - whole
journeys, driven the way Claude Desktop / Cursor would drive them: discover
tools, thread ids from one call to the next, poll a background search, recover
from plausible mistakes. Only externals are faked (the LLM provider and the
job-board scrape, at the same seams the other suites use); every FitWright
seam - routing, guards, billing, scheduling, DB - is the real one.

Cross-verification is the spine of the suite: every write an MCP client makes
must be visible through the REST API the browser uses (real session + CSRF,
no dependency overrides), and vice versa - one database, zero divergence.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from app.models import CreditAccount, User
from tests.integration.conftest import (
    mcp_call as _call,
    mcp_error_text as _error_text,
    mcp_ok as _ok,
    mcp_tools_list as _tools_list,
)

pytestmark = pytest.mark.integration

PASSWORD = "correct-horse-battery-staple-9"

#: Field names that must never appear in an MCP tool payload: they are
#: REST/database internals (identity plumbing, secrets), not caller data.
_INTERNAL_KEYS = {"user_id", "token_hash", "csrf_secret", "password_hash"}


# ---------------------------------------------------------------------------
# Realistic fixtures data (multi-line, unicode, emoji - the shape real users
# paste, not ASCII one-liners).
# ---------------------------------------------------------------------------

MASTER_MARKDOWN = """# Jane Okafor
**Senior Site Reliability Engineer** — London, UK 🇬🇧
jane.okafor@example.com · +44 7700 900123 · github.com/janeokafor

## Experience
### Lead SRE — Northwind Traders (2021–present)
- Run 14 production Kubernetes clusters (EKS) serving 40M req/day
- Cut incident MTTR 62% by rebuilding the on-call rotation and runbooks
- Led the Terraform migration of 300+ cloud resources 🛠️

### Senior Platform Engineer — Initech (2017–2021)
- Built the internal deployment platform (Python, Go, ArgoCD)
- Owned observability: Prometheus, Grafana, OpenTelemetry rollout

## Skills
Kubernetes · Python · Go · Terraform · AWS · PostgreSQL · SRE practices
"""

TAILORED_MARKDOWN = """# Jane Okafor
**Senior Site Reliability Engineer (Kubernetes)** — London, UK

Tailored for {company}: platform reliability at scale, Kubernetes depth,
and a track record of turning on-call pain into calm dashboards.
"""

JD_NORTHWIND = """Senior Site Reliability Engineer (Kubernetes) 🚀 — Northwind Traders, London (hybrid)

About the role
We keep a 40M-requests/day platform boring. You'll own reliability for our
Kubernetes estate, drive incident practice, and make observability a first
class product for every engineer.

What you'll do
• Run and evolve multi-cluster EKS in production
• Own SLOs for the checkout and search paths
• Automate toil away with Python and Go

Requirements
• 5+ years running Kubernetes in production
• Deep Prometheus / Grafana / OpenTelemetry experience
• Strong Python; comfortable reading Go
• Calm under incident pressure — you've carried a pager seriously

Nice to have
• Terraform at scale · ArgoCD · AWS certifications
• Experience with cost optimisation of large clusters

Salary: £95k–£120k + equity. We sponsor visas. 🌍
"""

JD_GLOBEX = """Platform Reliability Engineer — Globex Corporation (Remote, EU)

Globex builds logistics software moving 2M packages a day. The platform
team keeps it up; you'll join as a senior reliability engineer.

Responsibilities
- Operate Kubernetes (AKS) and the CI/CD spine
- Define and defend SLOs with product teams
- Take part in a humane on-call rotation (1 week in 6)

Requirements
- Kubernetes in production for 3+ years
- Python or Go for automation
- Fluent written English; French is a plus 🇫🇷
"""

PROCESSED_DATA = {
    "personalInfo": {
        "name": "Jane Okafor",
        "title": "Senior Site Reliability Engineer",
        "location": "London, UK",
    },
    "skills": ["kubernetes", "python", "go", "terraform", "aws", "prometheus"],
    "summary": "SRE with 8 years running Kubernetes in production.",
}

COVER_LETTER_TEXT = (
    "Dear Northwind Traders team,\n\n"
    "Your 40M-requests-per-day platform is exactly the scale I love keeping "
    "boring: at Northwind— no wait, at Initech and since 2021 at my current "
    "role — I've run 14 EKS clusters and cut incident MTTR by 62% 🚀.\n\n"
    "I'd welcome a conversation.\n\nJane Okafor"
)


# ---------------------------------------------------------------------------
# LLM + scrape mocking (same seams as test_mcp_tools_ai / _search)
# ---------------------------------------------------------------------------


@pytest.fixture
def credits_on(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "ai_credits_enabled", True)
    monkeypatch.setattr("app.ai_metered.user_has_own_key", lambda _uid: False)
    return settings


@pytest.fixture
def llm_mocked(monkeypatch):
    from app.routers import resumes as resumes_router
    from app.schemas.models import InterviewPrepData

    cover_letter = AsyncMockLike(COVER_LETTER_TEXT)
    prep_payload = {
        "role_fit_analysis": [
            "Eight years of Kubernetes in production matches the core requirement",
        ],
        "resume_questions": [
            {
                "question": "Walk me through cutting incident MTTR by 62% 📉",
                "focus_area": "incident management",
                "suggested_answer_points": [
                    "Rebuilt the on-call rotation",
                    "Runbook-first incident practice",
                ],
            }
        ],
        "project_follow_ups": [],
        "skill_gaps": [
            {
                "skill": "ArgoCD",
                "why_it_matters": "The JD lists the CI/CD spine as core",
                "preparation_suggestion": "Demo a small app deploy",
            }
        ],
        "talking_points": [
            "Multi-cluster EKS at 40M req/day scale",
            "SLO ownership for checkout and search",
        ],
    }
    interview_prep = AsyncMockLike(InterviewPrepData.model_validate(prep_payload))

    def _no_real_provider(*args, **kwargs):
        raise AssertionError("real LLM provider call during test")

    monkeypatch.setattr(resumes_router, "generate_cover_letter", cover_letter)
    monkeypatch.setattr(resumes_router, "generate_interview_prep", interview_prep)
    monkeypatch.setattr("app.llm.litellm.acompletion", _no_real_provider)
    return {"cover_letter": cover_letter, "interview_prep": interview_prep, "payload": prep_payload}


class AsyncMockLike:
    """Minimal AsyncMock stand-in (avoids the unittest import in this suite)."""

    def __init__(self, return_value):
        self.return_value = return_value
        self.await_count = 0

    async def __call__(self, *args, **kwargs):
        self.await_count += 1
        return self.return_value

    def assert_not_awaited(self):
        assert self.await_count == 0, f"awaited {self.await_count} times"

    def assert_awaited_once(self):
        assert self.await_count == 1, f"awaited {self.await_count} times"


@pytest.fixture(autouse=True)
def _clean_search_state():
    from app.job_discovery import search_jobs
    from app.routers import discovery

    search_jobs.reset_for_tests()
    discovery._search_timestamps.clear()
    yield
    search_jobs.reset_for_tests()
    discovery._search_timestamps.clear()


def _finish_the_scrape(monkeypatch, saved: int = 3) -> None:
    from app.routers import discovery

    async def fake_work(payload, user_id, db, config, job=None):
        await asyncio.sleep(0)
        assert job is not None
        job.saved = saved

    monkeypatch.setattr(discovery, "_execute_manual_search", fake_work)


def _gate_the_scrape(monkeypatch) -> dict:
    """Block the scrape until released - supports CONCURRENT searches (each
    in-flight job gets its own Event; release() opens them all)."""
    from app.routers import discovery

    state: dict = {"events": [], "loop": None}

    async def fake_work(payload, user_id, db, config, job=None):
        state["loop"] = asyncio.get_running_loop()
        event = asyncio.Event()
        state["events"].append(event)
        await event.wait()
        assert job is not None
        job.saved = 3

    monkeypatch.setattr(discovery, "_execute_manual_search", fake_work)
    return state


def _release(state: dict) -> None:
    for event in state["events"]:
        state["loop"].call_soon_threadsafe(event.set)


def _poll_until_done(client: TestClient, token: str, search_id: str) -> dict:
    state: dict = {}
    deadline = time.time() + 5
    while time.time() < deadline:
        state = _ok(_call(client, token, "get_job_search_status", {"search_id": search_id}))
        if state["status"] == "done":
            return state
        time.sleep(0.05)
    pytest.fail(f"search never finished; last state: {state}")


# ---------------------------------------------------------------------------
# Harness: one mounted app, MCP via the sync client (bearer), REST via real
# session-authenticated browser clients (httpx, https base so Secure
# __Host- cookies travel).
# ---------------------------------------------------------------------------


@pytest.fixture
async def e2e(auth_env, mcp_app, mcp_token, isolated_db, owner_id, monkeypatch):
    """The whole FitWright app with the MCP mount on, over the isolated DB.

    JOB_DISCOVERY is flipped on for the search legs of the journeys (it ships
    off; the kill-switch regression lives in test_mcp_tools_search).
    """
    from app.applications import submissions
    from app.config import settings as app_settings

    monkeypatch.setattr(submissions, "db", isolated_db)
    monkeypatch.setattr(app_settings, "JOB_DISCOVERY", True)
    app = mcp_app(True)
    with TestClient(app) as mcp:
        yield SimpleNamespace(
            app=app,
            mcp=mcp,
            db=isolated_db,
            owner_id=owner_id,
            owner_token=mcp_token["raw"],
        )


def _browser(app) -> AsyncClient:
    # https base_url so the httpx cookie jar stores/returns Secure cookies.
    return AsyncClient(transport=ASGITransport(app=app), base_url="https://test")


async def _new_user(db, email: str, name: str = "Journeys User") -> str:
    from app.auth.accounts import create_user
    from app.auth.passwords import get_password_service

    record = await create_user(
        email=email,
        name=name,
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


async def _login_browser(app, email: str) -> AsyncClient:
    """A browser client with a REAL session (login endpoint + cookies)."""
    client = _browser(app)
    csrf = (await client.get("/api/v1/auth/csrf")).json()["csrfToken"]
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": PASSWORD},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 200, resp.text
    return client


async def _browser_post(client: AsyncClient, path: str, body: dict) -> dict:
    """A browser write: session cookie + the matching CSRF header."""
    csrf = client.cookies.get("csrf")
    resp = await client.post(path, json=body, headers={"X-CSRF-Token": csrf})
    assert resp.status_code in (200, 201), f"{path} -> {resp.status_code}: {resp.text}"
    return resp.json()


async def _browser_get(client: AsyncClient, path: str) -> dict:
    resp = await client.get(path)
    assert resp.status_code == 200, f"{path} -> {resp.status_code}: {resp.text}"
    return resp.json()


# ---------------------------------------------------------------------------
# Seeding + assertions helpers
# ---------------------------------------------------------------------------


async def _fund(db, user_id: str, *, allowance: int = 0, wallet: int = 0):
    await db.get_or_create_credit_account(user_id)
    async with db.session_factory() as session:
        row = await session.get(CreditAccount, user_id)
        row.allowance_credits = allowance
        row.wallet_credits = wallet
        row.allowance_period_start = datetime.now(timezone.utc).isoformat()
        await session.commit()


async def _price(db, feature: str) -> int:
    from app.ai_feature_prices import resolve_feature_cost

    return (await resolve_feature_cost(db, feature)).effective_credits


async def _seed_workspace(db, user_id: str) -> SimpleNamespace:
    """Master + two tailored resumes (realistic content), as a real user has."""
    master = await db.create_resume(
        user_id,
        content=MASTER_MARKDOWN,
        filename="jane-okafor-master.md",
        is_master=True,
        processing_status="ready",
        title="Master — Jane Okafor",
    )
    tailored = []
    for company, jd, title in (
        ("Northwind Traders", JD_NORTHWIND, "Tailored — Northwind SRE"),
        ("Globex Corporation", JD_GLOBEX, "Tailored — Globex Reliability"),
    ):
        job = await db.create_job(user_id, content=jd)
        resume = await db.create_resume(
            user_id,
            content=TAILORED_MARKDOWN.format(company=company),
            filename=f"tailored-{company.split()[0].lower()}.md",
            parent_id=master["resume_id"],
            processed_data=PROCESSED_DATA,
            processing_status="ready",
            title=title,
        )
        await db.create_improvement(
            user_id,
            original_resume_id=master["resume_id"],
            tailored_resume_id=resume["resume_id"],
            job_id=job["job_id"],
            improvements=[],
        )
        tailored.append(resume)
    return SimpleNamespace(master=master, tailored=tailored)


def _assert_no_internal_leakage(payload, path: str = "payload") -> None:
    """No REST/DB internals (user ids, secrets) travel to the AI client."""
    if isinstance(payload, dict):
        for key, value in payload.items():
            assert key not in _INTERNAL_KEYS, f"{path}.{key} leaked to the MCP caller"
            _assert_no_internal_leakage(value, f"{path}.{key}")
    elif isinstance(payload, list):
        for index, item in enumerate(payload):
            _assert_no_internal_leakage(item, f"{path}[{index}]")


def _future_iso(hours: int = 48) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


async def _ledger(db, user_id: str) -> list[dict]:
    return await db.list_usage(user_id, limit=100)


async def _wallet(db, user_id: str) -> int:
    account = await db.get_or_create_credit_account(user_id)
    return account["wallet_credits"]


# ---------------------------------------------------------------------------
# Workflow 1 — the full user journey, one AI conversation
# ---------------------------------------------------------------------------


class TestFullUserJourney:
    async def test_discover_to_reminder_end_to_end(
        self, e2e, credits_on, llm_mocked, monkeypatch
    ):
        db, mcp = e2e.db, e2e.mcp

        user = await _new_user(db, "jane@example.com", "Jane Okafor")
        token = await _mcp_token_for(user, "claude-desktop")
        workspace = await _seed_workspace(db, user)
        _finish_the_scrape(monkeypatch, saved=4)

        # 1. Discover tools - what every MCP client does first.
        listing = _tools_list(mcp, token)["result"]["tools"]
        tool_names = {t["name"] for t in listing}
        assert {
            "list_resumes", "get_resume", "start_job_search",
            "get_job_search_status", "check_duplicate", "add_application",
            "create_reminder", "update_application_status",
            "list_applications", "list_reminders",
        } <= tool_names
        for tool in listing:
            assert "inputSchema" in tool, tool["name"]
            assert "token" not in json.dumps(tool["inputSchema"])

        # 2. List resumes: two tailored ones (the master is excluded by design).
        resumes = _ok(_call(mcp, token, "list_resumes", {}))
        _assert_no_internal_leakage(resumes)
        assert {r["resume_id"] for r in resumes["resumes"]} == {
            t["resume_id"] for t in workspace.tailored
        }

        # 3. Get the Northwind-tailored resume in full.
        northwind = next(
            t for t in workspace.tailored
            if "Northwind" in (t.get("title") or "")
        )
        detail = _ok(_call(mcp, token, "get_resume", {"resume_id": northwind["resume_id"]}))
        _assert_no_internal_leakage(detail)
        assert detail["content"] == TAILORED_MARKDOWN.format(company="Northwind Traders")
        assert detail["processed_data"]["skills"][0] == "kubernetes"

        # 4. Job search: start, poll to done (scrape mocked).
        search = _ok(_call(mcp, token, "start_job_search", {"query": "Senior SRE Kubernetes London"}))
        assert search["status"] == "running"
        assert search["already_running"] is False
        done = _poll_until_done(mcp, token, search["search_id"])
        _assert_no_internal_leakage(done)
        assert done["status"] == "done"
        assert done["saved"] == 4

        # 5. Duplicate check before tracking (advisory, no match yet).
        dupe = _ok(_call(mcp, token, "check_duplicate", {"company": "Northwind Traders", "role": "Senior Site Reliability Engineer"}))
        assert dupe == {"is_duplicate": False, "application": None}

        # 6. Add the application from the pasted JD.
        added = _ok(
            _call(
                mcp, token, "add_application",
                {
                    "job_description": JD_NORTHWIND,
                    "resume_id": northwind["resume_id"],
                    "company": "Northwind Traders",
                    "role": "Senior Site Reliability Engineer",
                },
            )
        )
        _assert_no_internal_leakage(added)
        application_id = added["application_id"]
        assert added["status"] == "applied"

        # 7. Reminder to follow up.
        reminder = _ok(
            _call(
                mcp, token, "create_reminder",
                {
                    "application_id": application_id,
                    "remind_at": _future_iso(72),
                    "note": "Follow up with Dana (recruiter) if no reply 📮",
                },
            )
        )
        _assert_no_internal_leakage(reminder)
        assert reminder["application_id"] == application_id
        assert reminder["status"] == "pending"

        # 8. Status moves (company replied).
        moved = _ok(
            _call(
                mcp, token, "update_application_status",
                {"application_id": application_id, "status": "interview"},
            )
        )
        assert moved["status"] == "interview"

        # 9. The board reflects everything.
        board = _ok(_call(mcp, token, "list_applications", {}))
        _assert_no_internal_leakage(board)
        assert board["total"] == 1
        assert [c["application_id"] for c in board["columns"]["interview"]] == [application_id]
        assert all(board["columns"][s] == [] for s in ("saved", "applied", "rejected"))

        # Duplicate check now finds the live card.
        dupe_after = _ok(_call(mcp, token, "check_duplicate", {"company": "northwind traders", "role": "senior site reliability engineer"}))
        assert dupe_after["is_duplicate"] is True
        assert dupe_after["application"]["application_id"] == application_id

        # 10. The reminder is listed with the note.
        reminders = _ok(_call(mcp, token, "list_reminders", {"application_id": application_id}))
        _assert_no_internal_leakage(reminders)
        assert reminders["total"] == 1
        assert reminders["reminders"][0]["id"] == reminder["id"]
        assert "Dana" in reminders["reminders"][0]["note"]

        # AI generation never ran in this workflow - the journey above is free.
        llm_mocked["cover_letter"].assert_not_awaited()
        llm_mocked["interview_prep"].assert_not_awaited()
        assert await _ledger(db, user) == []


# ---------------------------------------------------------------------------
# Workflow 2 — the AI journey, verified through both channels
# ---------------------------------------------------------------------------


class TestAiJourney:
    async def test_generate_verify_via_mcp_and_rest_same_truth(
        self, e2e, credits_on, llm_mocked
    ):
        db, mcp, app = e2e.db, e2e.mcp, e2e.app
        user = await _new_user(db, "ai-jane@example.com")
        token = await _mcp_token_for(user, "cursor")
        browser = await _login_browser(app, "ai-jane@example.com")
        workspace = await _seed_workspace(db, user)
        resume_id = workspace.tailored[0]["resume_id"]
        price_cl = await _price(db, "cover_letter")
        price_ip = await _price(db, "interview_prep")
        # Buffer on purpose: reuse-without-regenerate passes the same balance
        # guard the REST route has BEFORE the handler returns the saved copy,
        # so the wallet must be non-zero for a reuse call to be served (a
        # zero-balance reuse is refused identically on both channels).
        await _fund(db, user, wallet=price_cl + price_ip + 100)

        # (a) Cover letter via MCP...
        letter = _ok(_call(mcp, token, "generate_cover_letter", {"resume_id": resume_id}))
        assert letter["content"] == COVER_LETTER_TEXT

        # ...and via the REST endpoints the browser uses: same content, same ledger.
        rest_resume = await _browser_get(browser, f"/api/v1/resumes?resume_id={resume_id}")
        assert rest_resume["data"]["cover_letter"] == COVER_LETTER_TEXT
        rest_usage = await _browser_get(browser, "/api/v1/credits/usage?limit=100")
        assert rest_usage["items"][0]["feature"] == "cover_letter"
        assert rest_usage["items"][0]["credits_charged"] == price_cl
        assert rest_usage["items"][0]["outcome"] == "ok"

        # (b) Interview prep via MCP...
        prep = _ok(_call(mcp, token, "generate_interview_prep", {"resume_id": resume_id}))
        assert prep["interview_prep"]["talking_points"] == llm_mocked["payload"]["talking_points"]

        # ...verified via REST: same prep object, same ledger row.
        rest_resume = await _browser_get(browser, f"/api/v1/resumes?resume_id={resume_id}")
        assert rest_resume["data"]["interview_prep"] == prep["interview_prep"]
        rest_usage = await _browser_get(browser, "/api/v1/credits/usage?limit=100")
        features = [(i["feature"], i["credits_charged"]) for i in rest_usage["items"]]
        assert ("interview_prep", price_ip) in features
        assert ("cover_letter", price_cl) in features

        # The browser-visible ledger and the DB ledger are the same rows.
        rows = await _ledger(db, user)
        assert [(r["feature"], r["credits_charged"]) for r in rows] == [
            ("interview_prep", price_ip),
            ("cover_letter", price_cl),
        ]

        # (c) Reuse without regenerate: same content, no provider call, no
        # charge - on BOTH channels (the browser's generate endpoint and the
        # MCP tool run the same handler under the same billing context).
        wallet_before = await _wallet(db, user)
        charged_before = [r for r in rows if r["credits_charged"] > 0]
        reused_letter = _ok(_call(mcp, token, "generate_cover_letter", {"resume_id": resume_id}))
        reused_prep = _ok(_call(mcp, token, "generate_interview_prep", {"resume_id": resume_id}))
        assert reused_letter["content"] == COVER_LETTER_TEXT
        assert reused_prep["interview_prep"] == prep["interview_prep"]
        assert llm_mocked["cover_letter"].await_count == 1
        assert llm_mocked["interview_prep"].await_count == 1
        assert await _wallet(db, user) == wallet_before  # no double charge

        rows_after = await _ledger(db, user)
        assert [r for r in rows_after if r["credits_charged"] > 0] == charged_before
        # The free passes are provable, not silent: one zero-charge row each.
        free_rows = [(r["feature"], r["credits_charged"], r["outcome"]) for r in rows_after if r["credits_charged"] == 0]
        assert ("cover_letter", 0, "ok") in free_rows
        assert ("interview_prep", 0, "ok") in free_rows

        # REST reuse is free too: the browser POSTing the generate endpoint
        # against the saved copy settles nothing.
        rest_reuse = await _browser_post(
            browser,
            f"/api/v1/resumes/{resume_id}/generate-cover-letter",
            {},
        )
        assert rest_reuse["content"] == COVER_LETTER_TEXT
        assert await _wallet(db, user) == wallet_before
        assert llm_mocked["cover_letter"].await_count == 1


# ---------------------------------------------------------------------------
# Workflow 3 — cross-verification: MCP writes <-> REST reads, REST writes <->
# MCP reads. One DB, zero divergence.
# ---------------------------------------------------------------------------


class TestCrossVerification:
    async def test_every_mcp_write_is_visible_to_the_browser(
        self, e2e, credits_on
    ):
        db, mcp, app = e2e.db, e2e.mcp, e2e.app
        user = await _new_user(db, "browser@example.com")
        token = await _mcp_token_for(user, "claude-desktop")
        browser = await _login_browser(app, "browser@example.com")
        workspace = await _seed_workspace(db, user)
        resume_id = workspace.tailored[0]["resume_id"]

        # MCP: add a card.
        added = _ok(
            _call(
                mcp, token, "add_application",
                {
                    "job_description": JD_GLOBEX,
                    "resume_id": resume_id,
                    "company": "Globex Corporation",
                    "role": "Platform Reliability Engineer",
                },
            )
        )
        application_id = added["application_id"]

        # Browser: the board and the card detail show it.
        board = await _browser_get(browser, "/api/v1/applications")
        ids = [c["application_id"] for cards in board["columns"].values() for c in cards]
        assert ids == [application_id]
        detail = await _browser_get(browser, f"/api/v1/applications/{application_id}")
        assert detail["company"] == "Globex Corporation"
        assert detail["status"] == "applied"
        assert detail["job_content"] == JD_GLOBEX

        # MCP: move status, add a reminder.
        _ok(
            _call(
                mcp, token, "update_application_status",
                {"application_id": application_id, "status": "response"},
            )
        )
        _ok(
            _call(
                mcp, token, "create_reminder",
                {
                    "application_id": application_id,
                    "remind_at": _future_iso(24),
                    "note": "Reply to HR email ✉️",
                },
            )
        )

        # Browser: sees the move and the reminder, verbatim.
        detail = await _browser_get(browser, f"/api/v1/applications/{application_id}")
        assert detail["status"] == "response"
        reminders = await _browser_get(
            browser, f"/api/v1/applications/{application_id}/reminders"
        )
        assert len(reminders) == 1
        assert reminders[0]["note"] == "Reply to HR email ✉️"

        # Field-for-field: the REST reminder and the MCP reminder are the same.
        mcp_reminders = _ok(
            _call(mcp, token, "list_reminders", {"application_id": application_id})
        )
        assert mcp_reminders["reminders"][0]["id"] == reminders[0]["id"]
        assert mcp_reminders["reminders"][0]["due_at"] == reminders[0]["due_at"]
        assert set(mcp_reminders["reminders"][0]) == set(reminders[0])

    async def test_rest_created_application_appears_in_mcp(self, e2e):
        db, mcp, app = e2e.db, e2e.mcp, e2e.app
        user = await _new_user(db, "rest-first@example.com")
        token = await _mcp_token_for(user, "cursor")
        browser = await _login_browser(app, "rest-first@example.com")
        workspace = await _seed_workspace(db, user)
        resume_id = workspace.tailored[1]["resume_id"]

        # The browser (REST) creates the card, exactly as the web app does.
        created = await _browser_post(
            browser,
            "/api/v1/applications",
            {
                "job_description": JD_NORTHWIND,
                "resume_id": resume_id,
                "company": "Northwind Traders",
                "role": "Senior Site Reliability Engineer",
            },
        )
        application_id = created["application_id"]

        # MCP sees it: on the board, in detail, and in a duplicate check.
        board = _ok(_call(mcp, token, "list_applications", {}))
        assert [c["application_id"] for c in board["columns"]["applied"]] == [application_id]
        detail = _ok(_call(mcp, token, "get_application", {"application_id": application_id}))
        assert detail["job_content"] == JD_NORTHWIND
        dupe = _ok(
            _call(
                mcp, token, "check_duplicate",
                {"company": "Northwind Traders", "role": "Senior Site Reliability Engineer"},
            )
        )
        assert dupe["is_duplicate"] is True
        assert dupe["application"]["application_id"] == application_id

        # MCP can attach a reminder to the REST-created card - same DB truth.
        reminder = _ok(
            _call(
                mcp, token, "create_reminder",
                {"application_id": application_id, "remind_at": _future_iso(48), "note": "Prep 🧠"},
            )
        )
        rest_reminders = await _browser_get(
            browser, f"/api/v1/applications/{application_id}/reminders"
        )
        assert rest_reminders[0]["id"] == reminder["id"]


# ---------------------------------------------------------------------------
# Workflow 4 — realistic error journeys an AI client must recover from
# ---------------------------------------------------------------------------


class TestErrorJourneys:
    async def test_master_resume_for_cover_letter_is_actionable_refusal(
        self, e2e, credits_on, llm_mocked
    ):
        """The classic AI mistake: it passes the master resume, not a tailored
        one. The refusal is one line with a machine code, nothing is charged,
        and the master resume is untouched."""
        db, mcp = e2e.db, e2e.mcp
        user = await _new_user(db, "err-master@example.com")
        token = await _mcp_token_for(user, "claude-desktop")
        workspace = await _seed_workspace(db, user)
        await _fund(db, user, wallet=100)

        text = _error_text(
            _call(
                mcp, token, "generate_cover_letter",
                {"resume_id": workspace.master["resume_id"]},
            )
        )
        assert "http_400" in text
        assert "tailored" in text
        assert "Traceback" not in text

        # State unchanged: no provider call, no charge, no saved letter, no hold.
        # (The billing context writes a zero-charge "failed" ledger row for the
        # refused call - by design, so a non-bill is provable - which is NOT a
        # charge: the wallet and the hold are untouched.)
        llm_mocked["cover_letter"].assert_not_awaited()
        rows = await _ledger(db, user)
        assert all(r["credits_charged"] == 0 for r in rows)
        assert await _wallet(db, user) == 100
        stored = await db.get_resume(user, workspace.master["resume_id"])
        assert stored.get("cover_letter") is None
        assert (await db.get_or_create_credit_account(user))["reserved_credits"] == 0

    async def test_lowercase_status_is_actionable_and_changes_nothing(
        self, e2e
    ):
        db, mcp = e2e.db, e2e.mcp
        user = await _new_user(db, "err-status@example.com")
        token = await _mcp_token_for(user, "cursor")
        workspace = await _seed_workspace(db, user)

        card = _ok(
            _call(
                mcp, token, "add_application",
                {
                    "job_description": JD_GLOBEX,
                    "resume_id": workspace.tailored[0]["resume_id"],
                    "company": "Globex Corporation",
                    "role": "Platform Reliability Engineer",
                },
            )
        )
        text = _error_text(
            _call(
                mcp, token, "update_application_status",
                {"application_id": card["application_id"], "status": "Interview"},
            )
        )
        assert "invalid_status" in text
        assert "interview" in text  # valid values listed
        assert "Traceback" not in text
        # Unchanged: still applied.
        detail = await db.get_application_detail(user, card["application_id"])
        assert detail["status"] == "applied"

    async def test_sloppy_remind_at_is_actionable_and_changes_nothing(
        self, e2e
    ):
        db, mcp = e2e.db, e2e.mcp
        user = await _new_user(db, "err-remind@example.com")
        token = await _mcp_token_for(user, "claude-desktop")
        workspace = await _seed_workspace(db, user)
        card = _ok(
            _call(
                mcp, token, "add_application",
                {
                    "job_description": JD_NORTHWIND,
                    "resume_id": workspace.tailored[0]["resume_id"],
                    "company": "Northwind Traders",
                    "role": "Senior SRE",
                },
            )
        )
        text = _error_text(
            _call(
                mcp, token, "create_reminder",
                {"application_id": card["application_id"], "remind_at": "day after tomorrow"},
            )
        )
        assert "invalid_reminder" in text
        assert "ISO-8601" in text
        assert "Traceback" not in text
        listed = _ok(_call(mcp, token, "list_reminders", {"application_id": card["application_id"]}))
        assert listed["total"] == 0

    async def test_past_remind_at_is_accepted_identically_on_both_channels(
        self, e2e
    ):
        """Deliberate divergence from the task brief, documented: a PAST
        remind_at is NOT refused - REST accepts it too (the scheduling suite
        seeds past reminders on purpose; a past due time fires immediately,
        i.e. "remind me now" semantics). The contract under test is parity:
        MCP and REST give the same answer, so an AI client is never surprised
        by a channel-specific rule."""
        db, mcp, app = e2e.db, e2e.mcp, e2e.app
        user = await _new_user(db, "past@example.com")
        token = await _mcp_token_for(user, "claude-desktop")
        browser = await _login_browser(app, "past@example.com")
        workspace = await _seed_workspace(db, user)
        card = _ok(
            _call(
                mcp, token, "add_application",
                {
                    "job_description": JD_GLOBEX,
                    "resume_id": workspace.tailored[0]["resume_id"],
                    "company": "Globex Corporation",
                    "role": "Platform Reliability Engineer",
                },
            )
        )
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()

        via_mcp = _ok(
            _call(
                mcp, token, "create_reminder",
                {"application_id": card["application_id"], "remind_at": past, "note": "now-ish"},
            )
        )
        assert via_mcp["id"]

        via_rest = await _browser_post(
            browser,
            f"/api/v1/applications/{card['application_id']}/reminders",
            {"due_at": past, "note": "rest-now-ish"},
        )
        assert via_rest["id"]
        listed = _ok(_call(mcp, token, "list_reminders", {"application_id": card["application_id"]}))
        assert listed["total"] == 2  # both channels created exactly one each

    async def test_cross_type_id_confusion_is_not_found_not_a_crash(
        self, e2e
    ):
        """An AI threading the wrong id type (a resume id where an application
        id belongs) gets a clean not-found pointing at the listing tool."""
        mcp = e2e.mcp
        user = await _new_user(e2e.db, "err-ids@example.com")
        token = await _mcp_token_for(user, "cursor")
        workspace = await _seed_workspace(e2e.db, user)

        text = _error_text(
            _call(mcp, token, "get_resume", {"resume_id": "not-a-real-id"})
        )
        assert "resume_not_found" in text
        assert "list_resumes" in text
        assert "Traceback" not in text

        text = _error_text(
            _call(
                mcp, token, "update_application_status",
                {"application_id": workspace.tailored[0]["resume_id"], "status": "interview"},
            )
        )
        assert "application_not_found" in text
        assert "list_applications" in text
        assert "Traceback" not in text


# ---------------------------------------------------------------------------
# Workflow 5 — billing integrity across the journey
# ---------------------------------------------------------------------------


class TestBillingIntegrity:
    async def test_total_spent_is_exactly_the_sum_of_operations(
        self, e2e, credits_on, llm_mocked
    ):
        db, mcp = e2e.db, e2e.mcp
        user = await _new_user(db, "billing@example.com")
        token = await _mcp_token_for(user, "claude-desktop")
        workspace = await _seed_workspace(db, user)
        price_cl = await _price(db, "cover_letter")
        price_ip = await _price(db, "interview_prep")
        wallet0 = price_cl + price_ip + 100  # buffer: reuse needs a live balance
        await _fund(db, user, wallet=wallet0)

        # The whole billed journey: one letter, one prep, then free reuse.
        letter = _ok(
            _call(
                mcp, token, "generate_cover_letter",
                {"resume_id": workspace.tailored[0]["resume_id"]},
            )
        )
        prep = _ok(
            _call(
                mcp, token, "generate_interview_prep",
                {"resume_id": workspace.tailored[0]["resume_id"]},
            )
        )
        _ok(_call(mcp, token, "generate_cover_letter", {"resume_id": workspace.tailored[0]["resume_id"]}))
        _ok(_call(mcp, token, "generate_interview_prep", {"resume_id": workspace.tailored[0]["resume_id"]}))

        assert letter["content"] and prep["interview_prep"]
        rows = await _ledger(db, user)
        # Exactly one charged row per billed operation - nothing hidden, nothing
        # doubled - and the reuse passes settle zero (provable free passes).
        assert [(r["feature"], r["credits_charged"], r["outcome"]) for r in rows if r["credits_charged"] > 0] == [
            ("interview_prep", price_ip, "ok"),
            ("cover_letter", price_cl, "ok"),
        ]
        # The two reuse calls above wrote zero-charge rows, nothing more.
        reuse_rows = [r for r in rows if r["credits_charged"] == 0]
        assert {r["feature"] for r in reuse_rows} == {"cover_letter", "interview_prep"}
        assert sum(r["credits_charged"] for r in rows) == wallet0 - await _wallet(db, user)
        account = await db.get_or_create_credit_account(user)
        assert account["reserved_credits"] == 0, "hold leaked"
        assert await _wallet(db, user) == 100

    async def test_zero_balance_refusal_mid_journey_leaves_state_intact(
        self, e2e, credits_on, llm_mocked
    ):
        db, mcp = e2e.db, e2e.mcp
        user = await _new_user(db, "midjourney@example.com")
        token = await _mcp_token_for(user, "cursor")
        workspace = await _seed_workspace(db, user)
        price_cl = await _price(db, "cover_letter")
        await _fund(db, user, wallet=price_cl)  # exactly one cover letter

        # Free part of the journey first: a tracked card + reminder.
        card = _ok(
            _call(
                mcp, token, "add_application",
                {
                    "job_description": JD_NORTHWIND,
                    "resume_id": workspace.tailored[0]["resume_id"],
                    "company": "Northwind Traders",
                    "role": "Senior SRE",
                },
            )
        )
        _ok(
            _call(
                mcp, token, "create_reminder",
                {"application_id": card["application_id"], "remind_at": _future_iso()},
            )
        )

        # The one funded AI call succeeds.
        _ok(_call(mcp, token, "generate_cover_letter", {"resume_id": workspace.tailored[0]["resume_id"]}))

        # The next billed call hits the empty wallet - one actionable line.
        text = _error_text(
            _call(
                mcp, token, "generate_interview_prep",
                {"resume_id": workspace.tailored[0]["resume_id"]},
            )
        )
        assert "insufficient_credits" in text
        assert "Traceback" not in text
        llm_mocked["interview_prep"].assert_not_awaited()

        # Prior state fully intact: card, reminder, saved letter, one ledger row.
        board = _ok(_call(mcp, token, "list_applications", {}))
        assert board["total"] == 1
        reminders = _ok(_call(mcp, token, "list_reminders", {"application_id": card["application_id"]}))
        assert reminders["total"] == 1
        stored = await db.get_resume(user, workspace.tailored[0]["resume_id"])
        assert stored["cover_letter"] == COVER_LETTER_TEXT
        rows = await _ledger(db, user)
        assert [r["feature"] for r in rows] == ["cover_letter"]
        account = await db.get_or_create_credit_account(user)
        assert account["wallet_credits"] == 0
        assert account["reserved_credits"] == 0


# ---------------------------------------------------------------------------
# Workflow 6 — two users, interleaved: no cross-contamination
# ---------------------------------------------------------------------------


class TestTwoUserInterleaving:
    async def test_interleaved_journeys_stay_isolated(
        self, e2e, credits_on, llm_mocked, monkeypatch
    ):
        db, mcp = e2e.db, e2e.mcp

        # User A: the fixture owner. User B: a fresh user with own token.
        token_a = e2e.owner_token
        user_a = e2e.owner_id
        user_b = await _new_user(db, "b-interleaved@example.com", "User B")
        token_b = await _mcp_token_for(user_b, "b-client")
        workspace_a = await _seed_workspace(db, user_a)
        workspace_b = await _seed_workspace(db, user_b)
        await _fund(db, user_b, wallet=100)
        state = _gate_the_scrape(monkeypatch)

        # Interleaved reads: each sees only their own resumes.
        a_resumes = _ok(_call(mcp, token_a, "list_resumes", {}))
        b_resumes = _ok(_call(mcp, token_b, "list_resumes", {}))
        assert {r["resume_id"] for r in a_resumes["resumes"]} == {
            t["resume_id"] for t in workspace_a.tailored
        }
        assert {r["resume_id"] for r in b_resumes["resumes"]} == {
            t["resume_id"] for t in workspace_b.tailored
        }
        assert not ({r["resume_id"] for r in a_resumes["resumes"]}
                    & {r["resume_id"] for r in b_resumes["resumes"]})

        # Interleaved searches: two users, two concurrent searches.
        search_a = _ok(_call(mcp, token_a, "start_job_search", {"query": "SRE London"}))
        search_b = _ok(_call(mcp, token_b, "start_job_search", {"query": "DevOps Berlin"}))
        assert search_a["search_id"] != search_b["search_id"]
        # B polling A's search id learns nothing.
        leaked = _ok(_call(mcp, token_b, "get_job_search_status", {"search_id": search_a["search_id"]}))
        assert leaked["status"] == "expired"
        _release(state)
        done_a = _poll_until_done(mcp, token_a, search_a["search_id"])
        done_b = _poll_until_done(mcp, token_b, search_b["search_id"])
        assert done_a["status"] == done_b["status"] == "done"

        # Interleaved writes: two cards, one per user.
        card_a = _ok(
            _call(
                mcp, token_a, "add_application",
                {
                    "job_description": JD_NORTHWIND,
                    "resume_id": workspace_a.tailored[0]["resume_id"],
                    "company": "Northwind Traders",
                    "role": "Senior SRE",
                },
            )
        )
        card_b = _ok(
            _call(
                mcp, token_b, "add_application",
                {
                    "job_description": JD_GLOBEX,
                    "resume_id": workspace_b.tailored[0]["resume_id"],
                    "company": "Globex Corporation",
                    "role": "Platform Reliability Engineer",
                },
            )
        )

        # Interleaved reminders: each on their own card.
        reminder_a = _ok(
            _call(
                mcp, token_a, "create_reminder",
                {"application_id": card_a["application_id"], "remind_at": _future_iso(24)},
            )
        )
        _ok(
            _call(
                mcp, token_b, "create_reminder",
                {"application_id": card_b["application_id"], "remind_at": _future_iso(48)},
            )
        )

        # Boards do not mix.
        board_a = _ok(_call(mcp, token_a, "list_applications", {}))
        board_b = _ok(_call(mcp, token_b, "list_applications", {}))
        assert board_a["total"] == 1
        assert board_b["total"] == 1
        assert [c["company"] for c in board_a["columns"]["applied"]] == ["Northwind Traders"]
        assert [c["company"] for c in board_b["columns"]["applied"]] == ["Globex Corporation"]

        # Reminders do not mix; cross-user ids are not-found, never data.
        a_reminders = _ok(_call(mcp, token_a, "list_reminders", {"application_id": card_a["application_id"]}))
        b_reminders = _ok(_call(mcp, token_b, "list_reminders", {"application_id": card_b["application_id"]}))
        assert a_reminders["total"] == 1
        assert b_reminders["total"] == 1
        assert a_reminders["reminders"][0]["id"] == reminder_a["id"]
        text = _error_text(
            _call(
                mcp, token_b, "list_reminders",
                {"application_id": card_a["application_id"]},
            )
        )
        assert "application_not_found" in text
        assert "Traceback" not in text

        # Duplicate checks stay per-user: B has NOT applied to Northwind.
        dupe_b = _ok(
            _call(mcp, token_b, "check_duplicate", {"company": "Northwind Traders", "role": "Senior SRE"})
        )
        assert dupe_b == {"is_duplicate": False, "application": None}

        # Billing stays per-user: only B generated, only B has ledger rows.
        _ok(
            _call(
                mcp, token_b, "generate_cover_letter",
                {"resume_id": workspace_b.tailored[0]["resume_id"]},
            )
        )
        assert [r["feature"] for r in await _ledger(db, user_b)] == ["cover_letter"]
        assert await _ledger(db, user_a) == []
        assert await _wallet(db, user_b) == 100 - await _price(db, "cover_letter")

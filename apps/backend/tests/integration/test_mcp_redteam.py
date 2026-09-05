"""MCP red-team suite (Task 10): adversarial attacks against the live mount.

Every test here is a REAL attack through the mounted ASGI app (the same
``/api/v1/mcp`` a deployed server exposes) - never a re-assertion of unit
behavior. The seven attack classes from the task brief:

1. cross-user: every tool called with the other user's resource ids
2. token misuse: expired / revoked / malformed / injection bearer strings
3. privilege: user token against admin + REST surfaces (mount-only by design)
4. parameter abuse: 1MB args, nulls, nested/wrong types -> error, never 500
5. business-rule bypass: duplicate cooldown, search rate limit, LLM limits
6. leakage: no hashes / other-user fields / stack traces / token material
7. CSRF boundary: cookies never authenticate the mount; bearers never
   authenticate REST

A finding is a test that fails; each class documents what the attacker must
NOT obtain. Run contiguously with the other integration files (the known
conftest-collection quirk).
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.config import settings as app_settings
from app.models import User

pytestmark = pytest.mark.integration

MCP = "/api/v1/mcp/"

ALL_TOOL_NAMES = {
    "list_resumes",
    "get_resume",
    "list_applications",
    "get_application",
    "get_apply_queue",
    "check_duplicate",
    "add_application",
    "update_application_status",
    "list_reminders",
    "create_reminder",
    "generate_cover_letter",
    "generate_interview_prep",
    "start_job_search",
    "get_job_search_status",
}


def _post(client: TestClient, body: dict, token: str | None = None):
    """One MCP JSON-RPC POST; returns the raw response (status code matters)."""
    headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
    return client.post(MCP, json=body, headers=headers)


def _call(client: TestClient, token: str, name: str, arguments):
    """One ``tools/call`` round-trip (arguments may be deliberately hostile)."""
    return _post(
        client,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
        token,
    ).json()


def _tools_list(client: TestClient, token: str):
    return _post(
        client, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, token
    ).json()


def _ok(result: dict) -> dict:
    """Assert a successful tool result and return its payload."""
    assert result.get("error") is None, result
    res = result["result"]
    assert res.get("isError") is not True, res
    if "structuredContent" in res:
        return res["structuredContent"]
    return json.loads(res["content"][0]["text"])


def _error_text(result: dict) -> str:
    """Assert a tool-level error result and return its message."""
    assert result.get("error") is None, result  # protocol-level, not tool-level
    res = result["result"]
    assert res.get("isError") is True, res
    return res["content"][0]["text"]


def _refused(result: dict) -> str:
    """A hostile call refused at EITHER layer (protocol or tool), as text.

    Parameter abuse does not get to choose WHERE it is rejected - the contract
    is only that it is rejected (never a 500, never execution).
    """
    if result.get("error") is not None:  # JSON-RPC level (e.g. bad params)
        return json.dumps(result["error"])
    res = result.get("result", {})
    if res.get("isError") is True:
        return res["content"][0]["text"]
    raise AssertionError(f"hostile call was not refused: {result}")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def mcp_client(auth_env, mcp_app, mcp_token, isolated_db, monkeypatch):
    """A live MCP-mounted TestClient plus ``(client, owner_token, db)``."""
    from app.applications import submissions

    monkeypatch.setattr(submissions, "db", isolated_db)
    app = mcp_app(True)
    with TestClient(app) as client:
        yield client, mcp_token["raw"]


@pytest.fixture(autouse=True)
def _clean_search_state():
    """Blank slate per test: no in-flight search jobs, no cooldown timestamps."""
    from app.job_discovery import search_jobs
    from app.routers import discovery

    search_jobs.reset_for_tests()
    discovery._search_timestamps.clear()
    yield
    search_jobs.reset_for_tests()
    discovery._search_timestamps.clear()


async def _seed_resume(db, user_id: str, **kwargs) -> dict:
    defaults = dict(
        content="# Jane Doe\nSenior SRE",
        filename="resume.md",
        is_master=False,
        processing_status="ready",
    )
    defaults.update(kwargs)
    return await db.create_resume(user_id, **defaults)


async def _seed_user(db, email: str) -> str:
    uid = str(uuid4())
    async with db.session_factory() as session:
        session.add(User(id=uid, email=email, name="U", role="user", status="active"))
        await session.commit()
    return uid


async def _seed_card(db, user_id: str, **kw) -> dict:
    job = await db.create_job(user_id, content="JD")
    defaults = dict(
        job_id=job["job_id"], resume_id="r1", status="applied",
        company="Acme", role="SRE",
    )
    defaults.update(kw)
    return await db.create_application(user_id, **defaults)


async def _other_users_token(db) -> str:
    """A second, unrelated user's MCP bearer token (the attacker)."""
    from app.auth.mcp_tokens import get_mcp_token_service

    user_b = await _seed_user(db, "attacker@example.com")
    _, raw_b = await get_mcp_token_service().issue(user_b, "attacker-client")
    return raw_b


def _future_iso() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()


# ---------------------------------------------------------------------------
# Attack class 1: cross-user - every tool with the victim's resource ids
# ---------------------------------------------------------------------------


class TestCrossUser:
    async def test_attacker_token_gets_nothing_from_victim(
        self, mcp_client, isolated_db, owner_id
    ):
        """The full read surface, attacked with user A's ids under user B's
        token: every call is empty or not-found, and none of A's data (resume
        text, company names, reminders) travels back."""
        client, token_a = mcp_client
        marker = "VICTIM-SECRET-CONTENT-8f3a"
        resume = await _seed_resume(isolated_db, owner_id, content=f"# {marker}")
        card = await _seed_card(isolated_db, owner_id, company="Victim Co", role="SRE")
        token_b = await _other_users_token(isolated_db)

        # Reads with the victim's ids.
        assert "resume_not_found" in _error_text(
            _call(client, token_b, "get_resume", {"resume_id": resume["resume_id"]})
        )
        assert "application_not_found" in _error_text(
            _call(
                client, token_b, "get_application",
                {"application_id": card["application_id"]},
            )
        )
        assert "application_not_found" in _error_text(
            _call(
                client, token_b, "list_reminders",
                {"application_id": card["application_id"]},
            )
        )
        # Victim's own duplicate is invisible to the attacker.
        dup = _ok(
            _call(client, token_b, "check_duplicate", {"company": "Victim Co", "role": "SRE"})
        )
        assert dup == {"is_duplicate": False, "application": None}
        # The attacker's own listings are empty.
        assert _ok(_call(client, token_b, "list_resumes", {})) == {"resumes": []}
        assert _ok(_call(client, token_b, "get_apply_queue", {})) == {"queue": [], "total": 0}
        board = _ok(_call(client, token_b, "list_applications", {}))
        assert board["total"] == 0
        assert all(col == [] for col in board["columns"].values())

        # No victim material anywhere in the raw responses.
        for name, args in (
            ("get_resume", {"resume_id": resume["resume_id"]}),
            ("get_application", {"application_id": card["application_id"]}),
            ("list_applications", {}),
        ):
            body = _post(
                client,
                {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                 "params": {"name": name, "arguments": args}},
                token_b,
            )
            assert marker not in body.text
            assert "Victim Co" not in body.text
            assert owner_id not in body.text

    async def test_attacker_cannot_mutate_victim_rows(
        self, mcp_client, isolated_db, owner_id
    ):
        client, token_a = mcp_client
        card = await _seed_card(isolated_db, owner_id, status="applied")
        token_b = await _other_users_token(isolated_db)

        assert "application_not_found" in _error_text(
            _call(
                client, token_b, "update_application_status",
                {"application_id": card["application_id"], "status": "rejected"},
            )
        )
        assert "application_not_found" in _error_text(
            _call(
                client, token_b, "create_reminder",
                {"application_id": card["application_id"], "remind_at": _future_iso()},
            )
        )

        # Nothing changed, nothing created.
        detail = await isolated_db.get_application_detail(owner_id, card["application_id"])
        assert detail["status"] == "applied"
        listed = _ok(
            _call(client, token_a, "list_reminders", {"application_id": card["application_id"]})
        )
        assert listed["total"] == 0

    async def test_add_application_with_victims_resume_id_leaks_nothing(
        self, mcp_client, isolated_db, owner_id
    ):
        """The one place a foreign id is storeable: the attacker attaches the
        victim's resume_id to their OWN card (REST has the same shape - the
        field is a reference, not a grant). The card is the attacker's, and the
        resume join is caller-scoped, so the victim's content never comes back."""
        client, token_a = mcp_client
        marker = "VICTIM-SECRET-CONTENT-91cd"
        resume = await _seed_resume(isolated_db, owner_id, content=f"# {marker}")
        token_b = await _other_users_token(isolated_db)

        created = _ok(
            _call(
                client, token_b, "add_application",
                {
                    "job_description": "attacker's own JD",
                    "company": "Attacker Co",
                    "role": "SRE",
                    "resume_id": resume["resume_id"],
                },
            )
        )
        assert created["application_id"]
        assert created["company"] == "Attacker Co"

        # The detail view resolves the resume CALLER-scoped: no victim resume.
        detail = _ok(
            _call(client, token_b, "get_application", {"application_id": created["application_id"]})
        )
        assert detail["resume"] is None
        assert marker not in json.dumps(detail)
        # And the victim's resume row is untouched.
        stored = await isolated_db.get_resume(owner_id, resume["resume_id"])
        assert marker in stored["content"]

    async def test_ai_generation_with_victims_resume_id_is_refused(
        self, mcp_client, isolated_db, owner_id, monkeypatch
    ):
        """Charging attacker B to generate against victim A's resume must be
        refused at the ownership check - B pays nothing, A's data stays put."""
        client, _ = mcp_client
        monkeypatch.setattr(app_settings, "ai_credits_enabled", True)
        monkeypatch.setattr("app.ai_metered.user_has_own_key", lambda _uid: False)
        resume = await _seed_resume(isolated_db, owner_id)
        token_b = await _other_users_token(isolated_db)

        text = _error_text(
            _call(client, token_b, "generate_cover_letter", {"resume_id": resume["resume_id"]})
        )
        assert "404" in text
        assert "Traceback" not in text
        # The refused call may leave an error-outcome ledger row (REST parity:
        # the metered context wraps the handler), but it can never CHARGE.
        user_b = await _victim_of(token_b, isolated_db)
        for row in await isolated_db.list_usage(user_b):
            assert row["credits_charged"] == 0

    async def test_search_status_with_victims_search_id_is_expired(
        self, mcp_client, isolated_db, owner_id, monkeypatch
    ):
        """Attacker B polling victim A's search_id learns nothing: expired."""
        import asyncio

        from app.routers import discovery

        client, token_a = mcp_client
        monkeypatch.setattr(app_settings, "JOB_DISCOVERY", True)

        async def fake_work(payload, user_id, db, config, job=None):
            await asyncio.sleep(0)
            job.saved = 1

        monkeypatch.setattr(discovery, "_execute_manual_search", fake_work)

        started = _ok(_call(client, token_a, "start_job_search", {"query": "python"}))
        token_b = await _other_users_token(isolated_db)

        leaked = _ok(
            _call(client, token_b, "get_job_search_status", {"search_id": started["search_id"]})
        )
        assert leaked["status"] == "expired"
        assert leaked.get("saved", 0) == 0
        assert leaked.get("query") in (None, "")


async def _victim_of(raw_token: str, db) -> str:
    """The user id a raw token belongs to (test helper, via the service)."""
    from app.auth.mcp_tokens import get_mcp_token_service

    row = await get_mcp_token_service().verify(raw_token)
    assert row is not None
    return row["user_id"]


# ---------------------------------------------------------------------------
# Attack class 2: token misuse
# ---------------------------------------------------------------------------

MALFORMED_BEARERS = [
    "fw_",                                # empty payload after the prefix
    "abc123",                             # non-fw_ token
    "gh_" + "x" * 40,                     # another scheme's token shape
    "fw_' OR '1'='1' --",                 # SQL injection
    "fw_; DROP TABLE mcp_tokens; --",     # SQL injection, destructive
    'fw_"}, "sub": "other-user',          # JSON injection into claims
    "fw_" + "x" * 100_000,                # oversized token
    "fw_\x00\x1b[31m",              # null + escape control characters
]


class TestTokenMisuse:
    async def test_malformed_and_injection_bearers_are_401(
        self, mcp_client
    ):
        client, _ = mcp_client
        for bad in MALFORMED_BEARERS:
            res = _post(
                client, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, bad
            )
            assert res.status_code == 401, (bad[:30], res.status_code)

    async def test_bare_and_wrong_scheme_authorization_headers_are_401(
        self, mcp_client
    ):
        client, _ = mcp_client
        for header_value in ("Bearer", "Bearer ", "Basic fw_realshape", "fw_noscheme"):
            res = client.post(
                MCP,
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                headers={"Authorization": header_value},
            )
            assert res.status_code == 401, (header_value, res.status_code)

    async def test_expired_token_is_401(self, mcp_client, isolated_db, owner_id):
        from app.auth.mcp_tokens import get_mcp_token_service
        from app.models import McpToken

        client, _ = mcp_client
        svc = get_mcp_token_service()
        rec, raw = await svc.issue(owner_id, "short-lived", ttl_days=1)
        assert (await svc.verify(raw)) is not None  # sanity: valid before expiry

        # Force the expiry into the past.
        async with isolated_db.session_factory() as s:
            row = await s.get(McpToken, rec["id"])
            row.expires_at = "2020-01-01T00:00:00+00:00"
            await s.commit()

        res = _post(client, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, raw)
        assert res.status_code == 401
        assert await svc.verify(raw) is None

    async def test_revoked_token_is_401(self, mcp_client, isolated_db, owner_id):
        from app.auth.mcp_tokens import get_mcp_token_service

        client, _ = mcp_client
        svc = get_mcp_token_service()
        rec, raw = await svc.issue(owner_id, "doomed")
        assert (await svc.verify(raw)) is not None
        assert await svc.revoke(owner_id, rec["id"])

        res = _post(client, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, raw)
        assert res.status_code == 401

    async def test_injection_attempts_leave_the_token_table_intact(
        self, mcp_client, isolated_db, owner_id
    ):
        """Firing every hostile bearer string, then using the real token:
        the mcp_tokens table still verifies (no injection landed, no drop)."""
        from app.auth.mcp_tokens import get_mcp_token_service

        client, token = mcp_client
        for bad in MALFORMED_BEARERS:
            _post(client, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, bad)

        res = _post(client, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, token)
        assert res.status_code == 200
        assert await get_mcp_token_service().verify(token) is not None


# ---------------------------------------------------------------------------
# Attack class 3: privilege - the token is mount-only
# ---------------------------------------------------------------------------


class TestPrivilegeBoundary:
    async def test_bearer_cannot_reach_admin_rest_routes(
        self, mcp_client, monkeypatch
    ):
        """A user's MCP token against the admin surface: 401. Admin capability
        checks consult the session principal only - never MCP tokens."""
        client, token = mcp_client
        monkeypatch.setattr(app_settings, "single_user_mode", False)  # hosted
        monkeypatch.setattr(app_settings, "admin_enabled", True)

        for path in ("/api/v1/admin/stats", "/api/v1/admin/users"):
            res = client.get(path, headers={"Authorization": f"Bearer {token}"})
            assert res.status_code == 401, (path, res.status_code, res.text)
            # And the refusal is not a disguised server error.
            assert "Traceback" not in res.text

    async def test_bearer_cannot_mint_tokens_via_rest(
        self, mcp_client, monkeypatch
    ):
        """Token-by-bearer must not bootstrap itself: creating tokens needs a
        verified browser session, with CSRF, on the REST route."""
        client, token = mcp_client
        monkeypatch.setattr(app_settings, "single_user_mode", False)

        res = client.post(
            "/api/v1/mcp/tokens",
            json={"label": "self-propagating"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 401, res.text

    async def test_bearer_cannot_reach_user_rest_routes(
        self, mcp_client, monkeypatch
    ):
        client, token = mcp_client
        monkeypatch.setattr(app_settings, "single_user_mode", False)

        res = client.get(
            "/api/v1/resumes/list", headers={"Authorization": f"Bearer {token}"}
        )
        assert res.status_code == 401


# ---------------------------------------------------------------------------
# Attack class 4: parameter abuse
# ---------------------------------------------------------------------------


class TestParameterAbuse:
    async def test_1mb_resume_id_is_an_error_never_500(self, mcp_client):
        """Red-team finding F1 regression: a 1MB id echoed verbatim into the
        tool's error message used to pin a worker at 100% CPU for minutes
        (FastMCP logger.exception -> rich traceback rendering of the huge
        string). The refusal must come back fast, with a TRUNCATED id."""
        client, token = mcp_client
        res = _post(
            client,
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
             "params": {"name": "get_resume",
                        "arguments": {"resume_id": "x" * 1_048_576}}},
            token,
        )
        assert res.status_code != 500
        text = _refused(res.json())
        assert "Traceback" not in text
        assert "resume_not_found" in text
        assert "truncated, 1048576 chars" in text
        assert len(text) < 500  # the megabyte never comes back

    async def test_1mb_job_description_does_not_crash(
        self, mcp_client, isolated_db, owner_id
    ):
        """REST's manual-add body has no upper bound, so a 1MB JD must be
        handled (accepted or refused) without a 500 - never a crash."""
        client, token = mcp_client
        resume = await _seed_resume(isolated_db, owner_id)

        res = _post(
            client,
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
             "params": {"name": "add_application",
                        "arguments": {
                            "job_description": "y" * 1_048_576,
                            "company": "Acme",
                            "role": "SRE",
                            "resume_id": resume["resume_id"],
                        }}},
            token,
        )
        assert res.status_code != 500
        body = res.json()
        if body.get("error") is None and body["result"].get("isError") is not True:
            payload = _ok(body)
            assert payload["application_id"]  # accepted, REST-parity

    async def test_resume_id_as_array_is_refused_never_500(self, mcp_client):
        client, token = mcp_client
        for arguments in (
            {"resume_id": ["not", "a", "string"]},
            {"resume_id": {"nested": "object"}},
            {"resume_id": 12345},
            {"resume_id": None},
        ):
            res = _post(
                client,
                {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                 "params": {"name": "get_resume", "arguments": arguments}},
                token,
            )
            assert res.status_code != 500, arguments
            text = _refused(res.json())
            assert "Traceback" not in text

    async def test_null_and_nested_status_strings_are_refused_never_500(
        self, mcp_client, isolated_db, owner_id
    ):
        client, token = mcp_client
        card = await _seed_card(isolated_db, owner_id)
        hostile_statuses = [
            None,
            {"nested": "applied"},
            ["applied"],
            "applied'; DROP TABLE applications; --",
            "APPLIED' UNION SELECT * FROM users--",
            "",
            "z" * 1_048_576,  # 1MB status string (F1 class: echoed if invalid)
        ]
        for status in hostile_statuses:
            res = _post(
                client,
                {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                 "params": {"name": "update_application_status",
                            "arguments": {"application_id": card["application_id"],
                                          "status": status}}},
                token,
            )
            assert res.status_code != 500, status
            text = _refused(res.json())
            assert "Traceback" not in text

        # After all of that, the card is untouched.
        detail = await isolated_db.get_application_detail(owner_id, card["application_id"])
        assert detail["status"] == "applied"

    async def test_arguments_as_wrong_json_shape_is_refused_never_500(
        self, mcp_client
    ):
        client, token = mcp_client
        for arguments in ("string-not-object", [1, 2, 3], 42):
            res = _post(
                client,
                {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                 "params": {"name": "list_resumes", "arguments": arguments}},
                token,
            )
            assert res.status_code != 500, arguments
            _refused(res.json())
        # arguments: null is protocol-equivalent to omitted (no arguments) -
        # FastMCP runs the tool with its defaults, which for a no-param tool
        # is the same call. Not a bypass: required params still refuse below.
        ok_none = _post(
            client,
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
             "params": {"name": "list_resumes", "arguments": None}},
            token,
        )
        assert _ok(ok_none.json()) == {"resumes": []}
        res = _post(
            client,
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
             "params": {"name": "get_resume", "arguments": None}},
            token,
        )
        assert res.status_code != 500
        _refused(res.json())

    async def test_unknown_tool_name_is_protocol_error_never_500(
        self, mcp_client
    ):
        """An attacker probing for hidden tools gets a protocol-level refusal."""
        client, token = mcp_client
        for name in ("admin_delete_user", "get_all_users", "__proto__", ""):
            res = _post(
                client,
                {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                 "params": {"name": name, "arguments": {}}},
                token,
            )
            assert res.status_code != 500, name
            _refused(res.json())


# ---------------------------------------------------------------------------
# Attack class 5: business-rule bypass
# ---------------------------------------------------------------------------


class TestBusinessRuleBypass:
    async def test_duplicate_cooldown_stays_advisory_on_mcp(
        self, mcp_client, isolated_db, owner_id
    ):
        """The cool-off duplicate guard is advisory on REST; MCP must not
        harden OR weaken it. A recent same-company+role application is flagged
        by check_duplicate, and add_application still creates the card."""
        client, token = mcp_client
        resume = await _seed_resume(isolated_db, owner_id)
        await _seed_card(isolated_db, owner_id, company="Acme", role="SRE")

        dup = _ok(_call(client, token, "check_duplicate", {"company": "acme", "role": "sre"}))
        assert dup["is_duplicate"] is True

        added = _ok(
            _call(
                client, token, "add_application",
                {"job_description": "JD", "company": "Acme", "role": "SRE",
                 "resume_id": resume["resume_id"]},
            )
        )
        assert added["application_id"]
        assert added["status"] == "applied"

    async def test_search_rate_limit_enforced_on_mcp_path(
        self, mcp_client, isolated_db, owner_id, monkeypatch
    ):
        """The 1-search/10s cooldown cannot be dodged by switching transport:
        the second rapid start is refused before any work."""
        import asyncio

        from app.routers import discovery

        client, token = mcp_client
        monkeypatch.setattr(app_settings, "JOB_DISCOVERY", True)

        async def fake_work(payload, user_id, db, config, job=None):
            await asyncio.sleep(0)
            job.saved = 1

        monkeypatch.setattr(discovery, "_execute_manual_search", fake_work)

        first = _ok(_call(client, token, "start_job_search", {"query": "python"}))
        assert first["status"] == "running"

        message = _error_text(_call(client, token, "start_job_search", {"query": "react"}))
        assert "http_429" in message
        assert "Traceback" not in message

    async def test_llm_rate_limit_fires_before_spend_on_mcp_path(
        self, mcp_client, isolated_db, owner_id, monkeypatch
    ):
        """Bursting generate_cover_letter through MCP cannot outspend the REST
        rate limit: the second call is refused and never billed."""
        from unittest.mock import AsyncMock

        from app.models import CreditAccount
        from app.routers import resumes as resumes_router
        from app.schemas.models import InterviewPrepData

        client, token = mcp_client
        monkeypatch.setattr(app_settings, "ai_credits_enabled", True)
        monkeypatch.setattr(app_settings, "llm_rate_per_min_user", 1)
        monkeypatch.setattr("app.ai_metered.user_has_own_key", lambda _uid: False)
        monkeypatch.setattr(
            resumes_router, "generate_cover_letter",
            AsyncMock(return_value="Dear Team, excited to apply."),
        )

        # A tailored resume ready for generation (tools-ai seeding pattern).
        master = await isolated_db.get_master_resume(owner_id) or await _seed_resume(
            isolated_db, owner_id, filename="master.md", is_master=True
        )
        job = await isolated_db.create_job(owner_id, content="Senior SRE JD")
        tailored = await _seed_resume(
            isolated_db, owner_id, filename="tailored.md",
            parent_id=master["resume_id"],
            processed_data={"skills": ["kubernetes"], "summary": "SRE"},
        )
        await isolated_db.create_improvement(
            owner_id,
            original_resume_id=master["resume_id"],
            tailored_resume_id=tailored["resume_id"],
            job_id=job["job_id"],
            improvements=[],
        )
        # Fund the wallet so only the rate limit can refuse.
        await isolated_db.get_or_create_credit_account(owner_id)
        async with isolated_db.session_factory() as session:
            row = await session.get(CreditAccount, owner_id)
            row.wallet_credits = 100
            row.allowance_credits = 0
            row.allowance_period_start = datetime.now(timezone.utc).isoformat()
            await session.commit()

        first = _ok(_call(client, token, "generate_cover_letter", {"resume_id": tailored["resume_id"]}))
        assert first["content"]

        second = _error_text(
            _call(client, token, "generate_cover_letter", {"resume_id": tailored["resume_id"]})
        )
        assert "rate_limited" in second

        rows = await isolated_db.list_usage(owner_id)
        assert len(rows) == 1, "the rate-limited call must not be billed"
        assert rows[0]["outcome"] == "ok"


# ---------------------------------------------------------------------------
# Attack class 6: leakage
# ---------------------------------------------------------------------------


class TestLeakage:
    async def test_tools_list_and_schemas_leak_no_secrets(
        self, mcp_client, isolated_db, owner_id
    ):
        """The discovery surface: no token hashes, no other-user fields, no
        auth parameters, and the tool list is exactly the shipped surface."""
        client, token = mcp_client
        token_hash = hashlib.sha256(token.encode()).hexdigest()

        res = _post(client, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, token)
        assert res.status_code == 200
        assert token not in res.text
        assert token_hash not in res.text
        assert "token_hash" not in res.text

        body = res.json()
        tools = {t["name"]: t for t in body["result"]["tools"]}
        assert set(tools) == ALL_TOOL_NAMES, (
            f"unexpected tool surface: {set(tools) ^ ALL_TOOL_NAMES}"
        )
        for name, tool in tools.items():
            assert "inputSchema" in tool, name
            # Auth never appears as a parameter - it travels in the header.
            props = tool["inputSchema"].get("properties", {})
            assert "token" not in props, name
            assert "authorization" not in props, name
            assert "api_key" not in props, name
            # No stack traces or internal paths in descriptions.
            assert "Traceback" not in json.dumps(tool), name

    async def test_no_stack_traces_in_any_refusal(
        self, mcp_client, isolated_db, owner_id
    ):
        """Tool errors are one-line actionable messages - an attacker probing
        error paths gets no file paths, no frames, no internals."""
        client, token = mcp_client
        card = await _seed_card(isolated_db, owner_id)
        resume = await _seed_resume(isolated_db, owner_id)

        hostile_calls = [
            ("get_resume", {"resume_id": "ghost"}),
            ("get_application", {"application_id": "ghost"}),
            ("update_application_status", {"application_id": "ghost", "status": "interview"}),
            ("list_reminders", {"application_id": "ghost"}),
            ("create_reminder", {"application_id": card["application_id"], "remind_at": "nope"}),
            ("add_application", {"job_description": "JD"}),
            ("generate_cover_letter", {"resume_id": resume["resume_id"]}),
            ("check_duplicate", {"company": "x", "role": "y"}),
        ]
        for name, args in hostile_calls:
            res = _post(
                client,
                {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                 "params": {"name": name, "arguments": args}},
                token,
            )
            assert res.status_code != 500, name
            assert "Traceback" not in res.text, name
            assert ".py" not in res.text, name
            assert "/app/" not in res.text, name

    async def test_logs_never_contain_token_material(
        self, mcp_client, isolated_db, owner_id, caplog
    ):
        """Across a valid call, hostile bearers, and a verifier outage, the
        application logs record no raw token and no sha256 material.

        Asserted at the app's configured INFO level (production LOG_LEVEL). A
        library-internal DEBUG channel (aiosqlite) echoes every SQL query
        parameter - including token hashes and password hashes, app-wide - so
        DEBUG database logging is out of scope here and noted in the task
        report as a pre-existing, non-MCP property.
        """
        from app.auth.mcp_tokens import get_mcp_token_service

        client, token = mcp_client
        token_hash = hashlib.sha256(token.encode()).hexdigest()

        async def _outage(raw: str):
            raise RuntimeError("simulated outage under attack")

        with caplog.at_level(logging.INFO):
            # A valid call.
            _post(client, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, token)
            # Hostile bearers.
            for bad in MALFORMED_BEARERS:
                _post(client, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, bad)
            # A cross-user attack.
            token_b = await _other_users_token(isolated_db)
            _call(client, token_b, "list_resumes", {})
            # An infra outage mid-attack.
            real_verify = get_mcp_token_service().verify
            get_mcp_token_service().verify = _outage
            try:
                _post(client, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, token)
            finally:
                get_mcp_token_service().verify = real_verify

        assert token not in caplog.text, "raw token leaked to logs"
        assert token_hash not in caplog.text, "token hash leaked to logs"
        assert token_b not in caplog.text, "attacker token leaked to logs"

    async def test_listing_payloads_carry_no_hash_or_foreign_identity_fields(
        self, mcp_client, isolated_db, owner_id
    ):
        """Every listing tool's payload is caller-scoped: no token_hash, no
        user ids other than the caller's own rows, no other emails."""
        client, token = mcp_client
        await _seed_resume(isolated_db, owner_id)
        await _seed_card(isolated_db, owner_id)
        victim_email = "victim@example.com"
        victim = await _seed_user(isolated_db, victim_email)
        await _seed_resume(isolated_db, victim)
        await _seed_card(isolated_db, victim, company="Victim Co")

        for name, args in (
            ("list_resumes", {}),
            ("list_applications", {}),
            ("get_apply_queue", {}),
        ):
            body = _post(
                client,
                {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                 "params": {"name": name, "arguments": args}},
                token,
            )
            assert victim_email not in body.text, name
            assert "token_hash" not in body.text, name
            assert "Victim Co" not in body.text, name


# ---------------------------------------------------------------------------
# Attack class 7: CSRF / session boundary
# ---------------------------------------------------------------------------


@pytest.fixture
async def session_client(auth_env, mcp_app, mcp_token, isolated_db, monkeypatch):
    """A hosted-mode TestClient (https base_url so the cookie jar works) that
    can establish a REAL browser session through the login endpoints."""
    from app.applications import submissions

    monkeypatch.setattr(submissions, "db", isolated_db)
    monkeypatch.setattr(app_settings, "single_user_mode", False)
    app = mcp_app(True)
    with TestClient(app, base_url="https://test") as client:
        yield client


async def _login(client: TestClient, email: str, password: str) -> None:
    """The real login flow: pre-session CSRF, then POST /auth/login."""
    csrf = client.get("/api/v1/auth/csrf").json()["csrfToken"]
    res = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == 200, res.text


class TestCsrfBoundary:
    async def test_valid_session_cookie_does_not_authenticate_the_mount(
        self, session_client, isolated_db
    ):
        """A fully valid browser session (cookie jar full) POSTing to the MCP
        mount WITHOUT a bearer is refused - twice over:

        - without the session CSRF header, the hosted CSRF middleware blocks
          the mutation at 403 before the mount is ever reached;
        - WITH the CSRF header (a fully well-formed browser request), the
          mount itself rejects it at 401: cookies are not MCP credentials.
        """
        from app.auth.accounts import create_user
        from app.auth.passwords import get_password_service

        client = session_client
        await create_user(
            email="session@example.com",
            name="S",
            password_hash=get_password_service().hash_password(
                "correct-horse-battery-staple-9"
            ),
            role="user",
            status="active",
            email_verified_at="2024-01-01T00:00:00+00:00",
            db=isolated_db,
        )
        await _login(client, "session@example.com", "correct-horse-battery-staple-9")

        # The session is real: the REST API accepts it.
        assert client.get("/api/v1/resumes/list").status_code == 200

        # 1) No CSRF header: blocked upstream of the mount (403, no bypass).
        res = client.post(MCP, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert res.status_code == 403, res.text
        assert res.json()["detail"] == "csrf_failed"

        # 2) Full well-formed browser request (CSRF header present): the mount
        #    itself must still demand a bearer.
        client.headers["X-CSRF-Token"] = client.cookies.get("csrf")
        for body in (
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
             "params": {"name": "list_resumes", "arguments": {}}},
        ):
            res = client.post(MCP, json=body)
            assert res.status_code == 401, res.text

    async def test_bearer_is_ignored_by_rest_routes(
        self, session_client, mcp_client
    ):
        """The mirror image: a bearer token (and no cookie) on REST routes is
        ignored - the Authorization header grants nothing outside the mount."""
        client = session_client
        _, token = mcp_client

        res = client.get(
            "/api/v1/resumes/list", headers={"Authorization": f"Bearer {token}"}
        )
        assert res.status_code == 401

        res = client.post(
            "/api/v1/mcp/tokens",
            json={"label": "escalate"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 401

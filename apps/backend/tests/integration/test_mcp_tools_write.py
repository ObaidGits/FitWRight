"""MCP write tools (Task 6): add application, update status, reminders.

Same harness as ``test_mcp_tools_read.py``: one ``tools/call`` JSON-RPC POST
against the real mounted FastMCP app (bearer ``fw_`` token), over the isolated
temp DB. The write boundary matters most here: a mutation requested with user
B's token against user A's rows must fail as not-found and change nothing.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.models import User

pytestmark = pytest.mark.integration

MCP = "/api/v1/mcp/"

WRITE_TOOLS = {
    "add_application",
    "update_application_status",
    "list_reminders",
    "create_reminder",
}


def _call(client: TestClient, token: str, name: str, arguments: dict):
    """One ``tools/call`` JSON-RPC round-trip; returns the parsed body."""
    return client.post(
        MCP,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
        headers={"Authorization": f"Bearer {token}"},
    ).json()


def _tools_list(client: TestClient, token: str):
    return client.post(
        MCP,
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={"Authorization": f"Bearer {token}"},
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


@pytest.fixture
async def mcp_client(auth_env, mcp_app, mcp_token, isolated_db, monkeypatch):
    """A live MCP-mounted TestClient plus ``(client, owner_token, db)``.

    Same pattern as ``test_mcp_tools_read.py``: ``submissions`` captured ``db``
    at import time and is re-pointed at this test's isolated DB.
    """
    from app.applications import submissions

    monkeypatch.setattr(submissions, "db", isolated_db)
    app = mcp_app(True)
    with TestClient(app) as client:
        yield client, mcp_token["raw"]


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
    """A tracker card owned by ``user_id`` (job + application, REST shape)."""
    job = await db.create_job(user_id, content="JD")
    defaults = dict(
        job_id=job["job_id"], resume_id="r1", status="applied",
        company="Acme", role="SRE",
    )
    defaults.update(kw)
    return await db.create_application(user_id, **defaults)


async def _other_users_token(db, owner_id: str) -> str:
    """A second, unrelated user's MCP bearer token (cross-user boundary)."""
    from app.auth.mcp_tokens import get_mcp_token_service

    user_b = await _seed_user(db, "b@example.com")
    _, raw_b = await get_mcp_token_service().issue(user_b, "b-client")
    return raw_b


def _future_iso() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()


# ---------------------------------------------------------------------------
# 1. add_application
# ---------------------------------------------------------------------------


class TestAddApplication:
    async def test_happy_path_creates_applied_card(self, mcp_client, isolated_db, owner_id):
        client, token = mcp_client
        resume = await _seed_resume(isolated_db, owner_id)

        payload = _ok(
            _call(
                client,
                token,
                "add_application",
                {
                    "job_description": "We need an SRE to run Kubernetes.",
                    "company": "Acme",
                    "role": "Site Reliability Engineer",
                    "resume_id": resume["resume_id"],
                },
            )
        )

        assert payload["application_id"]
        assert payload["status"] == "applied"  # REST default
        assert payload["company"] == "Acme"
        assert payload["role"] == "Site Reliability Engineer"
        assert payload["resume_id"] == resume["resume_id"]
        # The pasted JD is stored on the linked job (REST manual-add behavior).
        detail = await isolated_db.get_application_detail(
            owner_id, payload["application_id"]
        )
        assert detail["job_content"] == "We need an SRE to run Kubernetes."

    async def test_oversized_job_description_is_accepted(self, mcp_client, isolated_db, owner_id):
        """REST's ManualApplicationCreate has min_length=1 and no upper bound,
        so a 10k-char JD must be accepted verbatim (no invented tool limit)."""
        client, token = mcp_client
        resume = await _seed_resume(isolated_db, owner_id)
        big_jd = "x" * 10_000

        payload = _ok(
            _call(
                client,
                token,
                "add_application",
                {
                    "job_description": big_jd,
                    "company": "Acme",
                    "role": "SRE",
                    "resume_id": resume["resume_id"],
                },
            )
        )

        detail = await isolated_db.get_application_detail(
            owner_id, payload["application_id"]
        )
        assert detail["job_content"] == big_jd

    async def test_missing_resume_id_is_actionable_error(self, mcp_client):
        client, token = mcp_client
        text = _error_text(
            _call(
                client,
                token,
                "add_application",
                {"job_description": "JD", "company": "Acme", "role": "SRE"},
            )
        )
        assert "resume_id_required" in text
        assert "list_resumes" in text  # tells the client what to do next
        assert "Traceback" not in text

    async def test_empty_job_description_is_rejected(self, mcp_client, isolated_db, owner_id):
        """REST bounds job_description at min_length=1; the tool mirrors it."""
        client, token = mcp_client
        resume = await _seed_resume(isolated_db, owner_id)
        text = _error_text(
            _call(
                client,
                token,
                "add_application",
                {"job_description": "", "resume_id": resume["resume_id"]},
            )
        )
        assert "job_description" in text
        assert "Traceback" not in text

    async def test_extracts_company_role_when_omitted(
        self, mcp_client, isolated_db, owner_id, monkeypatch
    ):
        """No company/role given -> the REST handler's best-effort extraction
        fills them in (the tool reuses the same helper, not a copy)."""
        client, token = mcp_client
        resume = await _seed_resume(isolated_db, owner_id)

        async def fake_extract(jd: str) -> dict:
            return {"company": "Globex", "role": "Platform Engineer"}

        monkeypatch.setattr(
            "app.routers.applications._extract_company_role", fake_extract
        )

        payload = _ok(
            _call(
                client,
                token,
                "add_application",
                {"job_description": "JD text", "resume_id": resume["resume_id"]},
            )
        )

        assert payload["company"] == "Globex"
        assert payload["role"] == "Platform Engineer"


# ---------------------------------------------------------------------------
# 2. update_application_status
# ---------------------------------------------------------------------------


class TestUpdateApplicationStatus:
    async def test_moves_card_to_new_column(self, mcp_client, isolated_db, owner_id):
        client, token = mcp_client
        card = await _seed_card(isolated_db, owner_id, status="applied")

        payload = _ok(
            _call(
                client,
                token,
                "update_application_status",
                {"application_id": card["application_id"], "status": "interview"},
            )
        )

        assert payload["application_id"] == card["application_id"]
        assert payload["status"] == "interview"
        # Persisted, not just echoed back.
        detail = await isolated_db.get_application_detail(
            owner_id, card["application_id"]
        )
        assert detail["status"] == "interview"

    async def test_invalid_status_is_actionable_error(self, mcp_client, isolated_db, owner_id):
        client, token = mcp_client
        card = await _seed_card(isolated_db, owner_id)

        text = _error_text(
            _call(
                client,
                token,
                "update_application_status",
                {"application_id": card["application_id"], "status": "bogus"},
            )
        )

        assert "invalid_status" in text
        assert "interview" in text  # the valid values are listed in the error
        assert "Traceback" not in text

    async def test_cross_user_update_is_not_found_and_changes_nothing(
        self, mcp_client, isolated_db, owner_id
    ):
        client, token_a = mcp_client
        card = await _seed_card(isolated_db, owner_id, status="applied")
        raw_b = await _other_users_token(isolated_db, owner_id)

        text = _error_text(
            _call(
                client,
                raw_b,
                "update_application_status",
                {"application_id": card["application_id"], "status": "rejected"},
            )
        )

        assert "application_not_found" in text
        assert "Traceback" not in text
        # A's card is untouched.
        detail = await isolated_db.get_application_detail(
            owner_id, card["application_id"]
        )
        assert detail["status"] == "applied"

    async def test_unknown_application_is_actionable_error(self, mcp_client):
        client, token = mcp_client
        text = _error_text(
            _call(
                client,
                token,
                "update_application_status",
                {"application_id": "ghost", "status": "interview"},
            )
        )
        assert "application_not_found" in text
        assert "list_applications" in text
        assert "Traceback" not in text


# ---------------------------------------------------------------------------
# 3. create_reminder + list_reminders
# ---------------------------------------------------------------------------


class TestCreateReminder:
    async def test_happy_path_creates_and_lists(self, mcp_client, isolated_db, owner_id):
        client, token = mcp_client
        card = await _seed_card(isolated_db, owner_id)

        created = _ok(
            _call(
                client,
                token,
                "create_reminder",
                {
                    "application_id": card["application_id"],
                    "remind_at": _future_iso(),
                    "note": "Follow up with the recruiter",
                },
            )
        )

        assert created["id"]
        assert created["application_id"] == card["application_id"]
        assert created["note"] == "Follow up with the recruiter"
        assert created["due_at"]

        listed = _ok(
            _call(
                client,
                token,
                "list_reminders",
                {"application_id": card["application_id"]},
            )
        )
        assert listed["total"] == 1
        assert listed["reminders"][0]["id"] == created["id"]

    async def test_cross_user_application_is_not_found(
        self, mcp_client, isolated_db, owner_id
    ):
        client, _ = mcp_client
        card = await _seed_card(isolated_db, owner_id)
        raw_b = await _other_users_token(isolated_db, owner_id)

        text = _error_text(
            _call(
                client,
                raw_b,
                "create_reminder",
                {"application_id": card["application_id"], "remind_at": _future_iso()},
            )
        )

        assert "application_not_found" in text
        assert "Traceback" not in text
        # Nothing was created against A's card.
        listed = _ok(
            _call(
                client,
                mcp_client[1],
                "list_reminders",
                {"application_id": card["application_id"]},
            )
        )
        assert listed["total"] == 0

    async def test_non_iso_remind_at_is_actionable_error(self, mcp_client, isolated_db, owner_id):
        client, token = mcp_client
        card = await _seed_card(isolated_db, owner_id)

        text = _error_text(
            _call(
                client,
                token,
                "create_reminder",
                {"application_id": card["application_id"], "remind_at": "tomorrow-ish"},
            )
        )

        assert "invalid_reminder" in text
        assert "ISO-8601" in text
        assert "Traceback" not in text

    async def test_oversized_note_is_rejected(self, mcp_client, isolated_db, owner_id):
        """REST's ReminderCreate bounds note at 1000 chars; the tool mirrors it."""
        client, token = mcp_client
        card = await _seed_card(isolated_db, owner_id)

        text = _error_text(
            _call(
                client,
                token,
                "create_reminder",
                {
                    "application_id": card["application_id"],
                    "remind_at": _future_iso(),
                    "note": "n" * 1001,
                },
            )
        )

        assert "note" in text
        assert "Traceback" not in text


class TestListReminders:
    async def test_unknown_application_is_not_found(self, mcp_client):
        client, token = mcp_client
        text = _error_text(
            _call(client, token, "list_reminders", {"application_id": "ghost"})
        )
        assert "application_not_found" in text
        assert "list_applications" in text
        assert "Traceback" not in text

    async def test_empty_list_for_application_without_reminders(
        self, mcp_client, isolated_db, owner_id
    ):
        client, token = mcp_client
        card = await _seed_card(isolated_db, owner_id)

        payload = _ok(
            _call(client, token, "list_reminders", {"application_id": card["application_id"]})
        )
        assert payload == {"reminders": [], "total": 0}


# ---------------------------------------------------------------------------
# 4. Tool schemas
# ---------------------------------------------------------------------------


class TestToolSchemas:
    async def test_tools_list_has_all_write_tools_with_required_params(self, mcp_client):
        client, token = mcp_client
        tools = {
            t["name"]: t for t in _tools_list(client, token)["result"]["tools"]
        }

        assert WRITE_TOOLS <= set(tools), f"missing: {WRITE_TOOLS - set(tools)}"

        # Params the client MUST supply are marked required; auth params never
        # leak into the schema (the token arrives via the Authorization header).
        assert tools["add_application"]["inputSchema"]["required"] == ["job_description"]
        assert set(tools["update_application_status"]["inputSchema"]["required"]) == {
            "application_id",
            "status",
        }
        assert tools["list_reminders"]["inputSchema"]["required"] == ["application_id"]
        assert set(tools["create_reminder"]["inputSchema"]["required"]) == {
            "application_id",
            "remind_at",
        }

"""MCP read tools (Task 5): resumes, applications, queue, duplicates.

Each test is one ``tools/call`` JSON-RPC POST against the real mounted FastMCP
app (bearer ``fw_`` token from the ``mcp_token`` fixture), over the isolated
temp DB. The tools resolve the caller from the token claims (``sub``), so the
cross-user test is the real boundary: user B's token must get errors/empties
for user A's rows, never A's data.
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.models import User

pytestmark = pytest.mark.integration

MCP = "/api/v1/mcp/"

ALL_TOOLS = {
    "list_resumes",
    "get_resume",
    "list_applications",
    "get_application",
    "get_apply_queue",
    "check_duplicate",
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
    """Assert a successful tool result and return its payload.

    FastMCP returns the dict either as ``structuredContent`` (MCP spec) or as
    JSON text content, depending on client capabilities - accept both.
    """
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

    ``app.applications.submissions`` captured ``db`` at import time, so it is
    re-pointed at this test's isolated DB (same pattern as
    ``test_application_submissions``); the tool modules themselves resolve
    ``app.database.db`` at call time and need no patching.
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


# ---------------------------------------------------------------------------
# 1. list_resumes
# ---------------------------------------------------------------------------


class TestListResumes:
    async def test_returns_lightweight_summaries_not_full_documents(
        self, mcp_client, isolated_db, owner_id
    ):
        client, token = mcp_client
        await _seed_resume(isolated_db, owner_id, filename="a.md")
        await _seed_resume(
            isolated_db, owner_id, filename="b.md", title="B - tailored"
        )

        payload = _ok(_call(client, token, "list_resumes", {}))

        resumes = payload["resumes"]
        assert len(resumes) == 2
        for item in resumes:
            # Identity + freshness fields only.
            assert item["resume_id"]
            assert item["filename"]
            assert item["updated_at"]
            # The whole point of the summary: never ship the parsed resume.
            assert "processed_data" not in item
            assert "content" not in item

    async def test_new_user_has_empty_list(self, mcp_client):
        client, token = mcp_client
        payload = _ok(_call(client, token, "list_resumes", {}))
        assert payload == {"resumes": []}


# ---------------------------------------------------------------------------
# 2. get_resume
# ---------------------------------------------------------------------------


class TestGetResume:
    async def test_happy_path_returns_full_resume(self, mcp_client, isolated_db, owner_id):
        client, token = mcp_client
        seeded = await _seed_resume(
            isolated_db,
            owner_id,
            content="# Jane Doe",
            processed_data={"skills": ["kubernetes"]},
        )

        payload = _ok(_call(client, token, "get_resume", {"resume_id": seeded["resume_id"]}))

        assert payload["resume_id"] == seeded["resume_id"]
        assert payload["content"] == "# Jane Doe"
        assert payload["processed_data"] == {"skills": ["kubernetes"]}

    async def test_unknown_id_is_actionable_tool_error(self, mcp_client):
        client, token = mcp_client
        text = _error_text(_call(client, token, "get_resume", {"resume_id": "nope"}))
        assert "resume_not_found" in text
        assert "list_resumes" in text  # tells the client what to do next
        assert "Traceback" not in text


# ---------------------------------------------------------------------------
# 3. list_applications
# ---------------------------------------------------------------------------


class TestListApplications:
    async def test_groups_cards_by_status(self, mcp_client, isolated_db, owner_id):
        client, token = mcp_client
        await isolated_db.create_application(
            owner_id, job_id="j1", resume_id="r1", status="applied",
            company="Acme", role="SRE",
        )
        await isolated_db.create_application(
            owner_id, job_id="j2", resume_id="r2", status="saved",
            company="Beta", role="Dev",
        )

        payload = _ok(_call(client, token, "list_applications", {}))

        columns = payload["columns"]
        assert [c["company"] for c in columns["applied"]] == ["Acme"]
        assert [c["role"] for c in columns["saved"]] == ["Dev"]
        assert payload["total"] == 2

    async def test_new_user_has_empty_board(self, mcp_client):
        client, token = mcp_client
        payload = _ok(_call(client, token, "list_applications", {}))
        assert payload == {"columns": {}, "total": 0}


# ---------------------------------------------------------------------------
# 4. get_application
# ---------------------------------------------------------------------------


class TestGetApplication:
    async def test_happy_path_returns_card_with_detail(self, mcp_client, isolated_db, owner_id):
        client, token = mcp_client
        card = await isolated_db.create_application(
            owner_id, job_id="j1", resume_id="r1", status="applied",
            company="Acme", role="SRE",
        )

        payload = _ok(
            _call(client, token, "get_application", {"application_id": card["application_id"]})
        )

        assert payload["application_id"] == card["application_id"]
        assert payload["company"] == "Acme"

    async def test_unknown_id_is_actionable_tool_error(self, mcp_client):
        client, token = mcp_client
        text = _error_text(
            _call(client, token, "get_application", {"application_id": "nope"})
        )
        assert "application_not_found" in text
        assert "list_applications" in text
        assert "Traceback" not in text


# ---------------------------------------------------------------------------
# 5. get_apply_queue + check_duplicate
# ---------------------------------------------------------------------------


class TestApplyQueue:
    async def test_lists_saved_cards_in_queue_order(self, mcp_client, isolated_db, owner_id):
        client, token = mcp_client
        await isolated_db.create_application(
            owner_id, job_id="j1", resume_id="r1", status="saved",
            company="Acme", role="SRE",
        )
        await isolated_db.create_application(
            owner_id, job_id="j2", resume_id="r2", status="saved",
            company="Beta", role="Dev",
        )
        # Applied cards are not queue work.
        await isolated_db.create_application(
            owner_id, job_id="j3", resume_id="r3", status="applied",
            company="Gamma", role="Ops",
        )

        payload = _ok(_call(client, token, "get_apply_queue", {}))

        assert payload["total"] == 2
        assert [q["company"] for q in payload["queue"]] == ["Acme", "Beta"]


class TestCheckDuplicate:
    async def test_finds_live_match_case_insensitively(self, mcp_client, isolated_db, owner_id):
        client, token = mcp_client
        await isolated_db.create_application(
            owner_id, job_id="j1", resume_id="r1", status="applied",
            company="Acme Corp", role="Site Reliability Engineer",
        )

        payload = _ok(
            _call(client, token, "check_duplicate", {"company": "acme corp", "role": "site reliability engineer"})
        )

        assert payload["is_duplicate"] is True
        assert payload["application"]["company"] == "Acme Corp"
        assert payload["application"]["application_id"]

    async def test_different_role_is_not_a_duplicate(self, mcp_client, isolated_db, owner_id):
        client, token = mcp_client
        await isolated_db.create_application(
            owner_id, job_id="j1", resume_id="r1", status="applied",
            company="Acme Corp", role="Site Reliability Engineer",
        )

        payload = _ok(
            _call(client, token, "check_duplicate", {"company": "Acme Corp", "role": "Product Manager"})
        )

        assert payload == {"is_duplicate": False, "application": None}


# ---------------------------------------------------------------------------
# 6. Cross-user isolation
# ---------------------------------------------------------------------------


class TestCrossUserIsolation:
    async def test_user_b_token_never_sees_user_a_rows(self, mcp_client, isolated_db, owner_id):
        client, token_a = mcp_client
        seeded = await _seed_resume(isolated_db, owner_id, filename="a.md")

        from app.auth.mcp_tokens import get_mcp_token_service

        user_b = await _seed_user(isolated_db, "b@example.com")
        _, raw_b = await get_mcp_token_service().issue(user_b, "b-client")

        # B asking for A's resume id: 404-style tool error, never A's data.
        text = _error_text(_call(client, raw_b, "get_resume", {"resume_id": seeded["resume_id"]}))
        assert "resume_not_found" in text

        # B's own listing is empty.
        payload = _ok(_call(client, raw_b, "list_resumes", {}))
        assert payload == {"resumes": []}

        # Sanity: A's own token still sees the row.
        assert len(_ok(_call(client, token_a, "list_resumes", {}))["resumes"]) == 1

    async def test_user_b_duplicate_check_ignores_user_a_application(
        self, mcp_client, isolated_db, owner_id
    ):
        client, _ = mcp_client
        await isolated_db.create_application(
            owner_id, job_id="j1", resume_id="r1", status="applied",
            company="Acme", role="SRE",
        )

        from app.auth.mcp_tokens import get_mcp_token_service

        user_b = await _seed_user(isolated_db, "b@example.com")
        _, raw_b = await get_mcp_token_service().issue(user_b, "b-client")

        payload = _ok(
            _call(client, raw_b, "check_duplicate", {"company": "Acme", "role": "SRE"})
        )
        assert payload == {"is_duplicate": False, "application": None}


# ---------------------------------------------------------------------------
# 7. Tool schemas
# ---------------------------------------------------------------------------


class TestToolSchemas:
    async def test_tools_list_has_all_read_tools_with_required_params(self, mcp_client):
        client, token = mcp_client
        tools = {
            t["name"]: t for t in _tools_list(client, token)["result"]["tools"]
        }

        assert ALL_TOOLS <= set(tools), f"missing: {ALL_TOOLS - set(tools)}"

        for name, tool in tools.items():
            assert "inputSchema" in tool, name

        # Params the client MUST supply are marked required; auth params never
        # leak into the schema (the token arrives via the Authorization header).
        assert tools["get_resume"]["inputSchema"]["required"] == ["resume_id"]
        assert set(tools["get_application"]["inputSchema"]["required"]) == {
            "application_id",
        }
        assert set(tools["check_duplicate"]["inputSchema"]["required"]) == {
            "company",
            "role",
        }
        assert tools["list_resumes"]["inputSchema"].get("required", []) == []
        assert tools["list_applications"]["inputSchema"].get("required", []) == []
        assert tools["get_apply_queue"]["inputSchema"].get("required", []) == []

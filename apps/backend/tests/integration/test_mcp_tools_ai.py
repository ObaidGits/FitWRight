"""MCP AI tools (Task 7): cover letter + interview prep, billed like REST.

The billing contract is the point: an MCP tool call must hit the SAME two
guards, in the SAME order, as the REST route
(``llm_rate_limit_dep`` then ``ai_metered(feature)``), and must produce the
same ledger row the REST endpoint produces for the same input. The parity
tests drive the real REST endpoint (same router, same dependencies, auth
dependency overridden to the test user) and compare what each path charged.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.models import CreditAccount, User
from tests.integration.conftest import (
    mcp_call as _call,
    mcp_error_text as _error_text,
    mcp_ok as _ok,
    mcp_tools_list as _tools_list,
)

pytestmark = pytest.mark.integration

AI_TOOLS = {"generate_cover_letter", "generate_interview_prep"}


# ---------------------------------------------------------------------------
# Fixtures and seeding
# ---------------------------------------------------------------------------


@pytest.fixture
def credits_on(monkeypatch):
    """Enable credit charging for this test; nobody is on their own key
    (the own-key path gets its own dedicated test below)."""
    from app.config import settings

    monkeypatch.setattr(settings, "ai_credits_enabled", True)
    monkeypatch.setattr("app.ai_metered.user_has_own_key", lambda _uid: False)
    return settings


@pytest.fixture
def llm_mocked(monkeypatch):
    """Fake the provider boundary the REST handlers call - no real LLM in CI.

    ``generate_cover_letter`` / ``generate_interview_prep`` are module-level
    names in ``app.routers.resumes`` (imported from the services), so patching
    them there is exactly what the endpoint executes. The underlying litellm
    call sites are additionally booby-trapped: if any code path drifts toward
    a real provider call, the test fails loudly instead of silently paying.
    """
    from app.routers import resumes as resumes_router
    from app.schemas.models import InterviewPrepData

    cover_letter = AsyncMock(return_value="Dear Hiring Team, I am excited to apply.")
    # The real service returns a validated InterviewPrepData instance (the
    # endpoint serializes it with .model_dump()), so the mock must too.
    interview_prep = AsyncMock(
        return_value=InterviewPrepData.model_validate(_interview_prep_payload())
    )

    def _no_real_provider(*args, **kwargs):
        raise AssertionError("real LLM provider call during test")

    monkeypatch.setattr(resumes_router, "generate_cover_letter", cover_letter)
    monkeypatch.setattr(resumes_router, "generate_interview_prep", interview_prep)
    monkeypatch.setattr("app.llm.litellm.acompletion", _no_real_provider)
    return {"cover_letter": cover_letter, "interview_prep": interview_prep}


def _interview_prep_payload() -> dict:
    """A valid InterviewPrepData-shaped dict (the mocked LLM output)."""
    return {
        "role_fit_analysis": ["Strong infrastructure background"],
        "resume_questions": [
            {
                "question": "Tell me about running Kubernetes in production",
                "focus_area": "infrastructure",
                "suggested_answer_points": ["Ran multi-cluster SRE on-call"],
            }
        ],
        "project_follow_ups": [],
        "skill_gaps": [
            {
                "skill": "Terraform",
                "why_it_matters": "The JD lists IaC as required",
                "preparation_suggestion": "Build a small lab project",
            }
        ],
        "talking_points": ["Led the migration to microservices"],
    }


async def _fund(db, user_id: str, *, allowance: int = 0, wallet: int = 0):
    """Create an account and fund it directly (test_ai_metered_endpoint pattern)."""
    await db.get_or_create_credit_account(user_id)
    async with db.session_factory() as session:
        row = await session.get(CreditAccount, user_id)
        row.allowance_credits = allowance
        row.wallet_credits = wallet
        # Stamp the CURRENT period so a zero allowance means "out of credits",
        # not "never granted" (which the lazy refill would top up to 50).
        row.allowance_period_start = datetime.now(timezone.utc).isoformat()
        await session.commit()


async def _seed_tailored_resume(db, user_id: str, **kwargs) -> dict:
    """A tailored resume with its job context, ready for AI generation.

    The parent is the user's master resume, created on first use and reused
    after that (the single-master invariant means a second master insert would
    fail, and a test seeding two tailored resumes hits this immediately).
    """
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


def _rest_call(user_id: str, path: str):
    """POST a real REST AI endpoint with the metering dependencies intact.

    A throwaway app mounting the REAL resumes router - the route dependencies
    (``llm_rate_limit_dep`` + ``ai_metered``) run exactly as in production;
    only the auth dependency is overridden to the test user (the
    ``test_ai_metered_endpoint`` pattern, applied to the real routes).
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient as _TestClient

    from app.auth import get_effective_user_id, require_verified_user_id
    from app.errors import install_error_handlers
    from app.routers import resumes as resumes_router

    app = FastAPI()
    install_error_handlers(app)
    app.dependency_overrides[get_effective_user_id] = lambda: user_id
    app.dependency_overrides[require_verified_user_id] = lambda: user_id
    app.include_router(resumes_router.router)
    with _TestClient(app) as client:
        return client.post(path)


# ---------------------------------------------------------------------------
# 1. generate_cover_letter
# ---------------------------------------------------------------------------


class TestGenerateCoverLetter:
    async def test_bills_exactly_like_the_rest_endpoint(
        self, mcp_client, isolated_db, owner_id, credits_on, llm_mocked
    ):
        """THE parity test: same input through REST and through MCP must land
        the same ledger row - same feature name, same charge, hold released."""
        client, token = mcp_client
        await _fund(isolated_db, owner_id, wallet=100)
        rest_resume = await _seed_tailored_resume(isolated_db, owner_id)
        mcp_resume = await _seed_tailored_resume(isolated_db, owner_id)

        rest = _rest_call(
            owner_id, f"/resumes/{rest_resume['resume_id']}/generate-cover-letter"
        )
        assert rest.status_code == 200, rest.text

        payload = _ok(
            _call(client, token, "generate_cover_letter", {"resume_id": mcp_resume["resume_id"]})
        )
        assert payload["content"] == "Dear Hiring Team, I am excited to apply."

        rows = await isolated_db.list_usage(owner_id)  # newest first
        assert len(rows) == 2, "one ledger row per generation"
        assert rows[0]["feature"] == rows[1]["feature"] == "cover_letter"
        assert rows[0]["credits_charged"] == rows[1]["credits_charged"] > 0
        assert rows[0]["outcome"] == rows[1]["outcome"] == "ok"

        account = await isolated_db.get_or_create_credit_account(owner_id)
        assert account["reserved_credits"] == 0, "hold leaked"
        assert account["wallet_credits"] == 100 - 2 * rows[0]["credits_charged"]
        assert llm_mocked["cover_letter"].await_count == 2

    async def test_zero_balance_is_refused_before_any_work(
        self, mcp_client, isolated_db, owner_id, credits_on, llm_mocked
    ):
        """402-equivalent refusal, BEFORE the LLM runs - no partial work."""
        client, token = mcp_client
        await _fund(isolated_db, owner_id, wallet=0)
        resume = await _seed_tailored_resume(isolated_db, owner_id)

        text = _error_text(
            _call(client, token, "generate_cover_letter", {"resume_id": resume["resume_id"]})
        )

        assert "insufficient_credits" in text
        assert "Traceback" not in text
        # No work: no provider call, nothing saved, nothing metered, no hold.
        llm_mocked["cover_letter"].assert_not_awaited()
        stored = await isolated_db.get_resume(owner_id, resume["resume_id"])
        assert stored.get("cover_letter") is None
        assert await isolated_db.list_usage(owner_id) == []
        account = await isolated_db.get_or_create_credit_account(owner_id)
        assert account["reserved_credits"] == 0

    async def test_rate_limited_user_gets_rate_limited_error(
        self, mcp_client, isolated_db, owner_id, credits_on, llm_mocked, monkeypatch
    ):
        """The per-user LLM rate limit guards the MCP tool exactly like the
        REST route dependency - and BEFORE any billing or work."""
        from app.config import settings

        monkeypatch.setattr(settings, "llm_rate_per_min_user", 1)
        client, token = mcp_client
        await _fund(isolated_db, owner_id, wallet=100)
        resume = await _seed_tailored_resume(isolated_db, owner_id)

        first = _ok(
            _call(client, token, "generate_cover_letter", {"resume_id": resume["resume_id"]})
        )
        assert first["content"]

        second = _error_text(
            _call(client, token, "generate_cover_letter", {"resume_id": resume["resume_id"]})
        )
        assert "rate_limited" in second
        assert "Traceback" not in second
        # The refused call did not reach the provider - and was never billed.
        assert llm_mocked["cover_letter"].await_count == 1
        rows = await isolated_db.list_usage(owner_id)
        assert len(rows) == 1, "the rate-limited call must not appear in the ledger"
    async def test_own_key_user_is_metered_but_charged_nothing(
        self, mcp_client, isolated_db, owner_id, credits_on, llm_mocked, monkeypatch
    ):
        """user_has_own_key path: same metering, zero charge - even though the
        account is empty (bringing your own key is free forever)."""
        monkeypatch.setattr("app.ai_metered.user_has_own_key", lambda _uid: True)
        client, token = mcp_client
        await _fund(isolated_db, owner_id, wallet=0)
        resume = await _seed_tailored_resume(isolated_db, owner_id)

        payload = _ok(
            _call(client, token, "generate_cover_letter", {"resume_id": resume["resume_id"]})
        )
        assert payload["content"]

        rows = await isolated_db.list_usage(owner_id)
        assert len(rows) == 1, "metered for observability"
        assert rows[0]["feature"] == "cover_letter"
        assert rows[0]["credits_charged"] == 0
        account = await isolated_db.get_or_create_credit_account(owner_id)
        assert account["wallet_credits"] == 0

    async def test_llm_is_mocked_no_real_provider_calls(
        self, mcp_client, isolated_db, owner_id, credits_on, llm_mocked
    ):
        """The tool path runs entirely against the faked provider: any drift
        toward a real litellm call trips the booby trap in ``llm_mocked``."""
        client, token = mcp_client
        await _fund(isolated_db, owner_id, wallet=100)
        resume = await _seed_tailored_resume(isolated_db, owner_id)

        payload = _ok(
            _call(client, token, "generate_cover_letter", {"resume_id": resume["resume_id"]})
        )
        assert payload["content"] == "Dear Hiring Team, I am excited to apply."
        llm_mocked["cover_letter"].assert_awaited_once()

    async def test_unknown_resume_is_actionable_error(
        self, mcp_client, isolated_db, owner_id, credits_on
    ):
        client, token = mcp_client
        text = _error_text(
            _call(client, token, "generate_cover_letter", {"resume_id": "ghost"})
        )
        assert "404" in text
        assert "not found" in text.lower()
        assert "Traceback" not in text


# ---------------------------------------------------------------------------
# 2. generate_interview_prep
# ---------------------------------------------------------------------------


class TestGenerateInterviewPrep:
    async def test_bills_exactly_like_the_rest_endpoint(
        self, mcp_client, isolated_db, owner_id, credits_on, llm_mocked
    ):
        """Parity for the second feature: same ledger row via REST and MCP."""
        client, token = mcp_client
        await _fund(isolated_db, owner_id, wallet=200)
        rest_resume = await _seed_tailored_resume(isolated_db, owner_id)
        mcp_resume = await _seed_tailored_resume(isolated_db, owner_id)

        rest = _rest_call(
            owner_id,
            f"/resumes/{rest_resume['resume_id']}/generate-interview-prep",
        )
        assert rest.status_code == 200, rest.text

        payload = _ok(
            _call(client, token, "generate_interview_prep", {"resume_id": mcp_resume["resume_id"]})
        )
        assert payload["interview_prep"]["talking_points"] == [
            "Led the migration to microservices"
        ]

        rows = await isolated_db.list_usage(owner_id)  # newest first
        assert len(rows) == 2
        assert rows[0]["feature"] == rows[1]["feature"] == "interview_prep"
        assert rows[0]["credits_charged"] == rows[1]["credits_charged"] > 0
        assert rows[0]["outcome"] == rows[1]["outcome"] == "ok"

        account = await isolated_db.get_or_create_credit_account(owner_id)
        assert account["reserved_credits"] == 0
        assert account["wallet_credits"] == 200 - 2 * rows[0]["credits_charged"]
        assert llm_mocked["interview_prep"].await_count == 2

    async def test_zero_balance_is_refused_before_any_work(
        self, mcp_client, isolated_db, owner_id, credits_on, llm_mocked
    ):
        client, token = mcp_client
        await _fund(isolated_db, owner_id, wallet=0)
        resume = await _seed_tailored_resume(isolated_db, owner_id)

        text = _error_text(
            _call(client, token, "generate_interview_prep", {"resume_id": resume["resume_id"]})
        )

        assert "insufficient_credits" in text
        llm_mocked["interview_prep"].assert_not_awaited()
        stored = await isolated_db.get_resume(owner_id, resume["resume_id"])
        assert stored.get("interview_prep") is None
        assert await isolated_db.list_usage(owner_id) == []

    async def test_own_key_user_is_metered_but_charged_nothing(
        self, mcp_client, isolated_db, owner_id, credits_on, llm_mocked, monkeypatch
    ):
        monkeypatch.setattr("app.ai_metered.user_has_own_key", lambda _uid: True)
        client, token = mcp_client
        await _fund(isolated_db, owner_id, wallet=0)
        resume = await _seed_tailored_resume(isolated_db, owner_id)

        payload = _ok(
            _call(client, token, "generate_interview_prep", {"resume_id": resume["resume_id"]})
        )
        assert payload["interview_prep"]

        rows = await isolated_db.list_usage(owner_id)
        assert len(rows) == 1
        assert rows[0]["feature"] == "interview_prep"
        assert rows[0]["credits_charged"] == 0

    async def test_rate_limited_user_gets_rate_limited_error(
        self, mcp_client, isolated_db, owner_id, credits_on, llm_mocked, monkeypatch
    ):
        """M2: interview prep is guarded by the same per-user rate limit, before
        any billing or work - mirroring its REST route dependency."""
        from app.config import settings

        monkeypatch.setattr(settings, "llm_rate_per_min_user", 1)
        client, token = mcp_client
        await _fund(isolated_db, owner_id, wallet=200)
        resume = await _seed_tailored_resume(isolated_db, owner_id)

        first = _ok(
            _call(client, token, "generate_interview_prep", {"resume_id": resume["resume_id"]})
        )
        assert first["interview_prep"]

        second = _error_text(
            _call(client, token, "generate_interview_prep", {"resume_id": resume["resume_id"]})
        )
        assert "rate_limited" in second
        assert llm_mocked["interview_prep"].await_count == 1
        rows = await isolated_db.list_usage(owner_id)
        assert len(rows) == 1, "the rate-limited call must not appear in the ledger"

    async def test_regenerate_true_overrides_saved_copy(
        self, mcp_client, isolated_db, owner_id, credits_on, llm_mocked
    ):
        """regenerate=true forces a fresh (billed) generation even when a
        saved copy exists - same semantics as the REST endpoint."""
        client, token = mcp_client
        await _fund(isolated_db, owner_id, wallet=100)
        resume = await _seed_tailored_resume(isolated_db, owner_id)
        await isolated_db.update_resume(owner_id, resume["resume_id"], {"cover_letter": "saved"})

        # cover letter tool: saved copy reused without a provider call...
        reused = _ok(
            _call(client, token, "generate_cover_letter", {"resume_id": resume["resume_id"]})
        )
        assert reused["content"] == "saved"
        llm_mocked["cover_letter"].assert_not_awaited()

        # ...unless regenerate is explicitly requested.
        fresh = _ok(
            _call(
                client,
                token,
                "generate_cover_letter",
                {"resume_id": resume["resume_id"], "regenerate": True},
            )
        )
        assert fresh["content"] == "Dear Hiring Team, I am excited to apply."
        llm_mocked["cover_letter"].assert_awaited_once()


# ---------------------------------------------------------------------------
# 3. Credential resolution (C1 regression)
# ---------------------------------------------------------------------------


class _ProviderSentinel(Exception):
    """Raised by the get_llm_config spy after recording the resolved caller."""


class TestCredentialResolution:
    async def test_own_key_user_call_resolves_their_key_not_the_operators(
        self, mcp_client, isolated_db, owner_id, credits_on, monkeypatch
    ):
        """C1 regression: an own-key user's MCP call must resolve THEIR provider
        key.

        On REST, the ``get_effective_user_id`` dependency publishes the caller
        on the request-scoped ContextVar, and ``get_llm_config`` reads it to
        decide whose encrypted key store to open (R10.6). The MCP tools call
        the handlers outside any request scope, so the tool itself must publish
        the token's user id - otherwise the resolution falls through to the
        bootstrap owner's (the operator's) key while ``metered_ai_call``
        charges nothing: operator-funded free generation.

        The assertion sits at the credential-resolution boundary itself
        (``get_llm_config``), one frame below the service mocks used elsewhere
        in this file. A second user (NOT the bootstrap owner) makes the
        fallback detectable: without the fix the recorded id is the owner's.
        """
        from app.auth.mcp_tokens import get_mcp_token_service

        user_b = str(uuid4())
        async with isolated_db.session_factory() as session:
            session.add(User(id=user_b, email="ownkey@example.com", name="B", role="user", status="active"))
            await session.commit()
        _, raw_b = await get_mcp_token_service().issue(user_b, "b-client")
        resume_b = await _seed_tailored_resume(isolated_db, user_b)

        # user_b is on their own key (the C1 scenario); the owner is not.
        monkeypatch.setattr(
            "app.ai_metered.user_has_own_key", lambda uid: uid == user_b
        )

        from app.services import interview_prep as interview_prep_service

        seen: list[str | None] = []

        def spy(*args, **kwargs):
            from app.auth.context import get_current_user_id

            seen.append(get_current_user_id())
            raise _ProviderSentinel(f"resolved caller: {seen[-1]}")

        # Both entry points the handlers reach: app.llm.get_llm_config (used
        # by complete()/complete_json()) and the copy interview_prep imports.
        monkeypatch.setattr("app.llm.get_llm_config", spy)
        monkeypatch.setattr(interview_prep_service, "get_llm_config", spy)

        client, _ = mcp_client
        expected_rows = 1
        for tool, feature in (
            ("generate_cover_letter", "cover_letter"),
            ("generate_interview_prep", "interview_prep"),
        ):
            seen.clear()
            # The provider "fails" (sentinel); the handler wraps it as a 502
            # ApiError, which the tool renders as an actionable error. What
            # matters is WHICH user the credential resolution saw.
            text = _error_text(_call(client, raw_b, tool, {"resume_id": resume_b["resume_id"]}))
            assert seen == [user_b], (
                f"{tool} resolved credentials for {seen}; expected the token "
                f"owner {user_b} (R10.6: one user's key never serves another's calls)"
            )
            assert "Traceback" not in text
            # The zero-charge own-key ledger row was still written (one per
            # tool iteration - the newest row is the one just recorded).
            rows = await isolated_db.list_usage(user_b)
            assert len(rows) == expected_rows
            assert rows[0]["feature"] == feature
            assert rows[0]["credits_charged"] == 0
            expected_rows += 1


# ---------------------------------------------------------------------------
# 4. Tool schemas
# ---------------------------------------------------------------------------


class TestToolSchemas:
    async def test_tools_list_has_both_ai_tools_with_resume_id_required(
        self, mcp_client
    ):
        client, token = mcp_client
        tools = {
            t["name"]: t for t in _tools_list(client, token)["result"]["tools"]
        }

        assert AI_TOOLS <= set(tools), f"missing: {AI_TOOLS - set(tools)}"

        # resume_id is the one param the client MUST supply; regenerate is
        # optional and the auth token never leaks into the schema.
        for name in AI_TOOLS:
            assert tools[name]["inputSchema"]["required"] == ["resume_id"], name

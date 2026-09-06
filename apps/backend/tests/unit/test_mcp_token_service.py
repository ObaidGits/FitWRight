"""Unit tests for the MCP bearer-token service (app.auth.mcp_tokens).

Covers the six behaviors from the MCP integration plan: issue stores only the
sha256 (raw token starts with ``fw_``), verify accepts an issued token and
rejects unknown/malformed/revoked/expired ones, revoke is scoped to the owner,
list_for_user never exposes ``token_hash``, and touch writes ``last_used_at``
at most once per 60s per token. Hardening additions: verify() also refuses
tokens whose owner is disabled or soft-deleted (mirroring the session path),
expiry comparison is fail-closed (house ``_now_lt_iso``), and issue() enforces
the per-user active-token cap.
"""

from datetime import datetime, timezone

import pytest

from app.auth.mcp_tokens import (
    McpTokenLimitError,
    get_mcp_token_service,
    reset_mcp_token_service,
)


@pytest.fixture
async def users(svc, isolated_db):
    """Two real users (FK on mcp_tokens.user_id is enforced, so tokens need
    actual ``users`` rows). Returns (owner, other)."""
    from app.auth.accounts import create_user

    owner = await create_user(
        email="mcp-owner@test.local", name="Owner", password_hash=None,
        status="active", db=isolated_db,
    )
    other = await create_user(
        email="mcp-other@test.local", name="Other", password_hash=None,
        status="active", db=isolated_db,
    )
    return owner.id, other.id


async def _set_user(db, user_id: str, **values) -> None:
    """Patch a users row directly (status / deleted_at)."""
    from app.models import User

    async with db.session_factory() as s:
        row = await s.get(User, user_id)
        for key, value in values.items():
            setattr(row, key, value)
        await s.commit()


@pytest.fixture
async def svc(isolated_db):
    """Process-wide service bound to the isolated test DB; dropped on teardown.

    Pre-resets just like the integration ``mcp_token`` fixture: a prior test
    that failed mid-teardown must never leak its (closed-DB) service here.
    """
    reset_mcp_token_service()
    s = get_mcp_token_service()
    yield s
    reset_mcp_token_service()


async def test_issue_stores_only_hash(svc, users):
    user_id, _ = users
    rec, raw = await svc.issue(user_id, "claude-desktop", ttl_days=0)
    assert raw.startswith("fw_") and len(raw) > 30
    assert rec["user_id"] == user_id
    assert "token_hash" not in rec
    assert await svc.verify(raw) is not None


async def test_verify_rejects_unknown_and_malformed(svc, users):
    # Unknown-but-well-formed token -> None (no existence disclosure).
    assert await svc.verify("fw_" + "x" * 40) is None
    # Malformed: tokens without the fw_ prefix never reach the DB lookup.
    assert await svc.verify("ghp_" + "x" * 40) is None
    assert await svc.verify("") is None


async def test_verify_rejects_revoked(svc, users):
    user_id, _ = users
    rec, raw = await svc.issue(user_id, "x", ttl_days=0)
    assert await svc.revoke(user_id, rec["id"]) is True
    assert await svc.verify(raw) is None


async def test_revoke_scoped_to_owner(svc, users):
    user_id, other_id = users
    rec, raw = await svc.issue(user_id, "x", ttl_days=0)
    assert await svc.revoke(other_id, rec["id"]) is False
    assert await svc.verify(raw) is not None


async def test_verify_rejects_expired(svc, users):
    user_id, _ = users
    _rec, raw = await svc.issue(user_id, "x", ttl_days=-1)  # negative = already expired
    assert await svc.verify(raw) is None


async def test_list_masks_hash(svc, users):
    user_id, _ = users
    await svc.issue(user_id, "x", ttl_days=0)
    listing = await svc.list_for_user(user_id)
    assert listing and all("token_hash" not in r for r in listing)


async def test_touch_throttled_to_once_per_minute(svc, users):
    user_id, _ = users
    rec, _raw = await svc.issue(user_id, "x", ttl_days=0)
    await svc.touch(rec["id"])
    first = (await svc.list_for_user(user_id))[0]["last_used_at"]
    assert first is not None
    # Second touch within the same minute must be a no-op (no DB write).
    await svc.touch(rec["id"])
    second = (await svc.list_for_user(user_id))[0]["last_used_at"]
    assert second == first


async def test_verify_succeeds_when_touch_raises(svc, users, monkeypatch, caplog):
    """Telemetry must never take a valid auth down: a failing last_used_at
    stamp (DB hiccup) leaves verify() returning the token row."""
    user_id, _ = users
    _rec, raw = await svc.issue(user_id, "x", ttl_days=0)

    async def _boom(token_id: str) -> None:
        raise RuntimeError("last_used_at write failed")

    monkeypatch.setattr(svc, "touch", _boom)
    assert await svc.verify(raw) is not None


# ---------------------------------------------------------------------------
# Hardening: owner account state gates verify() (same rule as sessions)
# ---------------------------------------------------------------------------


class TestOwnerAccountState:
    async def test_disabled_owner_token_rejected(self, svc, users, isolated_db):
        """Admin disables the user: sessions die, and so must the MCP tokens
        (red-team H1 - the token must not outlive the ban)."""
        user_id, _ = users
        _rec, raw = await svc.issue(user_id, "x", ttl_days=0)
        assert await svc.verify(raw) is not None  # sanity: live while active

        await _set_user(isolated_db, user_id, status="disabled")
        assert await svc.verify(raw) is None

    async def test_soft_deleted_owner_token_rejected(self, svc, users, isolated_db):
        user_id, _ = users
        _rec, raw = await svc.issue(user_id, "x", ttl_days=0)

        # Soft delete = deleted_at set AND status disabled (admin lifecycle).
        await _set_user(
            isolated_db, user_id, status="disabled",
            deleted_at=datetime.now(timezone.utc).isoformat(),
        )
        assert await svc.verify(raw) is None

    async def test_active_owner_token_still_verifies(self, svc, users):
        user_id, _ = users
        _rec, raw = await svc.issue(user_id, "x", ttl_days=0)
        assert await svc.verify(raw) is not None


# ---------------------------------------------------------------------------
# Hardening: fail-closed expiry (house _now_lt_iso semantics)
# ---------------------------------------------------------------------------


class TestFailClosedExpiry:
    async def test_token_expiring_this_exact_second_is_expired(
        self, svc, users, isolated_db
    ):
        """Boundary: ``now == expires_at`` must read as expired (fail-closed),
        not "one last free second"."""
        from app.models import McpToken

        user_id, _ = users
        rec, raw = await svc.issue(user_id, "x", ttl_days=1)
        now_iso = datetime.now(timezone.utc).isoformat()
        async with isolated_db.session_factory() as s:
            row = await s.get(McpToken, rec["id"])
            row.expires_at = now_iso
            await s.commit()
        assert await svc.verify(raw) is None

    async def test_malformed_expires_at_reads_as_expired(
        self, svc, users, isolated_db
    ):
        """A corrupt/legacy expires_at value must not authenticate (the old
        lexical compare could let garbage through)."""
        from app.models import McpToken

        user_id, _ = users
        rec, raw = await svc.issue(user_id, "x", ttl_days=1)
        async with isolated_db.session_factory() as s:
            row = await s.get(McpToken, rec["id"])
            row.expires_at = "not-a-timestamp"
            await s.commit()
        assert await svc.verify(raw) is None

    async def test_future_expiry_still_verifies(self, svc, users):
        user_id, _ = users
        _rec, raw = await svc.issue(user_id, "x", ttl_days=1)
        assert await svc.verify(raw) is not None


# ---------------------------------------------------------------------------
# Hardening: per-user active-token cap
# ---------------------------------------------------------------------------


class TestActiveTokenCap:
    async def test_issue_refused_at_cap(self, svc, users, monkeypatch):
        from app.config import settings

        user_id, _ = users
        monkeypatch.setattr(settings, "mcp_max_tokens_per_user", 2)
        assert await svc.issue(user_id, "one", ttl_days=0)
        assert await svc.issue(user_id, "two", ttl_days=0)
        with pytest.raises(McpTokenLimitError) as exc_info:
            await svc.issue(user_id, "three", ttl_days=0)
        assert "Revoke" in str(exc_info.value)  # actionable message

    async def test_revoking_frees_a_slot(self, svc, users, monkeypatch):
        from app.config import settings

        user_id, _ = users
        monkeypatch.setattr(settings, "mcp_max_tokens_per_user", 2)
        _a, _raw_a = await svc.issue(user_id, "one", ttl_days=0)
        rec_b, _raw_b = await svc.issue(user_id, "two", ttl_days=0)
        await svc.revoke(user_id, rec_b["id"])
        rec_c, raw_c = await svc.issue(user_id, "three", ttl_days=0)
        assert await svc.verify(raw_c) is not None

    async def test_default_cap_is_ten(self):
        from app.config import settings

        assert settings.mcp_max_tokens_per_user == 10

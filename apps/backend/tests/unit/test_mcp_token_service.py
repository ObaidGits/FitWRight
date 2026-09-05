"""Unit tests for the MCP bearer-token service (app.auth.mcp_tokens).

Covers the six behaviors from the MCP integration plan: issue stores only the
sha256 (raw token starts with ``fw_``), verify accepts an issued token and
rejects unknown/malformed/revoked/expired ones, revoke is scoped to the owner,
list_for_user never exposes ``token_hash``, and touch writes ``last_used_at``
at most once per 60s per token.
"""

import pytest

from app.auth.mcp_tokens import get_mcp_token_service, reset_mcp_token_service


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


@pytest.fixture
async def svc(isolated_db):
    """Process-wide service bound to the isolated test DB; dropped on teardown."""
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

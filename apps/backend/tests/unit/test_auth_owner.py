"""Cross-worker durability tests for single-user owner bootstrap."""

import asyncio

import pytest
from sqlalchemy import func, select

from app.auth.owner import ensure_owner, normalize_email, resolve_owner_id_sync
from app.config import settings
from app.database import Database
from app.models import User


async def _assert_single_owner(db: Database, expected_id: str) -> None:
    async with db.session_factory() as session:
        result = await session.execute(
            select(func.count(User.id)).where(
                User.email == normalize_email(settings.owner_email)
            )
        )
        assert result.scalar_one() == 1
        owner = await session.get(User, expected_id)
        assert owner is not None
        assert owner.role == "admin"
        assert owner.status == "active"


@pytest.mark.asyncio
async def test_async_owner_bootstrap_is_atomic_across_database_facades(tmp_path):
    dbs = [Database(tmp_path / "async-owner.db") for _ in range(6)]
    try:
        owner_ids = await asyncio.gather(*(ensure_owner(db) for db in dbs))
        assert len(set(owner_ids)) == 1
        await _assert_single_owner(dbs[0], owner_ids[0])
    finally:
        await asyncio.gather(*(db.close() for db in dbs))


@pytest.mark.asyncio
async def test_sync_owner_bootstrap_is_atomic_across_database_facades(tmp_path):
    dbs = [Database(tmp_path / "sync-owner.db") for _ in range(6)]
    try:
        owner_ids = await asyncio.gather(
            *(asyncio.to_thread(resolve_owner_id_sync, db) for db in dbs)
        )
        assert len(set(owner_ids)) == 1
        await _assert_single_owner(dbs[0], owner_ids[0])
    finally:
        await asyncio.gather(*(db.close() for db in dbs))


@pytest.mark.asyncio
async def test_existing_owner_does_not_rehash_configured_password(tmp_path, monkeypatch):
    import app.auth.owner as owner_module

    first = Database(tmp_path / "existing-owner.db")
    second = Database(tmp_path / "existing-owner.db")
    hashes: list[str] = []

    def fake_hash(password: str) -> str:
        hashes.append(password)
        return "argon2-test-hash"

    monkeypatch.setattr(settings, "owner_password", "configured-owner-password")
    monkeypatch.setattr(owner_module, "_hash_owner_password", fake_hash)
    try:
        first_id = await ensure_owner(first)
        second_id = await ensure_owner(second)
        assert second_id == first_id
        assert hashes == ["configured-owner-password"]
    finally:
        await asyncio.gather(first.close(), second.close())
"""Channel credentials: storing, reading, and never leaking them.

These exist because credential storage NEVER WORKED and nothing noticed. The original
design put channel keys in the `api_keys` table under a reserved owner id, but that
table's user_id is a foreign key to users - so every insert failed, and the channels
feature could not have served one request. No test covered the write path, so a fully
green suite said nothing about it.

The lesson encoded here: test the path that touches the database, not just the function
that decides what to write.
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
class TestChannelCredentialStorage:
    async def test_a_credential_can_actually_be_stored_and_read_back(self, isolated_db):
        """The regression test for the foreign-key failure."""
        from app.crypto import decrypt, encrypt
        from app.database import db

        channel = await db.create_ai_channel(
            name="Primary", provider="openai", model="gpt-4o-mini", api_base=None
        )
        assert await db.set_ai_channel_key(channel["id"], encrypt("sk-secret")) is True

        stored = await db.get_ai_channel_key(channel["id"])
        assert stored is not None
        assert decrypt(stored) == "sk-secret"

    async def test_the_ciphertext_is_absent_from_the_channel_dict(self, isolated_db):
        """What the API serialises. A credential must not be able to leak by simply
        being present in the dict every endpoint returns."""
        from app.crypto import encrypt
        from app.database import db

        channel = await db.create_ai_channel(
            name="Primary", provider="openai", model="gpt-4o-mini", api_base=None
        )
        await db.set_ai_channel_key(channel["id"], encrypt("sk-secret"))

        fetched = await db.get_ai_channel(channel["id"])
        assert fetched is not None
        assert "api_key_ciphertext" not in fetched
        assert "sk-secret" not in str(fetched)

    async def test_deleting_a_channel_takes_its_credential_with_it(self, isolated_db):
        """Same lifecycle, no orphan to clean up - one reason the credential belongs
        on the row rather than in a side table."""
        from app.crypto import encrypt
        from app.database import db

        channel = await db.create_ai_channel(
            name="Primary", provider="openai", model="gpt-4o-mini", api_base=None
        )
        await db.set_ai_channel_key(channel["id"], encrypt("sk-secret"))
        await db.delete_ai_channel(channel["id"])

        assert await db.get_ai_channel_key(channel["id"]) is None

    async def test_a_credential_can_be_cleared(self, isolated_db):
        from app.crypto import encrypt
        from app.database import db

        channel = await db.create_ai_channel(
            name="Primary", provider="openai", model="gpt-4o-mini", api_base=None
        )
        await db.set_ai_channel_key(channel["id"], encrypt("sk-secret"))
        await db.set_ai_channel_key(channel["id"], None)
        assert await db.get_ai_channel_key(channel["id"]) is None

    async def test_setting_a_key_on_a_missing_channel_reports_false(self, isolated_db):
        from app.database import db

        assert await db.set_ai_channel_key("no-such-channel", "x") is False

    async def test_bulk_read_returns_only_channels_that_have_one(self, isolated_db):
        from app.crypto import encrypt
        from app.database import db

        with_key = await db.create_ai_channel(
            name="With", provider="openai", model="gpt-4o-mini", api_base=None
        )
        await db.create_ai_channel(
            name="Without", provider="ollama", model="llama3", api_base="http://x"
        )
        await db.set_ai_channel_key(with_key["id"], encrypt("sk-secret"))

        keys = await db.get_ai_channel_keys()
        assert list(keys) == [with_key["id"]]

    async def test_an_undecryptable_credential_does_not_break_the_others(
        self, isolated_db
    ):
        """After an encryption-secret change one key may be unreadable. That must
        degrade to "this channel has no credential", not take down every channel."""
        from app.ai_routing import _load_channel_keys
        from app.crypto import encrypt
        from app.database import db

        good = await db.create_ai_channel(
            name="Good", provider="openai", model="gpt-4o-mini", api_base=None
        )
        bad = await db.create_ai_channel(
            name="Bad", provider="openai", model="gpt-4o-mini", api_base=None
        )
        await db.set_ai_channel_key(good["id"], encrypt("sk-good"))
        await db.set_ai_channel_key(bad["id"], "not-a-valid-ciphertext")

        keys = await _load_channel_keys()
        assert keys.get(good["id"]) == "sk-good"
        assert bad["id"] not in keys

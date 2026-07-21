"""Per-user API-key resolution in llm.py (Task 3.3, R10.6).

``get_llm_config`` must resolve the *caller's* provider key: one user's key can
never serve another user's LLM calls. Resolution follows an explicit ``user_id``
argument first, then the request-scoped effective user published on the context
var (by the ``get_effective_user_id`` dependency).
"""

from uuid import uuid4

import pytest

import app.config as config_module
from app import crypto
from app.auth.context import reset_current_user_id, set_current_user_id
from app.database import Database
from app.llm import get_llm_config
from app.models import User


@pytest.fixture
async def two_user_db(tmp_path, monkeypatch):
    """A temp DB (swapped in as the global) with two users holding distinct keys."""
    db = Database(db_path=tmp_path / "keys.db")
    monkeypatch.setattr("app.database.db", db)
    # Route config.json + crypto to temp so the test is hermetic.
    monkeypatch.setattr(config_module, "CONFIG_FILE_PATH", tmp_path / "config.json")
    monkeypatch.setattr(config_module.settings, "data_dir", tmp_path)
    crypto.reset_cache()

    async def _mkuser(email):
        uid = str(uuid4())
        async with db.session_factory() as session:
            session.add(User(id=uid, email=email, name="U", role="user", status="active"))
            await session.commit()
        return uid

    user_a = await _mkuser("a@example.com")
    user_b = await _mkuser("b@example.com")
    # Config selects the openai provider; each user has a different openai key.
    config_module._write_config_json({"provider": "openai", "model": "gpt-4"})
    db.set_api_key_ciphertext(user_a, "openai", crypto.encrypt("key-A"))
    db.set_api_key_ciphertext(user_b, "openai", crypto.encrypt("key-B"))
    try:
        yield db, user_a, user_b
    finally:
        crypto.reset_cache()
        await db.close()


class TestPerUserKeyResolution:
    async def test_explicit_user_id_resolves_that_users_key(self, two_user_db):
        _, user_a, user_b = two_user_db
        assert get_llm_config(user_a).api_key == "key-A"
        assert get_llm_config(user_b).api_key == "key-B"

    async def test_context_var_resolves_the_request_user_key(self, two_user_db):
        _, user_a, user_b = two_user_db
        token = set_current_user_id(user_b)
        try:
            # No explicit user_id -> falls back to the request-scoped effective user.
            assert get_llm_config().api_key == "key-B"
        finally:
            reset_current_user_id(token)

    async def test_one_users_key_never_serves_another(self, two_user_db):
        _, user_a, user_b = two_user_db
        assert get_llm_config(user_a).api_key != get_llm_config(user_b).api_key

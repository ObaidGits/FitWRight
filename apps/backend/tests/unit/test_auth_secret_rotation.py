"""Secret-rotation dual-key verification (Task 9.2, R16.3).

`SESSION_SECRET` signs the pre-session CSRF token (login-CSRF double-submit) and
the transient OAuth state cookie (`oauth_txn`). Rotation must be seamless: an
artifact signed with the *previous* key still verifies during the overlap
window, while one signed with an *unrelated* key never does. These tests pin
that contract end-to-end for both artifacts via the public helpers the router
uses, so a regression in the rotation window fails loudly.
"""

from __future__ import annotations

from app.auth.csrf import (
    issue_presession_token,
    presession_double_submit_ok,
    verify_presession_token,
)
from app.auth.oauth.state import (
    OAuthTransaction,
    deserialize_transaction,
    serialize_transaction,
)
from app.config import Settings

OLD = "old-session-secret-abcdefgh"
NEW = "new-session-secret-abcdefgh"
UNRELATED = "unrelated-secret-zyxwvuts"


def _settings(**over) -> Settings:
    base = dict(
        single_user_mode=True,
        session_secret=NEW,
        session_secret_prev=OLD,
        ip_hash_secret="unit-test-ip-hash-secret-abc",
    )
    base.update(over)
    return Settings(**base)


class TestPreSessionTokenRotation:
    def test_previous_key_still_verifies(self):
        token = issue_presession_token(OLD)
        # After rotation to NEW (with OLD as prev), the old token still validates.
        assert verify_presession_token(token, NEW, secret_prev=OLD) is True

    def test_unrelated_key_rejected(self):
        token = issue_presession_token(UNRELATED)
        assert verify_presession_token(token, NEW, secret_prev=OLD) is False

    def test_double_submit_full_path_accepts_previous_key(self):
        token = issue_presession_token(OLD)
        # cookie == header (double-submit) AND signature valid under prev key.
        assert presession_double_submit_ok(token, token, NEW, secret_prev=OLD) is True

    def test_double_submit_full_path_rejects_unrelated_key(self):
        token = issue_presession_token(UNRELATED)
        assert presession_double_submit_ok(token, token, NEW, secret_prev=OLD) is False


class TestOAuthTransactionCookieRotation:
    def _txn(self) -> OAuthTransaction:
        return OAuthTransaction(
            provider="google", state="st", nonce="no", verifier="ve", next="/home"
        )

    def test_cookie_signed_with_previous_key_still_verifies(self):
        # Signed while OLD was the active secret (no prev configured then).
        signed_old = serialize_transaction(
            self._txn(), config=_settings(session_secret=OLD, session_secret_prev="")
        )
        # After rotation: current=NEW, prev=OLD -> still deserializes.
        rotated = deserialize_transaction(signed_old, config=_settings())
        assert rotated is not None
        assert rotated.state == "st" and rotated.next == "/home"

    def test_cookie_signed_with_unrelated_key_rejected(self):
        signed_bad = serialize_transaction(
            self._txn(),
            config=_settings(session_secret=UNRELATED, session_secret_prev=""),
        )
        assert deserialize_transaction(signed_bad, config=_settings()) is None

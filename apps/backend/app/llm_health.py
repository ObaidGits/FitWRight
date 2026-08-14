"""Remembers that a provider rejected our credentials, so the UI can say so.

The gap this closes: ``llm_configured`` on the status endpoint only means "a key
string exists". Because :func:`app.llm.resolve_api_key` falls back to the
deployment-level ``LLM_API_KEY``, a user who has configured nothing at all still
reports as configured whenever the environment carries a key - including a stale
one. The frontend gate therefore passed, the upload ran, the text was extracted,
and only then did the provider answer 401. The user had already waited through a
parse to be told to go and fix Settings.

Nothing in the system remembered that answer, so the next upload made the same
trip. This module is that memory: an authentication failure is recorded against
the user, ``/status`` then reports ``llm_healthy=false``, and the existing
fail-closed gate blocks the *next* upload before a file is even read.

State is in-process, matching the single-worker deployment. A restart forgets the
rejection, which is the safe direction to be wrong in: the worst case is the user
is allowed one more attempt that re-learns the same fact, rather than being locked
out of a provider key they have since repaired.

Only authentication is tracked. A timeout or a rate limit is transient and must
not latch a user out of their own AI - they are told to retry instead.
"""

from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass(frozen=True)
class CredentialRejection:
    """A provider's refusal of the key we sent, as last observed."""

    provider: str
    at: float
    detail: str


_rejections: dict[str, CredentialRejection] = {}


def mark_credentials_rejected(user_id: str, provider: str, detail: str = "") -> None:
    """Record that ``provider`` refused this user's key."""
    if not user_id:
        return
    _rejections[user_id] = CredentialRejection(
        provider=provider or "unknown", at=time.time(), detail=detail
    )


def clear_credentials_rejected(user_id: str) -> None:
    """Forget the refusal.

    Called when the user saves configuration or a connection test succeeds -
    both mean the previous verdict is stale and the gate must reopen. Saving is
    enough on its own: refusing to reopen until a live test succeeds would leave
    a user who has just pasted a correct key still locked out.
    """
    if user_id:
        _rejections.pop(user_id, None)


def credentials_rejected(user_id: str | None) -> CredentialRejection | None:
    return _rejections.get(user_id) if user_id else None


def reset_for_tests() -> None:
    _rejections.clear()

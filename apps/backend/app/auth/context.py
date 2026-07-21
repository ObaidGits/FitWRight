"""Request-scoped effective user id (ADR-4, R10.6).

The LLM call graph (``llm.py`` -> services -> ``get_llm_config`` ->
``resolve_api_key``) is deep and synchronous, so threading ``user_id`` through
every signature would touch dozens of unrelated functions. Instead the effective
user id for the current request is published on a :class:`contextvars.ContextVar`
by the :func:`app.auth.principal.get_effective_user_id` dependency, and read back
by the api-key resolution path so one user's provider key can never serve
another user's LLM calls (R10.6).

``ContextVar`` is the correct primitive here: it is task-local, propagates across
``await`` boundaries within the same request, and is isolated between concurrent
requests.
"""

from __future__ import annotations

from contextvars import ContextVar

__all__ = ["set_current_user_id", "get_current_user_id", "reset_current_user_id"]

_current_user_id: ContextVar[str | None] = ContextVar("current_user_id", default=None)


def set_current_user_id(user_id: str | None):
    """Publish the effective user id for the current request; returns the token."""
    return _current_user_id.set(user_id)


def get_current_user_id() -> str | None:
    """Return the effective user id published for the current request, if any."""
    return _current_user_id.get()


def reset_current_user_id(token) -> None:
    """Restore the previous context value (best-effort)."""
    try:
        _current_user_id.reset(token)
    except (ValueError, LookupError):
        pass

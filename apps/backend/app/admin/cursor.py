"""Opaque keyset cursor encode/decode + admin search sanitization (Task 1.3/3.1).

Admin lists are cursor-paginated (keyset, never offset — R11.1). A cursor is the
base64url-encoded JSON of the last row's ``(sort_key, id)`` tuple; it is opaque
to the client and stable under concurrent insert/delete (the ``id`` tie-break
guarantees a total order). A malformed cursor is rejected with ``bad_cursor``
rather than silently ignored, so a tampered value can't scan from the start.

Search input (``q``) is length-bounded and control-character-stripped before it
reaches the query or any log/audit line (log/CRLF-injection defense, R4.3/9.3).
"""

from __future__ import annotations

import base64
import binascii
import json

__all__ = [
    "encode_cursor",
    "decode_cursor",
    "CursorError",
    "sanitize_query",
    "MAX_QUERY_LENGTH",
]

# Bound the search term so it can't blow up a query or a log line (R4.3).
MAX_QUERY_LENGTH = 128


class CursorError(ValueError):
    """Raised when a cursor is malformed/tampered (router → 400 ``bad_cursor``)."""


def encode_cursor(sort_key: str, row_id: str) -> str:
    """Encode ``(sort_key, id)`` into an opaque base64url cursor."""
    raw = json.dumps([sort_key, row_id], separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def decode_cursor(cursor: str | None) -> tuple[str, str] | None:
    """Decode a cursor back into ``(sort_key, id)``; ``None`` for no cursor.

    Raises :class:`CursorError` on any malformed/tampered value.
    """
    if cursor is None or cursor == "":
        return None
    try:
        padding = "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(cursor + padding)
        data = json.loads(raw)
    except (binascii.Error, ValueError, TypeError) as exc:
        raise CursorError("bad_cursor") from exc
    if (
        not isinstance(data, list)
        or len(data) != 2
        or not all(isinstance(x, str) for x in data)
    ):
        raise CursorError("bad_cursor")
    return data[0], data[1]


def sanitize_query(q: str | None) -> str | None:
    """Return a bounded, control-character-free search term, or ``None``.

    Strips CR/LF and other C0/C1 control characters (log-injection defense),
    collapses surrounding whitespace, and length-bounds to
    :data:`MAX_QUERY_LENGTH`. An empty result becomes ``None`` (no filter).
    """
    if not q:
        return None
    cleaned = "".join(" " if (ord(ch) < 0x20 or ord(ch) == 0x7F) else ch for ch in q)
    cleaned = cleaned.strip()
    if len(cleaned) > MAX_QUERY_LENGTH:
        cleaned = cleaned[:MAX_QUERY_LENGTH]
    return cleaned or None

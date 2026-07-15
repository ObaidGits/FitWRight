"""Profile version snapshots — mirrors ``app/versions/service.py`` for profiles.

Reuses the generic, well-tested gzip/hash helpers from the resume version
service (``compress_processed_data`` / ``decompress_version``) so there is a
single serialization + dedupe implementation. Snapshots are content-hash
deduped, manual-save debounced, and capped with prune (the oldest snapshot —
typically the ``migration`` baseline — is always retained). All persistence
goes through the ``app.database`` facade, scoped by ``user_id``.
"""

from __future__ import annotations

import logging
from typing import Any

from app.versions.service import (
    VersionServiceError,
    compress_processed_data,
    decompress_version,
)

logger = logging.getLogger(__name__)

# Valid profile snapshot sources (design §13.13).
SOURCES = frozenset({"manual", "import", "merge", "ai", "migration"})

__all__ = [
    "SOURCES",
    "capture_profile_snapshot",
    "list_profile_version_metadata",
    "get_profile_version_data",
]


def _db():
    """Resolve the live DB facade lazily (tests swap ``database.db``)."""
    from app import database

    return database.db


def _within_seconds(created_at_iso: str | None, seconds: int) -> bool:
    if not created_at_iso or seconds <= 0:
        return False
    from datetime import datetime, timezone

    try:
        ts = datetime.fromisoformat(created_at_iso)
    except ValueError:
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - ts).total_seconds() < seconds


async def capture_profile_snapshot(
    user_id: str,
    profile_id: str,
    data: Any,
    source: str,
    *,
    label: str | None = None,
    debounce_seconds: int | None = None,
) -> dict[str, Any] | None:
    """Capture a snapshot of ``data`` unless deduped/debounced.

    Returns the created snapshot metadata, or ``None`` when skipped (identical
    content hash, or a debounced rapid manual save).
    """
    if source not in SOURCES:
        raise VersionServiceError("invalid", f"Unknown profile snapshot source: {source!r}")
    if data is None:
        return None

    from app.config import settings

    cap = settings.profile_history_cap
    if debounce_seconds is None:
        debounce_seconds = settings.profile_manual_debounce_seconds

    blob, size_bytes, content_hash = compress_processed_data(data)

    db = _db()
    latest = await db.get_latest_profile_version(user_id, profile_id)
    if latest is not None:
        if latest.get("content_hash") == content_hash:
            return None
        if (
            source == "manual"
            and latest.get("source") == "manual"
            and _within_seconds(latest.get("created_at"), debounce_seconds)
        ):
            return None

    created = await db.create_profile_version(
        user_id,
        profile_id,
        source=source,
        label=label,
        content_hash=content_hash,
        data_gz=blob,
        size_bytes=size_bytes,
    )
    try:
        await db.prune_profile_versions(user_id, profile_id, cap)
    except Exception:  # pragma: no cover - prune is best-effort
        logger.exception("Profile version prune failed for %s", profile_id)
    return created


async def list_profile_version_metadata(
    user_id: str,
    profile_id: str,
    *,
    limit: int = 50,
    cursor: str | None = None,
) -> dict[str, Any]:
    """Metadata-only, keyset-paginated page of profile snapshots."""
    limit = max(1, min(100, limit))
    rows = await _db().list_profile_versions(
        user_id, profile_id, limit=limit + 1, cursor=cursor
    )
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = None
    if has_more and page:
        last = page[-1]
        next_cursor = f"{last['created_at']}|{last['id']}"
    return {"items": page, "next_cursor": next_cursor}


async def get_profile_version_data(user_id: str, version_id: str) -> dict[str, Any]:
    """Return a single profile snapshot's metadata + decompressed document."""
    row = await _db().get_profile_version(user_id, version_id)
    if row is None:
        raise VersionServiceError("not_found", "Version not found.")
    data = decompress_version(row["data_gz"])
    meta = {k: v for k, v in row.items() if k != "data_gz"}
    return {**meta, "data": data}

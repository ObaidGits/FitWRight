"""Version-history business logic (design §A, Requirements 1–3).

Pure helpers (canonical hashing, gzip compress/decompress, field diff, prune
decision) plus orchestration that persists through the ``app.database`` facade
(the only place owned-table queries may live — the scoping guard). Nothing here
touches the ORM directly; it composes facade methods that are all scoped by
``user_id``.

Design guarantees implemented here:
- **Dedupe (R1.2):** a snapshot whose ``content_hash`` equals the latest
  snapshot's is skipped (no-op), so identical consecutive states never persist.
- **Debounce (R1.2):** rapid ``manual`` saves within ``debounce_seconds`` of the
  latest ``manual`` snapshot are coalesced (the newer content replaces nothing —
  the write is simply skipped; the periodic autosave will capture the settled
  state), preventing snapshot spam.
- **Compression (R1.1):** payloads are gzip(json) — never raw JSON — bounded by
  a canonical serialization so the hash is stable across key ordering.
- **Cap/prune (R1.3):** at most ``cap`` snapshots per resume; the oldest
  ``original`` is always retained; the oldest non-``original`` rows are pruned.
- **Non-destructive restore (R2.1):** restore first snapshots the *current*
  state (so restore is itself reversible) then applies the chosen snapshot.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "VersionServiceError",
    "SOURCES",
    "compute_content_hash",
    "compress_processed_data",
    "decompress_version",
    "diff_processed_data",
    "capture_snapshot",
    "list_version_metadata",
    "get_version_data",
    "restore_version",
    "undo_last_ai",
    "compare_versions",
]

# Valid snapshot sources (Requirement 1.1).
SOURCES = frozenset({"original", "ai", "manual"})

# gzip level 6 is the sweet spot for JSON: near-max ratio at a fraction of the
# CPU of level 9. Snapshots are write-once so we optimize the stored size.
_GZIP_LEVEL = 6


class VersionServiceError(Exception):
    """Raised for version-history precondition failures.

    ``code`` maps to an HTTP status at the router boundary: ``not_found`` → 404,
    ``conflict`` → 409, ``invalid`` → 422.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _canonical_json(data: Any) -> str:
    """Deterministic JSON: sorted keys, compact separators, non-ASCII preserved.

    Canonicalization makes ``content_hash`` stable regardless of key insertion
    order, so semantically identical states dedupe even if the producer emits
    keys in a different order.
    """
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_content_hash(processed_data: Any) -> str:
    """Return the sha256 hex of the canonical JSON of ``processed_data``."""
    return hashlib.sha256(_canonical_json(processed_data).encode("utf-8")).hexdigest()


def compress_processed_data(processed_data: Any) -> tuple[bytes, int, str]:
    """Return ``(gzip_bytes, uncompressed_size, content_hash)`` for a payload."""
    canonical = _canonical_json(processed_data).encode("utf-8")
    blob = gzip.compress(canonical, compresslevel=_GZIP_LEVEL)
    digest = hashlib.sha256(canonical).hexdigest()
    return blob, len(canonical), digest


def decompress_version(data_gz: bytes) -> dict[str, Any]:
    """Inflate a stored snapshot blob back into the processed_data dict.

    Raises :class:`VersionServiceError` (``invalid``) on a corrupt blob rather
    than leaking a raw ``gzip``/``json`` error to the client.
    """
    try:
        return json.loads(gzip.decompress(data_gz).decode("utf-8"))
    except (OSError, ValueError, EOFError) as exc:  # gzip/json/utf-8 failures
        raise VersionServiceError("invalid", "Snapshot payload is corrupt.") from exc


def diff_processed_data(before: Any, after: Any, *, _path: str = "") -> list[dict[str, Any]]:
    """Field-level diff between two processed_data structures.

    Returns a flat list of ``{path, action, before, after}`` entries where
    ``action`` is ``added`` | ``removed`` | ``changed``. Recurses into dicts and
    lists; scalars/leaf mismatches are reported as a single ``changed`` entry.
    Deterministic (sorted dict keys) so the diff is stable for snapshot tests.
    """
    changes: list[dict[str, Any]] = []

    if isinstance(before, dict) and isinstance(after, dict):
        for key in sorted(set(before) | set(after)):
            child = f"{_path}.{key}" if _path else str(key)
            if key not in before:
                changes.append({"path": child, "action": "added", "before": None, "after": after[key]})
            elif key not in after:
                changes.append({"path": child, "action": "removed", "before": before[key], "after": None})
            else:
                changes.extend(diff_processed_data(before[key], after[key], _path=child))
        return changes

    if isinstance(before, list) and isinstance(after, list):
        common = min(len(before), len(after))
        for i in range(common):
            changes.extend(diff_processed_data(before[i], after[i], _path=f"{_path}[{i}]"))
        for i in range(common, len(after)):
            changes.append({"path": f"{_path}[{i}]", "action": "added", "before": None, "after": after[i]})
        for i in range(common, len(before)):
            changes.append({"path": f"{_path}[{i}]", "action": "removed", "before": before[i], "after": None})
        return changes

    if before != after:
        changes.append({"path": _path or "$", "action": "changed", "before": before, "after": after})
    return changes


# ---------------------------------------------------------------------------
# Orchestration (persists via the app.database facade)
# ---------------------------------------------------------------------------


def _db():
    """Resolve the live DB facade lazily so tests' ``isolated_db`` swap is seen."""
    from app import database

    return database.db


async def capture_snapshot(
    user_id: str,
    resume_id: str,
    processed_data: Any,
    source: str,
    *,
    label: str | None = None,
    debounce_seconds: int | None = None,
) -> dict[str, Any] | None:
    """Capture a snapshot for ``resume_id`` unless deduped/debounced.

    Returns the created snapshot metadata, or ``None`` when the write was
    skipped (identical content hash, or a debounced rapid manual save). Never
    raises on a benign skip; a genuine persistence error propagates so callers
    can decide (snapshot capture is best-effort at most call sites).
    """
    if source not in SOURCES:
        raise VersionServiceError("invalid", f"Unknown snapshot source: {source!r}")
    if processed_data is None:
        return None

    from app.config import settings

    cap = settings.version_history_cap
    if debounce_seconds is None:
        debounce_seconds = settings.version_manual_debounce_seconds

    blob, size_bytes, content_hash = compress_processed_data(processed_data)

    db = _db()
    # Capture the resume's current appearance alongside the content so a future
    # restore reapplies the template it was saved with (Bug #3). Best-effort.
    template_settings: Any = None
    try:
        resume_row = await db.get_resume(user_id, resume_id)
        if resume_row:
            template_settings = resume_row.get("template_settings")
    except Exception:  # pragma: no cover - appearance capture is best-effort
        template_settings = None
    latest = await db.get_latest_resume_version(user_id, resume_id)
    if latest is not None:
        # Dedupe: identical consecutive content is a no-op (R1.2).
        if latest.get("content_hash") == content_hash:
            return None
        # Debounce: coalesce rapid manual saves (R1.2). Only manual→manual within
        # the window is skipped; original/ai are always significant.
        if (
            source == "manual"
            and latest.get("source") == "manual"
            and _within_seconds(latest.get("created_at"), debounce_seconds)
        ):
            return None

    created = await db.create_resume_version(
        user_id,
        resume_id,
        source=source,
        label=label,
        content_hash=content_hash,
        data_gz=blob,
        size_bytes=size_bytes,
        template_settings=template_settings,
    )
    # Prune to the per-resume cap, always retaining the oldest ``original``.
    try:
        await db.prune_resume_versions(user_id, resume_id, cap)
    except Exception:  # pragma: no cover - prune is best-effort, never fails write
        logger.exception("Version prune failed for resume %s", resume_id)
    return created


def _within_seconds(created_at_iso: str | None, seconds: int) -> bool:
    """Whether ``created_at_iso`` is within ``seconds`` of now (UTC)."""
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


async def list_version_metadata(
    user_id: str,
    resume_id: str,
    *,
    limit: int = 50,
    cursor: str | None = None,
) -> dict[str, Any]:
    """Return a metadata-only, keyset-paginated page of snapshots (R3.1).

    Never loads ``data_gz`` (payloads are fetched on demand). ``cursor`` is an
    opaque ``created_at|id`` token of the last row on the previous page.
    """
    limit = max(1, min(100, limit))
    rows = await _db().list_resume_versions(
        user_id, resume_id, limit=limit + 1, cursor=cursor
    )
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = None
    if has_more and page:
        last = page[-1]
        next_cursor = f"{last['created_at']}|{last['id']}"
    return {"items": page, "next_cursor": next_cursor}


async def get_version_data(user_id: str, version_id: str) -> dict[str, Any]:
    """Return a single snapshot's metadata + decompressed data (R3.1).

    Raises ``not_found`` for a missing/foreign snapshot (no existence
    disclosure).
    """
    row = await _db().get_resume_version(user_id, version_id)
    if row is None:
        raise VersionServiceError("not_found", "Version not found.")
    data = decompress_version(row["data_gz"])
    meta = {k: v for k, v in row.items() if k != "data_gz"}
    return {**meta, "processed_data": data}


async def restore_version(
    user_id: str,
    resume_id: str,
    version_id: str,
    *,
    expected_updated_at: str | None = None,
) -> dict[str, Any]:
    """Non-destructively restore ``version_id`` onto ``resume_id`` (R2.1/2.3).

    Snapshots the *current* processed_data first (so the restore is reversible),
    then applies the chosen snapshot atomically. ``expected_updated_at`` gives an
    optimistic-concurrency (CAS) guard: if provided and stale, raises
    ``conflict`` (409) so concurrent restores can't silently clobber.
    """
    db = _db()
    version = await db.get_resume_version(user_id, version_id)
    if version is None or version.get("resume_id") != resume_id:
        raise VersionServiceError("not_found", "Version not found.")

    resume = await db.get_resume(user_id, resume_id)
    if resume is None:
        raise VersionServiceError("not_found", "Resume not found.")

    if expected_updated_at is not None and resume.get("updated_at") != expected_updated_at:
        raise VersionServiceError(
            "conflict", "Resume changed since it was loaded; reload and retry."
        )

    # 1) Snapshot the current state so restore is reversible (skipped if the
    #    current state already equals the latest snapshot via dedupe).
    current = resume.get("processed_data")
    if current is not None:
        await capture_snapshot(
            user_id, resume_id, current, "manual", label="Before restore",
            debounce_seconds=0,
        )

    # 2) Apply the chosen snapshot atomically (CAS re-checked in the facade).
    restored_data = decompress_version(version["data_gz"])
    updated = await db.restore_resume_version(
        user_id,
        resume_id,
        processed_data=restored_data,
        template_settings=version.get("template_settings"),
        expected_updated_at=expected_updated_at,
    )
    if updated is None:
        raise VersionServiceError(
            "conflict", "Resume changed since it was loaded; reload and retry."
        )
    return updated


async def undo_last_ai(user_id: str, resume_id: str) -> dict[str, Any]:
    """Restore the snapshot immediately preceding the last ``ai`` snapshot (R2.2).

    If there is no ``ai`` snapshot, or none precedes it, raises ``not_found`` so
    the UI can surface "nothing to undo".
    """
    db = _db()
    target = await db.find_snapshot_before_last_ai(user_id, resume_id)
    if target is None:
        raise VersionServiceError("not_found", "No AI change to undo.")
    return await restore_version(user_id, resume_id, target["id"])


async def compare_versions(user_id: str, resume_id: str, a: str, b: str) -> dict[str, Any]:
    """Field-level diff between two owned snapshots of the same resume (R3.2)."""
    db = _db()
    row_a = await db.get_resume_version(user_id, a)
    row_b = await db.get_resume_version(user_id, b)
    for row, vid in ((row_a, a), (row_b, b)):
        if row is None or row.get("resume_id") != resume_id:
            raise VersionServiceError("not_found", f"Version not found: {vid}")
    data_a = decompress_version(row_a["data_gz"])
    data_b = decompress_version(row_b["data_gz"])
    return {
        "a": {k: v for k, v in row_a.items() if k != "data_gz"},
        "b": {k: v for k, v in row_b.items() if k != "data_gz"},
        "changes": diff_processed_data(data_a, data_b),
    }

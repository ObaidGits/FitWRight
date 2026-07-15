"""Pydantic schemas for resume version history (P3 §A, Requirements 1–3)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class VersionMetadata(BaseModel):
    """Metadata-only view of a snapshot (never carries the payload — R3.1)."""

    id: str
    resume_id: str
    source: Literal["original", "ai", "manual"]
    label: str | None = None
    content_hash: str
    size_bytes: int
    created_at: str


class VersionListResponse(BaseModel):
    """A keyset-paginated page of snapshot metadata."""

    items: list[VersionMetadata]
    next_cursor: str | None = None


class VersionDataResponse(VersionMetadata):
    """A single snapshot's metadata plus its decompressed processed_data."""

    processed_data: dict[str, Any]


class CreateManualSnapshotRequest(BaseModel):
    """Explicitly capture the current resume state as a labeled manual snapshot."""

    label: str | None = Field(default=None, max_length=200)


class RestoreVersionRequest(BaseModel):
    """Restore a snapshot with an optional optimistic-concurrency guard.

    ``expected_updated_at`` is the resume's ``updated_at`` the client last saw;
    when provided and stale the restore returns 409 (R2.3) so concurrent
    restores can't silently clobber.
    """

    expected_updated_at: str | None = None


class RestoreResponse(BaseModel):
    """The resume state after a restore/undo (the applied processed_data)."""

    resume_id: str
    updated_at: str
    processed_data: dict[str, Any] | None = None


class VersionDiffEntry(BaseModel):
    """A single field-level change between two snapshots."""

    path: str
    action: Literal["added", "removed", "changed"]
    before: Any = None
    after: Any = None


class VersionCompareResponse(BaseModel):
    """Field-level diff between two owned snapshots (R3.2)."""

    a: VersionMetadata
    b: VersionMetadata
    changes: list[VersionDiffEntry]

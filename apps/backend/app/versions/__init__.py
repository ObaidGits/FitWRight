"""P3 Productivity — resume version history (design §A, Requirements 1–3).

Immutable, gzip-compressed, content-hash-deduped snapshots of a resume's
``processed_data`` with non-destructive restore/undo and a field-level compare.
The pure logic (hashing, compression, diff, prune decision) lives in
:mod:`app.versions.service`; all owned-table persistence lives in the
``app.database`` facade (scoping guard), and the HTTP surface in
``app.routers.versions``.
"""

from app.versions.service import (
    VersionServiceError,
    capture_snapshot,
    compare_versions,
    compress_processed_data,
    compute_content_hash,
    decompress_version,
    list_version_metadata,
    restore_version,
    undo_last_ai,
)

__all__ = [
    "VersionServiceError",
    "capture_snapshot",
    "compare_versions",
    "compress_processed_data",
    "compute_content_hash",
    "decompress_version",
    "list_version_metadata",
    "restore_version",
    "undo_last_ai",
]

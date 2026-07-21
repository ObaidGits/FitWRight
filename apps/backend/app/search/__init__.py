"""P3 Productivity - Global search (design §C, Requirements 7-8).

A user-scoped, outbox-fed search index behind a dialect-aware ``SearchRepo``
port: SQLite uses an FTS5 mirror (bm25 ranking), Postgres uses ``tsvector`` + a
GIN index (``ts_rank``). Every query is parameterized and scoped by ``user_id``
**in SQL** so no crafted query string can cross users (R7.2). The SearchIndexer
(:mod:`app.search.indexer`) consumes outbox events into the index and supports a
full rebuild + drift detection.
"""

from app.search.indexer import (
    NODE_TYPES,
    ensure_search_consumers_registered,
    rebuild_user_index,
    search_drift,
)
from app.search.repo import get_search_repo

__all__ = [
    "NODE_TYPES",
    "ensure_search_consumers_registered",
    "rebuild_user_index",
    "search_drift",
    "get_search_repo",
]

"""Global search endpoints (P3 §C, Requirements 7-8).

User-scoped (scope enforced **in SQL**), FTS-ranked, cursor-paginated, and gated
by the ``SEARCH`` feature flag. Results are content-safe and deep-link to the
exact node. A user-scoped ``reindex`` rebuilds the caller's own index (recovery
+ initial backfill).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth import get_effective_user_id
from app.config import settings
from app.schemas.search import ReindexResponse, SearchResponse, SearchResult

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/search", tags=["Search"])

_VALID_TYPES = {"resume", "job", "application"}


def _require_enabled() -> None:
    if not settings.search_enabled:
        raise HTTPException(status_code=404, detail="search_disabled")


@router.get("", response_model=SearchResponse)
async def search(
    q: str = Query(..., min_length=1, max_length=200),
    types: str | None = Query(default=None, description="Comma-separated node types"),
    status: str | None = Query(default=None, max_length=64),
    date_from: str | None = Query(default=None, alias="from"),
    date_to: str | None = Query(default=None, alias="to"),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=50),
    user_id: str = Depends(get_effective_user_id),
    _: None = Depends(_require_enabled),
) -> SearchResponse:
    """Ranked, scoped, cursor-paginated full-text search (R7.2, R8.1)."""
    from app.search.repo import get_search_repo

    node_types: list[str] | None = None
    if types:
        node_types = [t.strip() for t in types.split(",") if t.strip() in _VALID_TYPES]
        if not node_types:
            node_types = None

    rows = await get_search_repo().search(
        user_id,
        q,
        limit=limit + 1,
        node_types=node_types,
        status=status,
        date_from=date_from,
        date_to=date_to,
        cursor=cursor,
    )
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = None
    if has_more and page:
        last = page[-1]
        next_cursor = f"{last['rank']}|{last['node_id']}"
    return SearchResponse(
        items=[SearchResult(**r) for r in page], next_cursor=next_cursor, query=q
    )


@router.post("/reindex", response_model=ReindexResponse)
async def reindex(
    user_id: str = Depends(get_effective_user_id),
    _: None = Depends(_require_enabled),
) -> ReindexResponse:
    """Rebuild the caller's own search index from source (R7.1 recovery)."""
    from app.search.indexer import rebuild_user_index

    counts = await rebuild_user_index(user_id)
    return ReindexResponse(indexed=counts)

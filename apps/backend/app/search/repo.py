"""Search-document data access — dialect-aware FTS, scoped in SQL (design §C).

Centralizes every ``search_documents`` query (allow-listed in the scoping guard).
Reads are **parameterized** and **scoped by ``user_id`` in SQL**, so a crafted
query string can never widen the scope or cross users (R7.2). Ranking is BM25 on
SQLite (via the ``search_fts`` mirror) and ``ts_rank`` on Postgres (GIN
``to_tsvector``). Cursor pagination is a stable ``(rank, node_id)`` keyset.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, func, select, text

from app.models import SearchDocument

logger = logging.getLogger(__name__)

__all__ = ["SearchRepo", "get_search_repo"]

# Alphanumeric (+unicode word) tokens only — everything else is dropped so a
# user's query can never inject FTS5/tsquery operators (defensive; params are
# bound regardless).
_TOKEN_RE = re.compile(r"\w+", re.UNICODE)
_MAX_TOKENS = 12


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sqlite_match_query(q: str) -> str | None:
    """Build a safe FTS5 MATCH expression: prefix-matched ANDed tokens."""
    tokens = _TOKEN_RE.findall(q or "")[:_MAX_TOKENS]
    if not tokens:
        return None
    return " ".join(f'"{t}"*' for t in tokens)


class SearchRepo:
    """Dialect-aware, user-scoped search index access."""

    def _sf(self):
        from app import database

        return database.db.session_factory

    def _dialect(self) -> str:
        from app import database

        return database.db.async_engine.dialect.name

    # -- write (indexer) ----------------------------------------------------

    async def upsert(
        self,
        *,
        user_id: str,
        node_type: str,
        node_id: str,
        title: str,
        body: str,
        status: str | None,
    ) -> None:
        """Idempotent upsert of a search document (triggers sync the FTS mirror)."""
        async with self._sf()() as session:
            row = await session.get(SearchDocument, (node_type, node_id))
            if row is None:
                session.add(
                    SearchDocument(
                        node_type=node_type,
                        node_id=node_id,
                        user_id=user_id,
                        title=title or "",
                        body=body or "",
                        status=status,
                        updated_at=_now(),
                    )
                )
            else:
                row.user_id = user_id
                row.title = title or ""
                row.body = body or ""
                row.status = status
                row.updated_at = _now()
            await session.commit()

    async def remove(self, node_type: str, node_id: str) -> None:
        async with self._sf()() as session:
            row = await session.get(SearchDocument, (node_type, node_id))
            if row is not None:
                await session.delete(row)
                await session.commit()

    # -- read (search) ------------------------------------------------------

    async def search(
        self,
        user_id: str,
        q: str,
        *,
        limit: int,
        node_types: list[str] | None = None,
        status: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        cursor: str | None = None,
    ) -> list[dict[str, Any]]:
        """Ranked, scoped, keyset-paginated search. Returns ``limit`` rows max."""
        cur_rank: float | None = None
        cur_id: str | None = None
        if cursor:
            rank_str, _, cur_id = cursor.partition("|")
            try:
                cur_rank = float(rank_str)
            except ValueError:
                cur_rank = None

        params: dict[str, Any] = {"uid": user_id, "lim": limit}
        filters = ""
        if node_types:
            names = {f"nt{i}": v for i, v in enumerate(node_types)}
            params.update(names)
            filters += f" AND sd.node_type IN ({', '.join(':' + k for k in names)})"
        if status:
            filters += " AND sd.status = :status"
            params["status"] = status
        if date_from:
            filters += " AND sd.updated_at >= :dfrom"
            params["dfrom"] = date_from
        if date_to:
            filters += " AND sd.updated_at <= :dto"
            params["dto"] = date_to

        if self._dialect() == "postgresql":
            return await self._search_pg(q, params, filters, cur_rank, cur_id)
        return await self._search_sqlite(q, params, filters, cur_rank, cur_id)

    async def _search_sqlite(self, q, params, filters, cur_rank, cur_id):
        match = _sqlite_match_query(q)
        if match is None:
            return []
        params["q"] = match
        cursor_clause = ""
        if cur_rank is not None and cur_id is not None:
            cursor_clause = " AND (rank > :crank OR (rank = :crank AND node_id > :cid))"
            params["crank"] = cur_rank
            params["cid"] = cur_id
        # Inner subquery over the FTS join; outer applies the keyset cursor.
        sql = text(
            "SELECT node_type, node_id, title, status, updated_at, rank FROM ("
            "  SELECT sd.node_type AS node_type, sd.node_id AS node_id, sd.title AS title,"
            "         sd.status AS status, sd.updated_at AS updated_at, bm25(search_fts) AS rank"
            "  FROM search_fts JOIN search_documents sd ON sd.rowid = search_fts.rowid"
            "  WHERE search_fts MATCH :q AND sd.user_id = :uid" + filters +
            ")" + (" WHERE" + cursor_clause[5:] if cursor_clause else "") +
            " ORDER BY rank ASC, node_id ASC LIMIT :lim"
        )
        async with self._sf()() as session:
            rows = (await session.execute(sql, params)).mappings().all()
            return [dict(r) for r in rows]

    async def _search_pg(self, q, params, filters, cur_rank, cur_id):
        params["q"] = q or ""
        cursor_clause = ""
        if cur_rank is not None and cur_id is not None:
            cursor_clause = " AND (rank < :crank OR (rank = :crank AND node_id > :cid))"
            params["crank"] = cur_rank
            params["cid"] = cur_id
        sql = text(
            "SELECT node_type, node_id, title, status, updated_at, rank FROM ("
            "  SELECT sd.node_type AS node_type, sd.node_id AS node_id, sd.title AS title,"
            "         sd.status AS status, sd.updated_at AS updated_at,"
            "         ts_rank(to_tsvector('english', sd.title || ' ' || sd.body),"
            "                 plainto_tsquery('english', :q)) AS rank"
            "  FROM search_documents sd"
            "  WHERE sd.user_id = :uid"
            "    AND to_tsvector('english', sd.title || ' ' || sd.body) @@ plainto_tsquery('english', :q)"
            + filters +
            ") ranked" + (" WHERE" + cursor_clause[5:] if cursor_clause else "") +
            " ORDER BY rank DESC, node_id ASC LIMIT :lim"
        )
        async with self._sf()() as session:
            rows = (await session.execute(sql, params)).mappings().all()
            return [dict(r) for r in rows]

    # -- rebuild / drift ----------------------------------------------------

    async def count(self, user_id: str) -> int:
        async with self._sf()() as session:
            return int(
                (
                    await session.execute(
                        select(func.count()).select_from(SearchDocument).where(
                            SearchDocument.user_id == user_id
                        )
                    )
                ).scalar() or 0
            )

    async def keys(self, user_id: str) -> set[tuple[str, str]]:
        """All ``(node_type, node_id)`` indexed for a user (drift detection)."""
        async with self._sf()() as session:
            rows = (
                await session.execute(
                    select(SearchDocument.node_type, SearchDocument.node_id).where(
                        SearchDocument.user_id == user_id
                    )
                )
            ).all()
            return {(r[0], r[1]) for r in rows}

    async def clear_user(self, user_id: str) -> int:
        """Remove every indexed doc for a user (rebuild precursor)."""
        async with self._sf()() as session:
            result = await session.execute(
                delete(SearchDocument).where(SearchDocument.user_id == user_id)
            )
            await session.commit()
            return int(result.rowcount or 0)


_repo: SearchRepo | None = None


def get_search_repo() -> SearchRepo:
    global _repo
    if _repo is None:
        _repo = SearchRepo()
    return _repo

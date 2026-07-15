"""Pydantic schemas for global search (P3 §C, Requirements 7–8)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

NodeType = Literal["resume", "job", "application"]


class SearchResult(BaseModel):
    """A single ranked, content-safe search hit that deep-links to a node."""

    node_type: NodeType
    node_id: str
    title: str
    status: str | None = None
    updated_at: str
    rank: float


class SearchResponse(BaseModel):
    items: list[SearchResult]
    next_cursor: str | None = None
    query: str


class ReindexResponse(BaseModel):
    """Result of a user-scoped index rebuild."""

    indexed: dict[str, int]

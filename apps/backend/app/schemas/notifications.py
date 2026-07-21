"""Pydantic schemas for the notifications API (P3 §B, Requirements 4-6)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Category = Literal["system", "reminder", "interview", "ai", "security"]
Priority = Literal["low", "normal", "high"]
Digest = Literal["off", "daily", "weekly"]


class NotificationResponse(BaseModel):
    """A single notification (content-safe: title/body carry no resume/JD data)."""

    id: str
    type: str
    category: Category
    priority: Priority
    title: str
    body: str | None = None
    node_type: str | None = None
    node_id: str | None = None
    group_key: str | None = None
    read: bool
    created_at: str


class NotificationListResponse(BaseModel):
    items: list[NotificationResponse]
    next_cursor: str | None = None


class UnreadCountResponse(BaseModel):
    """The O(1) unread badge + transport hints for the client poller."""

    unread: int
    transport: Literal["polling", "sse"]
    poll_interval_seconds: int


class ActionResponse(BaseModel):
    affected: int


class DismissGroupRequest(BaseModel):
    group_key: str = Field(min_length=1)


class CategoryPref(BaseModel):
    in_app: bool
    email: bool


class NotificationPrefsResponse(BaseModel):
    categories: dict[str, CategoryPref]
    digest: Digest


class CategoryPrefUpdate(BaseModel):
    category: Category
    in_app: bool
    email: bool


class UpdatePrefsRequest(BaseModel):
    """Update one or more category prefs and/or the digest setting."""

    categories: list[CategoryPrefUpdate] | None = None
    digest: Digest | None = None

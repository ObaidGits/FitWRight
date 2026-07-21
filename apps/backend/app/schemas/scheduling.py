"""Pydantic schemas for reminders, interviews, and the agenda (P3 §E/§F/§G)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ReminderStatus = Literal["pending", "snoozed", "firing", "fired", "cancelled"]
InterviewKind = Literal["screen", "technical", "onsite", "behavioral", "final", "other"]


# -- Reminders --------------------------------------------------------------


class ReminderCreate(BaseModel):
    """Create a reminder via an explicit UTC ``due_at`` or a quick ``preset``."""

    due_at: str | None = None
    preset: str | None = None
    tz: str = "UTC"
    note: str | None = Field(default=None, max_length=1000)
    recurrence: str | None = None


class ReminderUpdate(BaseModel):
    due_at: str | None = None
    tz: str | None = None
    note: str | None = Field(default=None, max_length=1000)
    recurrence: str | None = None


class ReminderSnooze(BaseModel):
    until: str | None = None
    preset: str | None = None


class ReminderResponse(BaseModel):
    id: str
    application_id: str
    due_at: str
    tz: str
    note: str | None = None
    recurrence: str | None = None
    status: ReminderStatus
    created_at: str
    updated_at: str


# -- Interviews -------------------------------------------------------------


class InterviewCreate(BaseModel):
    starts_at: str
    tz: str = "UTC"
    duration_min: int = Field(default=60, ge=1, le=1440)
    kind: InterviewKind = "screen"
    location: str | None = Field(default=None, max_length=500)
    notes: str | None = Field(default=None, max_length=2000)
    lead_times: list[int] | None = None


class InterviewUpdate(BaseModel):
    starts_at: str | None = None
    tz: str | None = None
    duration_min: int | None = Field(default=None, ge=1, le=1440)
    kind: InterviewKind | None = None
    location: str | None = Field(default=None, max_length=500)
    notes: str | None = Field(default=None, max_length=2000)
    lead_times: list[int] | None = None


class OverlapWarning(BaseModel):
    id: str
    starts_at: str


class InterviewResponse(BaseModel):
    id: str
    application_id: str
    starts_at: str
    tz: str
    duration_min: int
    kind: InterviewKind
    location: str | None = None
    notes: str | None = None
    lead_times: list[int]
    status: Literal["scheduled", "cancelled"]
    created_at: str
    updated_at: str
    # Soft warning on create/reschedule (never blocks) - empty when none.
    overlaps: list[OverlapWarning] = Field(default_factory=list)


# -- Agenda -----------------------------------------------------------------


class AgendaItem(BaseModel):
    kind: Literal["reminder", "interview"]
    id: str
    application_id: str
    when: str  # UTC ISO (due_at / starts_at)
    tz: str
    title: str
    status: str


class AgendaResponse(BaseModel):
    items: list[AgendaItem]
    next_cursor: str | None = None

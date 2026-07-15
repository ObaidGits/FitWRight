"""P3 Productivity — Reminders + Interviews on a shared scheduler (design §E/§F).

A claim-based :mod:`~app.scheduling.scheduler` scans due rows, atomically claims
them (prevent double-fire across workers), and emits events → notifications;
recurring reminders materialize their next occurrence on fire. CRUD + scans are
centralized + user-scoped in :mod:`~app.scheduling.repo`; timezone/DST-correct
recurrence math lives in :mod:`~app.scheduling.recurrence`; ICS export in
:mod:`~app.scheduling.ics`.
"""

from app.scheduling.recurrence import next_occurrence, validate_recurrence
from app.scheduling.scheduler import run_due_scans

__all__ = ["next_occurrence", "validate_recurrence", "run_due_scans"]

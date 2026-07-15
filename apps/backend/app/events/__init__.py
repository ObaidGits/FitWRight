"""Shared event platform (design §Platform, R16).

A transactional **outbox** (:mod:`app.events.outbox`) plus a registry-driven,
at-least-once, idempotent **consumer** framework. Producers emit domain events
in the same transaction as their change; async consumers (the notifier, the
search indexer) process them decoupled from the write path, with bounded retries
and a dead-letter park (DLQ) for poison events.
"""

from app.events.outbox import (
    OutboxEvent,
    emit,
    process_outbox_batch,
    register_handler,
    replay_dead_letters,
)
from app.events.types import EventType

__all__ = [
    "EventType",
    "OutboxEvent",
    "emit",
    "process_outbox_batch",
    "register_handler",
    "replay_dead_letters",
]

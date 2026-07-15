"""P4 Resilience — server-side building blocks.

Streaming AI (SSE relay + cross-worker task registry, cap, reaper), version-CAS
conflict resolution helpers, idempotency-key dedupe for autosave, and the
feature-flag surface. Every piece is flag-gated (``STREAMING_AI`` /
``OFFLINE_SUPPORT`` / ``ADVANCED_AUTOSAVE``) and user-scoped (ADR-4).
"""

from app.resilience.registry import (
    StreamRegistry,
    StreamSlot,
    get_stream_registry,
)
from app.resilience.idempotency import IdempotencyCache, get_idempotency_cache

__all__ = [
    "StreamRegistry",
    "StreamSlot",
    "get_stream_registry",
    "IdempotencyCache",
    "get_idempotency_cache",
]

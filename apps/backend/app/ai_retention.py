"""Ledger retention and reconciliation (tasks 6.3 and 5.4).

THE PRIVACY CONTRACT, stated explicitly because task 6.3 asks for it in writing:

``ai_usage_ledger`` is a PER-USER FINANCIAL RECORD. It holds who ran which feature,
when, how many tokens it used, what it cost the operator and what the user was charged.
It deliberately holds NONE of the following, and must never be extended to:

  * prompt or completion text, or any excerpt of either
  * the user's resume, job descriptions, or any document content
  * IP addresses, user agents, or device identifiers
  * anything that would let the operator reconstruct what a user wrote

This is the opposite contract from ``app/admin/ai_metrics.py``, which is deliberately
anonymous and aggregate, rejects a per-user dimension by design, and must never be
merged with this table. Two tables, two contracts, and the separation is the point.

RETENTION. Rows are kept long enough to settle a billing dispute and to reconcile
against a provider invoice, then deleted. The default is 400 days: comfortably longer
than a 12-month invoice cycle plus a dispute window, and short enough that the table
does not become an indefinite behavioural archive of every user's job search. Purchased
credits never expire, so the credit_transactions ledger - which is the balance's own
history and much smaller - is NOT trimmed here; deleting a grant row would make a
balance unexplainable.
"""

from __future__ import annotations

import logging

from app.config import settings

logger = logging.getLogger(__name__)

__all__ = ["reconcile_credits", "trim_usage_ledger"]


async def trim_usage_ledger(*, retention_days: int | None = None) -> int:
    """Delete usage rows past the retention horizon. Returns how many went.

    Only ``ai_usage_ledger``. Never ``credit_transactions``: that table explains how a
    balance came to be what it is, and a balance nobody can explain is worse than a
    large table.
    """
    from app.database import db

    days = int(retention_days or getattr(settings, "ai_usage_retention_days", 400))
    try:
        return await db.trim_ai_usage_ledger(older_than_days=days)
    except Exception:
        logger.exception("Usage ledger trim failed")
        return 0


async def reconcile_credits() -> dict:
    """Look for accounting states that should be impossible (task 5.4).

    Surfaced as a REPORT rather than auto-repaired. Every condition here means an
    assumption was violated, and silently "fixing" it would destroy the evidence of how
    - which is the only thing that lets the cause be found. A number that should always
    be zero is only useful if somebody sees it when it is not.
    """
    from app.database import db

    try:
        return await db.credit_reconciliation()
    except Exception:
        logger.exception("Credit reconciliation failed")
        return {"status": "error"}

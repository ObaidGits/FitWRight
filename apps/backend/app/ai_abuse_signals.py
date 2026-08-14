"""Free-tier abuse signals as a REVIEW FLAG, never an automatic block (task 6.2).

The distinction is the whole design. Every signal available here is circumstantial:
several accounts behind one IP is a family, an office, a university, a VPN, or a
country behind carrier NAT far more often than it is one person farming free credits.
Auto-blocking on it would lock out legitimate users who have done nothing wrong, and
they would have no way to explain themselves.

So this module ranks and explains. A human decides. That also keeps the operator honest
about how weak the evidence is - the explanation is written for someone who will have
to justify a decision to the person affected.

It reads only data the app already holds for other reasons (hashed IPs in the audit
trail, usage rows in the ledger). No new tracking, no device fingerprinting, nothing
retained that was not already retained.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["abuse_review_candidates"]


async def abuse_review_candidates(*, days: int = 7, limit: int = 20) -> list[dict[str, Any]]:
    """Accounts an operator may want to LOOK AT, with the reason spelled out.

    Ordered by how strong the combined signal is. Every entry carries a plain-language
    ``why`` and an explicit ``innocent_explanation``, because the second one is usually
    the true one and an operator reviewing a list of accusations should be reminded of
    that before they act.
    """
    from app.database import db

    candidates: list[dict[str, Any]] = []

    try:
        shared = await db.accounts_sharing_ip_hash(days=days, min_accounts=3)
    except Exception:
        logger.warning("Could not evaluate shared-IP signal")
        shared = []

    for group in shared:
        candidates.append(
            {
                "signal": "shared_ip",
                "strength": "weak",
                "ip_hash": group["ip_hash"],
                "user_ids": group["user_ids"],
                "why": (
                    f"{len(group['user_ids'])} accounts signed in from the same network "
                    f"in the last {days} days."
                ),
                "innocent_explanation": (
                    "A household, an office, a campus, a VPN, or a mobile carrier that "
                    "shares one address between thousands of people. This is normal for "
                    "most users and proves nothing on its own."
                ),
                "suggested_action": "Look at whether the accounts behave identically. Do not block on this alone.",
            }
        )

    try:
        burners = await db.accounts_spending_allowance_immediately(days=days, limit=limit)
    except Exception:
        logger.warning("Could not evaluate rapid-spend signal")
        burners = []

    for row in burners:
        candidates.append(
            {
                "signal": "allowance_drained_immediately",
                "strength": "weak",
                "user_ids": [row["user_id"]],
                "why": (
                    f"Spent its whole free allowance within {row['minutes']} minutes of "
                    "the account being created."
                ),
                "innocent_explanation": (
                    "An enthusiastic new user with a deadline. This is what a motivated "
                    "job seeker looks like, and it is the behaviour the product is for."
                ),
                "suggested_action": "Only interesting in combination with another signal.",
            }
        )

    # Weakest evidence last, so the review list does not open with its worst argument.
    order = {"strong": 0, "moderate": 1, "weak": 2}
    candidates.sort(key=lambda c: order.get(c["strength"], 9))
    return candidates[:limit]

"""AI alert conditions (task 5.3): channel failing, user spending oddly, cap near.

Three conditions, chosen because each one costs the operator real money or real users
while looking normal in every other view:

* A channel with a bad success rate is still "active" and still first in line. Failover
  hides it from users, which is the point - and also the reason nobody notices until the
  bill arrives.
* A single user spending far above everyone else is either a bug in our estimates, a
  compromised account, or someone building a business on the free tier. All three are
  worth a look and none of them announce themselves.
* A channel approaching its cap will stop serving traffic soon. Learning that from a
  cap that has ALREADY tripped means an outage first, explanation second.

Alerts are evaluated read-only over data already collected and returned as findings.
Nothing here blocks, throttles, or emails - it hands the operator a list. Auto-remediation
on signals this coarse would take a channel out of rotation because a provider had a bad
minute.
"""

from __future__ import annotations

import logging
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

__all__ = ["evaluate_ai_alerts"]

#: Below this, a channel is doing more harm than good. Not lower: transient provider
#: errors are normal and a jumpy threshold trains the operator to ignore the alert.
_MIN_SUCCESS_RATE = 0.85

#: Needs enough calls to mean anything. Two failures out of two is noise.
_MIN_CALLS_FOR_JUDGEMENT = 20

#: A user this many times the median cost is worth a look.
_SPEND_SPIKE_MULTIPLE = 10

#: Warn at this fraction of a channel's monthly cap.
_CAP_WARNING_FRACTION = 0.8


async def evaluate_ai_alerts(*, days: int = 7) -> list[dict[str, Any]]:
    """Return current AI alert findings, worst first. Never raises."""
    from app.database import db

    findings: list[dict[str, Any]] = []

    # 1. Channels failing behind the failover curtain.
    try:
        for row in await db.channel_performance(days=days):
            rate = row.get("success_rate")
            if (
                rate is not None
                and row["calls"] >= _MIN_CALLS_FOR_JUDGEMENT
                and rate < _MIN_SUCCESS_RATE
            ):
                findings.append(
                    {
                        "severity": "high",
                        "kind": "channel_error_rate",
                        "channel_id": row["channel_id"],
                        "detail": (
                            f"{int(rate * 100)}% success over {row['calls']} calls. "
                            "Failover is hiding this from users but you are paying for "
                            "the retries."
                        ),
                    }
                )
    except Exception:
        logger.warning("Channel error-rate alert could not be evaluated")

    # 2. A channel about to stop serving.
    try:
        spend = await db.channel_spend_micros_this_month()
        for channel in await db.list_ai_channels():
            cap_cents = channel.get("monthly_cost_cap_cents")
            if not cap_cents:
                continue
            used = spend.get(channel["id"], 0)
            cap_micros = int(cap_cents) * 10_000
            if used >= cap_micros:
                findings.append(
                    {
                        "severity": "high",
                        "kind": "channel_cap_reached",
                        "channel_id": channel["id"],
                        "detail": (
                            f"{channel['name']} has reached its monthly cap and is no "
                            "longer taking traffic."
                        ),
                    }
                )
            elif used >= cap_micros * _CAP_WARNING_FRACTION:
                findings.append(
                    {
                        "severity": "medium",
                        "kind": "channel_cap_approaching",
                        "channel_id": channel["id"],
                        "detail": (
                            f"{channel['name']} has used "
                            f"{int(used / cap_micros * 100)}% of its monthly cap."
                        ),
                    }
                )
    except Exception:
        logger.warning("Channel cap alert could not be evaluated")

    # 3. One user far above the rest.
    try:
        summary = await db.ai_spend_summary(days=days, top=25)
        users = summary.get("top_users") or []
        costs = sorted(u["cost_micros"] for u in users if u["cost_micros"] > 0)
        if len(costs) >= 5:
            median = costs[len(costs) // 2]
            for user in users:
                if median > 0 and user["cost_micros"] > median * _SPEND_SPIKE_MULTIPLE:
                    findings.append(
                        {
                            "severity": "medium",
                            "kind": "user_spend_spike",
                            "user_id": user["user_id"],
                            "detail": (
                                f"{user['calls']} calls costing "
                                f"{user['cost_micros'] / 1_000_000:.2f}, roughly "
                                f"{user['cost_micros'] // median}x the median user. "
                                "Could be a power user, a bug, or a compromised account."
                            ),
                        }
                    )
    except Exception:
        logger.warning("User spend-spike alert could not be evaluated")

    # 4. Invariants that should never break.
    try:
        from app.ai_retention import reconcile_credits

        report = await reconcile_credits()
        if report.get("status") == "attention":
            findings.append(
                {
                    "severity": "high",
                    "kind": "accounting_invariant_broken",
                    "detail": (
                        "Reconciliation found states that should be impossible: "
                        f"{report.get('findings')}"
                    ),
                }
            )
    except Exception:
        logger.warning("Reconciliation alert could not be evaluated")

    if not settings.ai_credits_enabled:
        # Still reported, but labelled: while the flag is off these are observations
        # about metering, not about anyone being charged.
        for f in findings:
            f["note"] = "Credits are disabled; no users are being charged."

    order = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda f: order.get(f["severity"], 9))
    return findings

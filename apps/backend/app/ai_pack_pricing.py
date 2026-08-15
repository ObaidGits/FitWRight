"""What a pack costs right now. One function, used by every surface.

The effective price is decided HERE and nowhere else. The customer-facing buy screen,
the admin preview, the order sent to the payment provider, and the amount re-checked
when the provider's webhook arrives all call the same function, because the failure mode
of duplicating it is the worst kind: a page that advertises one price while the charge is
another. That is not a bug report, it is a chargeback.

WHY A SALE IS AN EXPLICIT INTEGER, NOT A PERCENTAGE

The operator thinks in percentages and the admin form accepts one, but what is stored is
the computed integer. A stored percentage would be multiplied out again in every place
the price is shown or checked, and each of those could round differently. A one-paisa
disagreement between the page and the webhook check fails a purchase for a customer who
did nothing wrong.

WHY THE WINDOW EXPIRES BY ITSELF

An offer is only live between its start and end. Outside that, the regular price applies
immediately - no scheduled job to revert it, so nothing to forget or fail. An offer that
keeps selling at the discount because a cron did not run is a slow leak nobody notices.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

__all__ = ["PackOffer", "discounted_amount", "effective_offer", "percent_off"]


@dataclass(frozen=True)
class PackOffer:
    """A pack as a customer sees it right now, with the sale already resolved."""

    id: str
    label: str
    credits: int
    currency: str
    #: What they pay. Always the number to charge.
    amount_minor: int
    #: The struck-through "was" price, present only while a sale is live. None means
    #: there is no offer, so the UI must not render an empty comparison.
    compare_at_minor: int | None = None
    sale_label: str | None = None
    sale_ends_at: str | None = None
    description: str | None = None

    @property
    def on_sale(self) -> bool:
        return self.compare_at_minor is not None

    @property
    def percent_off(self) -> int:
        """Whole-percent saving, for display only. Never used to compute a charge."""
        if not self.compare_at_minor or self.compare_at_minor <= 0:
            return 0
        return round((1 - self.amount_minor / self.compare_at_minor) * 100)


def _parse(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        moment = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        # An unparseable bound is treated as ABSENT, which for a start date means "began
        # already" and for an end date means "no end". That is the lenient reading, and
        # it is the right one: a typo in a date must not silently charge the regular
        # price to customers who were shown a discount.
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def sale_is_live(
    *,
    sale_amount_minor: int | None,
    sale_starts_at: str | None,
    sale_ends_at: str | None,
    now: datetime | None = None,
) -> bool:
    """Whether a discount applies at this instant."""
    if sale_amount_minor is None:
        return False
    moment = now or datetime.now(timezone.utc)
    starts = _parse(sale_starts_at)
    ends = _parse(sale_ends_at)
    if starts and moment < starts:
        return False
    if ends and moment > ends:
        return False
    return True


def effective_offer(pack: dict, *, now: datetime | None = None) -> PackOffer:
    """Resolve a stored pack row into what the customer is offered right now."""
    regular = int(pack["amount_minor"])
    sale = pack.get("sale_amount_minor")
    sale = int(sale) if sale is not None else None

    live = sale_is_live(
        sale_amount_minor=sale,
        sale_starts_at=pack.get("sale_starts_at"),
        sale_ends_at=pack.get("sale_ends_at"),
        now=now,
    )

    # A "sale" that is not cheaper is refused at write time, but guard here too: if a
    # bad row ever exists, the customer must not be charged MORE under a discount label.
    if live and sale is not None and sale < regular:
        return PackOffer(
            id=str(pack["id"]),
            label=str(pack.get("label") or pack["id"]),
            credits=int(pack["credits"]),
            currency=str(pack.get("currency") or "INR"),
            amount_minor=sale,
            compare_at_minor=regular,
            sale_label=pack.get("sale_label"),
            sale_ends_at=pack.get("sale_ends_at"),
            description=pack.get("description"),
        )

    return PackOffer(
        id=str(pack["id"]),
        label=str(pack.get("label") or pack["id"]),
        credits=int(pack["credits"]),
        currency=str(pack.get("currency") or "INR"),
        amount_minor=regular,
        compare_at_minor=None,
        sale_label=None,
        sale_ends_at=None,
        description=pack.get("description"),
    )


def discounted_amount(amount_minor: int, percent: float) -> int:
    """The admin form's helper: turn "20% off" into an exact integer price.

    Rounds to the nearest minor unit and never below zero or above the original. The
    result is what gets STORED - this function runs once, when the operator saves, not on
    every page render.
    """
    pct = max(0.0, min(float(percent), 100.0))
    reduced = round(int(amount_minor) * (1 - pct / 100))
    return max(0, min(int(reduced), int(amount_minor)))


def percent_off(amount_minor: int, sale_amount_minor: int) -> int:
    """Whole-percent saving between two prices, for display."""
    if amount_minor <= 0 or sale_amount_minor >= amount_minor:
        return 0
    return round((1 - sale_amount_minor / amount_minor) * 100)

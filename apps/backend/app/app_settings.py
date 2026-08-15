"""Admin-editable application settings.

Each namespaced key has an owner module that validates its shape. This module owns the
generic read/write plus the two settings that exist today: the seller details printed on
a receipt, and the mail transport.

TWO RULES THAT MATTER

**Env vars win where they already exist.** For mail, an operator who configured
``EMAIL_PROVIDER``/SMTP in the environment must not see the admin panel silently ignore
it - so a database row is a FALLBACK, used only when the environment is unset. Settings
that never had an env var (seller details) live only here.

**Secrets never enter the JSON blob.** An SMTP password lives in the row's separate
encrypted column, so reading or logging a setting's value cannot leak it. The form
submits a blank password when unchanged, which is why the repository defaults to keeping
the stored secret rather than treating blank as "clear".

ON GST: the seller block carries optional tax fields that stay hidden while ``gstin`` is
blank. A fresh startup below the registration threshold issues a plain payment receipt
with no tax line and no GSTIN - putting a fake one on a document would be worse than
omitting it. Modelling the fields now means registering later is a settings change rather
than a schema migration plus a re-issue of every past receipt.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

__all__ = [
    "MAIL_EVENTS",
    "MailTransport",
    "SellerDetails",
    "get_mail_transport",
    "get_seller_details",
    "invalidate_settings_cache",
    "save_mail_transport",
    "save_seller_details",
]

SELLER_KEY = "billing.seller"
MAIL_KEY = "mail.transport"

#: The distinct emails the app can send, so an operator can see the list and choose
#: which are enabled. Keys are stable; labels are what the admin panel shows.
MAIL_EVENTS: dict[str, str] = {
    "email_verification": "Email verification / OTP",
    "password_reset": "Password reset",
    "welcome": "Welcome email after signup",
    "purchase_receipt": "Purchase confirmation + receipt",
}


@dataclass(frozen=True)
class SellerDetails:
    """Who the receipt is from. Printed on every receipt, so it is a legal-ish artifact."""

    business_name: str = ""
    address: str = ""
    email: str = ""
    phone: str = ""
    #: Blank until the operator is GST-registered. While blank, receipts show no tax
    #: line and no tax id at all rather than an empty label.
    gstin: str = ""
    #: Only meaningful once a GSTIN exists. Percent, as an integer.
    tax_percent: int = 0
    #: Free text under the totals ("This is a computer-generated receipt.").
    footer_note: str = ""

    @property
    def is_configured(self) -> bool:
        """A receipt with no seller name is not worth issuing."""
        return bool(self.business_name.strip())

    @property
    def charges_tax(self) -> bool:
        return bool(self.gstin.strip()) and int(self.tax_percent) > 0


@dataclass(frozen=True)
class MailTransport:
    """How mail leaves the building.

    ``source`` records where this came from so the admin panel can say "configured in the
    environment, not editable here" instead of showing a form that has no effect.
    """

    provider: str = ""  # "" | smtp | resend
    from_email: str = ""
    from_name: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_use_tls: bool = True
    #: Never returned to a client. Present only for the sender factory.
    secret: str = ""
    #: Which events are allowed to send. Absent key = enabled, so adding a new event
    #: does not require every existing deployment to opt in.
    enabled_events: dict[str, bool] = field(default_factory=dict)
    source: str = "unset"  # unset | env | database

    def sends(self, event: str) -> bool:
        return bool(self.enabled_events.get(event, True))


_cache: dict[str, object] = {}


def invalidate_settings_cache() -> None:
    """Called after an admin edit so the next read reflects it."""
    _cache.clear()


async def get_seller_details(db) -> SellerDetails:
    """Seller block for receipts. Never raises - a receipt is better than an error page."""
    if (cached := _cache.get(SELLER_KEY)) is not None:
        return cached  # type: ignore[return-value]
    try:
        row = await db.get_app_setting(SELLER_KEY)
    except Exception:
        logger.warning("Seller settings read failed; using empty seller details")
        return SellerDetails()
    value = (row or {}).get("value") or {}
    seller = SellerDetails(
        business_name=str(value.get("business_name") or ""),
        address=str(value.get("address") or ""),
        email=str(value.get("email") or ""),
        phone=str(value.get("phone") or ""),
        gstin=str(value.get("gstin") or ""),
        tax_percent=int(value.get("tax_percent") or 0),
        footer_note=str(value.get("footer_note") or ""),
    )
    _cache[SELLER_KEY] = seller
    return seller


async def save_seller_details(db, seller: dict, *, updated_by: str | None = None) -> SellerDetails:
    """Validate and store the seller block."""
    tax_percent = int(seller.get("tax_percent") or 0)
    if tax_percent < 0 or tax_percent > 100:
        raise ValueError("Tax percent must be between 0 and 100.")
    gstin = str(seller.get("gstin") or "").strip()
    if tax_percent > 0 and not gstin:
        # Charging tax without a registration number is not something to let through
        # quietly: the resulting receipt would be wrong in a way that matters.
        raise ValueError(
            "Add your GSTIN before setting a tax percent - a receipt cannot show tax "
            "without a registration number."
        )
    await db.set_app_setting(
        SELLER_KEY,
        value={
            "business_name": str(seller.get("business_name") or "").strip(),
            "address": str(seller.get("address") or "").strip(),
            "email": str(seller.get("email") or "").strip(),
            "phone": str(seller.get("phone") or "").strip(),
            "gstin": gstin,
            "tax_percent": tax_percent,
            "footer_note": str(seller.get("footer_note") or "").strip(),
        },
        updated_by=updated_by,
    )
    invalidate_settings_cache()
    return await get_seller_details(db)


async def get_mail_transport(db) -> MailTransport:
    """How to send mail: the environment first, then the database.

    The environment wins deliberately. An operator who set SMTP in ``.env`` and then finds
    the panel quietly overriding it has been misled by the UI, so the panel reports
    ``source="env"`` and explains that instead.
    """
    from app.config import settings

    if (cached := _cache.get(MAIL_KEY)) is not None:
        return cached  # type: ignore[return-value]

    env_provider = (getattr(settings, "email_provider", "") or "").strip().lower()
    if env_provider:
        transport = MailTransport(
            provider=env_provider,
            from_email=getattr(settings, "email_from", "") or "",
            smtp_host=getattr(settings, "email_smtp_host", "") or "",
            smtp_port=int(getattr(settings, "email_smtp_port", 587) or 587),
            smtp_user=getattr(settings, "email_smtp_user", "") or "",
            smtp_use_tls=bool(getattr(settings, "email_smtp_use_tls", True)),
            secret=getattr(settings, "email_smtp_password", "") or "",
            source="env",
        )
        _cache[MAIL_KEY] = transport
        return transport

    try:
        row = await db.get_app_setting(MAIL_KEY)
    except Exception:
        logger.warning("Mail settings read failed; mail will log instead of send")
        return MailTransport()

    if row is None:
        return MailTransport()

    value = row.get("value") or {}
    secret = ""
    if row.get("secret_ciphertext"):
        try:
            from app.crypto import decrypt

            secret = decrypt(row["secret_ciphertext"]) or ""
        except Exception:
            # Same failure mode as stored provider keys: the encryption secret changed.
            # Report it rather than silently sending unauthenticated.
            logger.warning("Stored mail secret could not be decrypted; treating as unset")

    transport = MailTransport(
        provider=str(value.get("provider") or "").strip().lower(),
        from_email=str(value.get("from_email") or ""),
        from_name=str(value.get("from_name") or ""),
        smtp_host=str(value.get("smtp_host") or ""),
        smtp_port=int(value.get("smtp_port") or 587),
        smtp_user=str(value.get("smtp_user") or ""),
        smtp_use_tls=bool(value.get("smtp_use_tls", True)),
        secret=secret,
        enabled_events={
            k: bool(v) for k, v in (value.get("enabled_events") or {}).items()
        },
        source="database",
    )
    _cache[MAIL_KEY] = transport
    return transport


async def save_mail_transport(
    db, payload: dict, *, secret: str | None = None, updated_by: str | None = None
) -> MailTransport:
    """Validate and store the mail transport.

    ``secret=None`` keeps whatever is stored, because the form submits a blank password
    when the operator did not intend to change it - treating blank as "clear" would break
    delivery every time an unrelated field was edited.
    """
    provider = str(payload.get("provider") or "").strip().lower()
    if provider not in ("", "smtp", "resend"):
        raise ValueError("Provider must be smtp, resend, or empty.")
    if provider == "smtp" and not str(payload.get("smtp_host") or "").strip():
        raise ValueError("SMTP needs a host.")
    if provider and not str(payload.get("from_email") or "").strip():
        # Every provider rejects a message with no sender, and the resulting error
        # surfaces far from here.
        raise ValueError("Set the 'from' address that mail will be sent from.")

    ciphertext = None
    if secret:
        from app.crypto import encrypt

        ciphertext = encrypt(secret)

    await db.set_app_setting(
        MAIL_KEY,
        value={
            "provider": provider,
            "from_email": str(payload.get("from_email") or "").strip(),
            "from_name": str(payload.get("from_name") or "").strip(),
            "smtp_host": str(payload.get("smtp_host") or "").strip(),
            "smtp_port": int(payload.get("smtp_port") or 587),
            "smtp_user": str(payload.get("smtp_user") or "").strip(),
            "smtp_use_tls": bool(payload.get("smtp_use_tls", True)),
            "enabled_events": {
                k: bool(v)
                for k, v in (payload.get("enabled_events") or {}).items()
                if k in MAIL_EVENTS
            },
        },
        secret_ciphertext=ciphertext,
        keep_existing_secret=ciphertext is None,
        updated_by=updated_by,
    )
    invalidate_settings_cache()
    return await get_mail_transport(db)

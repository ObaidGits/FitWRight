"""Payment receipts.

A customer who paid needs a document they can keep, forward to an employer, or attach to
a reimbursement claim. Before this, ``invoice_number`` was generated and stored and
nothing ever rendered it.

WHY IT IS CALLED A RECEIPT, NOT AN INVOICE

A tax invoice is a specific thing with specific required fields, and issuing one without
being registered would be wrong in a way that matters. So the default document is a plain
payment receipt: who paid, what for, how much, when, and a reference. The moment a GSTIN
is configured the same document gains the tax lines and calls itself a tax invoice - which
is why the tax fields were modelled from the start even though a fresh startup below the
registration threshold will not use them.

TAX IS DERIVED FROM WHAT WAS CHARGED, NOT ADDED TO IT

When tax applies, the amount the customer paid is treated as INCLUSIVE and the tax is
broken out of it. The alternative - adding tax on top - would mean the receipt disagrees
with the amount actually taken from their card, which is the one number they can verify
independently.

The document is composed here as HTML and rendered by the shared Chromium, so it depends
on stored data alone: no frontend route, no session, nothing that can render differently
for two people looking at the same payment.
"""

from __future__ import annotations

import html
import logging
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)

__all__ = ["ReceiptData", "build_receipt", "build_receipt_html", "render_receipt_pdf"]


def _money(minor: int, currency: str) -> str:
    symbol = "₹" if currency == "INR" else f"{currency} "
    return f"{symbol}{minor / 100:,.2f}"


def _fmt_date(iso: str | None) -> str:
    if not iso:
        return "-"
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).strftime("%d %B %Y")
    except (TypeError, ValueError):
        return iso


@dataclass(frozen=True)
class ReceiptData:
    """Everything printed on one receipt, already resolved."""

    number: str
    issued_on: str
    #: "receipt" until a GSTIN exists, then "tax_invoice".
    kind: str
    buyer_name: str
    buyer_email: str
    description: str
    credits: int
    currency: str
    #: What the customer actually paid.
    total_minor: int
    #: Tax broken OUT of the total. Zero when not registered.
    tax_minor: int
    tax_percent: int
    #: total_minor - tax_minor.
    subtotal_minor: int
    seller_name: str
    seller_address: str
    seller_email: str
    seller_gstin: str
    footer_note: str
    state: str

    @property
    def title(self) -> str:
        return "Tax Invoice" if self.kind == "tax_invoice" else "Payment Receipt"

    @property
    def shows_tax(self) -> bool:
        return self.kind == "tax_invoice" and self.tax_minor > 0


def build_receipt(purchase: dict, *, seller, buyer_name: str, buyer_email: str) -> ReceiptData:
    """Resolve a purchase plus seller settings into a printable receipt.

    Tax is extracted from an inclusive total using integer arithmetic - the same reason
    money is stored in minor units everywhere else in this system. A float here would
    round differently on different rows and make two receipts for the same pack disagree
    by a paisa.
    """
    total = int(purchase.get("amount_minor") or 0)
    currency = purchase.get("currency") or "INR"

    tax_minor = 0
    percent = 0
    if seller.charges_tax:
        percent = int(seller.tax_percent)
        # total = base + base*p/100  ->  tax = total*p / (100+p)
        tax_minor = round(total * percent / (100 + percent))

    return ReceiptData(
        number=purchase.get("invoice_number") or purchase.get("id") or "-",
        issued_on=purchase.get("granted_at") or purchase.get("created_at") or "",
        kind="tax_invoice" if seller.charges_tax else "receipt",
        buyer_name=buyer_name or "-",
        buyer_email=buyer_email or "-",
        description=f"{int(purchase.get('credits') or 0):,} AI credits"
        + (f" ({purchase.get('pack_id')})" if purchase.get("pack_id") else ""),
        credits=int(purchase.get("credits") or 0),
        currency=currency,
        total_minor=total,
        tax_minor=tax_minor,
        tax_percent=percent,
        subtotal_minor=total - tax_minor,
        seller_name=seller.business_name or "FitWright",
        seller_address=seller.address,
        seller_email=seller.email,
        seller_gstin=seller.gstin,
        footer_note=seller.footer_note,
        state=str(purchase.get("state") or ""),
    )


def build_receipt_html(receipt: ReceiptData) -> str:
    """One self-contained HTML document. Every value escaped.

    Inline styles and no external assets on purpose: the renderer loads this string with
    no network, so a stylesheet or webfont reference would silently not apply and the
    receipt would print unstyled.
    """
    e = html.escape

    tax_rows = ""
    if receipt.shows_tax:
        tax_rows = f"""
        <tr>
          <td class="label">Subtotal</td>
          <td class="value">{e(_money(receipt.subtotal_minor, receipt.currency))}</td>
        </tr>
        <tr>
          <td class="label">GST ({receipt.tax_percent}%)</td>
          <td class="value">{e(_money(receipt.tax_minor, receipt.currency))}</td>
        </tr>
        """

    gstin_line = (
        f'<p class="muted">GSTIN: {e(receipt.seller_gstin)}</p>' if receipt.seller_gstin else ""
    )
    address_line = (
        f'<p class="muted">{e(receipt.seller_address)}</p>' if receipt.seller_address else ""
    )
    seller_email_line = (
        f'<p class="muted">{e(receipt.seller_email)}</p>' if receipt.seller_email else ""
    )
    # A refunded payment must say so on its own receipt. Someone holding a receipt for
    # money they got back, with nothing indicating it, has a document that misleads them.
    refunded_banner = (
        '<p class="refunded">This payment was refunded.</p>'
        if receipt.state == "refunded"
        else ""
    )
    footer = f'<p class="muted small">{e(receipt.footer_note)}</p>' if receipt.footer_note else ""

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{e(receipt.title)} {e(receipt.number)}</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
          color: #1a1a17; margin: 0; padding: 48px; font-size: 13px; line-height: 1.5; }}
  .head {{ display: flex; justify-content: space-between; align-items: flex-start;
           border-bottom: 2px solid #1a1a17; padding-bottom: 16px; }}
  h1 {{ font-size: 22px; margin: 0 0 4px; }}
  h2 {{ font-size: 13px; text-transform: uppercase; letter-spacing: .06em;
        color: #6b6b63; margin: 28px 0 8px; }}
  .muted {{ color: #6b6b63; margin: 2px 0; }}
  .small {{ font-size: 11px; }}
  .right {{ text-align: right; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 8px; }}
  th {{ text-align: left; font-size: 11px; text-transform: uppercase; letter-spacing: .06em;
        color: #6b6b63; border-bottom: 1px solid #e7e5df; padding: 8px 0; }}
  td {{ padding: 10px 0; border-bottom: 1px solid #f2f1ed; }}
  td.value, th.value {{ text-align: right; }}
  td.label {{ color: #6b6b63; }}
  .total td {{ font-weight: 700; font-size: 15px; border-bottom: none; border-top: 2px solid #1a1a17; }}
  .refunded {{ margin-top: 16px; padding: 8px 12px; background: #fef2f2; color: #b91c1c;
               font-weight: 600; border-radius: 6px; }}
</style></head>
<body>
  <div class="head">
    <div>
      <h1>{e(receipt.seller_name)}</h1>
      {address_line}{seller_email_line}{gstin_line}
    </div>
    <div class="right">
      <h1>{e(receipt.title)}</h1>
      <p class="muted">No. {e(receipt.number)}</p>
      <p class="muted">{e(_fmt_date(receipt.issued_on))}</p>
    </div>
  </div>

  <h2>Billed to</h2>
  <p><strong>{e(receipt.buyer_name)}</strong></p>
  <p class="muted">{e(receipt.buyer_email)}</p>

  <h2>Details</h2>
  <table>
    <thead><tr><th>Description</th><th class="value">Amount</th></tr></thead>
    <tbody>
      <tr>
        <td>{e(receipt.description)}</td>
        <td class="value">{e(_money(receipt.total_minor, receipt.currency))}</td>
      </tr>
      {tax_rows}
      <tr class="total">
        <td>Total paid</td>
        <td class="value">{e(_money(receipt.total_minor, receipt.currency))}</td>
      </tr>
    </tbody>
  </table>

  {refunded_banner}

  <h2>Notes</h2>
  <p class="muted small">Credits purchased never expire.
  {"" if receipt.shows_tax else "This is a payment receipt, not a tax invoice."}</p>
  {footer}
</body></html>"""


async def render_receipt_pdf(receipt: ReceiptData) -> bytes:
    from app.pdf import render_html_to_pdf

    return await render_html_to_pdf(build_receipt_html(receipt))

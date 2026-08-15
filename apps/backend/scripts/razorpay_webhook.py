#!/usr/bin/env python3
"""Register, list or remove the Razorpay webhook.

Why a script rather than dashboard clicks: the secret configured in Razorpay and the one
in this app's environment MUST be the same string, and typing a 40-character secret into
two places is exactly where a mismatch comes from. This reads the app's own
``RAZORPAY_WEBHOOK_SECRET`` and sends that, so the two cannot disagree.

Razorpay does NOT generate the webhook secret - you choose it. That surprises people
coming from Stripe, where the provider hands you a ``whsec_...`` value.

Usage (from apps/backend):

    uv run python scripts/razorpay_webhook.py list
    uv run python scripts/razorpay_webhook.py create https://your-domain.com
    uv run python scripts/razorpay_webhook.py delete <webhook_id>

The URL argument is the SITE ROOT; the endpoint path is appended for you so it cannot be
mistyped.
"""

from __future__ import annotations

import base64
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

WEBHOOK_PATH = "/api/v1/credits/purchase/webhook"

#: Only the events that move money. Subscribing to everything means Razorpay retries
#: deliveries this app deliberately ignores, which buries the ones that matter.
EVENTS = ["order.paid", "payment.captured", "payment.failed", "refund.processed"]

API = "https://api.razorpay.com/v1/webhooks"


def _env() -> dict[str, str]:
    """Credentials from the real environment first, then apps/backend/.env.

    Production has no ``.env`` file - Heroku supplies config as environment variables -
    so reading only the file would make this script work locally and fail in the one
    place an operator most needs it.
    """
    import os

    keys = ("RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET", "RAZORPAY_WEBHOOK_SECRET")
    out = {key: os.environ[key] for key in keys if os.environ.get(key)}
    if all(key in out for key in ("RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET")):
        return out

    path = Path(__file__).resolve().parent.parent / ".env"
    if not path.exists():
        sys.exit(
            "Razorpay credentials not found in the environment, and no .env at "
            f"{path}"
        )
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            key = key.strip()
            # Real environment wins: it is what the running app itself reads.
            out.setdefault(key, value.strip())
    return out


def _call(method: str, url: str, payload: dict | None = None) -> tuple[int, dict]:
    env = _env()
    key_id = env.get("RAZORPAY_KEY_ID", "")
    key_secret = env.get("RAZORPAY_KEY_SECRET", "")
    if not key_id or not key_secret:
        sys.exit("RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET are not set in .env")

    auth = base64.b64encode(f"{key_id}:{key_secret}".encode()).decode()
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode()
        try:
            return exc.code, json.loads(body)
        except ValueError:
            return exc.code, {"raw": body[:500]}


def cmd_list() -> None:
    status, body = _call("GET", API)
    if status != 200:
        sys.exit(f"Razorpay returned {status}: {body}")
    items = body.get("items") or []
    if not items:
        print("No webhooks configured.")
        return
    for item in items:
        print(f"{item.get('id')}  active={item.get('active')}  {item.get('url')}")
        # Razorpay returns the FULL catalogue of available events with a 0/1 flag each,
        # so printing every key lists ~50 events that are not subscribed and makes the
        # webhook look wired to everything. Only the enabled ones are real.
        enabled = sorted(
            name for name, on in (item.get("events") or {}).items() if on
        )
        print(f"    subscribed ({len(enabled)}): {', '.join(enabled) or 'none'}")


def cmd_create(site_root: str) -> None:
    site_root = site_root.rstrip("/")
    if not site_root.startswith("https://"):
        # Razorpay will not deliver to plain HTTP, and a localhost URL is unreachable
        # from their servers - failing here is clearer than a webhook that never fires.
        sys.exit("The URL must start with https:// and be reachable from the internet.")

    secret = _env().get("RAZORPAY_WEBHOOK_SECRET", "")
    if not secret:
        sys.exit(
            "RAZORPAY_WEBHOOK_SECRET is empty in .env. Set it first - it must be the "
            "same value here and at Razorpay, or every webhook fails verification."
        )

    url = f"{site_root}{WEBHOOK_PATH}"
    status, body = _call(
        "POST",
        API,
        {
            "url": url,
            "secret": secret,
            # Razorpay expects a MAP of event name -> 1, not a list. Sending a list makes
            # it read the indices as event names and reject "1, 2, 3" - which is exactly
            # what happened on the first attempt.
            "events": {name: 1 for name in EVENTS},
        },
    )
    if status not in (200, 201):
        sys.exit(f"Razorpay returned {status}: {body}")
    print(f"Created webhook {body.get('id')}")
    print(f"  url:    {url}")
    print(f"  events: {', '.join(EVENTS)}")
    print("  secret: taken from .env (not printed)")


def cmd_delete(webhook_id: str) -> None:
    status, body = _call("DELETE", f"{API}/{webhook_id}")
    if status not in (200, 204):
        sys.exit(f"Razorpay returned {status}: {body}")
    print(f"Deleted webhook {webhook_id}")


def main() -> None:
    args = sys.argv[1:]
    if not args:
        sys.exit(__doc__)
    command = args[0]
    if command == "list":
        cmd_list()
    elif command == "create":
        if len(args) < 2:
            sys.exit("Usage: razorpay_webhook.py create https://your-domain.com")
        cmd_create(args[1])
    elif command == "delete":
        if len(args) < 2:
            sys.exit("Usage: razorpay_webhook.py delete <webhook_id>")
        cmd_delete(args[1])
    else:
        sys.exit(f"Unknown command {command!r}. Use list, create or delete.")


if __name__ == "__main__":
    main()

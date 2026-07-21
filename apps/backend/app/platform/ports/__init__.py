"""Canonical port namespace (ARCHITECTURE §11; IMPLEMENTATION_PLAN Phase 4).

A **port** is an interface with ≥2 real implementations or a genuine external
boundary that must be faked in tests. The concrete contracts already live in
their owning modules; this package gives them a single, discoverable name so
new code and contract tests refer to *ports* rather than reaching into
implementation modules. It intentionally **re-exports** (no duplication): the
behavior/definition stays with its owner.

Ports (each has ≥2 implementations today):
- ``KVStorePort``      - Local / DB / Redis            (``app.auth.kvstore``)
- ``StoragePort``      - Local disk / Cloudinary       (``app.storage.provider``)
- ``MailerPort``       - Logging / SMTP / Resend        (``app.auth.email``)
- ``CaptchaPort``      - AllowAll / Turnstile           (``app.auth.captcha``)
- ``BreachCheckPort``  - Noop / HIBP                    (``app.auth.breach``)

Deliberately NOT ports (single implementation / cross-cutting - ARCHITECTURE
§11): Search (Postgres FTS only), Secrets, Logging, Metrics, Config,
Notifications (domain, not infra), OAuth (detail inside identity).
"""

from __future__ import annotations

from app.auth.breach import BreachedPasswordCheck as BreachCheckPort
from app.auth.captcha import CaptchaVerifier as CaptchaPort
from app.auth.email import EmailSender as MailerPort
from app.auth.kvstore import KVStore as KVStorePort
from app.storage.provider import StorageProvider as StoragePort

__all__ = [
    "KVStorePort",
    "StoragePort",
    "MailerPort",
    "CaptchaPort",
    "BreachCheckPort",
]

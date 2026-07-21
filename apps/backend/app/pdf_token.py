"""Short-lived signed tokens for server-side PDF print rendering.

The PDF export flow renders the frontend ``/print/...`` route with headless
Chromium. That print page is a **server component** that fetches the resume from
the backend - but the headless browser carries no user session cookie, so in
hosted mode (``SINGLE_USER_MODE=false``) the backend rejects the fetch with 401
and the page 500s (-> "PDF rendering failed").

To authenticate that internal render without weakening the real auth surface,
the (already-authenticated) export endpoint mints a **short-lived, signed print
token** bound to ``(user_id, resume_id)`` and appends it to the print URL. The
print page presents it to a dedicated, read-only, token-verified data endpoint.

The token is signed with ``SESSION_SECRET`` (dual-key read window with
``SESSION_SECRET_PREV`` so secret rotation is seamless) using the same
``itsdangerous`` primitive as the OAuth transient cookie. It is stateless
(embedded timestamp enforces the TTL) and multi-worker-safe.
"""

from __future__ import annotations

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.config import Settings, settings as app_settings

__all__ = ["make_print_token", "verify_print_token", "PRINT_TOKEN_TTL_SECONDS"]

# itsdangerous salt namespacing these tokens (distinct from OAuth/session salts).
_PRINT_SALT = "resume-print-v1"

# The render must start within this window of the export request. Playwright
# navigation + SSR fetch complete in well under a minute; 5 min is generous
# headroom for a cold cache / slow host without leaving a long-lived credential.
PRINT_TOKEN_TTL_SECONDS = 300


def make_print_token(user_id: str, resume_id: str, *, config: Settings | None = None) -> str:
    """Sign a print token binding the render to ``(user_id, resume_id)``."""
    config = config or app_settings
    serializer = URLSafeTimedSerializer(config.session_secret, salt=_PRINT_SALT)
    return serializer.dumps({"uid": user_id, "rid": resume_id})


def verify_print_token(
    token: str, resume_id: str, *, config: Settings | None = None
) -> str | None:
    """Return the bound ``user_id`` if ``token`` is valid for ``resume_id``, else None.

    Rejects expired/forged tokens and tokens minted for a different resume
    (prevents using one resume's token to read another).
    """
    if not token:
        return None
    config = config or app_settings
    candidates = [config.session_secret]
    if getattr(config, "session_secret_prev", None):
        candidates.append(config.session_secret_prev)
    for secret in candidates:
        serializer = URLSafeTimedSerializer(secret, salt=_PRINT_SALT)
        try:
            data = serializer.loads(token, max_age=PRINT_TOKEN_TTL_SECONDS)
        except (SignatureExpired, BadSignature):
            continue
        except Exception:
            continue
        if isinstance(data, dict) and data.get("rid") == resume_id and data.get("uid"):
            return str(data["uid"])
    return None

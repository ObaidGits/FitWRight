"""Anonymous Public Profile endpoints (P7).

These are the **only** unauthenticated profile routes: they serve the safe,
visibility-gated public projection of a shared profile by slug. There is no user
scoping here by design — the resource is public — so the projection layer
(``app/profile/public.py``) is responsible for never emitting private fields
(salary, visa, phone unless the user exposed it), and the service gates on
``visibility`` (``private`` → 404, indistinguishable from an unknown slug to
prevent enumeration disclosure).

Gated by the ``PROFILE_ENABLED`` flag (off → 404). Rate-limited via the shared
public limiter to deter slug scraping.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request, Response

from app.auth.ratelimit import RateLimitRule, get_rate_limiter
from app.config import settings
from app.profile.schemas import PublicProfilePageResponse
from app.profile.service import profile_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/public/profiles", tags=["Public Profile"])

# Per-IP limit to deter slug scraping/enumeration of the anonymous surface.
_PUBLIC_RULE = RateLimitRule(limit=60, window_seconds=60)


def _require_enabled() -> None:
    if not settings.profile_enabled:
        raise HTTPException(status_code=404, detail="profile_disabled")


def _client_ip(request: Request) -> str:
    """Best-effort client IP (proxy-aware) for rate-limit bucketing."""
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def _enforce_rate_limit(request: Request) -> None:
    """Fail-open per-IP fixed-window limit on the public read surface."""
    result = await get_rate_limiter().check(
        "public_profile", _client_ip(request), _PUBLIC_RULE, fail_closed=False
    )
    if not result.allowed:
        raise HTTPException(status_code=429, detail="Too many requests. Please slow down.")


async def _record_view(slug: str, event_type: str) -> None:
    """Emit a view event attributed to the profile owner (best-effort analytics)."""
    try:
        from app.database import db
        from app.events import emit

        row = await db.get_profile_by_slug(slug)
        if row and row.get("user_id"):
            await emit(event_type, {"slug": slug}, user_id=row["user_id"])
    except Exception:  # pragma: no cover - analytics must never break a public view
        logger.debug("public view event failed for %s", slug, exc_info=True)


@router.get("/{slug}", response_model=PublicProfilePageResponse)
async def public_profile(slug: str, request: Request) -> PublicProfilePageResponse:
    """Return the public projection for ``slug`` (404 if private/unknown)."""
    _require_enabled()
    await _enforce_rate_limit(request)
    result = await profile_service.get_public_by_slug(slug)
    if result is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    await _record_view(slug, "public.viewed")
    return PublicProfilePageResponse(**result)


@router.get("/{slug}/portfolio")
async def public_portfolio(slug: str, request: Request) -> dict:
    """Return the portfolio projection for ``slug`` (404 if private/unknown)."""
    _require_enabled()
    await _enforce_rate_limit(request)
    result = await profile_service.get_public_portfolio_by_slug(slug)
    if result is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    await _record_view(slug, "portfolio.viewed")
    return result


@router.get("/{slug}/vcard")
async def public_profile_vcard(slug: str, request: Request) -> Response:
    """Return an RFC-6350 vCard for ``slug`` (404 if private/unknown)."""
    _require_enabled()
    await _enforce_rate_limit(request)
    vcard = await profile_service.get_public_vcard_by_slug(slug)
    if vcard is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    return Response(
        content=vcard,
        media_type="text/vcard; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{slug}.vcf"'},
    )

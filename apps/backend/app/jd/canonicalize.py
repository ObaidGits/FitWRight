"""URL canonicalization for JD extraction (§7 of enhancement plan).

Produces a deterministic, idempotent canonical URL used as the sole cache key,
SingleFlight dedup key, and metrics dimension. Strips tracking params, normalizes
case/path/port, and preserves ATS-meaningful query parameters.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse, urlunparse

__all__ = ["canonicalize_url", "redact_url"]

# Tracking params ALWAYS stripped (never semantically meaningful for job content)
_ALWAYS_STRIP = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "fbclid", "gclid", "gclsrc", "mc_cid", "mc_eid",
    "_ga", "_gl", "_hsenc", "_hsmi", "msclkid", "dclid", "twclid",
    "igshid", "s", "si",  # social tracking
})

# Params stripped ONLY on non-ATS domains (meaningful on ATS as referral codes)
_STRIP_ON_NON_ATS = frozenset({"ref", "source"})

# Known ATS domains where ref/source are meaningful
_ATS_DOMAINS = frozenset({
    "boards.greenhouse.io", "jobs.lever.co", "jobs.ashbyhq.com",
    "jobs.smartrecruiters.com", "apply.workable.com",
})

# Params that are NEVER stripped (semantically identify the job)
_PRESERVE_ALWAYS = frozenset({
    "id", "jobid", "postingid", "gh_jid", "lever_origin", "job_id",
    "requisitionid", "positionid",
})

# Sensitive tokens (§27 Privacy): stripped from the canonical form so they never
# land in cache keys, logs, or metrics. The ORIGINAL url (with token) is still
# used for the actual fetch — canonicalization only affects the cache/log key,
# which means two users pasting the same job with different signed tokens share
# one cache entry (privacy + efficiency).
_PRIVACY_STRIP = frozenset({
    "token", "key", "signature", "auth", "authorization", "apikey", "api_key",
    "access_token", "id_token", "sig", "sso", "session", "sessionid",
    "password", "pwd", "secret",
})


def _is_privacy_token(key_lower: str) -> bool:
    """Return True if a query param name looks like a credential/token."""
    if key_lower in _PRIVACY_STRIP:
        return True
    # AWS pre-signed URL params (X-Amz-Signature, X-Amz-Credential, ...)
    if key_lower.startswith("x-amz-") or key_lower.startswith("x_amz_"):
        return True
    return False


def _is_ats_domain(host: str) -> bool:
    """Check if host is a known ATS domain."""
    return any(host == d or host.endswith("." + d) for d in _ATS_DOMAINS)


def _normalize_path(path: str) -> str:
    """Collapse //, resolve /./, remove trailing slash (except root)."""
    # Decode then re-encode for consistent percent-encoding
    path = unquote(path)
    # Collapse multiple slashes
    path = re.sub(r"/+", "/", path)
    # Resolve /./
    path = re.sub(r"/\./", "/", path)
    # Remove trailing slash (keep root /)
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    # Re-encode (RFC 3986 safe chars)
    path = quote(path, safe="/:@!$&'()*+,;=-._~")
    return path or "/"


def canonicalize_url(url: str) -> str:
    """Canonicalize a URL for caching, dedup, and metrics.

    Idempotent: canonicalize(canonicalize(url)) == canonicalize(url).
    Deterministic: same logical URL always produces the same canonical form.
    """
    url = url.strip()
    parsed = urlparse(url)

    # 1-2. Lowercase scheme + host
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower()

    # 3. Remove default port
    port = parsed.port
    if (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
        port = None
    netloc = host if not port else f"{host}:{port}"

    # 4-5. Normalize path
    path = _normalize_path(parsed.path)

    # 6. Strip fragment
    # (already handled by not including parsed.fragment)

    # 7. Strip tracking params
    is_ats = _is_ats_domain(host)
    query_params = parse_qs(parsed.query, keep_blank_values=False)
    filtered_params: dict[str, list[str]] = {}
    for key, values in query_params.items():
        key_lower = key.lower()
        if key_lower in _ALWAYS_STRIP:
            continue
        # Sensitive tokens never enter the cache key / logs (§27).
        if _is_privacy_token(key_lower):
            continue
        if key_lower in _STRIP_ON_NON_ATS and not is_ats:
            continue
        # Preserve known-meaningful params regardless
        filtered_params[key] = values

    # 8. Sort remaining query params by key
    sorted_query = urlencode(
        [(k, v) for k in sorted(filtered_params.keys()) for v in filtered_params[k]],
        quote_via=quote,
    )

    # 9. Reconstruct (no fragment)
    return urlunparse((scheme, netloc, path, "", sorted_query, ""))


def redact_url(url: str) -> str:
    """Return a log-safe representation of ``url`` (§27 Privacy).

    Logs must never contain full URLs (they can carry tokens). We emit the
    scheme+host+path plus a short hash of the canonical form. Query strings are
    dropped entirely — even non-sensitive params — to guarantee no token leak.
    """
    import hashlib

    try:
        parsed = urlparse(url.strip())
        host = (parsed.hostname or "").lower()
        path = parsed.path or "/"
        digest = hashlib.sha256(canonicalize_url(url).encode()).hexdigest()[:12]
        return f"{parsed.scheme.lower()}://{host}{path}#{digest}"
    except Exception:
        # Never let logging redaction raise.
        return "<unparseable-url>"

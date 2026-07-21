"""robots.txt policy checker for JD extraction (§26 of enhancement plan).

This service is NOT a crawler - it performs user-initiated single-page fetches -
but we still respect robots.txt as a good citizen.

Policy (ADR-13, specificity rule):
- If robots.txt has a group for our named UA (``FitWrightBot``) -> apply it
  (longest-match Allow/Disallow wins).
- If there is NO group for our named UA, we PROCEED even if ``*`` is Disallow'd.
  Rationale: a blanket ``*`` block targets anonymous crawlers, not a named bot
  with a declared purpose and contact URL. A site that wants to block us can add
  a ``FitWrightBot`` group.

robots.txt is fetched through the SSRF-safe fetcher and cached per-domain for
24h. Failures to fetch robots.txt are fail-OPEN (allow) - robots.txt is
advisory, and a fetch error must never block a legitimate user request.
"""

from __future__ import annotations

import logging
import time
from urllib.parse import urlparse

from app.jd.ssrf import SsrfError, fetch_url_safely

logger = logging.getLogger(__name__)

__all__ = ["RobotsChecker", "RobotsDecision"]

OUR_UA_TOKEN = "fitwrightbot"
_CACHE_TTL = 86400  # 24h
_MAX_ROBOTS_BYTES = 512 * 1024


class RobotsDecision:
    """Result of a robots.txt check."""

    __slots__ = ("allowed", "crawl_delay", "reason")

    def __init__(self, allowed: bool, crawl_delay: float = 0.0, reason: str = ""):
        self.allowed = allowed
        self.crawl_delay = crawl_delay
        self.reason = reason


def _parse_robots(text: str) -> dict:
    """Parse robots.txt into {ua_token: {"allow": [...], "disallow": [...], "delay": float}}.

    Grouping follows the standard: consecutive User-agent lines share the rules
    that follow until the next User-agent line.
    """
    groups: dict[str, dict] = {}
    current_uas: list[str] = []
    expecting_rules = False

    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        field, _, value = line.partition(":")
        field = field.strip().lower()
        value = value.strip()

        if field == "user-agent":
            ua = value.lower()
            if expecting_rules:
                # New group starts after we've seen rules.
                current_uas = [ua]
                expecting_rules = False
            else:
                current_uas.append(ua)
            groups.setdefault(ua, {"allow": [], "disallow": [], "delay": 0.0})
        elif field in ("allow", "disallow") and current_uas:
            expecting_rules = True
            for ua in current_uas:
                g = groups.setdefault(ua, {"allow": [], "disallow": [], "delay": 0.0})
                g[field].append(value)
        elif field == "crawl-delay" and current_uas:
            expecting_rules = True
            try:
                delay = float(value)
            except ValueError:
                delay = 0.0
            for ua in current_uas:
                g = groups.setdefault(ua, {"allow": [], "disallow": [], "delay": 0.0})
                g["delay"] = delay
    return groups


def _path_matches(rule: str, path: str) -> bool:
    """Match a robots path rule against a URL path (supports * and $)."""
    if rule == "":
        return False  # empty Disallow means "allow all"
    # Translate the robots pattern into a simple prefix/wildcard match.
    if "*" not in rule and "$" not in rule:
        return path.startswith(rule)
    # Wildcard matching.
    import re

    pattern = re.escape(rule).replace(r"\*", ".*")
    if pattern.endswith(r"\$"):
        pattern = pattern[:-2] + "$"
    return re.match(pattern, path) is not None


def _decide(groups: dict, path: str) -> RobotsDecision:
    """Apply ADR-13 specificity rule to reach a decision for our UA."""
    group = groups.get(OUR_UA_TOKEN)
    if group is None:
        # No named group for us -> proceed (ADR-13), inherit crawl-delay from * if any.
        star = groups.get("*", {})
        return RobotsDecision(allowed=True, crawl_delay=star.get("delay", 0.0), reason="no_named_rule")

    # Longest-match wins between Allow and Disallow (standard precedence).
    best_allow = max((len(r) for r in group["allow"] if _path_matches(r, path)), default=-1)
    best_disallow = max((len(r) for r in group["disallow"] if _path_matches(r, path)), default=-1)

    if best_disallow > best_allow:
        return RobotsDecision(allowed=False, crawl_delay=group.get("delay", 0.0), reason="named_disallow")
    return RobotsDecision(allowed=True, crawl_delay=group.get("delay", 0.0), reason="named_allow")


class RobotsChecker:
    """Fetches, caches, and evaluates robots.txt per domain."""

    def __init__(self, kv):
        self._kv = kv

    def _cache_key(self, host: str) -> str:
        return f"jd:robots:{host.lower()}"

    async def _load_groups(self, scheme: str, host: str) -> dict | None:
        """Return parsed groups for a host, using the 24h cache. None on fetch error."""
        import json

        key = self._cache_key(host)
        try:
            cached = await self._kv.get(key)
        except Exception:
            cached = None
        if cached is not None:
            try:
                payload = json.loads(cached)
                return payload.get("groups", {})
            except (json.JSONDecodeError, TypeError):
                pass

        robots_url = f"{scheme}://{host}/robots.txt"
        try:
            text = await fetch_url_safely(robots_url)
        except SsrfError as exc:
            logger.debug("robots.txt fetch failed for %s: %s", host, exc.reason)
            # Fail-open: cache an empty allow-all so we don't refetch every request.
            groups: dict = {}
            try:
                await self._kv.set(key, json.dumps({"groups": groups, "at": time.time()}), ttl_seconds=_CACHE_TTL)
            except Exception:
                pass
            return None

        if len(text) > _MAX_ROBOTS_BYTES:
            text = text[:_MAX_ROBOTS_BYTES]
        groups = _parse_robots(text)
        try:
            await self._kv.set(key, json.dumps({"groups": groups, "at": time.time()}), ttl_seconds=_CACHE_TTL)
        except Exception:
            pass
        return groups

    async def check(self, url: str) -> RobotsDecision:
        """Check whether ``url`` may be fetched per robots.txt (fail-open)."""
        parsed = urlparse(url)
        scheme = (parsed.scheme or "https").lower()
        host = (parsed.hostname or "").lower()
        if not host:
            return RobotsDecision(allowed=True, reason="no_host")
        path = parsed.path or "/"

        groups = await self._load_groups(scheme, host)
        if groups is None:
            # Fetch error -> advisory file unavailable -> allow.
            return RobotsDecision(allowed=True, reason="robots_unavailable")
        if not groups:
            return RobotsDecision(allowed=True, reason="empty_robots")
        return _decide(groups, path)

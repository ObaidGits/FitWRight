/**
 * Route-guard middleware (Task 8.2).
 *
 * PRESENCE-ONLY fast path — this is UX, NOT the security boundary. It checks
 * only whether a session cookie is *present* (it can never read the httpOnly
 * value) and, if absent on a protected route, redirects to `/login?next=<path>`
 * before render so the user isn't briefly shown a protected shell. The
 * authoritative checks are the SSR layout session lookup and, above all, the
 * backend enforcing `user_id`/admin capability on every request (R11.1/11.5).
 *
 * In `SINGLE_USER_MODE` there is no login wall, so the guard is a no-op and
 * local zero-config boot is unchanged.
 *
 * It also forwards the resolved pathname as `x-pathname` so server layouts can
 * build an accurate `next` when they perform the authoritative redirect.
 */
import { NextResponse, type NextRequest } from 'next/server';
import { SINGLE_USER_MODE, SESSION_COOKIE_NAMES } from '@/lib/config/auth';

/**
 * Canonical host (e.g. `www.fitwright.tech`). Auth cookies are host-only:
 * the session cookie uses the `__Host-` prefix (no Domain allowed) and the
 * transient `oauth_txn` cookie is likewise host-scoped, while the backend's
 * `OAUTH_REDIRECT_URI`/`FRONTEND_BASE_URL` are pinned to a single host. If a
 * user lands on a different host of the same site (e.g. the apex
 * `fitwright.tech` instead of `www.`), the OAuth transient cookie set on start
 * is NOT sent to the callback host → `state_mismatch` → intermittent
 * "oauth_failed" that "works on retry" (the failure redirect lands them on the
 * canonical host). Redirecting to one canonical host up front removes that
 * whole class of cookie-fragmentation bugs.
 *
 * Runtime-configurable via `CANONICAL_HOST`; otherwise derived from the baked
 * `NEXT_PUBLIC_SITE_URL`. Empty → feature off (local/dev, single-origin).
 */
const CANONICAL_HOST: string = (() => {
  const explicit = process.env.CANONICAL_HOST?.trim();
  if (explicit) return explicit.toLowerCase();
  const site = process.env.NEXT_PUBLIC_SITE_URL;
  if (site) {
    try {
      return new URL(site).host.toLowerCase();
    } catch {
      /* ignore malformed site url */
    }
  }
  return '';
})();

/** Registrable-domain match: only canonicalize hosts of the SAME site (so the
 * apex + any subdomain fold onto the canonical host, while unrelated hosts —
 * `localhost`, `*.herokuapp.com`, preview domains — are left untouched). */
function isSameSiteHost(host: string, canonical: string): boolean {
  if (!host || !canonical) return false;
  const base = canonical.split('.').slice(-2).join('.');
  return host === base || host.endsWith(`.${base}`);
}

/** URL path prefixes that require a session (the `(app)` + `admin` groups). */
const PROTECTED_PREFIXES = [
  '/home',
  '/resumes',
  '/import',
  '/tailor',
  '/applications',
  '/wizard',
  '/settings',
  '/admin',
  '/builder',
];

function isProtected(pathname: string): boolean {
  return PROTECTED_PREFIXES.some(
    (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`)
  );
}

function hasSessionCookie(req: NextRequest): boolean {
  return SESSION_COOKIE_NAMES.some((name) => Boolean(req.cookies.get(name)?.value));
}

export function middleware(req: NextRequest): NextResponse {
  const { pathname, search } = req.nextUrl;

  // Canonical-host redirect FIRST — before any auth logic — so every browser
  // navigation (marketing, /login, app routes) settles on the single host that
  // owns the auth cookies. Skipped in single-user/local mode and when no
  // canonical host is configured. `x-forwarded-host` (set by Heroku's router)
  // is preferred so we compare against the public host, not an internal one.
  if (!SINGLE_USER_MODE && CANONICAL_HOST) {
    const host = (req.headers.get('x-forwarded-host') || req.headers.get('host') || '')
      .split(',')[0]
      .trim()
      .toLowerCase();
    if (host && host !== CANONICAL_HOST && isSameSiteHost(host, CANONICAL_HOST)) {
      const url = req.nextUrl.clone();
      url.host = CANONICAL_HOST;
      url.protocol = 'https:';
      url.port = '';
      // 308 preserves method for the rare non-GET, though these are navigations.
      return NextResponse.redirect(url, 308);
    }
  }

  // Forward the pathname so SSR layouts can build an accurate `next`.
  const requestHeaders = new Headers(req.headers);
  requestHeaders.set('x-pathname', pathname);
  const pass = NextResponse.next({ request: { headers: requestHeaders } });

  if (SINGLE_USER_MODE || !isProtected(pathname)) {
    return pass;
  }

  if (!hasSessionCookie(req)) {
    const url = req.nextUrl.clone();
    url.pathname = '/login';
    url.search = '';
    url.searchParams.set('next', `${pathname}${search}`);
    return NextResponse.redirect(url);
  }

  return pass;
}

export const config = {
  // Run on all navigable routes so the canonical-host redirect can catch
  // marketing + `/login` (where the OAuth flow is initiated), while the
  // session guard still only acts on PROTECTED_PREFIXES. Excludes API proxy
  // routes (`/api`, `/docs`, `/openapi.json`), Next internals, the service
  // worker, and static assets (anything with a file extension).
  matcher: [
    '/((?!api|docs|redoc|openapi.json|_next/static|_next/image|favicon.ico|sw.js|robots.txt|sitemap.xml|.*\\.[\\w]+$).*)',
  ],
};

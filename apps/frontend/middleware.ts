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
  matcher: [
    '/home/:path*',
    '/resumes/:path*',
    '/import/:path*',
    '/tailor/:path*',
    '/applications/:path*',
    '/wizard/:path*',
    '/settings/:path*',
    '/admin/:path*',
    '/builder/:path*',
  ],
};

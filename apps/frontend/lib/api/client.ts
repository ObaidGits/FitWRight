/**
 * Centralized API Client
 *
 * Single source of truth for API configuration and base fetch utilities.
 *
 * Auth plumbing (Task 8.2):
 * - Every request is sent with `credentials: 'include'` so the httpOnly
 *   `__Host-session` cookie rides along. Tokens are NEVER read from JS or stored
 *   in localStorage — the session lives only in the httpOnly cookie.
 * - Mutating requests (POST/PUT/PATCH/DELETE) get the double-submit CSRF token
 *   injected into `X-CSRF-Token`, read from the JS-readable `csrf` cookie.
 * - A single 401 interceptor invokes a registered handler (the SessionProvider
 *   clears its session query, broadcasts a multi-tab logout, and routes to
 *   `/login?next=…`). Auth-flow calls opt out via `skipAuthHandling` so an
 *   expected 401 (guest session probe, wrong password, step-up required) is
 *   handled inline instead of bouncing the user to the login page.
 */

import { CSRF_COOKIE_NAME } from '@/lib/config/auth';

const DEFAULT_PUBLIC_API_URL = '/';
const INTERNAL_API_ORIGIN = 'http://127.0.0.1:8000';

/** Extra options understood by {@link apiFetch} on top of the standard `RequestInit`. */
export interface ApiFetchInit extends RequestInit {
  /**
   * Skip the global 401 interceptor (the "session expired" redirect). Used by
   * the auth-flow calls whose 401s are expected and handled inline (login,
   * session probe, step-up, change-password), so they never bounce the user to
   * the login page. CSRF injection still happens for mutating requests.
   */
  skipAuthHandling?: boolean;
}

function normalizeApiUrl(value: string): string {
  const trimmed = value.trim();
  if (!trimmed || trimmed === '/') {
    return '/';
  }
  return trimmed.replace(/\/+$/, '');
}

function toApiBase(apiUrl: string): string {
  if (apiUrl === '/') {
    return '/api/v1';
  }
  return `${apiUrl}/api/v1`;
}

function resolveRuntimeApiBase(apiBase: string): string {
  if (typeof window !== 'undefined' || !apiBase.startsWith('/')) {
    return apiBase;
  }
  return `${INTERNAL_API_ORIGIN}${apiBase}`;
}

export const API_URL = normalizeApiUrl(process.env.NEXT_PUBLIC_API_URL ?? DEFAULT_PUBLIC_API_URL);
export const API_BASE = resolveRuntimeApiBase(toApiBase(API_URL));

// Default request timeout (ms). MUST match the backend's REQUEST_TIMEOUT_SECONDS
// and the Next.js proxyTimeout (next.config.ts) — the shortest layer aborts
// first, so all three are driven by the same NEXT_PUBLIC_REQUEST_TIMEOUT_MS env
// var. Bounded to [30s, 30min]. Local LLMs often need more than the 240s default.
const rawTimeoutMs = process.env.NEXT_PUBLIC_REQUEST_TIMEOUT_MS;
const parsedTimeoutMs = rawTimeoutMs ? Number(rawTimeoutMs) : NaN;
export const DEFAULT_TIMEOUT_MS = Number.isFinite(parsedTimeoutMs)
  ? Math.min(1_800_000, Math.max(30_000, parsedTimeoutMs))
  : 240_000;

const MUTATING_METHODS = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);

// ---------------------------------------------------------------------------
// Cookie + CSRF helpers
// ---------------------------------------------------------------------------

/** Read a cookie by name in the browser (returns null on the server or if absent). */
export function readCookie(name: string): string | null {
  if (typeof document === 'undefined') return null;
  const match = document.cookie.match(
    new RegExp('(?:^|; )' + name.replace(/([.$?*|{}()[\]\\/+^])/g, '\\$1') + '=([^;]*)')
  );
  return match ? decodeURIComponent(match[1]) : null;
}

/** Read the JS-readable CSRF token the backend sets on the `csrf` cookie. */
export function readCsrfToken(): string | null {
  return readCookie(CSRF_COOKIE_NAME);
}

// ---------------------------------------------------------------------------
// 401 interceptor registration (wired by the SessionProvider)
// ---------------------------------------------------------------------------

type UnauthorizedHandler = () => void;
let unauthorizedHandler: UnauthorizedHandler | null = null;

/**
 * Register the handler invoked when an authenticated app request returns 401.
 * The SessionProvider registers a handler that clears the cached session,
 * broadcasts a multi-tab logout, and routes to `/login?next=…`.
 */
export function setUnauthorizedHandler(handler: UnauthorizedHandler | null): void {
  unauthorizedHandler = handler;
}

/**
 * Standard fetch wrapper with common error handling.
 * Returns the Response object for flexibility.
 *
 * @param endpoint - API endpoint path or absolute URL
 * @param options - Standard RequestInit options (+ optional `skipAuthHandling`)
 * @param timeoutMs - Optional request timeout in milliseconds (default: DEFAULT_TIMEOUT_MS, from NEXT_PUBLIC_REQUEST_TIMEOUT_MS, 240_000 if unset)
 */
export async function apiFetch(
  endpoint: string,
  options?: ApiFetchInit,
  timeoutMs?: number
): Promise<Response> {
  const normalizedEndpoint = endpoint.startsWith('/') ? endpoint : `/${endpoint}`;
  const isAbsoluteUrl = endpoint.startsWith('http://') || endpoint.startsWith('https://');
  const isApiPath = normalizedEndpoint.startsWith('/api/');
  let url = `${API_BASE}${normalizedEndpoint}`;

  if (isAbsoluteUrl) {
    url = endpoint;
  } else if (isApiPath) {
    url = resolveRuntimeApiBase(normalizedEndpoint);
  }

  const { skipAuthHandling = false, ...init } = options ?? {};

  // Send cookies (httpOnly session + JS-readable csrf) with every request.
  init.credentials = init.credentials ?? 'include';

  // Inject the double-submit CSRF token on every mutating request (browser
  // only). This covers both the per-session token (after login) and the
  // pre-session token (after GET /auth/csrf) — both live on the `csrf` cookie.
  const method = (init.method ?? 'GET').toUpperCase();
  if (MUTATING_METHODS.has(method)) {
    const csrf = readCsrfToken();
    if (csrf) {
      const headers: Record<string, string> = { ...(init.headers as Record<string, string>) };
      if (!('X-CSRF-Token' in headers)) headers['X-CSRF-Token'] = csrf;
      init.headers = headers;
    }
  }

  // Defaults to DEFAULT_TIMEOUT_MS, which tracks the backend's
  // REQUEST_TIMEOUT_SECONDS (see next.config.ts proxyTimeout — all three layers
  // must agree or the shortest aborts first).
  const timeout = timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);

  try {
    const response = await fetch(url, { ...init, signal: controller.signal });
    // Single 401 interceptor: an authenticated app call whose session has
    // expired routes the user to /login (R11.3). Auth-flow calls opt out.
    if (
      response.status === 401 &&
      !skipAuthHandling &&
      typeof window !== 'undefined' &&
      unauthorizedHandler
    ) {
      unauthorizedHandler();
    }
    return response;
  } catch (error) {
    if (error instanceof Error && error.name === 'AbortError') {
      throw new Error(
        'Request timed out. If you are running a local LLM, increase NEXT_PUBLIC_REQUEST_TIMEOUT_MS (and the backend REQUEST_TIMEOUT_SECONDS to match); otherwise try a shorter job description or check your connection.'
      );
    }
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

/**
 * POST request with JSON body.
 */
export async function apiPost<T>(
  endpoint: string,
  body: T,
  timeoutMs?: number,
  init?: ApiFetchInit
): Promise<Response> {
  return apiFetch(
    endpoint,
    {
      ...init,
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...(init?.headers as Record<string, string>) },
      body: JSON.stringify(body),
    },
    timeoutMs
  );
}

/**
 * PATCH request with JSON body.
 */
export async function apiPatch<T>(
  endpoint: string,
  body: T,
  init?: ApiFetchInit
): Promise<Response> {
  return apiFetch(endpoint, {
    ...init,
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', ...(init?.headers as Record<string, string>) },
    body: JSON.stringify(body),
  });
}

/**
 * PUT request with JSON body.
 */
export async function apiPut<T>(endpoint: string, body: T): Promise<Response> {
  return apiFetch(endpoint, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

/**
 * DELETE request.
 */
export async function apiDelete(endpoint: string, init?: ApiFetchInit): Promise<Response> {
  return apiFetch(endpoint, { ...init, method: 'DELETE' });
}

/**
 * Builds the full upload URL for file uploads.
 */
export function getUploadUrl(): string {
  return `${API_BASE}/resumes/upload`;
}

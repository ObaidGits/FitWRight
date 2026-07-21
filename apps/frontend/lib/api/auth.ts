/**
 * Auth API client (Task 8.3) - real wiring to the backend `/api/v1/auth` +
 * `/api/v1/users/me` surface.
 *
 * SECURITY MODEL:
 * - Sessions live in the httpOnly `__Host-session` cookie; JS never reads or
 *   stores a token. Requests go through {@link apiFetch} with
 *   `credentials: 'include'`.
 * - Mutations carry the double-submit CSRF token (from the JS-readable `csrf`
 *   cookie) injected by {@link apiFetch}. Login/signup are pre-session and so
 *   first fetch a pre-session token via `GET /auth/csrf`.
 * - The backend error envelope is `{ error: { code, message, details? } }`;
 *   failures throw {@link AuthApiError} carrying the machine `code` so the UI
 *   can render a uniform, non-leaky banner and branch on specific outcomes.
 */
import { apiFetch, apiPost, apiPatch, apiDelete, API_BASE, type ApiFetchInit } from './client';

export type UserRole = 'user' | 'admin';
export type AccountStatus = 'active' | 'disabled' | 'pending_verification';

/** The only user shape the backend ever returns (mirrors backend `SafeUser`). */
export interface SafeUser {
  id: string;
  name: string;
  email: string;
  role: UserRole;
  status: AccountStatus;
  emailVerified: boolean;
  aal: string;
  avatarUrl?: string | null;
}

/** One active session in the device-management list (never the raw token). */
export interface DeviceSession {
  id: string;
  deviceLabel?: string | null;
  ipHash?: string | null;
  createdAt: string;
  lastSeenAt: string;
  current: boolean;
}

/** Thrown on any non-2xx auth response, carrying the backend `code`. */
export class AuthApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly details?: unknown;
  readonly retryAfter?: number;

  constructor(
    code: string,
    message: string,
    status: number,
    details?: unknown,
    retryAfter?: number
  ) {
    super(message);
    this.name = 'AuthApiError';
    this.code = code;
    this.status = status;
    this.details = details;
    this.retryAfter = retryAfter;
  }
}

/** Parse a failed response's ADR-7 envelope into an {@link AuthApiError}. */
async function toError(response: Response): Promise<AuthApiError> {
  let code = 'error';
  let message = 'Something went wrong. Please try again.';
  let details: unknown;
  try {
    const body = (await response.json()) as {
      error?: { code?: string; message?: string; details?: unknown };
      detail?: string;
    };
    if (body?.error) {
      code = body.error.code ?? code;
      message = body.error.message ?? message;
      details = body.error.details;
    } else if (typeof body?.detail === 'string') {
      code = body.detail;
    }
  } catch {
    /* non-JSON body - keep the generic message */
  }
  const retryHeader = response.headers.get('Retry-After');
  const retryAfter = retryHeader ? Number(retryHeader) : undefined;
  return new AuthApiError(
    code,
    message,
    response.status,
    details,
    Number.isFinite(retryAfter) ? retryAfter : undefined
  );
}

/** Parse a successful JSON body, or throw an {@link AuthApiError} on failure. */
async function json<T>(response: Response): Promise<T> {
  if (!response.ok) throw await toError(response);
  return (await response.json()) as T;
}

/** Ensure a response is 2xx (for endpoints with no meaningful body). */
async function ok(response: Response): Promise<void> {
  if (!response.ok) throw await toError(response);
}

const INLINE: ApiFetchInit = { skipAuthHandling: true };

// ---------------------------------------------------------------------------
// Session + credentials
// ---------------------------------------------------------------------------

/**
 * Fetch a pre-session CSRF token, which the backend sets on the `csrf` cookie.
 * Login/signup call this first so {@link apiFetch} can echo it in `X-CSRF-Token`
 * (login-CSRF defense, R2.5/12.2).
 */
export async function fetchCsrf(): Promise<void> {
  await ok(await apiFetch('/auth/csrf', INLINE));
}

/** Resolve the current session, or `null` for a guest (401 is expected). */
export async function fetchSession(): Promise<SafeUser | null> {
  const res = await apiFetch('/auth/session', INLINE);
  if (res.status === 401) return null;
  return json<SafeUser>(res);
}

/** Email/password login. Establishes a pre-session CSRF token first. */
export async function login(input: {
  email: string;
  password: string;
  rememberMe?: boolean;
}): Promise<SafeUser> {
  await fetchCsrf();
  const res = await apiPost(
    '/auth/login',
    { email: input.email, password: input.password, remember_me: input.rememberMe ?? false },
    undefined,
    INLINE
  );
  return json<SafeUser>(res);
}

export interface SignupResult {
  /** Set when verification is off (local): the user is signed in immediately. */
  user: SafeUser | null;
  /** Set when verification is on (hosted): a confirmation email was sent. */
  pendingVerification: boolean;
}

/** Create an account. Handles both the immediate-session and pending paths. */
export async function signup(input: {
  email: string;
  password: string;
  name: string;
}): Promise<SignupResult> {
  await fetchCsrf();
  const res = await apiPost(
    '/auth/signup',
    { email: input.email, password: input.password, name: input.name },
    undefined,
    INLINE
  );
  const body = await json<SafeUser | { status: string }>(res);
  if ('status' in body && body.status === 'pending_verification') {
    return { user: null, pendingVerification: true };
  }
  return { user: body as SafeUser, pendingVerification: false };
}

/** Revoke the current session and clear cookies. */
export async function logout(): Promise<void> {
  await ok(await apiPost('/auth/logout', {}, undefined, INLINE));
}

/** Revoke every session for the user (requires a recent step-up). */
export async function logoutAll(): Promise<void> {
  await ok(await apiPost('/auth/logout-all', {}, undefined, INLINE));
}

// ---------------------------------------------------------------------------
// Email verification
// ---------------------------------------------------------------------------

/** (Re)send an email-verification link (uniform, enumeration-safe). */
export async function requestVerification(email?: string): Promise<void> {
  await ok(await apiPost('/auth/verify/request', email ? { email } : {}, undefined, INLINE));
}

/** Redeem a verification token from the emailed link. */
export async function confirmVerification(token: string): Promise<void> {
  await ok(await apiPost('/auth/verify/confirm', { token }, undefined, INLINE));
}

// ---------------------------------------------------------------------------
// Password reset
// ---------------------------------------------------------------------------

/** Request a password-reset link (uniform response - no enumeration). */
export async function forgotPassword(email: string): Promise<void> {
  await fetchCsrf();
  await ok(await apiPost('/auth/password/forgot', { email }, undefined, INLINE));
}

/** Set a new password with a reset token; returns the freshly-signed-in user. */
export async function resetPassword(input: { token: string; password: string }): Promise<SafeUser> {
  await fetchCsrf();
  return json<SafeUser>(
    await apiPost(
      '/auth/password/reset',
      { token: input.token, password: input.password },
      undefined,
      INLINE
    )
  );
}

// ---------------------------------------------------------------------------
// Step-up + password/email change
// ---------------------------------------------------------------------------

/** Re-authenticate to open a step-up (sudo) window for sensitive actions. */
export async function stepUp(password: string): Promise<SafeUser> {
  return json<SafeUser>(await apiPost('/auth/step-up', { password }, undefined, INLINE));
}

/** Change the password from within a stepped-up session. */
export async function changePassword(input: {
  currentPassword: string;
  newPassword: string;
}): Promise<SafeUser> {
  return json<SafeUser>(
    await apiPost(
      '/auth/password/change',
      { current_password: input.currentPassword, new_password: input.newPassword },
      undefined,
      INLINE
    )
  );
}

// ---------------------------------------------------------------------------
// Profile + device management
// ---------------------------------------------------------------------------

/** Fetch the authenticated user's profile. */
export async function fetchMe(): Promise<SafeUser> {
  return json<SafeUser>(await apiFetch('/users/me', INLINE));
}

/** Update the display name (role/status are ignored server-side). */
export async function updateProfile(input: {
  name: string;
  updatedAt?: string;
}): Promise<SafeUser> {
  return json<SafeUser>(
    await apiPatch('/users/me', { name: input.name, updated_at: input.updatedAt }, INLINE)
  );
}

/** List the caller's active sessions for device management. */
export async function listSessions(): Promise<DeviceSession[]> {
  const body = await json<{ sessions: DeviceSession[] }>(
    await apiFetch('/users/me/sessions', INLINE)
  );
  return body.sessions;
}

/** Revoke one of the caller's sessions by id. */
export async function revokeSession(id: string): Promise<void> {
  await ok(await apiDelete(`/users/me/sessions/${encodeURIComponent(id)}`, INLINE));
}

/** Begin a verify-before-switch email change (requires step-up). */
export async function beginEmailChange(email: string): Promise<void> {
  await ok(await apiPost('/users/me/email', { email }, undefined, INLINE));
}

/** Confirm a pending email change with the token from the new address. */
export async function confirmEmailChange(token: string): Promise<SafeUser> {
  return json<SafeUser>(await apiPost('/users/me/email/confirm', { token }, undefined, INLINE));
}

// ---------------------------------------------------------------------------
// OAuth
// ---------------------------------------------------------------------------

/**
 * Build the OAuth start URL for a full-page navigation (the backend handles the
 * IdP round-trip and issues the session cookie server-side). `next` is a
 * same-origin app path the backend validates before honoring.
 */
export function oauthStartUrl(provider: 'google', next?: string): string {
  const base = `${API_BASE}/auth/oauth/${provider}/start`;
  return next ? `${base}?next=${encodeURIComponent(next)}` : base;
}

export const authApi = {
  fetchCsrf,
  fetchSession,
  login,
  signup,
  logout,
  logoutAll,
  requestVerification,
  confirmVerification,
  forgotPassword,
  resetPassword,
  stepUp,
  changePassword,
  fetchMe,
  updateProfile,
  listSessions,
  revokeSession,
  beginEmailChange,
  confirmEmailChange,
  oauthStartUrl,
};

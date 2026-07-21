/**
 * Shared API error parsing (frontend-wide).
 *
 * Every backend error uses the ADR-7 envelope `{ error: { code, message,
 * details? } }`, though a few FastAPI paths still return `{ detail }`. This
 * helper normalizes both into an {@link ApiError} carrying the machine `code`,
 * HTTP `status`, and any `Retry-After` hint, so callers can:
 *   - render a uniform, non-leaky message (never a bare "status 500"),
 *   - branch on specific outcomes (e.g. `rate_limited`), and
 *   - surface a retry countdown for 429s.
 *
 * This complements `AuthApiError` in `auth.ts` (which predates this helper and
 * stays as-is for the auth surface). New non-auth clients should use `parseError`
 * / `readJson` here for consistent 429 + error-envelope handling.
 */

export class ApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly details?: unknown;
  /** Seconds to wait before retrying, parsed from the `Retry-After` header (429s). */
  readonly retryAfter?: number;

  constructor(
    code: string,
    message: string,
    status: number,
    details?: unknown,
    retryAfter?: number
  ) {
    super(message);
    this.name = 'ApiError';
    this.code = code;
    this.status = status;
    this.details = details;
    this.retryAfter = retryAfter;
  }

  /** True when the backend rejected the call for exceeding a rate limit. */
  get isRateLimited(): boolean {
    return this.status === 429 || this.code === 'rate_limited';
  }
}

/** True when a string is (or begins as) an HTML document. Infrastructure layers
 * (Heroku router, proxies, CDNs) return HTML "Application Error" pages on 5xx;
 * such text must NEVER be surfaced to the user as an error message. */
export function looksLikeHtml(text: string): boolean {
  return /^\s*(?:<!doctype html|<html|<!--|<head|<body|<\?xml)/i.test(text);
}

/** Generic, safe messages for well-known statuses, used when the body carries
 * no usable JSON message (e.g. an edge/proxy HTML error page). */
const STATUS_FALLBACKS: Record<number, string> = {
  500: 'Something went wrong on our end. Please try again in a moment.',
  502: 'The service is temporarily unavailable. Please try again in a moment.',
  503: 'This feature is temporarily unavailable. Please try again in a moment.',
  504: 'This took too long and timed out. Please try again.',
};

/** Friendly, human message for a rate-limit rejection, including a wait hint. */
export function rateLimitMessage(retryAfter?: number): string {
  if (retryAfter && Number.isFinite(retryAfter) && retryAfter > 0) {
    const secs = Math.ceil(retryAfter);
    const unit = secs === 1 ? 'second' : 'seconds';
    return `You're going a little fast. Try again in ${secs} ${unit}.`;
  }
  return "You're going a little fast. Please wait a moment and try again.";
}

/**
 * Parse a failed `Response` into an {@link ApiError}. Tolerates non-JSON and
 * malformed bodies. Maps 429 to a friendly rate-limit message when the backend
 * did not supply a specific one.
 */
export async function parseError(
  response: Response,
  fallbackMessage = 'Something went wrong. Please try again.'
): Promise<ApiError> {
  let code = 'error';
  // Prefer a status-specific safe message over the generic fallback when the
  // body has nothing usable (e.g. a Heroku HTML "Application Error" page).
  let message = STATUS_FALLBACKS[response.status] ?? fallbackMessage;
  let details: unknown;

  // Read as text first so an HTML error page can never be mis-handled, then try
  // JSON. This tolerates infrastructure 5xx pages that aren't valid JSON.
  const raw = await response.text().catch(() => '');
  if (raw.trim() && !looksLikeHtml(raw)) {
    try {
      const body = JSON.parse(raw) as {
        error?: { code?: string; message?: string; details?: unknown };
        detail?: string | { code?: string; message?: string };
      };
      if (body?.error) {
        code = body.error.code ?? code;
        if (typeof body.error.message === 'string') message = body.error.message;
        details = body.error.details;
      } else if (typeof body?.detail === 'string') {
        message = body.detail;
      } else if (body?.detail && typeof body.detail === 'object') {
        code = body.detail.code ?? code;
        if (typeof body.detail.message === 'string') message = body.detail.message;
      }
    } catch {
      /* non-JSON body - keep the status/fallback message */
    }
  }
  // Defense in depth: never let an HTML/looks-like-markup message escape.
  if (looksLikeHtml(message)) {
    message = STATUS_FALLBACKS[response.status] ?? fallbackMessage;
  }

  const retryHeader = response.headers.get('Retry-After');
  const parsedRetry = retryHeader ? Number(retryHeader) : NaN;
  const retryAfter = Number.isFinite(parsedRetry) ? parsedRetry : undefined;

  if (response.status === 429) {
    code = code === 'error' ? 'rate_limited' : code;
    // Prefer the backend message only if it's specific; otherwise use ours.
    if (message === fallbackMessage) message = rateLimitMessage(retryAfter);
  }

  return new ApiError(code, message, response.status, details, retryAfter);
}

/** Parse a successful JSON body, or throw a normalized {@link ApiError}. */
export async function readJson<T>(response: Response, fallbackMessage?: string): Promise<T> {
  if (!response.ok) throw await parseError(response, fallbackMessage);
  return (await response.json()) as T;
}

/** Extract a user-facing string from any thrown value (Error, ApiError, etc.). */
export function toMessage(err: unknown, fallback = 'Something went wrong.'): string {
  if (err instanceof ApiError) return looksLikeHtml(err.message) ? fallback : err.message;
  if (err instanceof Error)
    return err.message && !looksLikeHtml(err.message) ? err.message : fallback;
  if (typeof err === 'string') return err && !looksLikeHtml(err) ? err : fallback;
  return fallback;
}

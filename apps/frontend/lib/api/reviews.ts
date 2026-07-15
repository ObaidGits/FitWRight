/**
 * Public review-submission API client (Connect page). Mirrors the contact
 * client: `apiPost` attaches CSRF for signed-in visitors and none for guests
 * (both valid — the backend only enforces CSRF with a session), and we opt out
 * of the global 401 redirect.
 */
import { apiPost, DEFAULT_TIMEOUT_MS } from './client';

export interface ReviewPayload {
  rating: number;
  title: string;
  body: string;
  name?: string;
  email?: string;
  /** Honeypot — must stay empty. */
  company_website?: string;
  elapsed_ms?: number;
}

export interface ReviewResult {
  message: string;
  reference: string;
}

export class ReviewError extends Error {
  constructor(
    public readonly status: number,
    message: string,
    public readonly retryAfterMs?: number
  ) {
    super(message);
    this.name = 'ReviewError';
  }
}

function statusMessage(status: number, parsed: string | null): string {
  if (parsed) return parsed;
  if (status === 429)
    return 'Thanks for the enthusiasm! Please wait a moment before submitting again.';
  if (status === 422) return 'Please check the highlighted fields and try again.';
  if (status >= 500)
    return 'Something went wrong on our side — your review wasn’t saved. Please retry.';
  return 'Could not submit your review. Please try again.';
}

export async function submitReview(payload: ReviewPayload): Promise<ReviewResult> {
  let res: Response;
  try {
    res = await apiPost('/reviews/', payload, DEFAULT_TIMEOUT_MS, { skipAuthHandling: true });
  } catch {
    throw new ReviewError(0, 'Network error — please check your connection and try again.');
  }
  if (!res.ok) {
    let parsed: string | null = null;
    try {
      const body = (await res.json()) as { error?: { message?: string }; detail?: unknown };
      if (body?.error?.message) parsed = body.error.message;
      else if (typeof body?.detail === 'string') parsed = body.detail;
    } catch {
      /* non-JSON */
    }
    const retryHeader = res.headers.get('Retry-After');
    const retryAfterMs = retryHeader ? Number(retryHeader) * 1000 : undefined;
    throw new ReviewError(
      res.status,
      statusMessage(res.status, parsed),
      Number.isFinite(retryAfterMs) ? retryAfterMs : undefined
    );
  }
  return (await res.json()) as ReviewResult;
}

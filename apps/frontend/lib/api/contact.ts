/**
 * Public contact-form API client.
 *
 * Talks to `POST /contact` (unauthenticated). `apiPost` attaches the CSRF token
 * automatically for a signed-in visitor and none for a guest — both are valid,
 * since the backend only enforces CSRF when a session is present. We opt out of
 * the global 401 redirect so an expired session never bounces a guest mid-send.
 */
import { apiPost, DEFAULT_TIMEOUT_MS } from './client';

export interface ContactPayload {
  name: string;
  email: string;
  subject: string;
  message: string;
  company?: string;
  linkedin?: string;
  purpose: string;
  project_type?: string;
  budget?: string;
  /** Honeypot — must stay empty. */
  company_website?: string;
  /** Milliseconds spent on the form (bot-timing heuristic). */
  elapsed_ms?: number;
}

export interface ContactResult {
  message: string;
  reference: string;
  estimated_response: string;
}

/** A failed contact submission carrying the HTTP status for tailored UX. */
export class ContactError extends Error {
  constructor(
    public readonly status: number,
    message: string,
    public readonly retryAfterMs?: number
  ) {
    super(message);
    this.name = 'ContactError';
  }
}

function statusMessage(status: number, parsed: string | null): string {
  if (parsed) return parsed;
  if (status === 429)
    return 'You’ve sent a few messages already. Please wait a moment and try again.';
  if (status === 422) return 'Please check the highlighted fields and try again.';
  if (status >= 500)
    return 'Something went wrong on our side. Your message wasn’t sent — please retry.';
  return 'Could not send your message. Please try again.';
}

export async function submitContact(payload: ContactPayload): Promise<ContactResult> {
  let res: Response;
  try {
    res = await apiPost('/contact/', payload, DEFAULT_TIMEOUT_MS, { skipAuthHandling: true });
  } catch {
    throw new ContactError(0, 'Network error — please check your connection and try again.');
  }

  if (!res.ok) {
    let parsedMessage: string | null = null;
    try {
      const body = (await res.json()) as {
        error?: { message?: string };
        detail?: unknown;
      };
      if (body?.error?.message) parsedMessage = body.error.message;
      else if (typeof body?.detail === 'string') parsedMessage = body.detail;
    } catch {
      /* non-JSON error body */
    }
    const retryHeader = res.headers.get('Retry-After');
    const retryAfterMs = retryHeader ? Number(retryHeader) * 1000 : undefined;
    throw new ContactError(
      res.status,
      statusMessage(res.status, parsedMessage),
      Number.isFinite(retryAfterMs) ? retryAfterMs : undefined
    );
  }

  return (await res.json()) as ContactResult;
}

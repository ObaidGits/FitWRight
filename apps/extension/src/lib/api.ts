/**
 * FitWright API client. Runs ONLY in the service worker.
 *
 * Auth model: the extension deliberately stores no credentials. It rides the
 * user's existing FitWright session cookie via `credentials: 'include'`, so
 * "sign in" means "sign in to FitWright in a normal tab" and signing out there
 * revokes the extension too. No token to leak, no password to keep.
 *
 * CSRF: the backend requires `X-CSRF-Token` on mutating requests, cross-checked
 * against the `csrf` cookie. The extension cannot read that cookie (different
 * origin, and we do not request the broad `cookies` permission), so it reads the
 * token from `GET /auth/csrf`, whose response body carries it. The cookie itself
 * still rides along automatically.
 */
import { getSettings, normalizeBaseUrl } from './storage';
import { API_VERSION } from './types';
import type {
  AutofillProfile,
  CaptureResponse,
  CapturedJob,
  DraftResult,
  MatchResult,
  PingResult,
  ScrapeResponse,
} from './types';

/** Raised when the user is not signed in to FitWright. */
export class NotSignedInError extends Error {
  constructor() {
    super('Not signed in to FitWright');
    this.name = 'NotSignedInError';
  }
}

/** Raised when the feature is off (kill-switch) or the route does not exist. */
export class FeatureDisabledError extends Error {
  constructor() {
    super('Job Discovery is disabled on this FitWright instance');
    this.name = 'FeatureDisabledError';
  }
}

const API_PREFIX = '/api/v1';

async function baseUrl(): Promise<string> {
  const settings = await getSettings();
  return normalizeBaseUrl(settings.apiBaseUrl);
}

// CSRF tokens are per-session and stable; cache to avoid a second round trip on
// every mutation, and clear on 403 so a rotated token self-heals.
let csrfToken: string | null = null;

async function getCsrfToken(base: string): Promise<string> {
  if (csrfToken) return csrfToken;
  const response = await fetch(`${base}${API_PREFIX}/auth/csrf`, {
    credentials: 'include',
  });
  if (!response.ok) throw new Error(`Could not obtain CSRF token (${response.status})`);
  const body = (await response.json()) as { csrfToken?: string };
  if (!body.csrfToken) throw new Error('CSRF token missing from response');
  csrfToken = body.csrfToken;
  return csrfToken;
}

interface RequestOptions {
  method?: 'GET' | 'POST' | 'PATCH' | 'DELETE';
  body?: unknown;
  /** Retry once after clearing the CSRF token. Internal. */
  retryOnCsrf?: boolean;
}

/**
 * Issue one API request, mapping FitWright's error shapes onto typed errors the
 * UI can act on.
 */
async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const base = await baseUrl();
  const method = options.method ?? 'GET';
  const headers: Record<string, string> = { Accept: 'application/json' };

  if (method !== 'GET') {
    headers['Content-Type'] = 'application/json';
    headers['X-CSRF-Token'] = await getCsrfToken(base);
  }

  let response: Response;
  try {
    response = await fetch(`${base}${API_PREFIX}${path}`, {
      method,
      headers,
      credentials: 'include',
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
    });
  } catch {
    // Network-level failure: FitWright not running, or the host permission for
    // this base URL was never granted.
    throw new Error(`Cannot reach FitWright at ${base}. Is it running?`);
  }

  if (response.status === 401) throw new NotSignedInError();
  if (response.status === 404) throw new FeatureDisabledError();

  if (response.status === 403 && options.retryOnCsrf !== false) {
    // Stale token (server restarted and rotated its signing secret).
    csrfToken = null;
    return request<T>(path, { ...options, retryOnCsrf: false });
  }

  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try {
      const body = (await response.json()) as { detail?: unknown; error?: { message?: string } };
      if (typeof body.detail === 'string') detail = body.detail;
      else if (body.error?.message) detail = body.error.message;
    } catch {
      /* non-JSON error body - keep the status-based message */
    }
    throw new Error(detail);
  }

  if (response.status === 204) return null as T;
  return (await response.json()) as T;
}

// --------------------------------------------------------------------------- //
// Endpoints
// --------------------------------------------------------------------------- //

export async function ping(): Promise<PingResult & { versionOk: boolean }> {
  const result = await request<PingResult>('/extension/ping');
  return { ...result, versionOk: result.api_version === API_VERSION };
}

/**
 * The profile forms are filled from.
 *
 * Pass the job's company and title when they are known: the server then attaches
 * the resume tailored for that role instead of the master one. Omitting them is
 * not an error, it just means the generic resume goes out - which is the whole
 * thing FitWright exists to avoid.
 */
export function getProfile(job?: { company?: string; title?: string }): Promise<AutofillProfile> {
  const qs = new URLSearchParams();
  if (job?.company?.trim()) qs.set('company', job.company.trim());
  if (job?.title?.trim()) qs.set('title', job.title.trim());
  const query = qs.toString();
  return request<AutofillProfile>(`/extension/profile${query ? `?${query}` : ''}`);
}

export function captureJob(job: CapturedJob): Promise<CaptureResponse> {
  return request<CaptureResponse>('/extension/capture', { method: 'POST', body: job });
}

/**
 * The server rejects a batch over 200 jobs rather than truncating it, so split
 * here. A single results page rarely exceeds 200, but a virtualized list that
 * the user scrolled a long way can.
 */
const MAX_BATCH = 200;

export async function sendScrapeResults(source: string, jobs: CapturedJob[]): Promise<ScrapeResponse> {
  if (jobs.length <= MAX_BATCH) {
    return request<ScrapeResponse>('/extension/scrape', {
      method: 'POST',
      body: { source, jobs },
    });
  }

  const totals: ScrapeResponse = { received: 0, saved: 0, source };
  for (let i = 0; i < jobs.length; i += MAX_BATCH) {
    const result = await request<ScrapeResponse>('/extension/scrape', {
      method: 'POST',
      body: { source, jobs: jobs.slice(i, i + MAX_BATCH) },
    });
    totals.received += result.received;
    totals.saved += result.saved;
  }
  return totals;
}

/**
 * Tell FitWright what a form asked for.
 *
 * Sent automatically after every autofill, which is exactly why it carries
 * labels, types and options but never values: this fires on every application
 * the user opens, and a record of what they typed is not ours to keep. The
 * server refuses a payload that contains one.
 */
export function reportForm(payload: {
  fields: {
    label: string;
    field_type: string;
    options: string[];
    filled: boolean;
    matched_key: string | null;
  }[];
  company?: string;
  ats?: string;
  url?: string;
}): Promise<{ seen: number; created: number; updated: number; needs_answer: number }> {
  return request('/extension/form-report', { method: 'POST', body: payload });
}

/**
 * Remember answers the user typed on the page and asked to keep.
 *
 * The counterpart to `reportForm`, and the only call that sends values. The
 * difference is consent: this one happens because they pressed a button saying
 * so, which is what makes teaching from the form in front of you preferable to
 * retyping it in Settings later.
 */
export function saveAnswers(payload: {
  answers: { label: string; value: unknown; field_type: string; options: string[] }[];
  company?: string;
  ats?: string;
  url?: string;
}): Promise<{ saved: number }> {
  return request('/extension/answers', { method: 'POST', body: payload });
}

export function matchJob(description: string, title: string): Promise<MatchResult> {  return request<MatchResult>('/extension/match', {
    method: 'POST',
    body: { description, title },
  });
}

export function draftAnswer(input: {
  question: string;
  description: string;
  company: string;
  title: string;
}): Promise<DraftResult> {
  return request<DraftResult>('/extension/draft', { method: 'POST', body: input });
}

export function markApplied(input: {
  fingerprint?: string;
  url?: string;
}): Promise<{ updated: boolean; fingerprint: string | null }> {
  return request('/extension/applied', { method: 'POST', body: input });
}

/**
 * Fetch the tailored resume PDF and return it as a data URL.
 *
 * A data URL rather than a Blob because the value has to survive
 * `chrome.runtime.sendMessage` (structured clone drops Blobs across contexts),
 * and the content script rebuilds a `File` from it to attach to the form.
 */
export async function fetchResumePdf(job?: {
  company?: string;
  title?: string;
}): Promise<{ dataUrl: string; filename: string; tailored: boolean } | null> {
  // Passing the job through is what makes the tailored resume reach the form.
  const profile = await getProfile(job);
  if (!profile.resume_pdf_path) return null;

  const base = await baseUrl();
  const response = await fetch(`${base}${profile.resume_pdf_path}`, {
    credentials: 'include',
  });
  if (!response.ok) return null;

  const blob = await response.blob();
  const dataUrl = await new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(blob);
  });

  const filename = (profile.resume_filename || 'resume.pdf').replace(/\.\w+$/, '') + '.pdf';
  return { dataUrl, filename, tailored: Boolean(profile.resume_tailored_for_role) };
}

/** Reset cached auth state - called when the base URL changes. */
export function resetAuthCache(): void {
  csrfToken = null;
}

import type {
  ImprovedResult,
  InterviewPrepData,
} from '@/components/common/resume_previewer_context';
import type {
  ResumeData,
  SectionMeta,
  CustomSection,
} from '@/components/dashboard/resume-component';
import { type TemplateSettings, DEFAULT_TEMPLATE_SETTINGS } from '@/lib/types/template-settings';
import { type PhotoConfig } from '@/lib/types/photo';
import { type Locale } from '@/i18n/config';
import {
  API_BASE,
  DEFAULT_TIMEOUT_MS,
  apiPost,
  apiPatch,
  apiDelete,
  apiFetch,
  readCsrfToken,
} from './client';
import { ApiError, parseError } from './errors';
import { SseDecoder, type SseEvent, type StreamTransport } from '@/lib/resilience/stream-client';

// Matches backend schemas/models.py ResumeData
interface ProcessedResume {
  personalInfo?: {
    name?: string;
    title?: string;
    email?: string;
    phone?: string;
    location?: string;
    website?: string | null;
    linkedin?: string | null;
    github?: string | null;
    // Photo System: resolved header photo URL + per-resume photo config, so the
    // fetch/update round-trip is fully typed and never drops the photo.
    avatarUrl?: string | null;
    photo?: PhotoConfig | null;
  };
  summary?: string;
  workExperience?: Array<{
    id: number;
    title?: string;
    company?: string;
    location?: string | null;
    years?: string;
    description?: string[];
  }>;
  education?: Array<{
    id: number;
    institution?: string;
    degree?: string;
    years?: string;
    description?: string | null;
  }>;
  personalProjects?: Array<{
    id: number;
    name?: string;
    role?: string;
    years?: string;
    github?: string | null;
    website?: string | null;
    description?: string[];
  }>;
  additional?: {
    technicalSkills?: string[];
    languages?: string[];
    certificationsTraining?: string[];
    awards?: string[];
  };
  // Section ordering/visibility + custom sections (optional; absent on older
  // resumes, in which case the render engine falls back to default ordering).
  sectionMeta?: SectionMeta[];
  customSections?: Record<string, CustomSection>;
}

interface ResumeResponse {
  request_id: string;
  data: {
    resume_id: string;
    raw_resume: {
      id: number | null;
      content: string;
      content_type: string;
      created_at: string;
      processing_status: 'pending' | 'processing' | 'ready' | 'failed';
    };
    processed_resume: ProcessedResume | null;
    cover_letter?: string | null;
    outreach_message?: string | null;
    interview_prep?: InterviewPrepData | null;
    parent_id?: string | null; // For determining if resume is tailored
    title?: string | null;
    /** Persisted appearance (template + customization); null ⇒ app default. */
    template_settings?: TemplateSettings | null;
    /** Optimistic-concurrency token (P4 R3.1). */
    version?: number | null;
  };
}

/** Thrown by {@link updateResume} on a 409 version conflict (P4 R3.2). */
export class ResumeConflictError extends Error {
  constructor(
    public readonly yourBaseVersion: number | null,
    public readonly currentVersion: number,
    public readonly currentData: unknown
  ) {
    super('Resume changed elsewhere (version conflict).');
    this.name = 'ResumeConflictError';
  }
}

export interface UpdateResumeOptions {
  /** Base version for the version-CAS `If-Match` header (P4 R3.1). */
  baseVersion?: number | null;
  /** Client idempotency key for safe retries (P4 R4.2). */
  idempotencyKey?: string;
}

/** A failed resume request carrying the HTTP status for retry classification. */
export class ResumeRequestError extends Error {
  constructor(
    public readonly status: number,
    message: string,
    public readonly retryAfterMs?: number
  ) {
    super(message);
    this.name = 'ResumeRequestError';
  }
}

/** Response from resume upload endpoint */
export interface ResumeUploadResponse {
  message: string;
  request_id: string;
  resume_id: string;
  processing_status: 'pending' | 'processing' | 'ready' | 'failed';
  is_master: boolean;
}

interface ImproveResumeConfirmRequest {
  resume_id: string;
  job_id: string;
  improved_data: ResumeData;
  improvements: Array<{
    suggestion: string;
    lineNumber?: number | null;
  }>;
}

function normalizeResumeId(resumeId: string): string {
  const normalized = resumeId.trim();
  if (!normalized) {
    throw new Error('Resume ID is required.');
  }
  return normalized;
}

export interface ResumeListItem {
  resume_id: string;
  filename: string | null;
  is_master: boolean;
  parent_id: string | null;
  processing_status: 'pending' | 'processing' | 'ready' | 'failed';
  created_at: string;
  updated_at: string;
  title?: string | null;
  // Optional lightweight snippet of associated job description (populated client-side)
  jobSnippet?: string;
}

async function postImprove(
  endpoint: string,
  payload: Record<string, unknown>
): Promise<ImprovedResult> {
  let response: Response;
  try {
    // Use the configurable request timeout so NEXT_PUBLIC_REQUEST_TIMEOUT_MS
    // actually applies to the long-running improve/preview/confirm calls (#776).
    response = await apiPost(endpoint, payload, DEFAULT_TIMEOUT_MS);
  } catch (networkError) {
    console.error(`Network error during ${endpoint}:`, networkError);
    throw networkError;
  }

  if (!response.ok) {
    // NEVER surface the raw body: a 5xx from the Heroku router is an HTML
    // "Application Error" page, and throwing it as the error message would
    // render raw HTML in the UI. `parseError` yields a clean, typed, user-facing
    // ApiError (status-specific message for 5xx, envelope/detail when present).
    throw await parseError(
      response,
      'Resume tailoring is temporarily unavailable. Please try again in a moment.'
    );
  }

  const text = await response.text().catch(() => '');
  try {
    return JSON.parse(text) as ImprovedResult;
  } catch (parseErr) {
    console.error(
      'Failed to parse improve response:',
      parseErr,
      'Raw response:',
      text.slice(0, 500)
    );
    // A 2xx with a non-JSON body (e.g. an edge/proxy returned HTML) — surface a
    // safe, typed error rather than leaking the body or a bare SyntaxError.
    throw new ApiError(
      'malformed_response',
      'The server returned an unexpected response. Please try again.',
      response.status
    );
  }
}

/** Uploads job descriptions and returns a job_id */
export async function uploadJobDescriptions(
  descriptions: string[],
  resumeId: string
): Promise<string> {
  const res = await apiPost('/jobs/upload', {
    job_descriptions: descriptions,
    resume_id: resumeId,
  });
  if (!res.ok) throw new Error(`Upload failed with status ${res.status}`);
  const data = await res.json();
  return data.job_id[0];
}

/** Structured keyword breakdown returned by {@link analyzeJob}. */
export interface JobAnalyzeKeywords {
  required_skills: string[];
  preferred_skills: string[];
  keywords: string[];
  experience_requirements: string[];
  seniority_level: string | null;
  experience_years: string | null;
}

/** Pre-generation job-fit analysis result. */
export interface JobAnalyzeResult {
  keywords: JobAnalyzeKeywords;
  matched: string[];
  missing: string[];
  fit_score: number | null;
}

/**
 * Analyze a job description for fit against a resume (explicit user action).
 *
 * This makes a single LLM keyword-extraction call on the backend and returns
 * the keyword breakdown plus, when `resumeId` points to a resume with
 * processed data, the matched/missing keywords and an overall fit score. It is
 * only ever invoked when the user explicitly clicks "Analyze fit" — never
 * automatically — to honor the cost-consent principle.
 */
export async function analyzeJob(
  jobDescription: string,
  resumeId?: string
): Promise<JobAnalyzeResult> {
  const res = await apiPost(
    '/jobs/analyze',
    {
      job_description: jobDescription,
      resume_id: resumeId ?? null,
    },
    DEFAULT_TIMEOUT_MS
  );
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`Failed to analyze job (status ${res.status}): ${text}`);
  }
  return (await res.json()) as JobAnalyzeResult;
}

/** Improves the resume and returns the full preview object */
export async function improveResume(
  resumeId: string,
  jobId: string,
  promptId?: string
): Promise<ImprovedResult> {
  return postImprove('/resumes/improve', {
    resume_id: resumeId,
    job_id: jobId,
    prompt_id: promptId ?? null,
  });
}

/** Previews the resume improvement without saving */
export async function previewImproveResume(
  resumeId: string,
  jobId: string,
  promptId?: string
): Promise<ImprovedResult> {
  return postImprove('/resumes/improve/preview', {
    resume_id: resumeId,
    job_id: jobId,
    prompt_id: promptId ?? null,
  });
}

/** The real tailor pipeline stages, in order, surfaced by the streaming path. */
export type TailorStageName = 'keywords' | 'plan' | 'rewrite' | 'refine' | 'score';

export interface TailorStageEvent {
  stage: TailorStageName;
  status: 'start' | 'done';
}

/** Raised by {@link streamImproveResume} when the user cancels the stream. */
export class TailorStreamCancelled extends Error {
  constructor() {
    super('Tailoring cancelled.');
    this.name = 'TailorStreamCancelled';
  }
}

/**
 * Stream the tailor pipeline as stage-progress SSE (P4 R1 pattern extended to
 * the multi-stage improve flow). `onStage` fires at each real backend boundary
 * (never fabricated), and the promise resolves the same {@link ImprovedResult}
 * the non-stream endpoint returns.
 *
 * Throws {@link TailorStreamCancelled} when `signal` aborts (caller should stop,
 * not fall back). Any other throw means the stream was unusable (flag off,
 * unsupported, network) and the caller should transparently fall back to
 * {@link previewImproveResume}.
 */
export async function streamImproveResume(
  resumeId: string,
  jobId: string,
  promptId: string | undefined,
  opts: { requestId: string; signal: AbortSignal; onStage?: (e: TailorStageEvent) => void }
): Promise<ImprovedResult> {
  const url = `${API_BASE}/resumes/improve/preview/stream?request_id=${encodeURIComponent(
    opts.requestId
  )}`;
  const csrf = readCsrfToken();
  const headers: Record<string, string> = {
    Accept: 'text/event-stream',
    'Content-Type': 'application/json',
  };
  if (csrf) headers['X-CSRF-Token'] = csrf;

  let res: Response;
  try {
    res = await fetch(url, {
      method: 'POST',
      credentials: 'include',
      headers,
      body: JSON.stringify({ resume_id: resumeId, job_id: jobId, prompt_id: promptId ?? null }),
      signal: opts.signal,
    });
  } catch (e) {
    if (opts.signal.aborted) throw new TailorStreamCancelled();
    throw e instanceof Error ? e : new Error('stream_open_failed');
  }
  if (!res.ok || !res.body) {
    // 409 (disabled) / 429 / any non-2xx → caller falls back to non-stream.
    throw new Error(`stream_open_failed:${res.status}`);
  }

  const reader = res.body.getReader();
  const decoder = new SseDecoder();
  const td = new TextDecoder();
  let result: ImprovedResult | null = null;
  let cancelled = false;
  let terminalError: string | null = null;

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      for (const ev of decoder.push(td.decode(value, { stream: true }))) {
        if (ev.event === 'stage') {
          opts.onStage?.(ev.data as TailorStageEvent);
        } else if (ev.event === 'done') {
          const d = ev.data as { cancelled?: boolean; result?: ImprovedResult };
          if (d?.cancelled) cancelled = true;
          else if (d?.result) result = d.result;
        } else if (ev.event === 'error') {
          terminalError = (ev.data as { message?: string })?.message ?? 'stream_error';
        }
      }
    }
  } catch (e) {
    if (opts.signal.aborted) throw new TailorStreamCancelled();
    throw e instanceof Error ? e : new Error('stream_read_failed');
  } finally {
    try {
      await reader.cancel();
    } catch {
      /* ignore */
    }
  }

  if (cancelled) throw new TailorStreamCancelled();
  if (terminalError) throw new Error(terminalError);
  if (!result) throw new Error('stream_incomplete');
  return result;
}

/** Signal the server to cancel an in-flight tailor stream (best-effort). */
export async function cancelTailorStream(requestId: string): Promise<void> {
  try {
    await apiPost(`/resumes/stream/${encodeURIComponent(requestId)}/cancel`, {});
  } catch {
    /* best-effort; the local abort already stopped consumption */
  }
}

/** Confirms and saves a tailored resume */
export async function confirmImproveResume(
  payload: ImproveResumeConfirmRequest
): Promise<ImprovedResult> {
  return postImprove('/resumes/improve/confirm', payload as unknown as Record<string, unknown>);
}

/** Fetches a raw resume record for previewing the original upload */
export async function fetchResume(resumeId: string): Promise<ResumeResponse['data']> {
  const res = await apiFetch(`/resumes?resume_id=${encodeURIComponent(resumeId)}`);
  if (!res.ok) {
    throw new Error(`Failed to load resume (status ${res.status}).`);
  }
  const payload = (await res.json()) as ResumeResponse;
  // Support both raw_resume content (initial) and processed_resume (if available)
  // The viewer/builder logic should prioritize processed data if present
  return payload.data;
}

export async function fetchResumeList(includeMaster = false): Promise<ResumeListItem[]> {
  const res = await apiFetch(`/resumes/list?include_master=${includeMaster ? 'true' : 'false'}`);
  if (!res.ok) {
    throw new Error(`Failed to load resumes list (status ${res.status}).`);
  }
  const payload = (await res.json()) as { data: ResumeListItem[] };
  return payload.data;
}

export async function updateResume(
  resumeId: string,
  resumeData: ProcessedResume,
  opts: UpdateResumeOptions = {}
): Promise<ResumeResponse['data']> {
  const headers: Record<string, string> = {};
  if (opts.baseVersion != null) headers['If-Match'] = String(opts.baseVersion);
  if (opts.idempotencyKey) headers['Idempotency-Key'] = opts.idempotencyKey;

  const res = await apiPatch(`/resumes/${encodeURIComponent(resumeId)}`, resumeData, {
    headers,
  });
  if (res.status === 409) {
    // Version conflict — parse the ADR-7 envelope details so the caller can
    // drive the explicit resolution flow (keep-mine / take-latest / merge).
    const body = await res.json().catch(() => null);
    const details = body?.error?.details ?? {};
    throw new ResumeConflictError(
      details.your_base_version ?? opts.baseVersion ?? null,
      details.current_version ?? 0,
      details.current_data
    );
  }
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    const retryAfter = res.headers.get('Retry-After');
    const retryAfterMs = retryAfter ? Number(retryAfter) * 1000 : undefined;
    throw new ResumeRequestError(
      res.status,
      `Failed to update resume (status ${res.status}): ${text}`,
      Number.isFinite(retryAfterMs) ? retryAfterMs : undefined
    );
  }
  const payload = (await res.json()) as ResumeResponse;
  return payload.data;
}

export function getResumePdfUrl(
  resumeId: string,
  settings?: TemplateSettings,
  locale?: Locale
): string {
  const normalizedId = normalizeResumeId(resumeId);
  const params = new URLSearchParams();

  if (settings) {
    params.set('template', settings.template);
    params.set('pageSize', settings.pageSize);
    params.set('marginTop', String(settings.margins.top));
    params.set('marginBottom', String(settings.margins.bottom));
    params.set('marginLeft', String(settings.margins.left));
    params.set('marginRight', String(settings.margins.right));
    params.set('sectionSpacing', String(settings.spacing.section));
    params.set('itemSpacing', String(settings.spacing.item));
    params.set('lineHeight', String(settings.spacing.lineHeight));
    params.set('fontSize', String(settings.fontSize.base));
    params.set('headerScale', String(settings.fontSize.headerScale));
    params.set('headerFont', settings.fontSize.headerFont);
    params.set('bodyFont', settings.fontSize.bodyFont);
    params.set('compactMode', String(settings.compactMode));
    params.set('showContactIcons', String(settings.showContactIcons));
    params.set('accentColor', settings.accentColor);
  } else {
    params.set('template', 'swiss-single');
    params.set('pageSize', 'A4');
  }
  if (locale) {
    params.set('lang', locale);
  }

  return `${API_BASE}/resumes/${encodeURIComponent(normalizedId)}/pdf?${params.toString()}`;
}

export async function downloadResumePdf(
  resumeId: string,
  settings?: TemplateSettings,
  locale?: Locale
): Promise<Blob> {
  const url = getResumePdfUrl(resumeId, settings, locale);
  const res = await apiFetch(url);
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`Failed to download resume (status ${res.status}): ${text}`);
  }
  return await res.blob();
}

/** Deletes a resume by ID */
export async function deleteResume(resumeId: string): Promise<void> {
  const res = await apiDelete(`/resumes/${encodeURIComponent(resumeId)}`);
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`Failed to delete resume (status ${res.status}): ${text}`);
  }
}

/** Updates the cover letter for a resume */
export async function updateCoverLetter(resumeId: string, content: string): Promise<void> {
  const res = await apiPatch(`/resumes/${encodeURIComponent(resumeId)}/cover-letter`, { content });
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`Failed to update cover letter (status ${res.status}): ${text}`);
  }
}

/** Updates the outreach message for a resume */
export async function updateOutreachMessage(resumeId: string, content: string): Promise<void> {
  const res = await apiPatch(`/resumes/${encodeURIComponent(resumeId)}/outreach-message`, {
    content,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`Failed to update outreach message (status ${res.status}): ${text}`);
  }
}

/**
 * Persist a resume's appearance (chosen template + customization).
 *
 * Appearance is a rendering artifact, not resume content, so the backend does
 * not bump the optimistic-concurrency version — saving a template change never
 * conflicts with an in-flight content edit.
 */
export async function updateResumeTemplateSettings(
  resumeId: string,
  settings: TemplateSettings
): Promise<void> {
  const res = await apiPatch(`/resumes/${encodeURIComponent(resumeId)}/template-settings`, {
    settings,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`Failed to save template settings (status ${res.status}): ${text}`);
  }
}

/** Merge a persisted (possibly partial/legacy) settings blob over the defaults. */
export function normalizeTemplateSettings(raw: unknown): TemplateSettings {
  if (!raw || typeof raw !== 'object') return DEFAULT_TEMPLATE_SETTINGS;
  const r = raw as Partial<TemplateSettings>;
  return {
    ...DEFAULT_TEMPLATE_SETTINGS,
    ...r,
    margins: { ...DEFAULT_TEMPLATE_SETTINGS.margins, ...(r.margins ?? {}) },
    spacing: { ...DEFAULT_TEMPLATE_SETTINGS.spacing, ...(r.spacing ?? {}) },
    fontSize: { ...DEFAULT_TEMPLATE_SETTINGS.fontSize, ...(r.fontSize ?? {}) },
  };
}

/**
 * Create a new resume from structured data (Sample Library "Use", duplication).
 * Never mutates an existing resume — always returns a fresh `resume_id`.
 */
export async function createResumeFromData(opts: {
  processed_data: ResumeData;
  title?: string | null;
  template_settings?: TemplateSettings | null;
  as_master?: boolean;
  source?: string;
}): Promise<ResumeUploadResponse> {
  const res = await apiPost('/resumes/from-data', opts, DEFAULT_TIMEOUT_MS);
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`Failed to create resume (status ${res.status}): ${text}`);
  }
  return res.json();
}

/** Renames a resume by updating its title */
export async function renameResume(resumeId: string, title: string): Promise<void> {
  const res = await apiPatch(`/resumes/${encodeURIComponent(resumeId)}/title`, { title });
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`Failed to rename resume (status ${res.status}): ${text}`);
  }
}

/** Downloads cover letter as PDF */
export function getCoverLetterPdfUrl(
  resumeId: string,
  pageSize: 'A4' | 'LETTER' = 'A4',
  locale?: Locale
): string {
  const normalizedId = normalizeResumeId(resumeId);
  const params = new URLSearchParams({ pageSize });
  if (locale) {
    params.set('lang', locale);
  }
  return `${API_BASE}/resumes/${encodeURIComponent(normalizedId)}/cover-letter/pdf?${params.toString()}`;
}

export async function downloadCoverLetterPdf(
  resumeId: string,
  pageSize: 'A4' | 'LETTER' = 'A4',
  locale?: Locale
): Promise<Blob> {
  const url = getCoverLetterPdfUrl(resumeId, pageSize, locale);
  const res = await apiFetch(url);
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`Failed to download cover letter (status ${res.status}): ${text}`);
  }
  return await res.blob();
}

/** Generates a cover letter on-demand for a tailored resume.
 *
 * By default the backend returns any previously saved cover letter without a
 * new LLM call (persistent reuse); pass `regenerate: true` for an explicit
 * "Regenerate" action to force fresh generation. */
export async function generateCoverLetter(resumeId: string, regenerate = false): Promise<string> {
  const suffix = regenerate ? '?regenerate=true' : '';
  const res = await apiPost(
    `/resumes/${encodeURIComponent(resumeId)}/generate-cover-letter${suffix}`,
    {}
  );
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`Failed to generate cover letter (status ${res.status}): ${text}`);
  }
  const data = await res.json();
  return data.content;
}

/** Generates an outreach message on-demand for a tailored resume.
 *
 * Reuses a previously saved message unless `regenerate: true` is passed. */
export async function generateOutreachMessage(
  resumeId: string,
  regenerate = false
): Promise<string> {
  const suffix = regenerate ? '?regenerate=true' : '';
  const res = await apiPost(
    `/resumes/${encodeURIComponent(resumeId)}/generate-outreach${suffix}`,
    {}
  );
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`Failed to generate outreach message (status ${res.status}): ${text}`);
  }
  const data = await res.json();
  return data.content;
}

/** Generates interview preparation on-demand for a tailored resume.
 *
 * Reuses previously saved interview prep unless `regenerate: true` is passed. */
export async function generateInterviewPrep(
  resumeId: string,
  regenerate = false
): Promise<InterviewPrepData> {
  const suffix = regenerate ? '?regenerate=true' : '';
  const res = await apiPost(
    `/resumes/${encodeURIComponent(resumeId)}/generate-interview-prep${suffix}`,
    {}
  );
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`Failed to generate interview preparation (status ${res.status}): ${text}`);
  }
  const data = (await res.json()) as { interview_prep: InterviewPrepData };
  return data.interview_prep;
}

export type StreamKind = 'cover-letter' | 'outreach';

/**
 * Build a {@link StreamTransport} for an AI generation (P4 R1).
 *
 * `open` streams SSE from the backend using `fetch` (so the caller's abort
 * signal really cancels the request — unlike the timeout-bounded `apiFetch`).
 * A 409 (streaming disabled/unsupported) or any transport error propagates so
 * the StreamController transparently falls back to the non-stream path.
 */
export function buildResumeStreamTransport(
  resumeId: string,
  kind: StreamKind,
  requestId: string
): StreamTransport {
  const id = normalizeResumeId(resumeId);
  const url = `${API_BASE}/resumes/${encodeURIComponent(id)}/${kind}/stream?request_id=${encodeURIComponent(
    requestId
  )}`;
  return {
    async *open(signal: AbortSignal): AsyncIterable<SseEvent> {
      const csrf = readCsrfToken();
      const headers: Record<string, string> = { Accept: 'text/event-stream' };
      if (csrf) headers['X-CSRF-Token'] = csrf;
      const res = await fetch(url, {
        method: 'POST',
        credentials: 'include',
        headers,
        signal,
      });
      if (!res.ok || !res.body) {
        // 409 (disabled/unsupported) or any error → trigger fallback.
        throw new Error(`stream_open_failed:${res.status}`);
      }
      const reader = res.body.getReader();
      const decoder = new SseDecoder();
      const td = new TextDecoder();
      try {
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          for (const ev of decoder.push(td.decode(value, { stream: true }))) {
            yield ev;
          }
        }
      } finally {
        try {
          await reader.cancel();
        } catch {
          /* ignore */
        }
      }
    },
    async cancel(): Promise<void> {
      await apiPost(`/resumes/stream/${encodeURIComponent(requestId)}/cancel`, {});
    },
    async fallback(): Promise<string> {
      return kind === 'cover-letter' ? generateCoverLetter(id) : generateOutreachMessage(id);
    },
  };
}

/** Retries AI processing for a failed resume */
export async function retryProcessing(resumeId: string): Promise<ResumeUploadResponse> {
  const res = await apiPost(`/resumes/${encodeURIComponent(resumeId)}/retry-processing`, {});
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`Failed to retry processing (status ${res.status}): ${text}`);
  }
  return res.json();
}

/** Fetches the job description used to tailor a resume */
export async function fetchJobDescription(
  resumeId: string
): Promise<{ job_id: string; content: string }> {
  const res = await apiFetch(`/resumes/${encodeURIComponent(resumeId)}/job-description`);
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`Failed to fetch job description (status ${res.status}): ${text}`);
  }
  return res.json();
}

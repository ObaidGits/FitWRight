import { getUploadUrl, readCsrfToken } from '@/lib/api/client';
import type { ResumeUploadResponse } from '@/lib/api/resume';
import { SseDecoder } from '@/lib/resilience/stream-client';

const ALLOWED_EXT = ['.pdf', '.doc', '.docx'];
const ALLOWED_MIME = [
  'application/pdf',
  'application/msword',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
];
export const MAX_UPLOAD_BYTES = 4 * 1024 * 1024;

/** Client-side validation mirroring the backend (MIME OR extension). */
export function validateResumeFile(file: File): string | null {
  const name = file.name.toLowerCase();
  const extOk = ALLOWED_EXT.some((e) => name.endsWith(e));
  const mimeOk = ALLOWED_MIME.includes(file.type);
  if (!extOk && !mimeOk) return 'Unsupported file. Please upload a PDF, DOC, or DOCX.';
  if (file.size > MAX_UPLOAD_BYTES) return 'File too large. Maximum size is 4MB.';
  if (file.size === 0) return 'That file appears to be empty.';
  return null;
}

export async function uploadResumeFile(file: File): Promise<ResumeUploadResponse> {
  const form = new FormData();
  form.append('file', file);
  // Raw fetch (not apiFetch, which can't stream FormData), so replicate its auth
  // plumbing: send cookies and echo the double-submit CSRF token. Missing the
  // X-CSRF-Token header makes the backend reject the upload with `csrf_failed`
  // in hosted mode (per-session CSRF is enforced whenever a session exists).
  const headers: Record<string, string> = {};
  const csrf = readCsrfToken();
  if (csrf) headers['X-CSRF-Token'] = csrf;
  const res = await fetch(getUploadUrl(), {
    method: 'POST',
    body: form,
    credentials: 'include',
    headers,
  });
  if (!res.ok) {
    let detail = 'Upload failed. Please try again.';
    try {
      const body = await res.json();
      if (body?.detail) detail = String(body.detail);
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  return res.json();
}

/** A live parse-stage event forwarded from the streaming upload endpoint. */
export interface ParseStageEvent {
  /** Real backend boundary: 'received' | 'extracting' | 'structuring'. */
  stage: string;
  status: 'active' | 'done';
}

export interface StreamUploadOptions {
  onStage?: (event: ParseStageEvent) => void;
  signal?: AbortSignal;
}

/** Sentinel thrown when the streaming endpoint is unavailable (flag off / no
 * body / network open failure), so the caller transparently falls back to the
 * non-streaming {@link uploadResumeFile}. */
export const STREAM_UNAVAILABLE = 'stream_unavailable';

/**
 * Upload + parse a resume via the SSE streaming endpoint, forwarding honest
 * per-stage progress through `onStage`. Mirrors the non-stream contract by
 * resolving the same {@link ResumeUploadResponse}.
 *
 * Throws `Error('stream_unavailable')` when streaming is off/unsupported (the
 * caller should fall back to {@link uploadResumeFile}); throws a clean,
 * user-facing message string when the server emits a terminal `error` event.
 */
export async function streamUploadResumeFile(
  file: File,
  opts: StreamUploadOptions = {}
): Promise<ResumeUploadResponse> {
  const form = new FormData();
  form.append('file', file);
  const headers: Record<string, string> = { Accept: 'text/event-stream' };
  const csrf = readCsrfToken();
  if (csrf) headers['X-CSRF-Token'] = csrf;

  let res: Response;
  try {
    res = await fetch(`${getUploadUrl()}/stream`, {
      method: 'POST',
      body: form,
      credentials: 'include',
      headers,
      signal: opts.signal,
    });
  } catch (e) {
    if (opts.signal?.aborted) throw e instanceof Error ? e : new Error('aborted');
    // Could not even open the stream (network / CORS) → fall back.
    throw new Error(STREAM_UNAVAILABLE);
  }

  if (!res.ok || !res.body) {
    // 409 (flag off), 404, or any non-2xx → transparent fallback.
    throw new Error(STREAM_UNAVAILABLE);
  }

  const reader = res.body.getReader();
  const decoder = new SseDecoder();
  const td = new TextDecoder();
  let result: ResumeUploadResponse | null = null;
  let terminalError: string | null = null;

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      for (const ev of decoder.push(td.decode(value, { stream: true }))) {
        if (ev.event === 'stage') {
          opts.onStage?.(ev.data as ParseStageEvent);
        } else if (ev.event === 'done') {
          const d = ev.data as { result?: ResumeUploadResponse };
          if (d?.result) result = d.result;
        } else if (ev.event === 'error') {
          terminalError = (ev.data as { message?: string })?.message ?? 'Upload failed.';
        }
      }
    }
  } finally {
    try {
      await reader.cancel();
    } catch {
      /* ignore */
    }
  }

  if (terminalError) throw new Error(terminalError);
  if (!result) throw new Error(STREAM_UNAVAILABLE);
  return result;
}

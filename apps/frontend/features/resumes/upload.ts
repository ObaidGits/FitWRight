import { getUploadUrl } from '@/lib/api/client';
import type { ResumeUploadResponse } from '@/lib/api/resume';

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
  const res = await fetch(getUploadUrl(), { method: 'POST', body: form });
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

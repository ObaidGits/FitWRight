/**
 * The apply queue and submission records.
 *
 * The queue holds *jobs*, not part-filled forms. A half-completed form on an
 * employer's site cannot be saved across tabs, so the queue's job is to decide
 * what you open next - the extension fills each one when you get there.
 *
 * A submission record is what was actually sent: the answers, which resume
 * version the employer saw, and how it was submitted. It is the only thing that
 * can answer "what notice period did I claim at Acme?" weeks later.
 */
import { apiFetch } from './client';

const PREFIX = '/applications';

export interface QueueItem {
  application_id: string;
  job_id: string;
  company: string | null;
  role: string | null;
  position: number;
  created_at: string;
}

export interface QueueResponse {
  items: QueueItem[];
  total: number;
}

export interface SubmissionRecord {
  application_id: string;
  company: string | null;
  role: string | null;
  status: string;
  applied_at: string | null;
  answers: Record<string, unknown>;
  resume_version_id: string | null;
  submitted_via: string | null;
  /** False for anything applied before submission recording existed. */
  has_record: boolean;
}

export interface DuplicateCheck {
  is_duplicate: boolean;
  duplicate: {
    application_id: string;
    company: string | null;
    role: string | null;
    status: string;
    applied_at: string | null;
  } | null;
}

export async function getApplyQueue(signal?: AbortSignal): Promise<QueueResponse> {
  const res = await apiFetch(`${PREFIX}/queue`, { method: 'GET', signal });
  if (!res.ok) throw new Error(`Loading the queue failed: ${res.status}`);
  return res.json();
}

export async function reorderApplyQueue(applicationIds: string[]): Promise<{ reordered: number }> {
  const res = await apiFetch(`${PREFIX}/queue/reorder`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ application_ids: applicationIds }),
  });
  if (!res.ok) throw new Error(`Reordering failed: ${res.status}`);
  return res.json();
}

export async function checkDuplicate(input: {
  company?: string | null;
  role?: string | null;
}): Promise<DuplicateCheck> {
  const res = await apiFetch(`${PREFIX}/queue/check-duplicate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  });
  if (!res.ok) throw new Error(`Duplicate check failed: ${res.status}`);
  return res.json();
}

export async function getSubmission(
  applicationId: string,
  signal?: AbortSignal,
): Promise<SubmissionRecord> {
  const res = await apiFetch(`${PREFIX}/${applicationId}/submission`, { method: 'GET', signal });
  if (!res.ok) throw new Error(`Loading the submission failed: ${res.status}`);
  return res.json();
}

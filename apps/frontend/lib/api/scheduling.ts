/**
 * Reminders, interviews, and agenda API (P3 §E/§F/§G, Requirements 10–12).
 *
 * Reminders + interviews are nested under an application; the agenda is a merged,
 * time-ordered view across all applications. Creates accept an optional
 * idempotency key (double-submit safety). All calls are user-scoped server-side.
 */
import { apiFetch, apiPost, apiPatch, apiDelete, API_BASE } from './client';

// -- types ------------------------------------------------------------------

export type ReminderStatus = 'pending' | 'snoozed' | 'firing' | 'fired' | 'cancelled';
export type InterviewKind = 'screen' | 'technical' | 'onsite' | 'behavioral' | 'final' | 'other';

export interface Reminder {
  id: string;
  application_id: string;
  due_at: string;
  tz: string;
  note: string | null;
  recurrence: string | null;
  status: ReminderStatus;
  created_at: string;
  updated_at: string;
}

export interface ReminderCreate {
  due_at?: string;
  preset?: string;
  tz?: string;
  note?: string | null;
  recurrence?: string | null;
}

export interface OverlapWarning {
  id: string;
  starts_at: string;
}

export interface Interview {
  id: string;
  application_id: string;
  starts_at: string;
  tz: string;
  duration_min: number;
  kind: InterviewKind;
  location: string | null;
  notes: string | null;
  lead_times: number[];
  status: 'scheduled' | 'cancelled';
  created_at: string;
  updated_at: string;
  overlaps: OverlapWarning[];
}

export interface InterviewCreate {
  starts_at: string;
  tz?: string;
  duration_min?: number;
  kind?: InterviewKind;
  location?: string | null;
  notes?: string | null;
  lead_times?: number[];
}

export interface AgendaItem {
  kind: 'reminder' | 'interview';
  id: string;
  application_id: string;
  when: string;
  tz: string;
  title: string;
  status: string;
}

export interface AgendaResponse {
  items: AgendaItem[];
  next_cursor: string | null;
}

// -- helpers ----------------------------------------------------------------

async function asJson<T>(res: Response, fallback: string): Promise<T> {
  if (!res.ok) {
    const data = (await res.json().catch(() => ({}))) as { detail?: unknown };
    const detail = typeof data.detail === 'string' ? data.detail : null;
    throw new Error(detail || `${fallback} (status ${res.status}).`);
  }
  return res.json() as Promise<T>;
}

function idemHeaders(key?: string): Record<string, string> {
  return key ? { 'Idempotency-Key': key } : {};
}

// -- reminders --------------------------------------------------------------

export async function listReminders(applicationId: string): Promise<Reminder[]> {
  const res = await apiFetch(`/applications/${applicationId}/reminders`, {
    credentials: 'include',
  });
  if (res.status === 404) return [];
  return asJson<Reminder[]>(res, 'Failed to load reminders');
}

export async function createReminder(
  applicationId: string,
  payload: ReminderCreate,
  idempotencyKey?: string
): Promise<Reminder> {
  const res = await apiPost(`/applications/${applicationId}/reminders`, payload, undefined, {
    headers: idemHeaders(idempotencyKey),
  });
  return asJson<Reminder>(res, 'Failed to create reminder');
}

export async function updateReminder(
  applicationId: string,
  reminderId: string,
  payload: Partial<ReminderCreate>
): Promise<Reminder> {
  const res = await apiPatch(`/applications/${applicationId}/reminders/${reminderId}`, payload);
  return asJson<Reminder>(res, 'Failed to update reminder');
}

export async function snoozeReminder(
  applicationId: string,
  reminderId: string,
  payload: { until?: string; preset?: string }
): Promise<Reminder> {
  const res = await apiPost(
    `/applications/${applicationId}/reminders/${reminderId}/snooze`,
    payload
  );
  return asJson<Reminder>(res, 'Failed to snooze reminder');
}

export async function cancelReminder(applicationId: string, reminderId: string): Promise<void> {
  await asJson(
    await apiDelete(`/applications/${applicationId}/reminders/${reminderId}`),
    'Failed to cancel reminder'
  );
}

// -- interviews -------------------------------------------------------------

export async function listInterviews(applicationId: string): Promise<Interview[]> {
  const res = await apiFetch(`/applications/${applicationId}/interviews`, {
    credentials: 'include',
  });
  if (res.status === 404) return [];
  return asJson<Interview[]>(res, 'Failed to load interviews');
}

export async function createInterview(
  applicationId: string,
  payload: InterviewCreate,
  idempotencyKey?: string
): Promise<Interview> {
  const res = await apiPost(`/applications/${applicationId}/interviews`, payload, undefined, {
    headers: idemHeaders(idempotencyKey),
  });
  return asJson<Interview>(res, 'Failed to schedule interview');
}

export async function updateInterview(
  applicationId: string,
  interviewId: string,
  payload: Partial<InterviewCreate>
): Promise<Interview> {
  const res = await apiPatch(`/applications/${applicationId}/interviews/${interviewId}`, payload);
  return asJson<Interview>(res, 'Failed to update interview');
}

export async function cancelInterview(applicationId: string, interviewId: string): Promise<void> {
  await asJson(
    await apiDelete(`/applications/${applicationId}/interviews/${interviewId}`),
    'Failed to cancel interview'
  );
}

/** Absolute URL to the interview's ICS download (opened in a new tab). */
export function interviewIcsUrl(interviewId: string): string {
  return `${API_BASE}/interviews/${interviewId}.ics`;
}

// -- agenda -----------------------------------------------------------------

export async function getAgenda(cursor?: string, limit = 20): Promise<AgendaResponse> {
  const params = new URLSearchParams();
  if (cursor) params.set('cursor', cursor);
  params.set('limit', String(limit));
  const res = await apiFetch(`/agenda?${params.toString()}`, { credentials: 'include' });
  if (res.status === 404) return { items: [], next_cursor: null };
  return asJson<AgendaResponse>(res, 'Failed to load agenda');
}

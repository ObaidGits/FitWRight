/**
 * Application answers - the questions job forms ask you.
 *
 * The browser extension reports every field a form asked for. Anything it could
 * not answer arrives here as `needs_answer`, and answering it once in Settings
 * teaches every future form.
 *
 * A field carries either its own `value` or a `profile_path` pointing into your
 * Profile - never both. When it points, the server resolves the live Profile
 * value and flags it `from_profile`, so editing your Profile updates the answer
 * everywhere instead of leaving a stale copy here.
 */
import { apiFetch } from './client';

const PREFIX = '/application-fields';

export type FieldType =
  | 'text'
  | 'textarea'
  | 'select'
  | 'radio'
  | 'checkbox'
  | 'date'
  | 'number'
  | 'file';

export type FieldStatus = 'needs_answer' | 'answered' | 'ignored';

export interface ApplicationField {
  id: string;
  /** The label exactly as the site wrote it. */
  label: string;
  label_normalized: string;
  /** Other wordings folded into this one question. */
  synonyms: string[];
  field_type: FieldType;
  /** The choices the form offered, so we can render the same ones. */
  options: string[];
  value: unknown | null;
  profile_path: string | null;
  /** True when `value` came from your Profile rather than being stored here. */
  from_profile: boolean;
  scope: 'global' | 'company';
  company: string | null;
  status: FieldStatus;
  source: 'learned' | 'user' | 'builtin';
  /** A screening question where a wrong answer can auto-reject you. */
  is_knockout: boolean;
  times_seen: number;
  last_seen_at: string | null;
  last_seen_url: string | null;
  last_seen_ats: string | null;
}

export interface FieldUpdate {
  value?: unknown;
  field_type?: FieldType;
  scope?: 'global' | 'company';
  company?: string | null;
  status?: FieldStatus;
  profile_path?: string | null;
  label?: string;
}

export async function listApplicationFields(
  params?: { status?: FieldStatus },
  signal?: AbortSignal,
): Promise<ApplicationField[]> {
  const qs = params?.status ? `?status_filter=${encodeURIComponent(params.status)}` : '';
  const res = await apiFetch(`${PREFIX}${qs}`, { method: 'GET', signal });
  if (!res.ok) throw new Error(`Loading answers failed: ${res.status}`);
  return res.json();
}

/** Counts for the nav badge, without fetching every answer. */
export interface FieldSummary {
  needs_answer: number;
  answered: number;
  total: number;
}

export async function getFieldSummary(signal?: AbortSignal): Promise<FieldSummary> {
  const res = await apiFetch(`${PREFIX}/summary`, { method: 'GET', signal });
  if (!res.ok) throw new Error(`Loading the answer count failed: ${res.status}`);
  return res.json();
}

/** One common application question the Profile cannot answer yet. */
export interface MissingField {
  key: string;
  label: string;
  /** essential | common | eligibility - see the backend for what each costs. */
  group: 'essential' | 'common' | 'eligibility';
}

/** How much of a typical form the Profile can fill. */
export interface Readiness {
  covered: number;
  total: number;
  missing: MissingField[];
  has_resume: boolean;
}

export async function getAutofillReadiness(signal?: AbortSignal): Promise<Readiness> {
  const res = await apiFetch(`${PREFIX}/readiness`, { method: 'GET', signal });
  if (!res.ok) throw new Error(`Loading profile readiness failed: ${res.status}`);
  return res.json();
}

export async function updateApplicationField(
  id: string,
  patch: FieldUpdate,
): Promise<ApplicationField> {
  const res = await apiFetch(`${PREFIX}/${id}`, {
    method: 'PATCH',
    // apiFetch does not set this - every client in this repo passes it, and
    // without it FastAPI cannot parse the body and answers 422.
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  });
  if (!res.ok) throw new Error(`Saving the answer failed: ${res.status}`);
  return res.json();
}

export async function deleteApplicationField(id: string): Promise<void> {
  const res = await apiFetch(`${PREFIX}/${id}`, { method: 'DELETE' });
  if (!res.ok && res.status !== 404) {
    throw new Error(`Removing the field failed: ${res.status}`);
  }
}

/** Fold a duplicate wording into `id`, keeping this field's answer. */
export async function mergeApplicationFields(
  id: string,
  otherId: string,
): Promise<ApplicationField> {
  const res = await apiFetch(`${PREFIX}/${id}/merge`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ other_id: otherId }),
  });
  if (!res.ok) throw new Error(`Merging failed: ${res.status}`);
  return res.json();
}

// --------------------------------------------------------------------------- //
// Grouping
// --------------------------------------------------------------------------- //

export type FieldGroup =
  | 'Personal & Contact'
  | 'Address'
  | 'Eligibility & Work Authorization'
  | 'Compensation & Availability'
  | 'Education'
  | 'Work History'
  | 'Custom';

/** The order groups appear in Settings, loosely following a form's own order. */
export const FIELD_GROUPS: FieldGroup[] = [
  'Personal & Contact',
  'Address',
  'Eligibility & Work Authorization',
  'Compensation & Availability',
  'Education',
  'Work History',
  'Custom',
];

/**
 * Which section a field belongs to, decided from its label.
 *
 * Grouping is what keeps this page usable: without it, a month of applying turns
 * Settings into a flat list of two hundred raw ATS labels. Order matters here -
 * the first pattern to match wins, so the specific ones are tested before the
 * broad ones ("postal code" must not fall into Personal via "code").
 */
export function groupForField(field: Pick<ApplicationField, 'label_normalized'>): FieldGroup {
  const label = field.label_normalized;

  if (/street|address|city|state|province|postal|zip|pincode|country|county/.test(label)) {
    return 'Address';
  }
  if (
    /sponsor|visa|work authoriz|legally.*work|right to work|citizen|permit|clearance/.test(label)
  ) {
    return 'Eligibility & Work Authorization';
  }
  if (/salary|compensation|ctc|notice period|available|start date|relocat|remote|hybrid/.test(label)) {
    return 'Compensation & Availability';
  }
  if (/degree|education|university|college|school|graduat|gpa|qualification/.test(label)) {
    return 'Education';
  }
  if (/experience|employer|current company|current role|job title|years? of/.test(label)) {
    return 'Work History';
  }
  if (/name|email|phone|mobile|linkedin|github|website|portfolio|pronoun/.test(label)) {
    return 'Personal & Contact';
  }
  return 'Custom';
}

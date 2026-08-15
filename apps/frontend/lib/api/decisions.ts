/**
 * The auto-apply-brain audit trail (Phase 0, .kiro/specs/auto-apply-brain/).
 *
 * Read-only from the frontend's point of view - decisions are written by the
 * extension as it fills a form. This module only surfaces "why did it fill
 * that", for the Applications page's per-application panel.
 */
import { apiFetch } from './client';

const PREFIX = '/application-fields';

export type ValueSource =
  | 'exact_rule'
  | 'cached_classification'
  | 'brain_classification'
  | 'brain_draft'
  | 'user_answer'
  | 'derived_rule';

export type Grade = 'green' | 'yellow' | 'red';

export interface DecisionRecord {
  label: string;
  resolved_target: string | null;
  value_source: ValueSource;
  confidence: number;
  is_knockout: boolean;
  filled: boolean;
  readback_ok: boolean | null;
  grade_contribution: Grade;
}

export interface ApplicationDecisions {
  application_id: string;
  grade: Grade;
  decisions: DecisionRecord[];
  held_reasons: string[];
}

export async function getApplicationDecisions(
  applicationId: string,
  signal?: AbortSignal
): Promise<ApplicationDecisions> {
  const res = await apiFetch(`${PREFIX}/decisions/${encodeURIComponent(applicationId)}`, {
    method: 'GET',
    signal,
  });
  if (!res.ok) throw new Error(`Loading the fill history failed: ${res.status}`);
  return res.json();
}

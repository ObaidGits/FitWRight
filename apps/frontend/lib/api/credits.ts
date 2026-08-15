/**
 * The user's own AI allowance.
 *
 * Mirrors app/routers/credits.py. The shape is deliberately mode-driven rather than
 * a bag of optional numbers: "own key", "unlimited", "disabled" and "credits" are
 * genuinely different states with different copy, and collapsing them into
 * `credits ?? 0` is how a user on their own key ends up being told they have
 * nothing left.
 */
import { apiFetch } from './client';

export type CreditsMode = 'credits' | 'own_key' | 'unlimited' | 'disabled';

export interface CreditAction {
  feature: string;
  label: string;
  remaining: number;
}

export interface MyCredits {
  mode: CreditsMode;
  unlimited: boolean;
  summary: string;
  actions: CreditAction[];
  credits_enabled: boolean;
  available_credits?: number;
  allowance_credits?: number;
  wallet_credits?: number;
  monthly_allowance?: number;
  allowance_period_start?: string | null;
  low?: boolean;
  own_key_is_free?: boolean;
}

export interface UsageItem {
  feature: string;
  credits_charged: number;
  outcome: string;
  created_at: string;
}

export async function getMyCredits(): Promise<MyCredits> {
  const res = await apiFetch('/credits');
  if (!res.ok) throw new Error('Could not load your AI usage');
  return res.json();
}

export async function getMyUsage(limit = 20): Promise<{ items: UsageItem[] }> {
  const res = await apiFetch(`/credits/usage?limit=${limit}`);
  if (!res.ok) throw new Error('Could not load your AI history');
  return res.json();
}

/** Human labels for the ledger's feature keys. */
const FEATURE_LABELS: Record<string, string> = {
  resume_parse: 'Resume upload',
  resume_tailor: 'Tailored resume',
  resume_wizard: 'Resume wizard',
  cover_letter: 'Cover letter',
  outreach: 'Outreach message',
  interview_prep: 'Interview prep',
  enrichment: 'Resume enhancement',
  jd_extract: 'Job analysis',
  discovery_recommend: 'Job recommendations',
  extension_draft: 'Application answer',
  match_score: 'Match score',
};

export function featureLabel(feature: string): string {
  return FEATURE_LABELS[feature] ?? feature.replace(/_/g, ' ');
}

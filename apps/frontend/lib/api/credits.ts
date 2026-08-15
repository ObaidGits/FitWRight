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
  /** What one of these costs. 0 when the operator has made it free. */
  credits_each?: number;
  is_free?: boolean;
  /** null when the action is free (unlimited by definition). */
  remaining: number | null;
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
  /** Credits for one complete application - the headline figure. */
  credits_per_application?: number;
  plan?: PlanSummary;
  /** Searches are capped but never charged, so they are reported separately. */
  search?: SearchAllowance;
}

export interface PlanSummary {
  id: string;
  label: string;
  price_minor: number;
  currency: string;
  monthly_credits: number;
  search_daily_limit: number | null;
  is_free: boolean;
  description: string | null;
}

/**
 * Searches are a rate limit, not a price. Kept separate from the credit balance so the
 * UI can say "back tomorrow" rather than "top up" - credits would not buy a search.
 */
export interface SearchAllowance {
  used_today: number;
  daily_limit: number | null;
  /** null = uncapped. */
  remaining: number | null;
  exhausted: boolean;
}

export interface FeaturePrice {
  feature: string;
  label: string;
  credits: number;
  is_free: boolean;
  description: string | null;
}

export interface PlanOption extends PlanSummary {
  is_current: boolean;
}

export interface Pricing {
  credits_enabled: boolean;
  credits_per_application: number;
  current_plan_id: string;
  features: FeaturePrice[];
  plans: PlanOption[];
}

export interface PurchaseRecord {
  id: string;
  pack_id: string;
  credits: number;
  amount_minor: number;
  currency: string;
  state: string;
  invoice_number: string | null;
  failure_reason: string | null;
  created_at: string;
  granted_at: string | null;
  refunded_at: string | null;
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

/** What every action costs, and the plans on offer. One call so the numbers agree. */
export async function getPricing(): Promise<Pricing> {
  const res = await apiFetch('/credits/pricing');
  if (!res.ok) throw new Error('Could not load pricing');
  return res.json();
}

/** This user's payment history. */
export async function getMyPurchases(limit = 20): Promise<{ items: PurchaseRecord[] }> {
  const res = await apiFetch(`/credits/purchases?limit=${limit}`);
  if (!res.ok) throw new Error('Could not load your payment history');
  return res.json();
}

/** Rupees (or whatever currency) from a minor-unit integer. */
export function formatMoney(minor: number, currency = 'INR'): string {
  const symbol = currency === 'INR' ? '₹' : `${currency} `;
  return `${symbol}${(minor / 100).toLocaleString(undefined, {
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  })}`;
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

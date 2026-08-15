/**
 * Server-side fetch of the public price list.
 *
 * Shared by the marketing homepage section and the /pricing page so both read the same
 * endpoint the same way - two copies of this would eventually disagree about the fallback,
 * and a pricing page that silently renders zeroes is worse than one that renders nothing.
 *
 * Deliberately NOT in `lib/api/credits.ts`: that module talks to the authenticated API
 * through the browser's `apiFetch`, whereas this runs on the server with no session and
 * needs an absolute URL.
 */

export interface PublicPlan {
  id: string;
  label: string;
  price_minor: number;
  currency: string;
  monthly_credits: number;
  search_daily_limit: number | null;
  is_free: boolean;
  description: string | null;
  approx_applications: number;
}

export interface PublicFeature {
  feature: string;
  label: string;
  credits: number;
  is_free: boolean;
  description: string | null;
}

export interface PublicPricing {
  credits_enabled: boolean;
  credits_per_application: number;
  plans: PublicPlan[];
  features: PublicFeature[];
}

/** Cache window for the marketing surfaces. An operator's price edit goes live inside it. */
export const PRICING_REVALIDATE_SECONDS = 300;

export async function fetchPublicPricing(): Promise<PublicPricing | null> {
  const base =
    process.env.NEXT_PUBLIC_API_BASE_URL ??
    process.env.API_BASE_URL ??
    'http://localhost:8000/api/v1';
  try {
    const res = await fetch(`${base}/public/pricing`, {
      next: { revalidate: PRICING_REVALIDATE_SECONDS },
    });
    if (!res.ok) return null;
    const data = (await res.json()) as PublicPricing;
    // A response with no plans is indistinguishable from a failure for rendering
    // purposes: there is nothing to show either way, and callers should fall back.
    if (!data?.plans?.length) return null;
    return data;
  } catch {
    return null;
  }
}

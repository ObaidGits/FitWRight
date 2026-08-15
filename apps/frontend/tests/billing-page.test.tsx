import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { ToastProvider } from '@/components/atelier/toast';

/**
 * The billing page - specifically, the messages a user sees when they run out.
 *
 * The owner's requirement was that running out is stated plainly rather than implied, and
 * the failure mode being guarded against is a generic "out of credits" that leaves the
 * user guessing WHICH thing they can no longer do, or - worse - tells someone who has run
 * out of job SEARCHES to buy credits. Searches are capped, not charged: credits would not
 * buy another one. These tests pin that the two limits stay distinguishable.
 */

vi.mock('@/lib/api/credits', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api/credits')>('@/lib/api/credits');
  return {
    ...actual,
    getMyCredits: vi.fn(),
    getPricing: vi.fn(),
    getMyPurchases: vi.fn(),
  };
});

// The Razorpay top-up card has its own network call and its own tests.
vi.mock('@/components/settings/buy-credits', () => ({ BuyCredits: () => null }));

const { getMyCredits, getPricing, getMyPurchases } = await import('@/lib/api/credits');
const BillingPage = (await import('@/app/(app)/billing/page')).default;

function pricing(over: Record<string, unknown> = {}) {
  return {
    credits_enabled: true,
    credits_per_application: 26,
    current_plan_id: 'free',
    features: [
      {
        feature: 'resume_tailor',
        label: 'Tailored resume',
        credits: 20,
        is_free: false,
        description: null,
      },
      {
        feature: 'match_score',
        label: 'Match score',
        credits: 0,
        is_free: true,
        description: null,
      },
    ],
    plans: [
      {
        id: 'free',
        label: 'Free',
        price_minor: 0,
        currency: 'INR',
        monthly_credits: 300,
        search_daily_limit: 20,
        is_free: true,
        is_current: true,
        description: null,
      },
      {
        id: 'job_hunt',
        label: 'Job Hunt',
        price_minor: 29900,
        currency: 'INR',
        monthly_credits: 2000,
        search_daily_limit: 100,
        is_free: false,
        is_current: false,
        description: null,
      },
    ],
    ...over,
  };
}

function credits(over: Record<string, unknown> = {}) {
  return {
    mode: 'credits',
    unlimited: false,
    summary: 'about 10 more applications',
    available_credits: 260,
    credits_per_application: 26,
    actions: [
      {
        feature: 'resume_tailor',
        label: 'Tailored resumes',
        credits_each: 20,
        is_free: false,
        remaining: 13,
      },
    ],
    credits_enabled: true,
    low: false,
    plan: {
      id: 'free',
      label: 'Free',
      price_minor: 0,
      currency: 'INR',
      monthly_credits: 300,
      search_daily_limit: 20,
      is_free: true,
      description: null,
    },
    search: { used_today: 3, daily_limit: 20, remaining: 17, exhausted: false },
    ...over,
  };
}

function renderPage() {
  return render(
    <ToastProvider>
      <BillingPage />
    </ToastProvider>
  );
}

describe('Billing page', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getPricing).mockResolvedValue(pricing() as never);
    vi.mocked(getMyPurchases).mockResolvedValue({ items: [] } as never);
  });

  it('leads with what the user can do, not a credit count', async () => {
    vi.mocked(getMyCredits).mockResolvedValue(credits() as never);
    renderPage();

    expect(await screen.findByText('about 10 more applications')).toBeInTheDocument();
    // The credit figure is present but secondary.
    expect(screen.getByText(/260 credits left/i)).toBeInTheDocument();
  });

  it('names exactly which action has run out rather than saying "out of credits"', async () => {
    vi.mocked(getMyCredits).mockResolvedValue(
      credits({
        available_credits: 0,
        summary: 'not enough for another application',
        low: true,
        actions: [
          {
            feature: 'outreach',
            label: 'Outreach messages',
            credits_each: 2,
            is_free: false,
            remaining: 0,
          },
        ],
      }) as never
    );
    renderPage();

    expect(await screen.findByText(/You've run out of:/i)).toBeInTheDocument();
    // Scoped to the warning banner: the label also appears in the per-action grid above,
    // and asserting on the page-wide text would pass even if the banner were missing.
    const banner = screen.getByText(/You've run out of:/i);
    expect(banner.textContent).toMatch(/outreach messages/i);
  });

  it('tells a user out of searches to come back tomorrow, NOT to buy credits', async () => {
    // The specific regression guarded against: searches are a rate limit, so offering a
    // top-up here would sell something that does not solve the problem.
    vi.mocked(getMyCredits).mockResolvedValue(
      credits({
        search: { used_today: 20, daily_limit: 20, remaining: 0, exhausted: true },
      }) as never
    );
    renderPage();

    expect(await screen.findByText(/used all of today's job searches/i)).toBeInTheDocument();
    const notice = screen.getByText(/resets at midnight UTC/i);
    expect(notice).toBeInTheDocument();
    expect(notice.textContent).not.toMatch(/credit/i);
  });

  it('shows remaining searches and says they never cost credits', async () => {
    vi.mocked(getMyCredits).mockResolvedValue(credits() as never);
    renderPage();

    expect(await screen.findByText(/17 of 20 job searches left today/i)).toBeInTheDocument();
    // The reassurance sits in the search card specifically. The prices card says the same
    // thing in its own words, so a page-wide match would not prove this one is present.
    const searchCard = screen.getByText(/17 of 20 job searches left today/i).parentElement;
    expect(searchCard?.textContent).toMatch(/never uses credits/i);
  });

  it('marks the current plan and shows the others in applications, not just credits', async () => {
    vi.mocked(getMyCredits).mockResolvedValue(credits() as never);
    renderPage();

    expect(await screen.findByText('Your plan')).toBeInTheDocument();
    // 2000 / 26 = 76 applications for the paid tier.
    expect(screen.getByText(/about 76 applications/i)).toBeInTheDocument();
  });

  it('renders a free action as included rather than as zero remaining', async () => {
    // "0 left" for something free is exactly the wrong reading.
    vi.mocked(getMyCredits).mockResolvedValue(
      credits({
        actions: [
          {
            feature: 'match_score',
            label: 'Match score',
            credits_each: 0,
            is_free: true,
            remaining: null,
          },
        ],
      }) as never
    );
    renderPage();

    expect(await screen.findByText('Included')).toBeInTheDocument();
  });

  it('never shows a limit to a user on their own key', async () => {
    vi.mocked(getMyCredits).mockResolvedValue(
      credits({ mode: 'own_key', unlimited: true, available_credits: undefined }) as never
    );
    renderPage();

    expect(await screen.findByText(/using your own AI key/i)).toBeInTheDocument();
    expect(screen.queryByText(/run out/i)).not.toBeInTheDocument();
  });

  it('shows a payment with its invoice reference and a failed one as not charged', async () => {
    vi.mocked(getMyCredits).mockResolvedValue(credits() as never);
    vi.mocked(getMyPurchases).mockResolvedValue({
      items: [
        {
          id: 'p1',
          pack_id: 'starter',
          credits: 200,
          amount_minor: 14900,
          currency: 'INR',
          state: 'granted',
          invoice_number: 'INV-2026-08-15-abc',
          failure_reason: null,
          created_at: '2026-08-15T10:00:00Z',
          granted_at: '2026-08-15T10:01:00Z',
          refunded_at: null,
        },
        {
          id: 'p2',
          pack_id: 'starter',
          credits: 200,
          amount_minor: 14900,
          currency: 'INR',
          state: 'failed',
          invoice_number: null,
          failure_reason: 'provider_failed',
          created_at: '2026-08-14T10:00:00Z',
          granted_at: null,
          refunded_at: null,
        },
      ],
    } as never);
    renderPage();

    expect(await screen.findByText(/INV-2026-08-15-abc/)).toBeInTheDocument();
    expect(screen.getByText('Credited')).toBeInTheDocument();
    // A failure the user can see is one they will not report as a missing payment.
    expect(screen.getByText(/didn't go through - you weren't charged/i)).toBeInTheDocument();
  });

  it('recovers rather than showing a blank page when the API fails', async () => {
    vi.mocked(getMyCredits).mockRejectedValue(new Error('down'));
    renderPage();

    await waitFor(() =>
      expect(screen.getByText(/Could not load your billing details/i)).toBeInTheDocument()
    );
  });
});

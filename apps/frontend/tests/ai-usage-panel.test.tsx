import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';

import { AiUsagePanel } from '@/components/settings/ai-usage-panel';
import type { MyCredits } from '@/lib/api/credits';

vi.mock('@/lib/api/credits', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api/credits')>('@/lib/api/credits');
  return {
    ...actual,
    getMyCredits: vi.fn(),
    getMyUsage: vi.fn().mockResolvedValue({ items: [] }),
  };
});

const { getMyCredits } = await import('@/lib/api/credits');

function credits(over: Partial<MyCredits> = {}): MyCredits {
  return {
    mode: 'credits',
    unlimited: false,
    summary: 'about 4 more tailored resumes',
    actions: [{ feature: 'resume_tailor', label: 'Tailored resumes', remaining: 4 }],
    credits_enabled: true,
    available_credits: 40,
    low: false,
    own_key_is_free: true,
    ...over,
  };
}

describe('AiUsagePanel', () => {
  beforeEach(() => vi.clearAllMocks());

  it('leads with what the user can still do, not a credit count', async () => {
    vi.mocked(getMyCredits).mockResolvedValue(credits());
    render(<AiUsagePanel />);
    expect(await screen.findByText(/about 4 more tailored resumes/i)).toBeInTheDocument();
  });

  it('never tells a user on their own key that they have a limit', async () => {
    // The specific regression this guards: collapsing the modes into
    // `credits ?? 0` would render "0 left" for someone spending their own money.
    vi.mocked(getMyCredits).mockResolvedValue(
      credits({ mode: 'own_key', unlimited: true, available_credits: undefined })
    );
    render(<AiUsagePanel />);

    expect(await screen.findByText(/using your own AI key/i)).toBeInTheDocument();
    expect(screen.queryByText(/\b0\b/)).not.toBeInTheDocument();
    expect(screen.queryByText(/running low/i)).not.toBeInTheDocument();
  });

  it('offers the free alternative when running low instead of a dead end', async () => {
    vi.mocked(getMyCredits).mockResolvedValue(credits({ low: true, summary: 'not enough' }));
    render(<AiUsagePanel />);

    expect(await screen.findByText(/running low/i)).toBeInTheDocument();
    expect(screen.getByText(/your own AI provider key/i)).toBeInTheDocument();
  });

  it('distinguishes a disabled account from an empty balance', async () => {
    // No amount of waiting or topping up fixes this one, so it must not look like
    // a refill is coming.
    vi.mocked(getMyCredits).mockResolvedValue(
      credits({ mode: 'disabled', summary: 'AI features are turned off for this account.' })
    );
    render(<AiUsagePanel />);

    expect(await screen.findByText(/turned off/i)).toBeInTheDocument();
    expect(screen.getByText(/isn't about running out/i)).toBeInTheDocument();
  });

  it('shows no limit at all while the feature ships dark', async () => {
    vi.mocked(getMyCredits).mockResolvedValue(
      credits({ mode: 'unlimited', unlimited: true, credits_enabled: false })
    );
    render(<AiUsagePanel />);
    expect(await screen.findByText(/AI features are included/i)).toBeInTheDocument();
  });

  it('renders nothing when the balance cannot be read', async () => {
    // A failed read must not imply a balance problem. This codebase already shipped
    // a bug where an AI credential failure rendered as "You are offline".
    vi.mocked(getMyCredits).mockRejectedValue(new Error('network'));
    const { container } = render(<AiUsagePanel />);
    await waitFor(() => expect(container.textContent).not.toMatch(/loading/i));
    expect(container.textContent).not.toMatch(/credits|running low|turned off/i);
  });
});

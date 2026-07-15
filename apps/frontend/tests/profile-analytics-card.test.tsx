import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

/**
 * AnalyticsCard (final vertical): renders non-zero usage counters and hides
 * entirely when there is no activity (avoids a noisy zero-state).
 */

const getProfileAnalyticsMock = vi.fn();
vi.mock('@/lib/api/professional-profile', async (orig) => {
  const actual = (await orig()) as Record<string, unknown>;
  return { ...actual, getProfileAnalytics: (...a: unknown[]) => getProfileAnalyticsMock(...a) };
});

import { AnalyticsCard } from '@/components/profile/analytics-card';

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

afterEach(() => vi.clearAllMocks());

describe('AnalyticsCard', () => {
  it('renders non-zero counters', async () => {
    getProfileAnalyticsMock.mockResolvedValue({
      counters: { resumes_generated: 3, imports: 1, public_views: 0 },
      completeness: 60,
      total_events: 4,
    });
    wrap(<AnalyticsCard />);
    expect(await screen.findByText('Activity')).toBeInTheDocument();
    expect(screen.getByText('Resumes generated')).toBeInTheDocument();
    expect(screen.getByText('3')).toBeInTheDocument();
    // Zero-valued counters are omitted.
    expect(screen.queryByText('Public views')).not.toBeInTheDocument();
  });

  it('renders nothing when there is no activity', async () => {
    getProfileAnalyticsMock.mockResolvedValue({ counters: {}, completeness: 0, total_events: 0 });
    const { container } = wrap(<AnalyticsCard />);
    // Allow the query to resolve, then assert empty render.
    await new Promise((r) => setTimeout(r, 0));
    expect(container.querySelector('dl')).toBeNull();
  });
});

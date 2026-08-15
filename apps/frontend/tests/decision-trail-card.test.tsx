/**
 * DecisionTrailCard - the "how this was filled" panel (auto-apply-brain
 * Phase 0).
 *
 * The behaviour worth pinning: the card is silent, not an error state, for an
 * application with no decision rows - that covers both "applied before this
 * shipped" and "never autofilled" without implying either is broken.
 */
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, expect, it, vi } from 'vitest';

import type { ApplicationDecisions } from '@/lib/api/decisions';

const getApplicationDecisionsMock = vi.fn();

vi.mock('@/lib/api/decisions', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api/decisions')>();
  return {
    ...actual,
    getApplicationDecisions: (...args: unknown[]) => getApplicationDecisionsMock(...args),
  };
});

function payload(overrides: Partial<ApplicationDecisions> = {}): ApplicationDecisions {
  return {
    application_id: 'app-1',
    grade: 'green',
    decisions: [
      {
        label: 'Email',
        resolved_target: 'email',
        value_source: 'exact_rule',
        confidence: 1,
        is_knockout: false,
        filled: true,
        readback_ok: true,
        grade_contribution: 'green',
      },
    ],
    held_reasons: [],
    ...overrides,
  };
}

function wrap(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

describe('DecisionTrailCard', () => {
  it('renders nothing while there are no decisions to show', async () => {
    getApplicationDecisionsMock.mockRejectedValue(new Error('404'));
    const { DecisionTrailCard } = await import('@/components/applications/decision-trail-card');
    const { container } = wrap(<DecisionTrailCard applicationId="app-none" />);

    await waitFor(() => expect(container).toBeEmptyDOMElement());
  });

  it('shows the grade and each field with a human source label', async () => {
    getApplicationDecisionsMock.mockResolvedValue(payload());
    const { DecisionTrailCard } = await import('@/components/applications/decision-trail-card');
    wrap(<DecisionTrailCard applicationId="app-1" />);

    expect(await screen.findByText('Ready to send')).toBeInTheDocument();
    expect(screen.getByText('Email')).toBeInTheDocument();
    expect(screen.getByText('Matched from your Profile')).toBeInTheDocument();
  });

  it('lists held reasons and marks a knockout field distinctly when not green', async () => {
    getApplicationDecisionsMock.mockResolvedValue(
      payload({
        grade: 'red',
        decisions: [
          {
            label: 'Visa status',
            resolved_target: 'visa_status',
            value_source: 'brain_classification',
            confidence: 0.8,
            is_knockout: true,
            filled: true,
            readback_ok: true,
            grade_contribution: 'red',
          },
        ],
        held_reasons: ['Screening question needs review: Visa status'],
      })
    );
    const { DecisionTrailCard } = await import('@/components/applications/decision-trail-card');
    wrap(<DecisionTrailCard applicationId="app-2" />);

    expect(await screen.findByText('Needs review')).toBeInTheDocument();
    expect(screen.getByText(/Screening question needs review/)).toBeInTheDocument();
    expect(screen.getByText(/screening question/)).toBeInTheDocument();
  });

  it('calls out a field that filled but did not read back cleanly', async () => {
    getApplicationDecisionsMock.mockResolvedValue(
      payload({
        grade: 'red',
        decisions: [
          {
            label: 'Phone',
            resolved_target: 'phone',
            value_source: 'exact_rule',
            confidence: 1,
            is_knockout: false,
            filled: true,
            readback_ok: false,
            grade_contribution: 'red',
          },
        ],
      })
    );
    const { DecisionTrailCard } = await import('@/components/applications/decision-trail-card');
    wrap(<DecisionTrailCard applicationId="app-3" />);

    expect(await screen.findByText('Filled, but could not confirm it stuck')).toBeInTheDocument();
  });
});

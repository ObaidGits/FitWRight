/**
 * The apply queue view.
 *
 * The behaviours pinned here are the ones a user would notice breaking: the queue
 * shows in server order with 1-based positions, an empty queue explains itself
 * rather than showing a bare panel, and the submission panel is honest about
 * applications that predate submission recording instead of implying nothing was
 * sent.
 */
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, expect, it, vi } from 'vitest';

import type { QueueItem, SubmissionRecord } from '@/lib/api/apply-queue';

vi.mock('@/components/atelier/toast', () => ({ useToast: () => ({ toast: vi.fn() }) }));

const getQueueMock = vi.fn();
const getSubmissionMock = vi.fn();
const reorderMock = vi.fn();

vi.mock('@/lib/api/apply-queue', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api/apply-queue')>();
  return {
    ...actual,
    getApplyQueue: (...args: unknown[]) => getQueueMock(...args),
    getSubmission: (...args: unknown[]) => getSubmissionMock(...args),
    reorderApplyQueue: (...args: unknown[]) => reorderMock(...args),
  };
});

function item(overrides: Partial<QueueItem> = {}): QueueItem {
  return {
    application_id: 'a1',
    job_id: 'j1',
    company: 'Acme',
    role: 'Backend Engineer',
    position: 0,
    created_at: '2026-08-01T00:00:00Z',
    ...overrides,
  };
}

function record(overrides: Partial<SubmissionRecord> = {}): SubmissionRecord {
  return {
    application_id: 'a1',
    company: 'Acme',
    role: 'Backend Engineer',
    status: 'applied',
    applied_at: '2026-08-02T00:00:00Z',
    answers: {},
    resume_version_id: null,
    submitted_via: null,
    has_record: false,
    ...overrides,
  };
}

async function renderQueue() {
  const { ApplyQueue } = await import('@/components/applications/apply-queue');
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ApplyQueue />
    </QueryClientProvider>
  );
}

describe('ApplyQueue', () => {
  it('lists the queue in server order with 1-based positions', async () => {
    getQueueMock.mockResolvedValue({
      items: [
        item({ application_id: 'a1', role: 'First Role', position: 0 }),
        item({ application_id: 'a2', role: 'Second Role', position: 1 }),
      ],
      total: 2,
    });
    await renderQueue();

    expect(await screen.findByText('First Role')).toBeInTheDocument();
    expect(screen.getByText('Second Role')).toBeInTheDocument();
    // Positions read as "next up", not as array indexes.
    expect(screen.getByText('1')).toBeInTheDocument();
    expect(screen.getByText('2')).toBeInTheDocument();
  });

  it('explains an empty queue instead of showing a bare panel', async () => {
    getQueueMock.mockResolvedValue({ items: [], total: 0 });
    await renderQueue();
    expect(await screen.findByText('Nothing queued')).toBeInTheDocument();
    expect(screen.getByText(/work down the list/)).toBeInTheDocument();
  });

  it('offers to open whatever is next', async () => {
    getQueueMock.mockResolvedValue({ items: [item({ application_id: 'top' })], total: 1 });
    await renderQueue();
    const link = await screen.findByRole('link', { name: /Open next/ });
    expect(link).toHaveAttribute('href', '/applications/top');
  });

  it('gives every row a labelled drag handle so it works without a mouse', async () => {
    getQueueMock.mockResolvedValue({ items: [item({ role: 'Backend Engineer' })], total: 1 });
    await renderQueue();
    expect(
      await screen.findByRole('button', { name: 'Reorder Backend Engineer' })
    ).toBeInTheDocument();
  });

  it('does not pretend a missing company or role is known', async () => {
    getQueueMock.mockResolvedValue({
      items: [item({ company: null, role: null })],
      total: 1,
    });
    await renderQueue();
    expect(await screen.findByText('Role not recorded')).toBeInTheDocument();
    expect(screen.getByText('Company not recorded')).toBeInTheDocument();
  });

  it('surfaces a load failure with a retry rather than an empty list', async () => {
    getQueueMock.mockRejectedValue(new Error('backend down'));
    await renderQueue();
    expect(await screen.findByText('Could not load your queue')).toBeInTheDocument();
  });

  it('is honest about applications that predate submission recording', async () => {
    getQueueMock.mockResolvedValue({ items: [item()], total: 1 });
    getSubmissionMock.mockResolvedValue(record({ has_record: false }));
    await renderQueue();

    (await screen.findByText('What I submitted')).click();
    await waitFor(() =>
      expect(
        screen.getByText(/before FitWright started keeping submission records/)
      ).toBeInTheDocument()
    );
  });

  it('shows the answers and resume version that were actually sent', async () => {
    getQueueMock.mockResolvedValue({ items: [item()], total: 1 });
    getSubmissionMock.mockResolvedValue(
      record({
        has_record: true,
        submitted_via: 'extension',
        resume_version_id: 'v3',
        answers: { 'Notice period': '30 days' },
      })
    );
    await renderQueue();

    (await screen.findByText('What I submitted')).click();
    await waitFor(() => expect(screen.getByText('extension')).toBeInTheDocument());
    expect(screen.getByText('v3')).toBeInTheDocument();
    expect(screen.getByText(/Notice period:/)).toBeInTheDocument();
    expect(screen.getByText('30 days')).toBeInTheDocument();
  });
});

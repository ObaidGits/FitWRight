import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

/**
 * Resume editor — identity-based item reordering.
 * Moving an experience item must (a) change the saved order and (b) preserve
 * fields the UI doesn't edit (stable id, location) — the guarantee of the
 * source-carrying, order-driven previewData rebuild.
 */

vi.mock('next/navigation', () => ({
  useParams: () => ({ id: 'r1' }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() }),
}));

const updateResumeMock = vi.fn().mockResolvedValue(undefined);
const refetchMock = vi.fn();

vi.mock('@/lib/api/resume', async (orig) => {
  const actual = (await orig()) as Record<string, unknown>;
  return { ...actual, updateResume: (...a: unknown[]) => updateResumeMock(...a) };
});

const DATA = {
  resume_id: 'r1',
  raw_resume: { processing_status: 'ready' },
  processed_resume: {
    personalInfo: { name: 'Jane' },
    summary: 'Summary',
    workExperience: [
      {
        id: 1,
        title: 'First Job',
        company: 'Acme',
        location: 'NYC',
        years: '2020',
        description: ['a'],
      },
      {
        id: 2,
        title: 'Second Job',
        company: 'Beta',
        location: 'LA',
        years: '2021',
        description: ['b'],
      },
    ],
    personalProjects: [],
    education: [],
    additional: { technicalSkills: [] },
  },
  parent_id: null,
  cover_letter: null,
  outreach_message: null,
  interview_prep: null,
};

vi.mock('@/features/resumes/hooks', () => ({
  useResume: () => ({ data: DATA, isLoading: false, isError: false, refetch: refetchMock }),
}));

const toastMock = vi.fn();
vi.mock('@/components/atelier/toast', async (orig) => {
  const actual = (await orig()) as Record<string, unknown>;
  return { ...actual, useToast: () => ({ toast: toastMock }) };
});

// Stub heavy/irrelevant children so the test focuses on reorder + save.
vi.mock('@/components/resume/render-template', () => ({ RenderTemplate: () => null }));
vi.mock('@/components/resume/export-button', () => ({ ExportButton: () => null }));
vi.mock('@/components/resume/version-history-panel', () => ({ VersionHistoryPanel: () => null }));
vi.mock('@/components/ai/ask-ai-dialog', () => ({ AskAiDialog: () => null }));
vi.mock('@/components/resume/generated-doc-card', () => ({ GeneratedDocCard: () => null }));
vi.mock('@/components/resume/interview-prep-card', () => ({ InterviewPrepCard: () => null }));
vi.mock('@/components/resume/jd-match-card', () => ({ JdMatchCard: () => null }));
vi.mock('@/components/common/unsaved-changes-guard', () => ({ UnsavedChangesGuard: () => null }));
vi.mock('@/components/resilience/recovery-banner', () => ({ RecoveryBanner: () => null }));

import ResumeEditorPage from '@/app/(app)/resumes/[id]/page';

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ResumeEditorPage />
    </QueryClientProvider>
  );
}

afterEach(() => {
  vi.clearAllMocks();
});

describe('Resume editor — item reordering', () => {
  it('swaps experience order on move-down and preserves non-edited fields on save', async () => {
    renderPage();

    // Two experience items render; move the first ("First Job") down.
    expect(screen.getByDisplayValue('First Job')).toBeInTheDocument();
    const moveDownButtons = screen.getAllByLabelText('Move down');
    fireEvent.click(moveDownButtons[0]);

    // Save becomes enabled once dirty.
    const saveBtn = screen.getByRole('button', { name: /^save$/i });
    await waitFor(() => expect(saveBtn).not.toBeDisabled());
    fireEvent.click(saveBtn);

    await waitFor(() => expect(updateResumeMock).toHaveBeenCalled());
    const [, payload] = updateResumeMock.mock.calls[0];
    const exp = payload.workExperience;
    // Order swapped…
    expect(exp[0].title).toBe('Second Job');
    expect(exp[1].title).toBe('First Job');
    // …and non-edited fields preserved with each moved item.
    expect(exp[0].location).toBe('LA');
    expect(exp[0].id).toBe(2);
    expect(exp[1].location).toBe('NYC');
    expect(exp[1].id).toBe(1);
  });

  it('disables move-up on the first item and move-down on the last', () => {
    renderPage();
    expect(screen.getAllByLabelText('Move up')[0]).toBeDisabled();
    const downs = screen.getAllByLabelText('Move down');
    expect(downs[downs.length - 1]).toBeDisabled();
  });
});

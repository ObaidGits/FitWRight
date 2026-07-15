import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

/**
 * Save flow: the user chooses to set the resume as master (when they have none)
 * or just save a regular resume; a pre-existing master is never auto-replaced.
 */

const pushMock = vi.fn();
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: pushMock, replace: vi.fn(), back: vi.fn() }),
}));

// Mutable status so each test can toggle whether a master already exists.
let statusData: { llm_configured: boolean; has_master_resume?: boolean } = {
  llm_configured: true,
};
vi.mock('@/features/home/hooks', () => ({
  useSystemStatus: () => ({ data: statusData }),
}));

vi.mock('@/components/resume/render-template', () => ({ RenderTemplate: () => null }));
vi.mock('@/components/atelier/toast', async (orig) => {
  const actual = (await orig()) as Record<string, unknown>;
  return { ...actual, useToast: () => ({ toast: vi.fn() }) };
});
vi.mock('@/lib/api/resume-wizard', async (orig) => {
  const actual = (await orig()) as Record<string, unknown>;
  return {
    ...actual,
    postResumeWizardTurn: vi.fn(),
    finalizeResumeWizard: vi.fn(),
    prefillResumeWizard: vi.fn(),
  };
});

import WizardPage from '@/app/(app)/wizard/page';
import {
  createInitialResumeWizardState,
  finalizeResumeWizard,
  type ResumeWizardState,
} from '@/lib/api/resume-wizard';

const mockedFinalize = vi.mocked(finalizeResumeWizard);

afterEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  statusData = { llm_configured: true };
});

function seedReviewDraft() {
  const base = createInitialResumeWizardState();
  const state: ResumeWizardState = {
    ...base,
    step: 'review',
    resume_data: {
      ...base.resume_data,
      personalInfo: { ...base.resume_data.personalInfo, name: 'Jane Doe' },
    },
  };
  localStorage.setItem(
    'fitwright-draft:resume-wizard',
    JSON.stringify({ value: state, savedAt: Date.now() })
  );
}

describe('Wizard save — master choice', () => {
  it('offers "set as master" (default on) when the user has no master', async () => {
    statusData = { llm_configured: true, has_master_resume: false };
    mockedFinalize.mockResolvedValueOnce({
      message: 'Master resume created.',
      request_id: 'r',
      resume_id: 'abc',
      processing_status: 'ready',
      is_master: true,
    });
    seedReviewDraft();
    render(<WizardPage />);

    const checkbox = await screen.findByRole('checkbox', { name: /set as my master resume/i });
    expect(checkbox).toBeChecked();

    fireEvent.click(screen.getByRole('button', { name: /save resume/i }));
    await waitFor(() => expect(mockedFinalize).toHaveBeenCalledWith(expect.anything(), true));
  });

  it('lets the user opt out of master and just save a regular resume', async () => {
    statusData = { llm_configured: true, has_master_resume: false };
    mockedFinalize.mockResolvedValueOnce({
      message: 'Resume saved.',
      request_id: 'r',
      resume_id: 'abc',
      processing_status: 'ready',
      is_master: false,
    });
    seedReviewDraft();
    render(<WizardPage />);

    fireEvent.click(await screen.findByRole('checkbox', { name: /set as my master resume/i }));
    fireEvent.click(screen.getByRole('button', { name: /save resume/i }));
    await waitFor(() => expect(mockedFinalize).toHaveBeenCalledWith(expect.anything(), false));
  });

  it('saves a regular resume (no master toggle) when a master already exists', async () => {
    statusData = { llm_configured: true, has_master_resume: true };
    mockedFinalize.mockResolvedValueOnce({
      message: 'Resume saved.',
      request_id: 'r',
      resume_id: 'abc',
      processing_status: 'ready',
      is_master: false,
    });
    seedReviewDraft();
    render(<WizardPage />);

    // No master checkbox; an explanatory note instead.
    expect(await screen.findByText(/already have a master resume/i)).toBeInTheDocument();
    expect(
      screen.queryByRole('checkbox', { name: /set as my master resume/i })
    ).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /save resume/i }));
    await waitFor(() => expect(mockedFinalize).toHaveBeenCalledWith(expect.anything(), false));
  });
});

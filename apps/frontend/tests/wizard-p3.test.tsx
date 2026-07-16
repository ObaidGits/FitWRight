import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';

/**
 * Phase P3 — prefill from profile (W-P3.2): on first load, if the server returns
 * a profile-prefilled state, the wizard adopts it (jumping past known sections)
 * — but only while still pristine.
 */

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() }),
}));

vi.mock('@tanstack/react-query', async (orig) => {
  const actual = (await orig()) as Record<string, unknown>;
  return { ...actual, useQueryClient: () => ({ invalidateQueries: vi.fn() }) };
});

vi.mock('@/features/home/hooks', () => ({
  useSystemStatus: () => ({ data: { llm_configured: true } }),
}));

vi.mock('@/components/resume/render-template', () => ({
  RenderTemplate: () => null,
}));

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
  prefillResumeWizard,
  type ResumeWizardState,
} from '@/lib/api/resume-wizard';

const mockedPrefill = vi.mocked(prefillResumeWizard);

afterEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
});

function prefilledState(): ResumeWizardState {
  const base = createInitialResumeWizardState();
  return {
    ...base,
    step: 'question',
    current_question: { text: 'Tell me about your education.', section: 'education' },
    progress: { current: 2, total: 6 },
    resume_data: {
      ...base.resume_data,
      personalInfo: {
        ...base.resume_data.personalInfo,
        name: 'Jane Doe',
        title: 'Backend Engineer',
      },
      workExperience: [{ id: 1, title: 'Eng', company: 'Acme', years: '2021', description: ['x'] }],
    },
  };
}

describe('Wizard P3 — prefill from profile (W-P3.2)', () => {
  it('adopts a profile-prefilled state on mount', async () => {
    mockedPrefill.mockResolvedValueOnce(prefilledState());
    render(<WizardPage />);

    // The wizard jumps to the education question (name/experience already known),
    // rather than the intro Identity form.
    await waitFor(() =>
      expect(screen.getByText(/tell me about your education/i)).toBeInTheDocument()
    );
    // The intro name field is not shown because identity is already prefilled.
    expect(screen.queryByLabelText(/your name/i)).not.toBeInTheDocument();
  });

  it('stays on the empty intro when there is no profile to prefill', async () => {
    mockedPrefill.mockResolvedValueOnce(null);
    render(<WizardPage />);
    // Intro Identity form remains.
    expect(await screen.findByLabelText(/your name/i)).toBeInTheDocument();
  });
});

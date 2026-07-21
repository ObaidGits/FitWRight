import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

/**
 * Phase P0 wizard behaviours:
 * - W-P0.2: the full wizard state is persisted to localStorage as a draft once
 *   the user makes real progress, so a reload never loses work.
 * - W-P0.1: pressing Back repopulates the input with the previous answer
 *   (`restored_answer`) so it can be edited, instead of clearing it.
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
  postResumeWizardTurn,
  type ResumeWizardState,
} from '@/lib/api/resume-wizard';

const DRAFT_KEY = 'fitwright-draft:resume-wizard';
const mockedTurn = vi.mocked(postResumeWizardTurn);

afterEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
});

function questionState(overrides: Partial<ResumeWizardState>): ResumeWizardState {
  const base = createInitialResumeWizardState();
  return {
    ...base,
    step: 'question',
    resume_data: {
      ...base.resume_data,
      personalInfo: { ...base.resume_data.personalInfo, name: 'Jane Doe' },
    },
    ...overrides,
  };
}

describe('Wizard P0 - draft persistence (W-P0.2)', () => {
  it('persists the wizard state to localStorage after real progress', async () => {
    mockedTurn.mockResolvedValueOnce({
      state: questionState({
        current_question: { text: 'Where have you worked?', section: 'workExperience' },
        asked_count: 1,
        history: [
          {
            question: "What's your name?",
            answer: 'Jane Doe, senior engineer',
            section: 'intro',
            resume_data_before: createInitialResumeWizardState().resume_data,
          },
        ],
      }),
    });

    render(<WizardPage />);
    expect(localStorage.getItem(DRAFT_KEY)).toBeNull(); // pristine intro is not persisted

    // Intro is a structured Identity form (W-P1.1): fill the name field, Start.
    fireEvent.change(screen.getByLabelText(/your name/i), {
      target: { value: 'Jane Doe' },
    });
    fireEvent.click(screen.getByRole('button', { name: /start/i }));

    await waitFor(() => {
      const raw = localStorage.getItem(DRAFT_KEY);
      expect(raw).toBeTruthy();
      expect(raw as string).toContain('Jane Doe');
    });
  });
});

describe('Wizard P0 - non-destructive Back (W-P0.1)', () => {
  it('repopulates the conversational input with the restored answer on Back', async () => {
    // First turn: submit the structured intro -> advance to a CONVERSATIONAL
    // question (education) that has history, so the Back control appears.
    mockedTurn.mockResolvedValueOnce({
      state: questionState({
        // Land on a structured section (skills) that still shows the Back control
        // because there's history.
        current_question: { text: 'What skills?', section: 'skills' },
        asked_count: 1,
        history: [
          {
            question: 'How would you summarize yourself?',
            answer: 'A seasoned engineer.',
            section: 'summary',
            resume_data_before: createInitialResumeWizardState().resume_data,
          },
        ],
      }),
    });

    render(<WizardPage />);
    fireEvent.change(screen.getByLabelText(/your name/i), { target: { value: 'Jane Doe' } });
    fireEvent.click(screen.getByRole('button', { name: /start/i }));

    const backButton = await screen.findByRole('button', { name: /^back$/i });

    // Second turn (Back): server restores the previous CONVERSATIONAL answer
    // (summary is the one free-text section) into the textarea.
    mockedTurn.mockResolvedValueOnce({
      state: questionState({
        current_question: { text: 'How would you summarize yourself?', section: 'summary' },
        asked_count: 0,
        history: [],
        restored_answer: 'A seasoned engineer.',
      }),
    });

    fireEvent.click(backButton);

    await waitFor(() => {
      expect(screen.getByPlaceholderText(/type your answer/i)).toHaveValue('A seasoned engineer.');
    });
  });
});

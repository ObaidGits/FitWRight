import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

/**
 * Phase P1 structured sections (W-P1.1/W-P1.2/W-P1.3):
 * - Identity/Contact/Skills are discrete fields/chips, submitted via the `structured`
 *   action (no LLM), with the confirmed values in the payload.
 * - Skill suggestions are confirmable (added on tap), never auto-applied.
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
import {
  isValidEmail,
  isValidUrlish,
  applyStructuredToResume,
} from '@/app/(app)/wizard/structured-sections';

const mockedTurn = vi.mocked(postResumeWizardTurn);

afterEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
});

function skillsState(): ResumeWizardState {
  const base = createInitialResumeWizardState();
  return {
    ...base,
    step: 'question',
    current_question: { text: 'What skills do you want to include?', section: 'skills' },
    inferred_skills: ['Docker'],
    resume_data: {
      ...base.resume_data,
      personalInfo: { ...base.resume_data.personalInfo, name: 'Jane Doe' },
    },
  };
}

describe('Wizard P1 — structured Identity (W-P1.1)', () => {
  it('gates Start until a name is entered and posts a structured turn to contact', async () => {
    mockedTurn.mockResolvedValue({ state: skillsState() });
    render(<WizardPage />);

    const start = screen.getByRole('button', { name: /start/i });
    expect(start).toBeDisabled(); // no name yet

    fireEvent.change(screen.getByLabelText(/your name/i), { target: { value: 'Jane Doe' } });
    fireEvent.change(screen.getByLabelText(/target role/i), {
      target: { value: 'Engineer' },
    });
    expect(start).toBeEnabled();

    fireEvent.click(start);

    await waitFor(() => expect(mockedTurn).toHaveBeenCalled());
    const call = mockedTurn.mock.calls[0][0];
    expect(call.action).toBe('structured');
    expect(call.structured?.personal_info?.name).toBe('Jane Doe');
    expect(call.structured?.personal_info?.title).toBe('Engineer');
    expect(call.structured?.next_section).toBe('contact');
  });
});

describe('Wizard P1 — skills chips + confirmable suggestions (W-P1.2)', () => {
  it('adds a suggested skill on tap and submits the confirmed list', async () => {
    // Seed the wizard directly on the skills section via a recovered draft
    // (also exercises W-P0.2 rehydration) to avoid navigating the whole flow.
    localStorage.setItem(
      'fitwright-draft:resume-wizard',
      JSON.stringify({ value: skillsState(), savedAt: Date.now() })
    );
    render(<WizardPage />);

    // On the skills section: the inferred skill shows as a suggestion.
    const suggestion = await screen.findByRole('button', { name: /docker/i });
    fireEvent.click(suggestion); // confirm the suggestion → becomes a chip

    // Also add a typed skill.
    fireEvent.change(screen.getByLabelText(/add a skill/i), { target: { value: 'Python' } });
    fireEvent.keyDown(screen.getByLabelText(/add a skill/i), { key: 'Enter' });

    mockedTurn.mockResolvedValueOnce({ state: skillsState() });
    fireEvent.click(screen.getByRole('button', { name: /continue/i }));

    await waitFor(() => expect(mockedTurn).toHaveBeenCalled());
    const skillsCall = mockedTurn.mock.calls[0][0];
    expect(skillsCall.action).toBe('structured');
    expect(skillsCall.structured?.technical_skills).toEqual(
      expect.arrayContaining(['Docker', 'Python'])
    );
  });
});

describe('structured-sections helpers', () => {
  it('validates email and urlish values', () => {
    expect(isValidEmail('jane@example.com')).toBe(true);
    expect(isValidEmail('nope')).toBe(false);
    expect(isValidUrlish('linkedin.com/in/jane')).toBe(true);
    expect(isValidUrlish('not a url')).toBe(false);
  });

  it('applies a structured update to resume data for the optimistic preview', () => {
    const base = createInitialResumeWizardState().resume_data;
    const next = applyStructuredToResume(base, {
      personal_info: { name: 'Jane' },
      technical_skills: ['Python', 'SQL'],
    });
    expect(next.personalInfo?.name).toBe('Jane');
    expect(next.additional?.technicalSkills).toEqual(['Python', 'SQL']);
    // Original is not mutated.
    expect(base.personalInfo?.name).toBe('');
  });
});

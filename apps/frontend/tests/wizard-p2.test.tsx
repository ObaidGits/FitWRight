import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

/**
 * Phase P2 wizard behaviours:
 * - W-P2.1/W-P2.2: structured Education card with progressive disclosure.
 * - W-P2.3: live Quality/ATS scoreboard from server-computed scores.
 * - W-P2.5: the progress bar exposes a proper `progressbar` role + ARIA values.
 */

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() }),
}));

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
  educationYearsDisplay,
  applyStructuredToResume,
  composeEducationDescription,
} from '@/app/(app)/wizard/structured-sections';

const DRAFT_KEY = 'fitwright-draft:resume-wizard';
const mockedTurn = vi.mocked(postResumeWizardTurn);

afterEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
});

function educationState(): ResumeWizardState {
  const base = createInitialResumeWizardState();
  return {
    ...base,
    step: 'question',
    current_question: { text: 'Tell me about your education.', section: 'education' },
    resume_data: {
      ...base.resume_data,
      personalInfo: { ...base.resume_data.personalInfo, name: 'Jane Doe' },
    },
    progress: { current: 1, total: 6 },
    scores: {
      completeness: 42,
      ats: 55,
      sections: [{ section: 'identity', level: 'strong' }],
    },
  };
}

function seedDraft(state: ResumeWizardState) {
  localStorage.setItem(DRAFT_KEY, JSON.stringify({ value: state, savedAt: Date.now() }));
}

describe('Wizard P2 — structured Education card (W-P2.1/W-P2.2/N3)', () => {
  it('progressively discloses advanced fields and submits a structured education entry', async () => {
    seedDraft(educationState());
    render(<WizardPage />);

    // Essentials are visible; advanced fields are hidden until expanded.
    const institution = await screen.findByLabelText(/school \/ university/i);
    expect(screen.queryByLabelText(/grade type/i)).not.toBeInTheDocument();

    fireEvent.change(institution, { target: { value: 'MIT' } });
    fireEvent.change(screen.getByLabelText(/degree/i), { target: { value: 'BSc CS' } });

    // Progressive disclosure (N3).
    fireEvent.click(screen.getByRole('button', { name: /add details/i }));
    const gradeType = await screen.findByLabelText(/grade type/i);
    fireEvent.change(gradeType, { target: { value: 'gpa' } });
    fireEvent.change(screen.getByLabelText(/^score$/i), { target: { value: '3.9' } });

    mockedTurn.mockResolvedValueOnce({ state: educationState() });
    fireEvent.click(screen.getByRole('button', { name: /continue/i }));

    await waitFor(() => expect(mockedTurn).toHaveBeenCalled());
    const call = mockedTurn.mock.calls[0][0];
    expect(call.action).toBe('structured');
    expect(call.structured?.education?.institution).toBe('MIT');
    expect(call.structured?.education?.gradeType).toBe('gpa');
    expect(call.structured?.education?.score).toBe('3.9');
  });
});

describe('Wizard P2 — live scoreboard + accessibility (W-P2.3/W-P2.5)', () => {
  it('shows Quality/ATS scores and an accessible progressbar', async () => {
    seedDraft(educationState());
    render(<WizardPage />);

    await screen.findByText(/school \/ university/i);

    // Scoreboard (W-P2.3).
    expect(screen.getByText('42%')).toBeInTheDocument();
    expect(screen.getByText('55%')).toBeInTheDocument();

    // Accessible progressbar (W-P2.5).
    const bar = screen.getByRole('progressbar', { name: /resume completion/i });
    expect(bar).toHaveAttribute('aria-valuenow');
    expect(bar).toHaveAttribute('aria-valuemin', '0');
    expect(bar).toHaveAttribute('aria-valuemax', '100');
  });
});

describe('education helpers', () => {
  it('formats the years display from structured year fields', () => {
    expect(educationYearsDisplay({ startYear: '2019', endYear: '2023' })).toBe('2019 - 2023');
    expect(educationYearsDisplay({ startYear: '2021', currentlyStudying: true })).toBe(
      '2021 - Present'
    );
    expect(educationYearsDisplay({ startYear: '2020' })).toBe('2020');
  });

  it('appends a structured education entry to the optimistic preview', () => {
    const base = createInitialResumeWizardState().resume_data;
    const next = applyStructuredToResume(base, {
      education: { institution: 'MIT', degree: 'BSc', startYear: '2019', endYear: '2023' },
    });
    expect(next.education?.length).toBe(1);
    expect(next.education?.[0].institution).toBe('MIT');
    expect(next.education?.[0].years).toBe('2019 - 2023');
    expect(base.education?.length ?? 0).toBe(0); // original not mutated
  });

  it('composes structured extras into the rendered description (no data loss)', () => {
    const desc = composeEducationDescription({
      specialization: 'Machine Learning',
      gradeType: 'gpa',
      score: '3.9',
      achievements: ["Dean's List", 'Valedictorian'],
    });
    expect(desc).toContain('Specialization: Machine Learning');
    expect(desc).toContain('GPA: 3.9');
    expect(desc).toContain("Dean's List");
    expect(desc).toContain('Valedictorian');
    // Empty extras -> empty string (no stray separators).
    expect(composeEducationDescription({})).toBe('');
  });
});

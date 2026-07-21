import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

/**
 * InterviewPrepCard (ported into the atelier editor):
 * - Non-tailored resumes see an explainer, no generate control.
 * - Tailored resumes generate structured prep; the result renders as sections
 *   and can be shown/hidden.
 */

const generateInterviewPrepMock = vi.fn();
vi.mock('@/lib/api/resume', () => ({
  generateInterviewPrep: (...a: unknown[]) => generateInterviewPrepMock(...a),
}));

const toastMock = vi.fn();
vi.mock('@/components/atelier/toast', async (orig) => {
  const actual = (await orig()) as Record<string, unknown>;
  return { ...actual, useToast: () => ({ toast: toastMock }) };
});

import { InterviewPrepCard } from '@/components/resume/interview-prep-card';

const SAMPLE = {
  role_fit_analysis: ['Strong backend match'],
  resume_questions: [
    {
      question: 'Tell me about your API work',
      focus_area: 'backend',
      suggested_answer_points: ['REST', 'auth'],
    },
  ],
  project_follow_ups: [],
  skill_gaps: [
    {
      skill: 'Kubernetes',
      why_it_matters: 'Ops-heavy role',
      preparation_suggestion: 'Do a tutorial',
    },
  ],
  talking_points: ['Led a migration'],
};

afterEach(() => {
  vi.clearAllMocks();
});

describe('InterviewPrepCard', () => {
  it('shows an explainer and no generate control for a non-tailored resume', () => {
    render(<InterviewPrepCard resumeId="r1" initialPrep={null} isTailored={false} />);
    expect(screen.getByText(/Tailor this resume to a job first/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /generate/i })).not.toBeInTheDocument();
  });

  it('generates prep and renders the structured sections', async () => {
    generateInterviewPrepMock.mockResolvedValue(SAMPLE);
    const onGenerated = vi.fn();
    render(
      <InterviewPrepCard resumeId="r1" initialPrep={null} isTailored onGenerated={onGenerated} />
    );

    fireEvent.click(screen.getByRole('button', { name: /generate interview prep/i }));

    await waitFor(() =>
      expect(screen.getByText('Tell me about your API work')).toBeInTheDocument()
    );
    expect(generateInterviewPrepMock).toHaveBeenCalledWith('r1');
    expect(screen.getByText('Role fit')).toBeInTheDocument();
    expect(screen.getByText('Skill gaps')).toBeInTheDocument();
    expect(screen.getByText('Kubernetes')).toBeInTheDocument();
    expect(onGenerated).toHaveBeenCalled();
    // After generating, the primary action becomes Regenerate.
    expect(screen.getByRole('button', { name: /regenerate/i })).toBeInTheDocument();
  });

  it('shows the stage timeline (not a bare spinner) while generating', async () => {
    // Never resolves -> stays generating.
    generateInterviewPrepMock.mockReturnValue(new Promise(() => {}));
    render(<InterviewPrepCard resumeId="r1" initialPrep={null} isTailored />);

    fireEvent.click(screen.getByRole('button', { name: /generate interview prep/i }));

    await waitFor(() => expect(screen.getByText('Analyzing role fit')).toBeInTheDocument());
    expect(screen.getByText('Preparing project follow-ups')).toBeInTheDocument();
    // The generate button is hidden while the timeline is shown.
    expect(
      screen.queryByRole('button', { name: /generate interview prep/i })
    ).not.toBeInTheDocument();
  });

  it('renders existing prep and toggles visibility', () => {
    render(<InterviewPrepCard resumeId="r1" initialPrep={SAMPLE} isTailored />);
    // Collapsed by default: content hidden, Show toggle present.
    expect(screen.queryByText('Led a migration')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /show/i }));
    expect(screen.getByText('Led a migration')).toBeInTheDocument();
  });
});

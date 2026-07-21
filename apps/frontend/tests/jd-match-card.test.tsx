import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

/**
 * JdMatchCard (ported into the atelier editor):
 * - Non-tailored resumes see an explainer, no check control.
 * - Tailored resumes fetch the JD, compute a match rate, and highlight matches
 *   against the live-edited resume.
 */

const fetchJobDescriptionMock = vi.fn();
vi.mock('@/lib/api/resume', () => ({
  fetchJobDescription: (...a: unknown[]) => fetchJobDescriptionMock(...a),
}));

const toastMock = vi.fn();
vi.mock('@/components/atelier/toast', async (orig) => {
  const actual = (await orig()) as Record<string, unknown>;
  return { ...actual, useToast: () => ({ toast: toastMock }) };
});

import { JdMatchCard } from '@/components/resume/jd-match-card';
import type { ResumeData } from '@/components/dashboard/resume-component';

const RESUME = {
  summary: 'Backend engineer skilled in Python and Postgres.',
  workExperience: [
    {
      id: 1,
      title: 'Engineer',
      company: 'Acme',
      years: '2020',
      description: ['Built Python services'],
    },
  ],
  education: [],
  personalProjects: [],
  additional: { technicalSkills: ['Python', 'Docker'] },
} as unknown as ResumeData;

afterEach(() => {
  vi.clearAllMocks();
});

describe('JdMatchCard', () => {
  it('shows an explainer and no check control for a non-tailored resume', () => {
    render(<JdMatchCard resumeId="r1" resumeData={RESUME} isTailored={false} />);
    expect(screen.getByText(/Tailor this resume to a job first/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /keyword match/i })).not.toBeInTheDocument();
  });

  it('fetches the JD, shows match stats, and highlights matched keywords', async () => {
    fetchJobDescriptionMock.mockResolvedValue({
      job_id: 'j1',
      content: 'We need a Python engineer with Kubernetes and Postgres experience.',
    });
    render(<JdMatchCard resumeId="r1" resumeData={RESUME} isTailored />);

    fireEvent.click(screen.getByRole('button', { name: /check keyword match/i }));

    await waitFor(() => expect(screen.getByText(/matched/i)).toBeInTheDocument());
    expect(fetchJobDescriptionMock).toHaveBeenCalledWith('r1');
    // "python" and "postgres" from the JD appear in the resume -> highlighted <mark>.
    const marks = document.querySelectorAll('mark');
    const marked = Array.from(marks).map((m) => m.textContent?.toLowerCase());
    expect(marked).toContain('python');
    // The JD text is shown too.
    expect(screen.getByText('Job description')).toBeInTheDocument();
  });
});

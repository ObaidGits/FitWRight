import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

/**
 * W-P2.2 hybrid Experience/Project cards: structured fields + AI-drafted bullets
 * + paste-to-parse. Verifies the submitted structured payload and the AI assists.
 */

vi.mock('@/lib/api/resume-wizard', async (orig) => {
  const actual = (await orig()) as Record<string, unknown>;
  return { ...actual, assistResumeWizard: vi.fn() };
});

import {
  ExperienceCard,
  ProjectCard,
  applyStructuredToResume,
  composeYears,
} from '@/app/(app)/wizard/structured-sections';
import {
  assistResumeWizard,
  createInitialResumeWizardState,
  type ResumeWizardStructuredUpdate,
} from '@/lib/api/resume-wizard';

const mockedAssist = vi.mocked(assistResumeWizard);

afterEach(() => vi.clearAllMocks());

function renderExperience() {
  let last: ResumeWizardStructuredUpdate | null = null;
  let valid = false;
  render(
    <ExperienceCard
      section="workExperience"
      data={createInitialResumeWizardState().resume_data}
      onChange={(u) => {
        last = u;
      }}
      onValidityChange={(v) => {
        valid = v;
      }}
    />
  );
  return {
    get last() {
      return last;
    },
    get valid() {
      return valid;
    },
  };
}

describe('ExperienceCard — manual structured entry', () => {
  it('submits discrete company/title/dates as one structured experience', async () => {
    const ctx = renderExperience();
    fireEvent.change(screen.getByLabelText(/^title$/i), {
      target: { value: 'Full Stack Engineer Intern' },
    });
    fireEvent.change(screen.getByLabelText(/^company$/i), { target: { value: 'TechStax' } });
    fireEvent.change(screen.getByLabelText(/^location$/i), { target: { value: 'Remote' } });
    fireEvent.change(screen.getByLabelText(/^start$/i), { target: { value: 'Jul 2025' } });
    fireEvent.change(screen.getByLabelText(/^end$/i), { target: { value: 'Jan 2026' } });

    await waitFor(() => {
      const exp = ctx.last?.experiences?.[0];
      expect(exp?.company).toBe('TechStax');
      expect(exp?.title).toBe('Full Stack Engineer Intern');
      expect(exp?.location).toBe('Remote');
      expect(exp?.years).toBe('Jul 2025 – Jan 2026');
    });
  });

  it('drafts bullets with AI from a plain description', async () => {
    mockedAssist.mockResolvedValueOnce({
      bullets: ['Built FastAPI services', 'Cut latency 30%'],
      entries: [],
    });
    renderExperience();
    fireEvent.change(screen.getByLabelText(/what did you do/i), {
      target: { value: 'I built backend APIs and reduced latency.' },
    });
    fireEvent.click(screen.getByRole('button', { name: /draft with ai/i }));

    await waitFor(() =>
      expect(mockedAssist).toHaveBeenCalledWith(
        expect.objectContaining({ kind: 'draft_bullets', section: 'workExperience' })
      )
    );
    await screen.findByDisplayValue('Built FastAPI services');
    expect(screen.getByDisplayValue('Cut latency 30%')).toBeInTheDocument();
  });

  it('parses a pasted multi-role blob into structured entries', async () => {
    mockedAssist.mockResolvedValueOnce({
      bullets: [],
      entries: [
        {
          title: 'FS Eng Intern',
          company: 'TechStax',
          years: 'Jul 2025 – Jan 2026',
          description: ['A'],
        },
        { title: 'FS Dev', company: 'Outbro', years: 'Nov 2023 – Jun 2025', description: ['B'] },
      ],
    });
    const ctx = renderExperience();
    fireEvent.click(screen.getByRole('button', { name: /paste & auto-fill/i }));
    fireEvent.change(screen.getByLabelText(/paste your experience/i), {
      target: { value: 'TechStax\nRemote\nFS Eng Intern\nJul 2025 – Jan 2026' },
    });
    fireEvent.click(screen.getByRole('button', { name: /extract fields/i }));

    await waitFor(() => expect(ctx.last?.experiences?.length).toBe(2));
    expect(ctx.last?.experiences?.map((e) => e.company)).toEqual(['TechStax', 'Outbro']);
  });
});

describe('ProjectCard', () => {
  it('submits a structured project entry', async () => {
    let last: ResumeWizardStructuredUpdate | null = null;
    render(
      <ProjectCard
        data={createInitialResumeWizardState().resume_data}
        onChange={(u) => {
          last = u;
        }}
        onValidityChange={() => {}}
      />
    );
    fireEvent.change(screen.getByLabelText(/project name/i), { target: { value: 'Sidecar' } });
    await waitFor(() => expect(last?.projects?.[0]?.name).toBe('Sidecar'));
  });
});

describe('experience helpers', () => {
  it('composeYears handles ranges and current', () => {
    expect(composeYears('Jul 2025', 'Jan 2026')).toBe('Jul 2025 – Jan 2026');
    expect(composeYears('Jul 2025', '', true)).toBe('Jul 2025 – Present');
    expect(composeYears('2020')).toBe('2020');
  });

  it('appends experiences/projects to the optimistic preview, skipping empties', () => {
    const base = createInitialResumeWizardState().resume_data;
    const next = applyStructuredToResume(base, {
      experiences: [
        { title: 'Eng', company: 'Acme', years: '2021' },
        { title: '', company: '' }, // empty -> skipped
      ],
      projects: [{ name: 'Alpha' }, { name: '' }],
    });
    expect(next.workExperience?.length).toBe(1);
    expect(next.workExperience?.[0].company).toBe('Acme');
    expect(next.personalProjects?.length).toBe(1);
    expect(base.workExperience?.length ?? 0).toBe(0); // not mutated
  });
});

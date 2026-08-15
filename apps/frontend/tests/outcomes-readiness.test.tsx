/**
 * Outcomes, readiness, and the mobile nav.
 *
 * These pin the judgements that make the new surfaces honest rather than merely
 * present:
 *
 * * a reply rate is not shown until the sample supports it, and the UI must say
 *   what is missing instead of printing a bare dash;
 * * an application still in flight is reported as waiting, not as a failure;
 * * readiness counts what autofill can actually fill, and calls out eligibility
 *   gaps as the ones that cost the user on every form;
 * * the mobile bottom bar is built by href, so adding a sidebar destination can
 *   never silently push a tab off a phone again.
 */
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, expect, it, vi } from 'vitest';

import { ToastProvider } from '@/components/atelier/toast';
import type { Outcomes as OutcomesData, ResumeOutcome } from '@/lib/api/apply-queue';
import type { Readiness } from '@/lib/api/application-fields';
import type { ProfileData } from '@/lib/api/professional-profile';

const getOutcomesMock = vi.fn();
const getReadinessMock = vi.fn();
const getSummaryMock = vi.fn();
const getProfessionalProfileMock = vi.fn();

vi.mock('@/lib/api/apply-queue', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api/apply-queue')>();
  return { ...actual, getOutcomes: (...a: unknown[]) => getOutcomesMock(...a) };
});

vi.mock('@/lib/api/application-fields', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api/application-fields')>();
  return {
    ...actual,
    getAutofillReadiness: (...a: unknown[]) => getReadinessMock(...a),
    getFieldSummary: (...a: unknown[]) => getSummaryMock(...a),
    listApplicationFields: () => Promise.resolve([]),
  };
});

vi.mock('@/lib/api/professional-profile', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api/professional-profile')>();
  return {
    ...actual,
    getProfessionalProfile: (...a: unknown[]) => getProfessionalProfileMock(...a),
  };
});

function emptyIdentity(): ProfileData['identity'] {
  return {
    name: '',
    headline: '',
    currentRole: '',
    currentCompany: '',
    yearsExperience: null,
    industry: '',
    careerStage: '',
    targetRoles: [],
    careerObjective: '',
    employmentStatus: '',
    availability: '',
    remotePreference: '',
    relocation: null,
    noticePeriod: '',
    workAuthorization: '',
    visaStatus: '',
    preferredLocations: [],
    salaryExpectation: '',
    careerVisibility: 'private',
    email: '',
    phone: '',
    location: '',
    timezone: '',
    website: null,
    linkedin: null,
    github: null,
    avatarUrl: null,
    address: { line1: '', line2: '', city: '', state: '', postalCode: '', country: '' },
  };
}

getProfessionalProfileMock.mockResolvedValue({
  version: 1,
  data: {
    identity: emptyIdentity(),
    summary: '',
    workExperience: [],
    education: [],
    personalProjects: [],
    skills: { technical: [], soft: [], languages: [], tools: [] },
    certifications: [],
    aiMemory: { tone: '', writingStyle: '', targetCompanies: [], targetIndustries: [] },
  } as unknown as ProfileData,
});

function resumeRow(overrides: Partial<ResumeOutcome> = {}): ResumeOutcome {
  return {
    resume_id: 'r1',
    name: 'backend-heavy.pdf',
    sent: 6,
    replied: 3,
    concluded: 6,
    rate: 0.5,
    ...overrides,
  };
}

function outcomes(overrides: Partial<OutcomesData> = {}): OutcomesData {
  return {
    resumes: [resumeRow()],
    min_sample: 3,
    sent: 6,
    replied: 3,
    ...overrides,
  };
}

function readiness(overrides: Partial<Readiness> = {}): Readiness {
  return {
    covered: 18,
    total: 21,
    missing: [],
    has_resume: true,
    ...overrides,
  };
}

function wrap(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>{ui}</ToastProvider>
    </QueryClientProvider>
  );
}

describe('Outcomes', () => {
  it('shows a reply rate once the sample supports it', async () => {
    getOutcomesMock.mockResolvedValue(outcomes());
    const { Outcomes } = await import('@/components/applications/outcomes');
    wrap(<Outcomes />);

    expect(await screen.findByText('backend-heavy.pdf')).toBeInTheDocument();
    expect(screen.getByText('50%')).toBeInTheDocument();
    expect(screen.getByText(/6 sent · 3 replied/)).toBeInTheDocument();
  });

  it('withholds the rate below the threshold and says what is needed', async () => {
    getOutcomesMock.mockResolvedValue(
      outcomes({
        resumes: [resumeRow({ sent: 1, replied: 1, concluded: 1, rate: null })],
        sent: 1,
        replied: 1,
      })
    );
    const { Outcomes } = await import('@/components/applications/outcomes');
    wrap(<Outcomes />);

    // "100%" off one application would be a lie dressed as a finding.
    expect(await screen.findByText('needs 3 finished')).toBeInTheDocument();
    expect(screen.queryByText('100%')).not.toBeInTheDocument();
  });

  it('reports in-flight applications as waiting, not as failures', async () => {
    getOutcomesMock.mockResolvedValue(
      outcomes({ resumes: [resumeRow({ sent: 8, replied: 3, concluded: 6, rate: 0.5 })] })
    );
    const { Outcomes } = await import('@/components/applications/outcomes');
    wrap(<Outcomes />);

    expect(await screen.findByText(/2 still waiting/)).toBeInTheDocument();
  });

  it('explains itself when nothing has been sent', async () => {
    getOutcomesMock.mockResolvedValue(outcomes({ resumes: [], sent: 0, replied: 0 }));
    const { Outcomes } = await import('@/components/applications/outcomes');
    wrap(<Outcomes />);

    expect(await screen.findByText('No sent applications yet')).toBeInTheDocument();
  });
});

describe('Answers page readiness card', () => {
  it('reports coverage as a fraction and a labelled progress bar', async () => {
    getReadinessMock.mockResolvedValue(readiness());
    getSummaryMock.mockResolvedValue({ needs_answer: 0, answered: 0, total: 0 });
    const AnswersPage = (await import('@/app/(app)/answers/page')).default;
    wrap(<AnswersPage />);

    expect(await screen.findByText(/answers 18 of 21/)).toBeInTheDocument();
    const bar = screen.getByRole('progressbar', {
      name: /profile completeness for application forms/i,
    });
    expect(bar).toHaveAttribute('aria-valuenow', '86');
  });

  it('names eligibility gaps and why they keep costing the user', async () => {
    getReadinessMock.mockResolvedValue(
      readiness({
        covered: 19,
        missing: [
          { key: 'work_authorization', label: 'Work authorization', group: 'eligibility' },
          { key: 'salary_expectation', label: 'Salary expectation', group: 'eligibility' },
        ],
      })
    );
    getSummaryMock.mockResolvedValue({ needs_answer: 0, answered: 0, total: 0 });
    const AnswersPage = (await import('@/app/(app)/answers/page')).default;
    wrap(<AnswersPage />);

    expect(await screen.findByText(/Eligibility · 2 missing/)).toBeInTheDocument();
    expect(screen.getAllByText('Work authorization').length).toBeGreaterThan(0);
    expect(screen.getByText(/every form asks you again/i)).toBeInTheDocument();
  });

  it('warns when no resume is uploaded, since file fields cannot be filled', async () => {
    getReadinessMock.mockResolvedValue(readiness({ has_resume: false }));
    getSummaryMock.mockResolvedValue({ needs_answer: 0, answered: 0, total: 0 });
    const AnswersPage = (await import('@/app/(app)/answers/page')).default;
    wrap(<AnswersPage />);

    expect(await screen.findByText(/No resume uploaded yet/)).toBeInTheDocument();
  });

  it('says so plainly when nothing is missing', async () => {
    getReadinessMock.mockResolvedValue(readiness({ covered: 21, missing: [] }));
    getSummaryMock.mockResolvedValue({ needs_answer: 0, answered: 0, total: 0 });
    const AnswersPage = (await import('@/app/(app)/answers/page')).default;
    wrap(<AnswersPage />);

    expect(
      await screen.findByText(/Every question forms usually ask is stored/)
    ).toBeInTheDocument();
  });
});

describe('mobile bottom nav', () => {
  it('renders its tabs by href so a new sidebar entry cannot drop one', async () => {
    vi.doMock('next/navigation', () => ({ usePathname: () => '/home' }));
    const { BottomNav } = await import('@/components/layout/bottom-nav');
    render(<BottomNav />);

    // The bug this replaces: indices 0-3 silently dropped Agenda when Discover
    // was inserted in the middle of PRIMARY_NAV.
    for (const label of ['Home', 'Resumes', 'Discover', 'Applications']) {
      expect(screen.getByRole('link', { name: label })).toBeInTheDocument();
    }
    expect(screen.getByRole('link', { name: 'Tailor to a job' })).toBeInTheDocument();
  });

  it('marks the current tab for assistive technology', async () => {
    vi.doMock('next/navigation', () => ({ usePathname: () => '/applications' }));
    vi.resetModules();
    const { BottomNav } = await import('@/components/layout/bottom-nav');
    render(<BottomNav />);

    await waitFor(() => {
      expect(screen.getByRole('link', { name: 'Applications' })).toHaveAttribute(
        'aria-current',
        'page'
      );
    });
  });
});

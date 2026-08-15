/**
 * EligibilityAnswers - the country-conditional rule toggle (auto-apply-brain
 * Phase 1).
 *
 * The behaviour worth pinning: the checkbox is off by default (existing
 * profiles behave exactly as before), and turning it on reveals exactly two
 * inputs - a same-country value and a default - never a rule builder.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, expect, it, vi } from 'vitest';

import { ToastProvider } from '@/components/atelier/toast';
import type { ProfileData } from '@/lib/api/professional-profile';

const getProfessionalProfileMock = vi.fn();
const saveProfileMock = vi.fn();

vi.mock('@/lib/api/professional-profile', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api/professional-profile')>();
  return {
    ...actual,
    getProfessionalProfile: (...a: unknown[]) => getProfessionalProfileMock(...a),
    saveProfile: (...a: unknown[]) => saveProfileMock(...a),
  };
});

function baseProfile(overrides: Partial<ProfileData['identity']> = {}): ProfileData {
  return {
    identity: {
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
      workAuthorization: 'Indian citizen',
      visaStatus: 'Not required',
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
      conditionalEligibility: {},
      ...overrides,
    },
    summary: '',
    workExperience: [],
    education: [],
    personalProjects: [],
    skills: { technical: [], soft: [], languages: [], tools: [] },
    certifications: [],
    aiMemory: { tone: '', writingStyle: '', targetCompanies: [], targetIndustries: [] },
  } as unknown as ProfileData;
}

function wrap(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>{ui}</ToastProvider>
    </QueryClientProvider>
  );
}

describe('EligibilityAnswers conditional rule toggle', () => {
  it('is off by default and does not show the two rule inputs', async () => {
    getProfessionalProfileMock.mockResolvedValue({ version: 1, data: baseProfile() });
    const { EligibilityAnswers } = await import('@/components/answers/eligibility-answers');
    wrap(<EligibilityAnswers />);

    await screen.findAllByText("Answer depends on the job's country");
    expect(screen.queryByLabelText('If the job is in your own country')).not.toBeInTheDocument();
  });

  it('reveals same-country and default inputs when checked', async () => {
    getProfessionalProfileMock.mockResolvedValue({ version: 1, data: baseProfile() });
    const { EligibilityAnswers } = await import('@/components/answers/eligibility-answers');
    wrap(<EligibilityAnswers />);

    const checkboxes = await screen.findAllByLabelText("Answer depends on the job's country");
    fireEvent.click(checkboxes[0]);

    expect(await screen.findByText('If the job is in your own country')).toBeInTheDocument();
    expect(screen.getByText('Otherwise')).toBeInTheDocument();
  });

  it('renders pre-existing rule values when the profile already has one enabled', async () => {
    getProfessionalProfileMock.mockResolvedValue({
      version: 1,
      data: baseProfile({
        conditionalEligibility: {
          visaStatus: {
            enabled: true,
            default: 'Yes - requires sponsorship',
            same_country_value: 'No - authorized to work',
          },
        },
      }),
    });
    const { EligibilityAnswers } = await import('@/components/answers/eligibility-answers');
    wrap(<EligibilityAnswers />);

    expect(await screen.findByDisplayValue('No - authorized to work')).toBeInTheDocument();
    expect(screen.getByDisplayValue('Yes - requires sponsorship')).toBeInTheDocument();
  });

  it('enables Save once a rule is toggled on', async () => {
    getProfessionalProfileMock.mockResolvedValue({ version: 1, data: baseProfile() });
    const { EligibilityAnswers } = await import('@/components/answers/eligibility-answers');
    wrap(<EligibilityAnswers />);

    const saveButton = await screen.findByRole('button', { name: 'Saved' });
    expect(saveButton).toBeDisabled();

    const checkboxes = await screen.findAllByLabelText("Answer depends on the job's country");
    fireEvent.click(checkboxes[0]);

    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Save answers' })).not.toBeDisabled()
    );
  });
});

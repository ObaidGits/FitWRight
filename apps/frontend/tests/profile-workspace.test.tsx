import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

/**
 * Profile workspace (Professional Profile System, P2):
 * - Loads the canonical profile and renders identity + summary fields.
 * - Save is disabled until the draft is dirty, then persists with the CAS
 *   base_version and shows a success toast.
 * - Generate resume is blocked while there are unsaved edits.
 * - A version-CAS conflict reloads the latest server state (no lost update).
 */

const pushMock = vi.fn();
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: pushMock, replace: vi.fn() }),
}));

const toastMock = vi.fn();
vi.mock('@/components/atelier/toast', async (orig) => {
  const actual = (await orig()) as Record<string, unknown>;
  return { ...actual, useToast: () => ({ toast: toastMock }) };
});

// The workspace surfaces the account avatar (from the account-profile query) in
// Overview, and refreshes the session for the nav badge.
const refreshSessionMock = vi.fn();
vi.mock('@/lib/context/session', () => ({
  useSession: () => ({
    user: { name: 'Ada Lovelace', avatarUrl: null },
    refresh: refreshSessionMock,
  }),
}));
vi.mock('@/lib/api/profile', async (orig) => {
  const actual = (await orig()) as Record<string, unknown>;
  return {
    ...actual,
    getProfile: vi
      .fn()
      .mockResolvedValue({ headline: null, location: null, links: [], avatar_url: null }),
  };
});

const getProfileMock = vi.fn();
const updateProfileMock = vi.fn();
const getCompletenessMock = vi.fn();
const generateResumeMock = vi.fn();

vi.mock('@/lib/api/professional-profile', async (orig) => {
  const actual = (await orig()) as Record<string, unknown>;
  return {
    ...actual,
    getProfessionalProfile: (...a: unknown[]) => getProfileMock(...a),
    updateProfessionalProfile: (...a: unknown[]) => updateProfileMock(...a),
    getProfileCompleteness: (...a: unknown[]) => getCompletenessMock(...a),
    generateResumeFromProfile: (...a: unknown[]) => generateResumeMock(...a),
  };
});

import { ProfileWorkspace } from '@/components/profile/profile-workspace';
import { ProfileConflictError, type ProfileData } from '@/lib/api/professional-profile';

function emptyProfileData(overrides: Partial<ProfileData> = {}): ProfileData {
  return {
    identity: {
      name: 'Ada Lovelace',
      headline: 'Engineer',
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
      email: 'ada@example.com',
      phone: '',
      location: '',
      timezone: '',
      website: null,
      linkedin: null,
      github: null,
      avatarUrl: null,
    },
    summary: 'Builds systems.',
    workExperience: [],
    education: [],
    personalProjects: [],
    skills: { technical: [], soft: [], languages: [], tools: [] },
    certifications: [],
    achievements: [],
    interests: [],
    links: [],
    customSections: {},
    sectionMeta: [],
    aiMemory: {
      writingStyle: '',
      tone: '',
      atsPreference: '',
      templatePreference: '',
      targetCompanies: [],
      targetIndustries: [],
      dos: [],
      donts: [],
    },
    meta: { schemaVersion: 1, source: 'manual', lastImportedResumeId: null, provenance: {} },
    ...overrides,
  };
}

function renderWorkspace() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ProfileWorkspace />
    </QueryClientProvider>
  );
}

afterEach(() => {
  vi.clearAllMocks();
});

describe('ProfileWorkspace', () => {
  it('loads and renders identity + summary', async () => {
    getProfileMock.mockResolvedValue({
      data: emptyProfileData(),
      completeness: 40,
      version: 3,
      updated_at: null,
    });
    getCompletenessMock.mockResolvedValue({ score: 40, suggestions: [] });

    renderWorkspace();

    expect(await screen.findByDisplayValue('Ada Lovelace')).toBeInTheDocument();
    expect(screen.getByDisplayValue('Builds systems.')).toBeInTheDocument();
  });

  it('surfaces profile photo management (upload/replace/remove) on the profile page', async () => {
    getProfileMock.mockResolvedValue({
      data: emptyProfileData(),
      completeness: 40,
      version: 3,
      updated_at: null,
    });
    getCompletenessMock.mockResolvedValue({ score: 40, suggestions: [] });

    renderWorkspace();

    // The shared AvatarUploader is present in the Overview section.
    await screen.findByDisplayValue('Ada Lovelace');
    expect(screen.getByText('Profile photo')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /upload profile photo/i })).toBeInTheDocument();
  });

  it('enables Save only when dirty and persists with base_version', async () => {
    getProfileMock.mockResolvedValue({
      data: emptyProfileData(),
      completeness: 40,
      version: 3,
      updated_at: null,
    });
    getCompletenessMock.mockResolvedValue({ score: 40, suggestions: [] });
    updateProfileMock.mockResolvedValue({
      data: emptyProfileData({ summary: 'Edited.' }),
      completeness: 45,
      version: 4,
      updated_at: null,
    });

    renderWorkspace();

    const summary = await screen.findByDisplayValue('Builds systems.');
    // Save is disabled until an edit makes the draft dirty.
    const saveBtn = screen.getByRole('button', { name: /Saved/i });
    expect(saveBtn).toBeDisabled();

    fireEvent.change(summary, { target: { value: 'Edited.' } });
    const dirtySave = screen.getByRole('button', { name: /Save changes/i });
    expect(dirtySave).toBeEnabled();

    fireEvent.click(dirtySave);
    await waitFor(() => expect(updateProfileMock).toHaveBeenCalledTimes(1));
    expect(updateProfileMock).toHaveBeenCalledWith(
      expect.objectContaining({ summary: 'Edited.' }),
      3
    );
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith(expect.objectContaining({ title: 'Profile saved' }))
    );
  });

  it('blocks Generate resume while there are unsaved edits', async () => {
    getProfileMock.mockResolvedValue({
      data: emptyProfileData(),
      completeness: 40,
      version: 3,
      updated_at: null,
    });
    getCompletenessMock.mockResolvedValue({ score: 40, suggestions: [] });

    renderWorkspace();

    const summary = await screen.findByDisplayValue('Builds systems.');
    fireEvent.change(summary, { target: { value: 'Dirty edit.' } });

    const generateBtn = screen.getByRole('button', { name: /Generate resume/i });
    expect(generateBtn).toBeDisabled();
    expect(generateResumeMock).not.toHaveBeenCalled();
  });

  it('reloads on a version-CAS conflict without losing the server state', async () => {
    getProfileMock.mockResolvedValue({
      data: emptyProfileData(),
      completeness: 40,
      version: 3,
      updated_at: null,
    });
    getCompletenessMock.mockResolvedValue({ score: 40, suggestions: [] });
    updateProfileMock.mockRejectedValue(
      new ProfileConflictError(5, {
        data: emptyProfileData({ summary: 'Server wins.' }),
        completeness: 50,
        version: 5,
        updated_at: null,
      })
    );

    renderWorkspace();

    const summary = await screen.findByDisplayValue('Builds systems.');
    fireEvent.change(summary, { target: { value: 'My edit.' } });
    fireEvent.click(screen.getByRole('button', { name: /Save changes/i }));

    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith(
        expect.objectContaining({ title: 'Profile changed elsewhere' })
      )
    );
    // The draft is reset to the server's current version.
    expect(await screen.findByDisplayValue('Server wins.')).toBeInTheDocument();
  });
});

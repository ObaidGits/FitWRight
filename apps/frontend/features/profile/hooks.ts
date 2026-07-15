'use client';

/**
 * Professional Profile data hooks (docs/architecture/PROFILE_SYSTEM_PLAN.md).
 *
 * The profile is the canonical career document; edits save through a
 * version-CAS PATCH so a concurrent write can never be silently lost (a stale
 * base version throws {@link ProfileConflictError}, which the UI reconciles).
 * A successful save refreshes the profile, completeness, history, and the
 * resume lists (a generated resume can appear elsewhere).
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import {
  aiSuggest,
  applyImport,
  applySync,
  generateResumeFromProfile,
  getProfessionalProfile,
  getProfileAnalytics,
  getProfileCompleteness,
  getPublicationState,
  listProfileVersions,
  previewImport,
  previewSync,
  publishProfile,
  restoreProfileVersion,
  unpublishProfile,
  updateAiMemory,
  updateProfessionalProfile,
  type AiMemory,
  type ProfileCompletenessResponse,
  type ProfileData,
  type ProfileResponse,
  type PublicTheme,
} from '@/lib/api/professional-profile';
import { invalidateResumeLists, queryKeys } from '@/lib/query/client';

export function useProfile() {
  return useQuery<ProfileResponse>({
    queryKey: queryKeys.professionalProfile,
    queryFn: getProfessionalProfile,
  });
}

export function useProfileCompleteness() {
  return useQuery<ProfileCompletenessResponse>({
    queryKey: queryKeys.professionalProfileCompleteness,
    queryFn: getProfileCompleteness,
  });
}

export function useSaveProfile() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ data, baseVersion }: { data: ProfileData; baseVersion: number }) =>
      updateProfessionalProfile(data, baseVersion),
    onSuccess: (updated) => {
      qc.setQueryData(queryKeys.professionalProfile, updated);
      qc.invalidateQueries({ queryKey: queryKeys.professionalProfileCompleteness });
      qc.invalidateQueries({ queryKey: queryKeys.professionalProfileVersions });
    },
  });
}

export function useGenerateResume() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: generateResumeFromProfile,
    onSuccess: (result) => {
      if (result.resume_id) invalidateResumeLists(qc);
    },
  });
}

export function useProfileVersions() {
  return useQuery({
    queryKey: queryKeys.professionalProfileVersions,
    queryFn: () => listProfileVersions(),
  });
}

export function useRestoreProfileVersion() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (versionId: string) => restoreProfileVersion(versionId),
    onSuccess: (updated) => {
      qc.setQueryData(queryKeys.professionalProfile, updated);
      qc.invalidateQueries({ queryKey: queryKeys.professionalProfileCompleteness });
      qc.invalidateQueries({ queryKey: queryKeys.professionalProfileVersions });
    },
  });
}

// --- Import / Merge (P3) ---------------------------------------------------

export function usePreviewImport() {
  return useMutation({
    mutationFn: ({ source, payload }: { source: string; payload: Record<string, unknown> }) =>
      previewImport(source, payload),
  });
}

export function useApplyImport() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: applyImport,
    onSuccess: (result) => {
      qc.setQueryData(queryKeys.professionalProfile, {
        data: result.data,
        completeness: result.completeness,
        version: result.version,
        updated_at: null,
      });
      qc.invalidateQueries({ queryKey: queryKeys.professionalProfileCompleteness });
      qc.invalidateQueries({ queryKey: queryKeys.professionalProfileVersions });
    },
  });
}

// --- AI layer (P5) ---------------------------------------------------------

export function useUpdateAiMemory() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ aiMemory, baseVersion }: { aiMemory: AiMemory; baseVersion: number }) =>
      updateAiMemory(aiMemory, baseVersion),
    onSuccess: (updated) => {
      qc.setQueryData(queryKeys.professionalProfile, updated);
      qc.invalidateQueries({ queryKey: queryKeys.professionalProfileCompleteness });
    },
  });
}

export function useAiSuggest() {
  return useMutation({
    mutationFn: ({
      kind,
      experienceUid,
    }: {
      kind: 'summary' | 'experience_bullets' | 'skills_normalize';
      experienceUid?: string;
    }) => aiSuggest(kind, experienceUid),
  });
}

// --- Public sharing (P7) ---------------------------------------------------

export function usePublicationState() {
  return useQuery({
    queryKey: queryKeys.professionalProfilePublication,
    queryFn: getPublicationState,
  });
}

export function usePublishProfile() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      visibility,
      slug,
      theme,
    }: {
      visibility: 'public' | 'unlisted';
      slug?: string;
      theme?: PublicTheme;
    }) => publishProfile(visibility, { slug, theme }),
    onSuccess: (state) => {
      qc.setQueryData(queryKeys.professionalProfilePublication, state);
    },
  });
}

export function useProfileAnalytics() {
  return useQuery({
    queryKey: queryKeys.professionalProfileAnalytics,
    queryFn: getProfileAnalytics,
  });
}

export function useUnpublishProfile() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: unpublishProfile,
    onSuccess: (state) => {
      qc.setQueryData(queryKeys.professionalProfilePublication, state);
    },
  });
}

// --- Synchronization (P4) --------------------------------------------------

export function usePreviewSync() {
  return useMutation({
    mutationFn: ({ resumeId, includePhoto }: { resumeId: string; includePhoto?: boolean }) =>
      previewSync(resumeId, includePhoto),
  });
}

export function useApplySync() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      resumeId,
      baseVersion,
      includePhoto,
    }: {
      resumeId: string;
      baseVersion: number;
      includePhoto?: boolean;
    }) => applySync(resumeId, baseVersion, includePhoto),
    onSuccess: () => {
      invalidateResumeLists(qc);
    },
  });
}

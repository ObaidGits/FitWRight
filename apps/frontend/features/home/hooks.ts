'use client';

/** Home data hooks (Task 6) — reuse the existing API via TanStack Query. */
import { useQuery } from '@tanstack/react-query';
import { fetchResumeList, type ResumeListItem } from '@/lib/api/resume';
import { listApplications, type ApplicationListResponse } from '@/lib/api/tracker';
import {
  fetchSetupStatus,
  fetchSystemStatus,
  type SetupStatus,
  type SystemStatus,
} from '@/lib/api/config';
import { queryKeys } from '@/lib/query/client';

export function useResumes() {
  return useQuery<ResumeListItem[]>({
    queryKey: queryKeys.resumes,
    queryFn: () => fetchResumeList(false),
  });
}

export function useApplications() {
  return useQuery<ApplicationListResponse>({
    queryKey: queryKeys.applications,
    queryFn: listApplications,
  });
}

export function shouldShowFirstRun(setup: SetupStatus | null | undefined): setup is SetupStatus {
  // Only an authoritative, persisted incomplete status opens onboarding.
  // Resume-list length is deliberately irrelevant because the normal list
  // excludes the master resume; using it caused established master-only users
  // to be misclassified as first-time users.
  return setup?.complete === false;
}

export function useSetupStatus() {
  return useQuery<SetupStatus>({
    queryKey: queryKeys.setup,
    queryFn: fetchSetupStatus,
    // Persisted facts only; mutations invalidate this key explicitly.
    staleTime: 5 * 60_000,
  });
}

export function useSystemStatus() {
  return useQuery<SystemStatus>({
    queryKey: queryKeys.status,
    queryFn: fetchSystemStatus,
    staleTime: 60_000,
  });
}

/** Flatten the grouped application columns into a single list. */
export function flattenApplications(data?: ApplicationListResponse) {
  if (!data) return [];
  return Object.values(data.columns).flat();
}

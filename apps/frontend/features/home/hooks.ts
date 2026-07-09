'use client';

/** Home data hooks (Task 6) — reuse the existing API via TanStack Query. */
import { useQuery } from '@tanstack/react-query';
import { fetchResumeList, type ResumeListItem } from '@/lib/api/resume';
import { listApplications, type ApplicationListResponse } from '@/lib/api/tracker';
import { fetchSystemStatus, type SystemStatus } from '@/lib/api/config';
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

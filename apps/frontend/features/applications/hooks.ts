'use client';

/** Applications pipeline + workspace hooks (Task 9). Reuse tracker API via Query. */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  listApplications,
  getApplicationDetail,
  updateApplication,
  deleteApplication,
  type ApplicationListResponse,
  type ApplicationStatus,
} from '@/lib/api/tracker';
import { queryKeys } from '@/lib/query/client';

export const STATUS_LABELS: Record<ApplicationStatus, string> = {
  saved: 'Saved',
  applied: 'Applied',
  no_response: 'No response',
  response: 'Response',
  interview: 'Interviewing',
  accepted: 'Offer / Accepted',
  rejected: 'Rejected',
};

export function useApplicationsBoard() {
  return useQuery<ApplicationListResponse>({
    queryKey: queryKeys.applications,
    queryFn: listApplications,
  });
}

export function useApplicationDetail(id: string) {
  return useQuery({
    queryKey: queryKeys.application(id),
    queryFn: () => getApplicationDetail(id),
    enabled: !!id,
  });
}

export function useMoveApplication() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, status }: { id: string; status: ApplicationStatus }) =>
      updateApplication(id, { status }),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.applications }),
  });
}

export function useUpdateApplicationNotes() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, notes }: { id: string; notes: string }) => updateApplication(id, { notes }),
    onSuccess: (_d, vars) => {
      qc.invalidateQueries({ queryKey: queryKeys.application(vars.id) });
      qc.invalidateQueries({ queryKey: queryKeys.applications });
    },
  });
}

export function useDeleteApplication() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteApplication(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.applications }),
  });
}

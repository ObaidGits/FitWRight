'use client';

/** Resume library + editor data hooks (Task 7) — reuse existing API via Query. */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  fetchResumeList,
  fetchResume,
  deleteResume,
  retryProcessing,
  type ResumeListItem,
} from '@/lib/api/resume';
import { queryKeys } from '@/lib/query/client';

export function useResumeLibrary() {
  return useQuery<ResumeListItem[]>({
    queryKey: [...queryKeys.resumes, 'library'],
    queryFn: () => fetchResumeList(true),
  });
}

export function useResume(resumeId: string) {
  return useQuery({
    queryKey: queryKeys.resume(resumeId),
    queryFn: () => fetchResume(resumeId),
    enabled: !!resumeId,
  });
}

export function useDeleteResume() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (resumeId: string) => deleteResume(resumeId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.resumes });
    },
  });
}

export function useRetryProcessing() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (resumeId: string) => retryProcessing(resumeId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.resumes });
    },
  });
}

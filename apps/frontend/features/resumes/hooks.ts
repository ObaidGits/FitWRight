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
    // Poll while any resume is still parsing so "Processing" flips to
    // "Ready"/"Failed" without a manual refresh; stop once all are settled.
    refetchInterval: (query) => {
      const items = query.state.data;
      const anyProcessing = items?.some(
        (r) => r.processing_status === 'processing' || r.processing_status === 'pending'
      );
      return anyProcessing ? 4000 : false;
    },
  });
}

export function useResume(resumeId: string) {
  return useQuery({
    queryKey: queryKeys.resume(resumeId),
    queryFn: () => fetchResume(resumeId),
    enabled: !!resumeId,
    // Poll while this resume is still processing so the editor updates when
    // parsing completes (or fails) without requiring a reload.
    refetchInterval: (query) => {
      const status = query.state.data?.raw_resume?.processing_status;
      return status === 'processing' || status === 'pending' ? 4000 : false;
    },
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

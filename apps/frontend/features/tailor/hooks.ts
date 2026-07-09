'use client';

/** Tailor flow data (Task 8). Reuses the existing improve pipeline. */
import { useQuery } from '@tanstack/react-query';
import { fetchResumeList, type ResumeListItem } from '@/lib/api/resume';
import { fetchPromptConfig } from '@/lib/api/config';

export function useTailorResumes() {
  return useQuery<ResumeListItem[]>({
    queryKey: ['resumes', 'tailor-sources'],
    // Include master; only "ready" resumes can be tailored.
    queryFn: () => fetchResumeList(true),
    select: (list) => list.filter((r) => r.processing_status === 'ready'),
  });
}

export function usePromptOptions() {
  return useQuery({ queryKey: ['config', 'prompts'], queryFn: fetchPromptConfig });
}

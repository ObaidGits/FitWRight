'use client';

/** TanStack Query data layer (Task 3.6 / Req 24.3). Single query client for the app. */
import * as React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

export function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        gcTime: 5 * 60_000,
        retry: 1,
        // Auto-refresh when the user returns to the tab so any change made
        // elsewhere (another tab/device, or a background job) is picked up.
        // staleTime still gates this, so it only refetches genuinely stale data.
        refetchOnWindowFocus: true,
        refetchOnReconnect: true,
      },
      mutations: { retry: 0 },
    },
  });
}

export function QueryProvider({ children }: { children: React.ReactNode }) {
  const [client] = React.useState(makeQueryClient);
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

/** Central query-key registry, organized around the object graph. */
export const queryKeys = {
  resumes: ['resumes'] as const,
  resume: (id: string) => ['resumes', id] as const,
  applications: ['applications'] as const,
  application: (id: string) => ['applications', id] as const,
  status: ['status'] as const,
  config: ['config'] as const,
  // P3 productivity surfaces.
  agenda: ['agenda'] as const,
  reminders: (applicationId: string) => ['reminders', applicationId] as const,
  interviews: (applicationId: string) => ['interviews', applicationId] as const,
  profile: ['profile'] as const,
  // Professional Profile System (docs/architecture/PROFILE_SYSTEM_PLAN.md) —
  // distinct from the lightweight account ``profile`` key above.
  professionalProfile: ['professional-profile'] as const,
  professionalProfileCompleteness: ['professional-profile', 'completeness'] as const,
  professionalProfileVersions: ['professional-profile', 'versions'] as const,
  professionalProfilePublication: ['professional-profile', 'publication'] as const,
  professionalProfileAnalytics: ['professional-profile', 'analytics'] as const,
  notificationsUnread: ['notifications', 'unread'] as const,
} as const;

// ---------------------------------------------------------------------------
// Shared invalidation helpers (auto-refresh after create/update/delete).
//
// Resume LIST surfaces (home ['resumes'], library ['resumes','library'], and
// the tailor source picker ['resumes','tailor-sources']) are refreshed WITHOUT
// touching the open editor detail (['resumes', id]) — a blanket ['resumes']
// invalidation would also refetch the detail and could clobber in-progress
// edits. Callers that DO want the detail refreshed (e.g. version restore)
// invalidate ``queryKeys.resume(id)`` explicitly.
// ---------------------------------------------------------------------------
export function invalidateResumeLists(qc: import('@tanstack/react-query').QueryClient): void {
  qc.invalidateQueries({ queryKey: queryKeys.resumes, exact: true });
  qc.invalidateQueries({ queryKey: [...queryKeys.resumes, 'library'] });
  qc.invalidateQueries({ queryKey: [...queryKeys.resumes, 'tailor-sources'] });
}

/** Refresh the application LIST surfaces (board + home count), not the detail. */
export function invalidateApplicationLists(qc: import('@tanstack/react-query').QueryClient): void {
  qc.invalidateQueries({ queryKey: queryKeys.applications, exact: true });
}

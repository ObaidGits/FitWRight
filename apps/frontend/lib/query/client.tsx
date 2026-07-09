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
        refetchOnWindowFocus: false,
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
} as const;

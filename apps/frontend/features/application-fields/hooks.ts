'use client';

/**
 * Hooks for the application-answers registry.
 *
 * Every mutation invalidates the whole list rather than patching the cache: a
 * single answer can move a field out of the review inbox, change its group, and
 * change the inbox count, and a merge deletes a second row entirely. Refetching
 * one small list is cheaper than getting any of that subtly wrong.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import {
  deleteApplicationField,
  getAutofillReadiness,
  getFieldSummary,
  listApplicationFields,
  mergeApplicationFields,
  updateApplicationField,
  type ApplicationField,
  type FieldStatus,
  type FieldSummary,
  type FieldUpdate,
  type Readiness,
} from '@/lib/api/application-fields';
import { getApplicationDecisions, type ApplicationDecisions } from '@/lib/api/decisions';

const keys = {
  all: ['application-fields'] as const,
  list: (status?: FieldStatus) => ['application-fields', status ?? 'all'] as const,
  summary: ['application-fields', 'summary'] as const,
  readiness: ['application-fields', 'readiness'] as const,
  decisions: (applicationId: string) => ['application-fields', 'decisions', applicationId] as const,
};

export function useApplicationFields(status?: FieldStatus) {
  return useQuery<ApplicationField[], Error>({
    queryKey: keys.list(status),
    queryFn: ({ signal }) => listApplicationFields({ status }, signal),
  });
}

/**
 * Counts for the nav badge. Sits under the same `all` key as the list, so
 * answering a question updates the badge through the existing invalidation.
 */
export function useFieldSummary() {
  return useQuery<FieldSummary, Error>({
    queryKey: keys.summary,
    queryFn: ({ signal }) => getFieldSummary(signal),
  });
}

/** How much of a typical application form the Profile can fill. */
export function useAutofillReadiness() {
  return useQuery<Readiness, Error>({
    queryKey: keys.readiness,
    queryFn: ({ signal }) => getAutofillReadiness(signal),
  });
}

/**
 * The audit trail for one application - why each field was filled the way it
 * was, and why the application isn't green if it isn't. Disabled until an id
 * is known, and silent on 404 (an application filled before this shipped, or
 * one that was never autofilled at all, simply has no rows).
 */
export function useApplicationDecisions(applicationId: string | null) {
  return useQuery<ApplicationDecisions, Error>({
    queryKey: keys.decisions(applicationId ?? ''),
    queryFn: ({ signal }) => getApplicationDecisions(applicationId as string, signal),
    enabled: Boolean(applicationId),
    retry: false,
  });
}

export function useUpdateApplicationField() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: FieldUpdate }) =>
      updateApplicationField(id, patch),
    onSuccess: () => void client.invalidateQueries({ queryKey: keys.all }),
  });
}

export function useDeleteApplicationField() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteApplicationField(id),
    onSuccess: () => void client.invalidateQueries({ queryKey: keys.all }),
  });
}

export function useMergeApplicationFields() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, otherId }: { id: string; otherId: string }) =>
      mergeApplicationFields(id, otherId),
    onSuccess: () => void client.invalidateQueries({ queryKey: keys.all }),
  });
}

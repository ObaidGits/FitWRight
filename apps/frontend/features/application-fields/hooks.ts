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
  listApplicationFields,
  mergeApplicationFields,
  updateApplicationField,
  type ApplicationField,
  type FieldStatus,
  type FieldUpdate,
} from '@/lib/api/application-fields';

const keys = {
  all: ['application-fields'] as const,
  list: (status?: FieldStatus) => ['application-fields', status ?? 'all'] as const,
};

export function useApplicationFields(status?: FieldStatus) {
  return useQuery<ApplicationField[], Error>({
    queryKey: keys.list(status),
    queryFn: ({ signal }) => listApplicationFields({ status }, signal),
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

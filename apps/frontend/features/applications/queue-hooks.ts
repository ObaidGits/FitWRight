'use client';

/**
 * Apply queue hooks.
 *
 * Reordering is optimistic. Dragging a card and watching it snap back for a
 * round trip before settling is the kind of lag that makes a queue feel worse
 * than a list, so the new order is applied locally at once and rolled back only
 * if the server rejects it.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import {
  getApplyQueue,
  getOutcomes,
  getSubmission,
  reorderApplyQueue,
  type Outcomes,
  type QueueResponse,
  type SubmissionRecord,
} from '@/lib/api/apply-queue';
import { queryKeys } from '@/lib/query/client';

const queueKey = ['applications', 'queue'] as const;
const outcomesKey = ['applications', 'outcomes'] as const;

export function useApplyQueue() {
  return useQuery<QueueResponse, Error>({
    queryKey: queueKey,
    queryFn: ({ signal }) => getApplyQueue(signal),
  });
}

/** Reply rate per resume. Read-only, so no invalidation of its own. */
export function useOutcomes() {
  return useQuery<Outcomes, Error>({
    queryKey: outcomesKey,
    queryFn: ({ signal }) => getOutcomes(signal),
  });
}

export function useReorderQueue() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (applicationIds: string[]) => reorderApplyQueue(applicationIds),

    onMutate: async (applicationIds) => {
      await client.cancelQueries({ queryKey: queueKey });
      const previous = client.getQueryData<QueueResponse>(queueKey);
      if (previous) {
        const byId = new Map(previous.items.map((item) => [item.application_id, item]));
        const reordered = applicationIds
          .map((id, index) => {
            const item = byId.get(id);
            return item ? { ...item, position: index } : null;
          })
          .filter((item): item is NonNullable<typeof item> => item !== null);
        client.setQueryData<QueueResponse>(queueKey, { ...previous, items: reordered });
      }
      return { previous };
    },

    onError: (_error, _ids, context) => {
      // Put the user's list back exactly as it was rather than leaving it in a
      // state neither they nor the server chose.
      if (context?.previous) client.setQueryData(queueKey, context.previous);
    },

    onSettled: () => {
      void client.invalidateQueries({ queryKey: queueKey });
      // The board shows the same rows, so it has to agree about their order.
      void client.invalidateQueries({ queryKey: queryKeys.applications });
    },
  });
}

export function useSubmission(applicationId: string | null) {
  return useQuery<SubmissionRecord, Error>({
    queryKey: ['applications', applicationId, 'submission'],
    queryFn: ({ signal }) => getSubmission(applicationId as string, signal),
    enabled: Boolean(applicationId),
  });
}

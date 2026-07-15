'use client';

/**
 * Agenda + scheduling data hooks (P3 §E/§F/§G) — TanStack Query.
 *
 * The agenda is a merged, time-ordered feed of upcoming reminders + interviews.
 * Quick actions (snooze, cancel) mutate through the reminder/interview API and
 * invalidate the agenda so the feed re-orders. Optimistic where it reads well
 * (snooze/cancel remove the item immediately, rolling back on error).
 */
import { useInfiniteQuery, useMutation, useQueryClient } from '@tanstack/react-query';

import {
  cancelInterview,
  cancelReminder,
  getAgenda,
  snoozeReminder,
  type AgendaItem,
  type AgendaResponse,
} from '@/lib/api/scheduling';
import { queryKeys } from '@/lib/query/client';

export type { AgendaItem };

export function useAgenda() {
  return useInfiniteQuery<AgendaResponse>({
    queryKey: queryKeys.agenda,
    queryFn: ({ pageParam }) => getAgenda(pageParam as string | undefined),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
}

/** Flatten infinite pages into a single item list. */
export function flattenAgenda(pages?: AgendaResponse[]): AgendaItem[] {
  if (!pages) return [];
  return pages.flatMap((p) => p.items);
}

export function useSnoozeReminder() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      applicationId,
      reminderId,
      preset,
    }: {
      applicationId: string;
      reminderId: string;
      preset: string;
    }) => snoozeReminder(applicationId, reminderId, { preset }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.agenda });
    },
  });
}

export function useCancelAgendaItem() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ item }: { item: AgendaItem }) =>
      item.kind === 'reminder'
        ? cancelReminder(item.application_id, item.id)
        : cancelInterview(item.application_id, item.id),
    // Optimistic: drop the item from every cached page immediately.
    onMutate: async ({ item }) => {
      await qc.cancelQueries({ queryKey: queryKeys.agenda });
      const prev = qc.getQueryData(queryKeys.agenda);
      qc.setQueryData(
        queryKeys.agenda,
        (old: { pages: AgendaResponse[]; pageParams: unknown[] } | undefined) => {
          if (!old) return old;
          return {
            ...old,
            pages: old.pages.map((p) => ({
              ...p,
              items: p.items.filter((i) => !(i.id === item.id && i.kind === item.kind)),
            })),
          };
        }
      );
      return { prev };
    },
    onError: (_err, _vars, ctx) => {
      if (ctx?.prev) qc.setQueryData(queryKeys.agenda, ctx.prev);
    },
    onSettled: () => {
      qc.invalidateQueries({ queryKey: queryKeys.agenda });
    },
  });
}

'use client';

/**
 * Per-application reminder + interview hooks (P3 §E/§F) — TanStack Query.
 *
 * Reads are keyed per application; every mutation invalidates both the per-app
 * list and the global agenda so all surfaces stay consistent.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import {
  cancelInterview,
  cancelReminder,
  createInterview,
  createReminder,
  listInterviews,
  listReminders,
  snoozeReminder,
  updateInterview,
  updateReminder,
  type Interview,
  type InterviewCreate,
  type Reminder,
  type ReminderCreate,
} from '@/lib/api/scheduling';
import { queryKeys } from '@/lib/query/client';

export type { Reminder, Interview };

export function useReminders(applicationId: string) {
  return useQuery<Reminder[]>({
    queryKey: queryKeys.reminders(applicationId),
    queryFn: () => listReminders(applicationId),
  });
}

export function useInterviews(applicationId: string) {
  return useQuery<Interview[]>({
    queryKey: queryKeys.interviews(applicationId),
    queryFn: () => listInterviews(applicationId),
  });
}

function useSchedulingInvalidate(applicationId: string) {
  const qc = useQueryClient();
  return () => {
    qc.invalidateQueries({ queryKey: queryKeys.reminders(applicationId) });
    qc.invalidateQueries({ queryKey: queryKeys.interviews(applicationId) });
    qc.invalidateQueries({ queryKey: queryKeys.agenda });
  };
}

export function useCreateReminder(applicationId: string) {
  const invalidate = useSchedulingInvalidate(applicationId);
  return useMutation({
    mutationFn: (payload: ReminderCreate) => createReminder(applicationId, payload),
    onSuccess: invalidate,
  });
}

export function useUpdateReminder(applicationId: string) {
  const invalidate = useSchedulingInvalidate(applicationId);
  return useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: Partial<ReminderCreate> }) =>
      updateReminder(applicationId, id, payload),
    onSuccess: invalidate,
  });
}

export function useSnoozeReminderInApp(applicationId: string) {
  const invalidate = useSchedulingInvalidate(applicationId);
  return useMutation({
    mutationFn: ({ id, preset }: { id: string; preset: string }) =>
      snoozeReminder(applicationId, id, { preset }),
    onSuccess: invalidate,
  });
}

export function useCancelReminder(applicationId: string) {
  const invalidate = useSchedulingInvalidate(applicationId);
  return useMutation({
    mutationFn: (id: string) => cancelReminder(applicationId, id),
    onSuccess: invalidate,
  });
}

export function useCreateInterview(applicationId: string) {
  const invalidate = useSchedulingInvalidate(applicationId);
  return useMutation({
    mutationFn: (payload: InterviewCreate) => createInterview(applicationId, payload),
    onSuccess: invalidate,
  });
}

export function useUpdateInterview(applicationId: string) {
  const invalidate = useSchedulingInvalidate(applicationId);
  return useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: Partial<InterviewCreate> }) =>
      updateInterview(applicationId, id, payload),
    onSuccess: invalidate,
  });
}

export function useCancelInterview(applicationId: string) {
  const invalidate = useSchedulingInvalidate(applicationId);
  return useMutation({
    mutationFn: (id: string) => cancelInterview(applicationId, id),
    onSuccess: invalidate,
  });
}

/** The browser's IANA timezone, sent with reminders/interviews for DST-correct display. */
export function browserTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
  } catch {
    return 'UTC';
  }
}

/** Convert a `datetime-local` value (local wall time) to a UTC ISO string. */
export function localInputToUtcIso(localValue: string): string {
  // `new Date(localValue)` interprets the value in the browser's local zone.
  return new Date(localValue).toISOString();
}

/** Convert a UTC ISO string to a `datetime-local`-compatible value (local zone). */
export function utcIsoToLocalInput(iso: string): string {
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

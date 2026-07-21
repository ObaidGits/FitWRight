'use client';

/**
 * NotificationPreferences (P3 §B / Requirement 6.1) - per-category delivery.
 *
 * Toggle in-app / email per category and pick a digest cadence. Wired to
 * `GET/PUT /notifications/prefs`; saves are debounced-free (explicit per toggle)
 * with optimistic cache updates. Loading / error states included.
 */
import * as React from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { Card } from '@/components/atelier/card';
import { Switch } from '@/components/atelier/misc';
import { Label } from '@/components/atelier/label';
import {
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
} from '@/components/atelier/select';
import { LoadingSkeleton, ErrorState } from '@/components/atelier/states';
import { useToast } from '@/components/atelier/toast';
import {
  getPrefs,
  updatePrefs,
  type NotificationCategory,
  type NotificationPrefs,
  type DigestMode,
} from '@/lib/api/notifications';

const CATEGORY_LABELS: Record<NotificationCategory, string> = {
  system: 'System (parsing, exports)',
  reminder: 'Follow-up reminders',
  interview: 'Interviews',
  ai: 'AI results',
  security: 'Security',
};

const CATEGORIES = Object.keys(CATEGORY_LABELS) as NotificationCategory[];
const PREFS_KEY = ['notifications', 'prefs'] as const;

export function NotificationPreferences() {
  const { toast } = useToast();
  const qc = useQueryClient();
  const query = useQuery<NotificationPrefs>({ queryKey: PREFS_KEY, queryFn: getPrefs });

  const save = useMutation({
    mutationFn: updatePrefs,
    onSuccess: (updated) => qc.setQueryData(PREFS_KEY, updated),
    onError: () => toast({ title: 'Could not update preferences', variant: 'error' }),
  });

  if (query.isLoading) return <LoadingSkeleton rows={3} />;
  if (query.isError || !query.data)
    return (
      <ErrorState
        description="Could not load notification preferences."
        onRetry={() => query.refetch()}
      />
    );

  const prefs = query.data;

  function setCategory(
    category: NotificationCategory,
    patch: { in_app?: boolean; email?: boolean }
  ) {
    const current = prefs.categories[category];
    save.mutate({
      categories: [
        {
          category,
          in_app: patch.in_app ?? current.in_app,
          email: patch.email ?? current.email,
        },
      ],
    });
  }

  return (
    <Card className="space-y-4 p-6">
      <div>
        <h2 className="text-sm font-semibold text-[var(--muted-foreground)]">Notifications</h2>
        <p className="text-xs text-[var(--muted-foreground)]">
          Choose what you get in-app and by email.
        </p>
      </div>

      <div className="space-y-3">
        {CATEGORIES.map((cat) => {
          const pref = prefs.categories[cat];
          return (
            <div key={cat} className="flex items-center justify-between gap-3">
              <p className="text-sm font-medium">{CATEGORY_LABELS[cat]}</p>
              <div className="flex items-center gap-4">
                <label className="flex items-center gap-1.5 text-xs text-[var(--muted-foreground)]">
                  In-app
                  <Switch
                    checked={pref.in_app}
                    onCheckedChange={(v) => setCategory(cat, { in_app: v })}
                    aria-label={`${CATEGORY_LABELS[cat]} in-app`}
                  />
                </label>
                <label className="flex items-center gap-1.5 text-xs text-[var(--muted-foreground)]">
                  Email
                  <Switch
                    checked={pref.email}
                    onCheckedChange={(v) => setCategory(cat, { email: v })}
                    aria-label={`${CATEGORY_LABELS[cat]} email`}
                  />
                </label>
              </div>
            </div>
          );
        })}
      </div>

      <div className="space-y-1.5">
        <Label>Email digest</Label>
        <Select
          value={prefs.digest}
          onValueChange={(v) => save.mutate({ digest: v as DigestMode })}
        >
          <SelectTrigger aria-label="Email digest frequency">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="off">Off (send emails immediately)</SelectItem>
            <SelectItem value="daily">Daily digest</SelectItem>
            <SelectItem value="weekly">Weekly digest</SelectItem>
          </SelectContent>
        </Select>
        <p className="text-xs text-[var(--muted-foreground)]">
          Batches low-priority email notifications; urgent items always send right away.
        </p>
      </div>
    </Card>
  );
}

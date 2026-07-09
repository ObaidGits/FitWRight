'use client';

/**
 * RecoveryBanner (Task 18 / Req 30.2, 30.4).
 * Non-blocking prompt shown when a newer local draft is found than the saved
 * copy. Restore is explicit; discard removes the draft. Also used for the
 * autosave-conflict prompt (keep mine / take latest).
 */
import * as React from 'react';
import History from 'lucide-react/dist/esm/icons/history';
import { Button } from '@/components/atelier/button';

interface RecoveryBannerProps {
  savedAt: number | null;
  onRestore: () => void;
  onDiscard: () => void;
  /** Copy overrides for the autosave-conflict variant. */
  title?: string;
  restoreLabel?: string;
  discardLabel?: string;
}

function relativeTime(ts: number | null): string {
  if (!ts) return 'recently';
  const diff = Date.now() - ts;
  const mins = Math.round(diff / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins} min ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs} hr ago`;
  return new Date(ts).toLocaleDateString();
}

export function RecoveryBanner({
  savedAt,
  onRestore,
  onDiscard,
  title,
  restoreLabel = 'Restore draft',
  discardLabel = 'Discard',
}: RecoveryBannerProps) {
  return (
    <div
      role="alert"
      className="flex flex-wrap items-center gap-3 rounded-[var(--radius-at-lg)] border border-[var(--at-warning)]/40 bg-[var(--at-warning)]/10 px-4 py-3"
    >
      <History className="h-5 w-5 shrink-0 text-[var(--at-warning)]" />
      <p className="flex-1 text-sm text-[var(--foreground)]">
        {title ?? `We found unsaved changes from ${relativeTime(savedAt)}. Restore them?`}
      </p>
      <div className="flex items-center gap-2">
        <Button size="sm" variant="ghost" onClick={onDiscard}>
          {discardLabel}
        </Button>
        <Button size="sm" onClick={onRestore}>
          {restoreLabel}
        </Button>
      </div>
    </div>
  );
}

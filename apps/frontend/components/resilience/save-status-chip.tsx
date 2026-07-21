'use client';

/**
 * SaveStatusChip (P4 R4.1, R6.5).
 *
 * Communicates autosave status - saved / dirty / saving / retrying / offline /
 * conflict - plus a last-saved relative time. Text + SR label, never color
 * alone (a11y). Drives off the {@link SaveStatus} from the SaveController.
 */
import * as React from 'react';
import Check from 'lucide-react/dist/esm/icons/check';
import Loader from 'lucide-react/dist/esm/icons/loader-circle';
import CloudOff from 'lucide-react/dist/esm/icons/cloud-off';
import RotateCw from 'lucide-react/dist/esm/icons/rotate-cw';
import AlertTriangle from 'lucide-react/dist/esm/icons/triangle-alert';
import Circle from 'lucide-react/dist/esm/icons/circle';
import type { SaveStatus } from '@/lib/resilience/save-controller';

function relativeTime(ts: number | null): string {
  if (!ts) return '';
  const diff = Date.now() - ts;
  const mins = Math.round(diff / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins} min ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs} hr ago`;
  return new Date(ts).toLocaleDateString();
}

const CONFIG: Record<
  SaveStatus,
  {
    label: (rel: string) => string;
    Icon: React.ComponentType<{ className?: string }>;
    spin?: boolean;
    tone: string;
  }
> = {
  idle: { label: () => 'Up to date', Icon: Check, tone: 'text-[var(--muted-foreground)]' },
  saved: {
    label: (rel) => (rel ? `Saved ${rel}` : 'Saved'),
    Icon: Check,
    tone: 'text-[var(--at-success,#16a34a)]',
  },
  dirty: { label: () => 'Unsaved changes', Icon: Circle, tone: 'text-[var(--muted-foreground)]' },
  saving: {
    label: () => 'Saving...',
    Icon: Loader,
    spin: true,
    tone: 'text-[var(--muted-foreground)]',
  },
  retrying: {
    label: () => 'Saved locally, will retry',
    Icon: RotateCw,
    tone: 'text-[var(--at-warning)]',
  },
  offline: {
    label: () => 'Offline - saved locally',
    Icon: CloudOff,
    tone: 'text-[var(--at-warning)]',
  },
  conflict: {
    label: () => 'Conflict - needs review',
    Icon: AlertTriangle,
    tone: 'text-[var(--at-warning)]',
  },
};

export interface SaveStatusChipProps {
  status: SaveStatus;
  lastSavedAt: number | null;
  /**
   * True when this tab is a follower (another tab is the autosave leader). A
   * follower's local `offline`/`retrying` states are not real network failures -
   * the leader owns saving - so we show an accurate, non-alarming label (R7).
   */
  isFollower?: boolean;
}

export function SaveStatusChip({ status, lastSavedAt, isFollower }: SaveStatusChipProps) {
  const followerMasksStatus =
    isFollower && (status === 'offline' || status === 'retrying' || status === 'dirty');
  if (followerMasksStatus) {
    return (
      <span
        role="status"
        aria-live="polite"
        className="inline-flex items-center gap-1.5 text-xs font-medium text-[var(--muted-foreground)]"
      >
        <Check className="h-3.5 w-3.5" aria-hidden="true" />
        <span>Saved locally - syncing in another tab</span>
      </span>
    );
  }
  const cfg = CONFIG[status];
  const rel = relativeTime(lastSavedAt);
  const label = cfg.label(rel);
  return (
    <span
      role="status"
      aria-live="polite"
      className={`inline-flex items-center gap-1.5 text-xs font-medium ${cfg.tone}`}
    >
      <cfg.Icon
        className={`h-3.5 w-3.5 ${cfg.spin ? 'motion-safe:animate-spin' : ''}`}
        aria-hidden="true"
      />
      <span>{label}</span>
    </span>
  );
}

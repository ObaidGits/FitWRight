'use client';

/**
 * Live narration for a search that outlives its own request.
 *
 * Without this the background search would be a downgrade: the button would
 * return instantly and then nothing visible would happen for half a minute. It
 * reports which boards have finished, how many jobs have landed, and — when a
 * board fails — says so instead of silently returning fewer results.
 */

import { Loader2, CheckCircle2, AlertTriangle, RotateCw } from 'lucide-react';

import type { SearchProgress } from '@/lib/api/discovery';

interface SearchProgressBarProps {
  progress: SearchProgress;
  /** Reload the feed. Offered on completion so the user is never stuck. */
  onRefresh?: () => void;
  onDismiss?: () => void;
}

function boardLabel(count: number): string {
  return count === 1 ? '1 board' : `${count} boards`;
}

export function SearchProgressBar({ progress, onRefresh, onDismiss }: SearchProgressBarProps) {
  const { status, sites_total, sites_done, found, saved, failures, error, elapsed_ms } = progress;

  const seconds = Math.round(elapsed_ms / 1000);
  // Fraction of boards finished — the only honest progress signal mid-scrape.
  const pct = sites_total > 0 ? Math.round((sites_done / sites_total) * 100) : 0;

  if (status === 'running') {
    return (
      <div
        className="rounded-[var(--radius-at-lg)] border border-[var(--primary)]/30 bg-[var(--primary)]/10 p-4"
        role="status"
        aria-live="polite"
      >
        <div className="flex items-center gap-3">
          <Loader2 className="h-4 w-4 shrink-0 animate-spin text-[var(--primary)]" />
          <div className="min-w-0 flex-1">
            <p className="text-sm font-medium text-[var(--foreground)]">
              Searching {boardLabel(sites_total)}…{' '}
              {found > 0 ? `${found} jobs so far` : 'this usually takes 15–30 seconds'}
            </p>
            <p className="mt-0.5 text-xs text-[var(--muted-foreground)]">
              {sites_done} of {sites_total} finished · {seconds}s elapsed · you can keep using the
              app
            </p>
          </div>
        </div>
        <div
          className="mt-3 h-1.5 w-full overflow-hidden rounded-full bg-[var(--primary)]/20"
          role="progressbar"
          aria-valuenow={pct}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label="Search progress"
        >
          <div
            className="h-full rounded-full bg-[var(--primary)] transition-all duration-500"
            style={{ width: `${Math.max(pct, 4)}%` }}
          />
        </div>
      </div>
    );
  }

  if (status === 'failed') {
    return (
      <div
        className="rounded-[var(--radius-at-lg)] border border-[var(--destructive)]/30 bg-[var(--destructive)]/10 p-4"
        role="alert"
      >
        <div className="flex items-start gap-3">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-[var(--destructive)]" />
          <div className="min-w-0 flex-1">
            <p className="text-sm font-medium text-[var(--foreground)]">
              The search did not complete
            </p>
            <p className="mt-0.5 text-xs text-[var(--muted-foreground)]">
              {error ?? 'No reason was reported.'}
              {saved > 0 && ` ${saved} jobs were saved before it stopped.`}
            </p>
          </div>
          {onDismiss && (
            <button
              type="button"
              onClick={onDismiss}
              className="text-xs font-medium text-[var(--destructive)] underline"
            >
              Dismiss
            </button>
          )}
        </div>
      </div>
    );
  }

  if (status === 'expired') {
    return (
      <div
        className="rounded-[var(--radius-at-lg)] border border-[var(--border)] bg-[var(--at-surface-2)] p-4"
        role="status"
      >
        <div className="flex items-start gap-3">
          <RotateCw className="mt-0.5 h-4 w-4 shrink-0 text-[var(--muted-foreground)]" />
          <div className="min-w-0 flex-1">
            <p className="text-sm font-medium text-[var(--foreground)]">
              Lost track of this search
            </p>
            <p className="mt-0.5 text-xs text-[var(--muted-foreground)]">
              The server restarted while it was running. Any jobs it had already found are in your
              feed — reload to see them.
            </p>
          </div>
          {onRefresh && (
            <button
              type="button"
              onClick={onRefresh}
              className="text-xs font-medium text-[var(--foreground)] underline"
            >
              Reload feed
            </button>
          )}
        </div>
      </div>
    );
  }

  // done
  const partial = failures.length > 0;
  const stateColor = partial ? 'var(--at-warning)' : 'var(--at-success)';
  return (
    <div
      className={
        partial
          ? 'rounded-[var(--radius-at-lg)] border border-[var(--at-warning)]/30 bg-[var(--at-warning)]/10 p-4'
          : 'rounded-[var(--radius-at-lg)] border border-[var(--at-success)]/30 bg-[var(--at-success)]/10 p-4'
      }
      role="status"
      aria-live="polite"
    >
      <div className="flex items-start gap-3">
        {partial ? (
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-[var(--at-warning)]" />
        ) : (
          <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-[var(--at-success)]" />
        )}
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-[var(--foreground)]">
            {saved > 0
              ? `Added ${saved} ${saved === 1 ? 'job' : 'jobs'} to your feed`
              : found > 0
                ? 'No new jobs — everything found was already in your feed'
                : 'No jobs matched this search'}
          </p>
          <p className="mt-0.5 text-xs text-[var(--muted-foreground)]">
            Searched {sites_done} of {boardLabel(sites_total)} in {seconds}s
            {partial &&
              ` · ${failures.length} could not be reached: ${failures
                .map((f) => f.source)
                .join(', ')}`}
          </p>
        </div>
        {onDismiss && (
          <button
            type="button"
            onClick={onDismiss}
            className="text-xs font-medium underline"
            style={{ color: stateColor }}
          >
            Dismiss
          </button>
        )}
      </div>
    </div>
  );
}

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
        className="rounded-lg border border-blue-200 bg-blue-50 p-4 dark:border-blue-900 dark:bg-blue-950/40"
        role="status"
        aria-live="polite"
      >
        <div className="flex items-center gap-3">
          <Loader2 className="h-4 w-4 shrink-0 animate-spin text-blue-600 dark:text-blue-400" />
          <div className="min-w-0 flex-1">
            <p className="text-sm font-medium text-blue-900 dark:text-blue-100">
              Searching {boardLabel(sites_total)}…{' '}
              {found > 0 ? `${found} jobs so far` : 'this usually takes 15–30 seconds'}
            </p>
            <p className="mt-0.5 text-xs text-blue-700 dark:text-blue-300">
              {sites_done} of {sites_total} finished · {seconds}s elapsed · you can keep using the
              app
            </p>
          </div>
        </div>
        <div
          className="mt-3 h-1.5 w-full overflow-hidden rounded-full bg-blue-200 dark:bg-blue-900"
          role="progressbar"
          aria-valuenow={pct}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label="Search progress"
        >
          <div
            className="h-full rounded-full bg-blue-600 transition-all duration-500 dark:bg-blue-400"
            style={{ width: `${Math.max(pct, 4)}%` }}
          />
        </div>
      </div>
    );
  }

  if (status === 'failed') {
    return (
      <div
        className="rounded-lg border border-red-200 bg-red-50 p-4 dark:border-red-900 dark:bg-red-950/40"
        role="alert"
      >
        <div className="flex items-start gap-3">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-red-600 dark:text-red-400" />
          <div className="min-w-0 flex-1">
            <p className="text-sm font-medium text-red-900 dark:text-red-100">
              The search did not complete
            </p>
            <p className="mt-0.5 text-xs text-red-700 dark:text-red-300">
              {error ?? 'No reason was reported.'}
              {saved > 0 && ` ${saved} jobs were saved before it stopped.`}
            </p>
          </div>
          {onDismiss && (
            <button
              type="button"
              onClick={onDismiss}
              className="text-xs font-medium text-red-700 underline dark:text-red-300"
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
        className="rounded-lg border border-neutral-200 bg-neutral-50 p-4 dark:border-neutral-800 dark:bg-neutral-900"
        role="status"
      >
        <div className="flex items-start gap-3">
          <RotateCw className="mt-0.5 h-4 w-4 shrink-0 text-neutral-500" />
          <div className="min-w-0 flex-1">
            <p className="text-sm font-medium text-neutral-900 dark:text-neutral-100">
              Lost track of this search
            </p>
            <p className="mt-0.5 text-xs text-neutral-600 dark:text-neutral-400">
              The server restarted while it was running. Any jobs it had already found are in your
              feed — reload to see them.
            </p>
          </div>
          {onRefresh && (
            <button
              type="button"
              onClick={onRefresh}
              className="text-xs font-medium text-neutral-700 underline dark:text-neutral-300"
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
  return (
    <div
      className={
        partial
          ? 'rounded-lg border border-amber-200 bg-amber-50 p-4 dark:border-amber-900 dark:bg-amber-950/40'
          : 'rounded-lg border border-green-200 bg-green-50 p-4 dark:border-green-900 dark:bg-green-950/40'
      }
      role="status"
      aria-live="polite"
    >
      <div className="flex items-start gap-3">
        {partial ? (
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600 dark:text-amber-400" />
        ) : (
          <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-green-600 dark:text-green-400" />
        )}
        <div className="min-w-0 flex-1">
          <p
            className={
              partial
                ? 'text-sm font-medium text-amber-900 dark:text-amber-100'
                : 'text-sm font-medium text-green-900 dark:text-green-100'
            }
          >
            {saved > 0
              ? `Added ${saved} ${saved === 1 ? 'job' : 'jobs'} to your feed`
              : found > 0
                ? 'No new jobs — everything found was already in your feed'
                : 'No jobs matched this search'}
          </p>
          <p
            className={
              partial
                ? 'mt-0.5 text-xs text-amber-700 dark:text-amber-300'
                : 'mt-0.5 text-xs text-green-700 dark:text-green-300'
            }
          >
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
            className={
              partial
                ? 'text-xs font-medium text-amber-700 underline dark:text-amber-300'
                : 'text-xs font-medium text-green-700 underline dark:text-green-300'
            }
          >
            Dismiss
          </button>
        )}
      </div>
    </div>
  );
}
